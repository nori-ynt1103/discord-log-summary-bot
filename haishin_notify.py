#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""イケハヤさんの新着を、Discord のチャンネルへ1回1通にまとめて知らせる。

二重投稿を防ぐため、1回の実行を「判定」と「投稿」の2回の起動に分けている。
  ① 判定: python3 haishin_notify.py --state 既読.json --message-out 文面.txt
       取得 → 新着判定 → 既読ファイルを書き換え → 文面ファイルを書く（投稿はしない）
  ② ワークフローが既読ファイルを haishin-state ブランチへ push（失敗したら③へ進まない）
  ③ 投稿: python3 haishin_notify.py --post 文面.txt --channel-id <ID>

確認用:
  python3 haishin_notify.py --dry-run                     # 既読を書き換えず、文面を表示するだけ
  python3 haishin_notify.py --dry-run --replay-hours 36   # 既読を無視して直近36時間を新着扱いにした文面
  python3 haishin_notify.py --check-channel <ID>          # チャンネル情報と投稿権限を読み取りAPIだけで確認

外部依存なし（標準ライブラリのみ）。Python 3.9 以上。
"""

import argparse
import json
import os
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCES = os.path.join(HERE, "haishin_sources.json")
DEFAULT_STATE = os.path.join(HERE, "haishin_seen.json")

JST = timezone(timedelta(hours=9))
FEED_UA = "Mozilla/5.0 (compatible; feed-reader)"
DISCORD_UA = "DiscordBot (https://discord.com/developers, 1.0)"
DISCORD_API = "https://discord.com/api/v10"
X_API = "https://api.x.com/2"
TIMEOUT = 20

NOTIFY_WINDOW = timedelta(hours=48)   # 公開からこれを超えた項目は通知しない（既読にだけする）
TITLE_MEMORY = timedelta(hours=48)    # 通知済みタイトルを覚えておく期間（後追い落とし用）
MAX_LINES = 8                         # 1通に載せる最大項目数
ANOMALY_LINES = 15                    # 束ねた後の項目数がこれを超えたら異常とみなし通知しない
MAX_CHARS = 1900                      # Discord の上限2000字に対して余裕を見た上限
MAX_SEEN = 500                        # 媒体ごとに覚えておく既読IDの上限
TITLE_MAX = 70                        # 1行に載せるタイトルの最大文字数

HEADER = "📮 イケハヤさんの新着"
KIND_ORDER = ["X記事", "新教材", "記事", "音声", "動画"]
LINK_ORDER = ["note", "substack", "standfm", "youtube_radio", "youtube_daigaku", "x_article", "brain"]

NS_ATOM = "{http://www.w3.org/2005/Atom}"
NS_YT = "{http://www.youtube.com/xml/schemas/2015}"


class FetchError(Exception):
    pass


def log(msg):
    print(msg, flush=True)


def gh_warning(msg):
    # GitHub Actions 上では注釈として目立たせる（ローカルではただの1行）
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::warning::" + msg.replace("\n", " "), flush=True)
    else:
        log("[警告] " + msg)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def http_request(url, headers=None, method="GET", body=None):
    """(status, bytes) を返す。通信エラーは FetchError。ヘッダーはログに出さない。"""
    h = {"User-Agent": FEED_UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        try:
            data = e.read()
        except Exception:
            data = b""
        return e.code, data
    except urllib.error.URLError as e:
        raise FetchError("接続できない: %s" % e.reason)
    except Exception as e:  # タイムアウト等
        raise FetchError("%s: %s" % (type(e).__name__, e))


def http_get(url, headers=None):
    status, data = http_request(url, headers=headers)
    if status != 200:
        raise FetchError("HTTP %s: %s" % (status, short_body(data)))
    return data


def short_body(data):
    try:
        return " ".join(data.decode("utf-8", "replace").split())[:200]
    except Exception:
        return ""


def load_json_bytes(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise FetchError("JSONとして読めない: %s" % e)


def parse_xml(raw):
    try:
        return ET.fromstring(raw)
    except ET.ParseError as e:
        raise FetchError("XMLとして読めない: %s" % e)


def parse_iso(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Python 3.9 の fromisoformat は小数秒の桁数にうるさいので6桁にそろえる
    if "." in s:
        head, rest = s.split(".", 1)
        frac = ""
        tz = ""
        for i, ch in enumerate(rest):
            if not ch.isdigit():
                frac, tz = rest[:i], rest[i:]
                break
        else:
            frac = rest
        s = head + "." + (frac + "000000")[:6] + tz
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_rfc822(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# 取得（媒体ごと）。返り値は item の配列
#   item = {source, id, title, url, published, kind, label}
# ---------------------------------------------------------------------------
def make_item(src, iid, title, url, published, kind=None, label=None):
    return {
        "source": src["key"],
        "id": str(iid),
        "title": " ".join((title or "").split()),
        "url": (url or "").strip(),
        "published": published,
        "kind": kind or src.get("kind", "記事"),
        "label": label or src.get("label", src["key"]),
    }


def fetch_rss(src):
    root = parse_xml(http_get(src["url"]))
    limit = int(src.get("max_items") or 50)
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or "").strip()
        items.append(make_item(src, guid or link, it.findtext("title"), link,
                               parse_rfc822(it.findtext("pubDate"))))
        if len(items) >= limit:
            break
    return items


def fetch_youtube(src):
    root = parse_xml(http_get(src["url"]))
    items = []
    for e in root.iter(NS_ATOM + "entry"):
        vid = (e.findtext(NS_YT + "videoId") or "").strip()
        link_el = e.find(NS_ATOM + "link")
        link = link_el.get("href", "") if link_el is not None else ""
        items.append(make_item(src, vid or link, e.findtext(NS_ATOM + "title"), link,
                               parse_iso(e.findtext(NS_ATOM + "published"))))
    return items


def fetch_brain(src):
    data = load_json_bytes(http_get(src["url"]))
    arts = data.get("data") if isinstance(data, dict) else None
    if not isinstance(arts, list):
        raise FetchError("想定した形のJSONではない（data が配列でない）")
    items = []
    for a in arts:
        aid = a.get("id")
        if not aid:
            continue
        account = ((a.get("user") or {}).get("account")) or "ikehaya"
        items.append(make_item(src, aid, a.get("title"),
                               "https://brain-market.com/u/%s/a/%s" % (account, aid),
                               parse_iso(a.get("published_at"))))
    return items


def fetch_substack(src):
    """API で有料/無料・記事/音声を見分ける。API が落ちたら RSS に退避（区別なし表示）。
    既読キーは canonical_url（RSS の link と同じ値）なので、退避しても全件が新着化しない。"""
    try:
        data = load_json_bytes(http_get(src["url"]))
        posts = data.get("posts") if isinstance(data, dict) else data
        if not isinstance(posts, list):
            raise FetchError("想定した形のJSONではない（posts が配列でない）")
        items = []
        for p in posts:
            url = (p.get("canonical_url") or "").strip()
            if not url:
                continue
            paid = "無料" if p.get("audience") == "everyone" else "有料"
            if p.get("type") == "podcast":
                kind, label = "音声", "Substack音声（%s）" % paid
            else:
                kind, label = "記事", "Substack（%s）" % paid
            items.append(make_item(src, url, p.get("title"), url, parse_iso(p.get("post_date")),
                                   kind=kind, label=label))
        if not items:
            raise FetchError("記事が1件も取れなかった")
        return items, None
    except FetchError as e:
        api_err = str(e)
    try:
        root = parse_xml(http_get(src["fallback_rss"]))
    except FetchError as e:  # 両方落ちたら媒体ごと失敗。どちらの理由も残す
        raise FetchError("API：%s ／ RSS：%s" % (api_err[:120], str(e)[:120]))
    items = []
    for it in root.iter("item"):
        link = (it.findtext("link") or "").strip()
        enc = it.find("enclosure")
        audio = enc is not None and (enc.get("type") or "").startswith("audio/")
        items.append(make_item(src, link, it.findtext("title"), link,
                               parse_rfc822(it.findtext("pubDate")),
                               kind="音声" if audio else "記事",
                               label="Substack音声" if audio else "Substack"))
    return items, "API失敗のためRSSに退避（%s）。有料/無料の区別なしで表示" % api_err


# ---------------------------------------------------------------------------
# X記事
# ---------------------------------------------------------------------------
def is_x_article(t):
    if t.get("article"):
        return True
    urls = ((t.get("entities") or {}).get("urls")) or []
    return any("x.com/i/article/" in (u.get("expanded_url") or "") for u in urls)


def fetch_x_articles(src, xstate, now, replay):
    """(status, note, items, first_run) を返す。xstate は書き換える（since_id・最終取得時刻）。"""
    token = (os.environ.get("X_BEARER_TOKEN") or "").strip()
    if not token:
        return "skipped", "X記事：未設定のためスキップ", [], False
    interval = timedelta(minutes=int(src.get("interval_minutes") or 60))
    last = parse_iso(xstate.get("last_fetch"))
    if last and now - last < interval:
        return "skipped", "X記事：前回の取得から%d分未満のためスキップ（従量課金の間引き）" % (interval.seconds // 60), [], False

    headers = {"Authorization": "Bearer " + token}
    # 読み取りの成否にかかわらず「この時刻に読みに行った」を記録して間引く
    xstate["last_fetch"] = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    username = src.get("username", "IHayato")
    if not xstate.get("user_id"):
        data = load_json_bytes(http_get("%s/users/by/username/%s" % (X_API, username), headers))
        uid = ((data or {}).get("data") or {}).get("id")
        if not uid:
            raise FetchError("ユーザーIDが取れなかった")
        xstate["user_id"] = uid
    since_id = None if replay else xstate.get("since_id")
    params = {
        "exclude": "retweets,replies",
        "tweet.fields": "created_at,article,entities",
        "max_results": "20" if since_id else "5",
    }
    if since_id:
        params["since_id"] = since_id
    url = "%s/users/%s/tweets?%s" % (X_API, xstate["user_id"], urllib.parse.urlencode(params))
    data = load_json_bytes(http_get(url, headers))
    tweets = data.get("data") or []
    newest = (data.get("meta") or {}).get("newest_id")
    if newest and not replay:
        xstate["since_id"] = newest
    items = []
    for t in tweets:
        if not is_x_article(t):
            continue
        title = ((t.get("article") or {}).get("title")) or (t.get("text") or "").split("\n")[0][:80]
        items.append(make_item(src, t.get("id"), title,
                               "https://x.com/%s/status/%s" % (username, t.get("id")),
                               parse_iso(t.get("created_at"))))
    note = "X記事：投稿%d件を読み、うち記事%d件" % (len(tweets), len(items))
    return "ok", note, items, since_id is None and not replay


FETCHERS = {"rss": fetch_rss, "youtube": fetch_youtube, "brain": fetch_brain}


# ---------------------------------------------------------------------------
# 既読ファイル
# ---------------------------------------------------------------------------
def empty_state():
    return {"version": 1, "sources": {}, "recent_titles": {}, "x": {}}


def load_state(path):
    if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
        return empty_state()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    st = empty_state()
    if isinstance(data, dict):
        st.update({k: v for k, v in data.items() if k in st})
    return st


def save_state(path, state):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".haishin.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ---------------------------------------------------------------------------
# タイトルの正規化・束ね・文面
# ---------------------------------------------------------------------------
def norm_title(title):
    """空白・記号・括弧類を除き、全角半角をそろえ、小文字にする。文字と数字だけ残す。"""
    t = unicodedata.normalize("NFKC", title or "").lower()
    return "".join(ch for ch in t if unicodedata.category(ch)[0] in ("L", "N"))


def fmt_jst(dt):
    return dt.astimezone(JST).strftime("%m/%d %H:%M") if dt else "日時不明"


def md_escape(s):
    out = []
    for ch in s:
        if ch in "\\*_~`|>[]":
            out.append("\\")
        out.append(ch)
    return "".join(out)


def group_items(items):
    groups = {}
    order = []
    for it in items:
        key = norm_title(it["title"]) or ("id:" + it["source"] + ":" + it["id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(it)
    return [(k, groups[k]) for k in order]


def link_sort_key(it):
    pos = LINK_ORDER.index(it["source"]) if it["source"] in LINK_ORDER else len(LINK_ORDER)
    return (pos, 0 if it["kind"] == "記事" else 1, it["label"])


def group_latest(members):
    ds = [m["published"] for m in members if m["published"]]
    return max(ds) if ds else datetime.min.replace(tzinfo=timezone.utc)


def render_line(members, audio_also_on):
    kinds = {m["kind"] for m in members}
    if "動画" in kinds and len(kinds) > 1:
        kinds.discard("動画")  # YouTube のラジオ版は音声の動画版なので「音声」で代表させる
    kind_label = "・".join(k for k in KIND_ORDER if k in kinds) or "新着"
    first = min(members, key=lambda m: m["published"] or datetime.max.replace(tzinfo=timezone.utc))
    title = first["title"] or "（タイトルなし）"
    if len(title) > TITLE_MAX:
        title = title[:TITLE_MAX] + "…"
    links = []
    seen_urls = set()
    for m in sorted(members, key=link_sort_key):
        if not m["url"] or m["url"] in seen_urls:
            continue
        seen_urls.add(m["url"])
        links.append("[%s](<%s>)" % (m["label"], m["url"]))
    line = "・%s「%s」 — %s" % (kind_label, md_escape(title), "／".join(links))
    if any(m["source"] == "standfm" for m in members) and audio_also_on:
        also = "・".join("[%s](<%s>)" % (a["label"], a["url"]) for a in audio_also_on)
        line += "（%sでも配信）" % also
    return line


def build_message(groups, audio_also_on):
    """groups は新しい順。最大8項目・1900字以内に収め、あふれた分は「ほか◯件」。"""
    lines = [render_line(m, audio_also_on) for _, m in groups]
    out = [HEADER]
    shown = 0
    for i, line in enumerate(lines):
        rest = len(lines) - i - 1
        footer = len("\nほか%d件" % (rest + 1)) if rest > 0 else 0
        candidate = "\n".join(out + [line])
        if shown >= MAX_LINES or len(candidate) + footer > MAX_CHARS:
            break
        out.append(line)
        shown += 1
    if shown == 0:
        return None
    if shown < len(lines):
        out.append("ほか%d件" % (len(lines) - shown))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 判定（①）
# ---------------------------------------------------------------------------
def run_check(args):
    with open(args.sources, encoding="utf-8") as f:
        conf = json.load(f)
    sources = [s for s in conf.get("sources", []) if s.get("enabled", True)]
    audio_also_on = conf.get("audio_also_on") or []

    state = load_state(args.state)
    now = datetime.now(timezone.utc)
    replay = None
    if args.dry_run and args.replay_hours and args.replay_hours > 0:
        replay = timedelta(hours=args.replay_hours)
    elif args.replay_hours:
        log("（--replay-hours は --dry-run のときだけ有効。今回は無視）")

    mode = "確認のみ（既読は書き換えない）" if args.dry_run else "本番判定"
    if replay:
        mode += "・既読を無視して直近%d時間を新着扱い" % args.replay_hours
    log("== イケハヤさん新着チェック %s JST（%s） ==" % (now.astimezone(JST).strftime("%Y-%m-%d %H:%M"), mode))

    changed = False
    new_items = []
    absorbed_titles = set()   # 初回の既読化で吸収したタイトル（同じ実行内の後追いも落とす）
    attempted = 0
    failed = []

    for src in sources:
        key = src["key"]
        if src["type"] == "x_article":
            xstate = dict(state.get("x") or {})
            try:
                status, note, items, first_run = fetch_x_articles(src, xstate, now, replay)
            except FetchError as e:
                attempted += 1
                failed.append(key)
                log("[%s] 取得失敗：%s（既読は進めない）" % (key, e))
            except Exception as e:  # 想定外の応答でも他の媒体は巻き添えにしない
                attempted += 1
                failed.append(key)
                log("[%s] 取得失敗：%s: %s（既読は進めない）" % (key, type(e).__name__, e))
            if key in failed:
                # 既読（since_id）は元に戻し、間引き用の時刻とユーザーIDだけ残す
                old_since = (state.get("x") or {}).get("since_id")
                xstate.pop("since_id", None)
                if old_since:
                    xstate["since_id"] = old_since
                if xstate != (state.get("x") or {}):
                    state["x"] = xstate
                    changed = True
                continue
            if status == "skipped":
                log("[%s] %s" % (key, note))
                continue
            attempted += 1
            log("[%s] %s" % (key, note))
            if xstate != (state.get("x") or {}):
                state["x"] = xstate
                changed = True
            if first_run:
                log("[%s] 初回のため通知なし・基準（since_id）の記録のみ" % key)
                for it in items:
                    absorbed_titles.add(norm_title(it["title"]))
                continue
            new_items.extend(items)
            continue

        attempted += 1
        extra = None
        try:
            if src["type"] == "substack":
                items, extra = fetch_substack(src)
            else:
                items = FETCHERS[src["type"]](src)
            items = [i for i in items if i["id"]]
            if not items:
                raise FetchError("1件も取れなかった（形式が変わった可能性）")
        except FetchError as e:
            failed.append(key)
            log("[%s] 取得失敗：%s（既読は進めない）" % (key, e))
            continue
        except Exception as e:  # 想定外でも他の媒体は続ける
            failed.append(key)
            log("[%s] 取得失敗：%s: %s（既読は進めない）" % (key, type(e).__name__, e))
            continue
        if extra:
            gh_warning("[%s] %s" % (key, extra))

        seen_ids = list(((state["sources"].get(key) or {}).get("ids")) or [])
        seen_set = set(seen_ids)
        latest = max(items, key=lambda i: i["published"] or datetime.min.replace(tzinfo=timezone.utc))
        if replay:
            new = [i for i in items if i["published"] and now - i["published"] <= replay]
            log("[%s] 取得%d件・直近%d時間ぶん%d件（最新 %s）" % (key, len(items), args.replay_hours, len(new),
                                                     fmt_jst(latest["published"])))
        elif not seen_ids:
            new = []
            for i in items:
                if i["published"] and now - i["published"] <= NOTIFY_WINDOW:
                    absorbed_titles.add(norm_title(i["title"]))
            log("[%s] 取得%d件・初回のため通知なし・既読化のみ（最新 %s）" % (key, len(items), fmt_jst(latest["published"])))
        else:
            new = [i for i in items if i["id"] not in seen_set]
            log("[%s] 取得%d件・未読%d件（最新 %s）" % (key, len(items), len(new), fmt_jst(latest["published"])))

        if not replay:
            fresh_ids = []
            for i in items:
                if i["id"] not in seen_set and i["id"] not in fresh_ids:
                    fresh_ids.append(i["id"])
            if fresh_ids:
                state["sources"][key] = {"ids": (fresh_ids + seen_ids)[:MAX_SEEN]}
                changed = True
        new_items.extend(new)

    # 公開から48時間を超えたものは通知しない
    fresh = []
    for it in new_items:
        if it["published"] and now - it["published"] <= NOTIFY_WINDOW:
            fresh.append(it)
        else:
            log("  - 48時間超または日時不明のため通知せず既読化のみ：[%s] %s（%s）" % (
                it["source"], it["title"][:40], fmt_jst(it["published"])))

    # 通知済みタイトル（48時間）と同じものは後追いとして落とす
    memory = {} if replay else dict(state.get("recent_titles") or {})
    active_memory = {k for k, v in memory.items() if parse_iso(v) and now - parse_iso(v) <= TITLE_MEMORY}
    groups = []
    for key, members in group_items(fresh):
        if not replay and (key in active_memory or key in absorbed_titles):
            for m in members:
                log("  - 後追い（同じタイトルを通知済み）のため通知せず：[%s] %s" % (m["source"], m["title"][:40]))
            continue
        groups.append((key, members))
    groups.sort(key=lambda g: group_latest(g[1]), reverse=True)

    # 通知したタイトル（と初回で吸収したタイトル）を覚える。期限切れの掃除は他の変更があるときだけ
    remember = [k for k, _ in groups if not k.startswith("id:")] + [t for t in absorbed_titles if t]
    if not replay and remember:
        stamp = now.isoformat(timespec="seconds")
        for k in remember:
            memory[k] = stamp
        changed = True
    if not replay and changed:
        state["recent_titles"] = {k: v for k, v in memory.items()
                                  if parse_iso(v) and now - parse_iso(v) <= TITLE_MEMORY}

    message = None
    anomaly = False
    if len(groups) > ANOMALY_LINES:
        anomaly = True
        gh_warning("新着が%d項目あり、上限%d項目を超えたため異常とみなし通知しない（既読化のみ）" % (len(groups), ANOMALY_LINES))
    elif groups:
        message = build_message(groups, audio_also_on)

    log("")
    log("取得：%d媒体中 失敗%d（%s）" % (attempted, len(failed), "、".join(failed) or "なし"))
    log("通知する項目：%d（束ねた後の数）" % (0 if anomaly else len(groups)))
    if replay and anomaly:
        log("（replay の検収用に、異常判定でも文面を表示する）")
        message = build_message(groups, audio_also_on)

    if message:
        log("---- 投稿予定の文面（%d字）----" % len(message))
        log(message)
        log("---- ここまで ----")
    else:
        log("投稿予定の文面：なし（投稿しない）")

    if failed:
        gh_warning("取得に失敗した媒体：%s（既読は進めていない）" % "、".join(failed))

    if not args.dry_run:
        if changed:
            save_state(args.state, state)
            log("既読ファイルを更新：%s" % args.state)
        else:
            log("既読ファイルに変更なし")
        if args.message_out:
            if message and not anomaly:
                with open(args.message_out, "w", encoding="utf-8") as f:
                    f.write(message)
            elif os.path.exists(args.message_out):
                os.remove(args.message_out)
    write_gh_output("has_message", "true" if (message and not anomaly and not args.dry_run) else "false")

    if attempted > 0 and len(failed) == attempted:
        log("全媒体の取得に失敗したため失敗終了")
        return 1
    if attempted == 0:
        log("取得を試みた媒体がない")
        return 1
    return 0


def write_gh_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s=%s\n" % (name, value))


# ---------------------------------------------------------------------------
# Discord（③投稿・チャンネル確認）
# ---------------------------------------------------------------------------
def discord_headers():
    token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    if not token:
        return None
    return {"Authorization": "Bot " + token, "User-Agent": DISCORD_UA, "Content-Type": "application/json"}


def discord_get(path, headers):
    status, data = http_request(DISCORD_API + path, headers=headers)
    if status != 200:
        raise FetchError("GET %s → HTTP %s: %s" % (path.split("?")[0], status, short_body(data)))
    return load_json_bytes(data)


def run_post(args):
    if (os.environ.get("HAISHIN_DRY_RUN") or "").lower() == "true":
        log("dry_run 指定中のため投稿しない（歯止め）")
        return 1
    if not args.channel_id:
        log("--channel-id がない")
        return 1
    headers = discord_headers()
    if not headers:
        log("DISCORD_BOT_TOKEN が未設定")
        return 1
    with open(args.post, encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        log("文面が空のため投稿しない")
        return 0
    if len(content) > 2000:
        log("文面が2000字を超えている（%d字）ため投稿しない" % len(content))
        return 1
    body = json.dumps({
        "content": content,
        "allowed_mentions": {"parse": []},
        "flags": 4,  # 埋め込みを出さない（リンクは <URL> でも抑止済み）
    }).encode("utf-8")
    url = "%s/channels/%s/messages" % (DISCORD_API, args.channel_id)
    for attempt in range(3):
        status, data = http_request(url, headers=headers, method="POST", body=body)
        if status == 429:
            try:
                wait = float(json.loads(data.decode("utf-8")).get("retry_after", 1))
            except Exception:
                wait = 1.0
            log("[429] レート制限。%.1f秒待って再試行" % wait)
            time.sleep(min(wait, 30) + 0.5)
            continue
        if 200 <= status < 300:
            log("投稿しました（チャンネル %s・%d字）" % (args.channel_id, len(content)))
            return 0
        # 5xx は実は投稿済みのこともあるので再試行しない（二重投稿防止）
        log("投稿に失敗：HTTP %s: %s" % (status, short_body(data)))
        return 1
    log("投稿に失敗：レート制限が続いた")
    return 1


VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11
SEND_IN_THREADS = 1 << 38
ADMINISTRATOR = 1 << 3
THREAD_TYPES = (10, 11, 12)


def run_check_channel(channel_id):
    headers = discord_headers()
    if not headers:
        log("[Discord] DISCORD_BOT_TOKEN が未設定のためチャンネル確認はスキップ")
        return
    try:
        ch = discord_get("/channels/%s" % channel_id, headers)
    except FetchError as e:
        log("[Discord] チャンネル取得失敗：%s" % e)
        return
    log("[Discord] チャンネル取得OK：#%s（type=%s）" % (ch.get("name"), ch.get("type")))
    # 以下、読み取りAPIだけで bot の権限を計算する（投稿はしない）
    try:
        me = discord_get("/users/@me", headers)
        gid = ch.get("guild_id")
        guild = discord_get("/guilds/%s" % gid, headers)
        member = discord_get("/guilds/%s/members/%s" % (gid, me["id"]), headers)
        perm_ch = ch
        if ch.get("type") in THREAD_TYPES and ch.get("parent_id"):
            perm_ch = discord_get("/channels/%s" % ch["parent_id"], headers)
        roles = {r["id"]: int(r.get("permissions", 0)) for r in guild.get("roles", [])}
        perms = roles.get(gid, 0)
        for rid in member.get("roles", []):
            perms |= roles.get(rid, 0)
        if perms & ADMINISTRATOR or guild.get("owner_id") == me["id"]:
            log("[Discord] bot（%s）は管理者権限あり → 閲覧・投稿とも可" % me.get("username"))
            return
        ows = {o["id"]: o for o in perm_ch.get("permission_overwrites", [])}
        if gid in ows:
            perms = (perms & ~int(ows[gid]["deny"])) | int(ows[gid]["allow"])
        allow = deny = 0
        for rid in member.get("roles", []):
            if rid in ows:
                allow |= int(ows[rid]["allow"])
                deny |= int(ows[rid]["deny"])
        perms = (perms & ~deny) | allow
        if me["id"] in ows:
            perms = (perms & ~int(ows[me["id"]]["deny"])) | int(ows[me["id"]]["allow"])
        need = SEND_IN_THREADS if ch.get("type") in THREAD_TYPES else SEND_MESSAGES
        log("[Discord] bot（%s）の権限：閲覧=%s・投稿=%s（ロールとチャンネルの上書き設定から計算）" % (
            me.get("username"), "可" if perms & VIEW_CHANNEL else "不可", "可" if perms & need else "不可"))
    except FetchError as e:
        log("[Discord] 権限の計算に必要な読み取りに失敗：%s" % e)
    except Exception as e:
        log("[Discord] 権限の計算で想定外のエラー：%s" % type(e).__name__)


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="イケハヤさんの新着を Discord へ1通にまとめて知らせる")
    ap.add_argument("--sources", default=DEFAULT_SOURCES)
    ap.add_argument("--state", default=DEFAULT_STATE, help="既読ファイルのパス")
    ap.add_argument("--message-out", help="投稿予定の文面を書くファイル（判定のみ。投稿はしない）")
    ap.add_argument("--dry-run", action="store_true", help="既読を書き換えず文面を表示するだけ")
    ap.add_argument("--replay-hours", type=int, default=0, help="dry-run のときだけ有効。既読を無視して直近N時間を新着扱い")
    ap.add_argument("--post", help="この文面ファイルを投稿する（判定はしない）")
    ap.add_argument("--channel-id", help="投稿先チャンネルID（--post 用）")
    ap.add_argument("--check-channel", help="チャンネル情報と bot の権限を読み取りAPIだけで確認する")
    args = ap.parse_args()

    if args.post:
        return run_post(args)
    if args.check_channel:
        run_check_channel(args.check_channel)
        return 0
    return run_check(args)


if __name__ == "__main__":
    sys.exit(main())
