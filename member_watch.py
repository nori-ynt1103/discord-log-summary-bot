"""
明鏡コミュニティ（Discord）メンバー数観測スクリプト
- 参加者数 / 明鏡ロール保持者数を Guild Members API から取得
- 明鏡購入者数（累計）をアフィリエイト通知チャンネルのメッセージから取得
- 履歴（history/member_watch_history.json）に日次で追記し、前日比つきの本文を組み立てて
  ラウンジチャンネルへ投稿する。

main.py と同じ流儀（requests + python-dotenv）で実装。外部依存は既存 requirements の範囲。
GitHub Actions で毎朝 5:00 JST 実行する想定。

使い方:
  python3 member_watch.py --dry-run     # 投稿せず本文を標準出力に出すだけ
  python3 member_watch.py               # 本番投稿
テスト（API をスキップしてダミー値で本文/履歴処理を確認）:
  python3 member_watch.py --dry-run \
      --test-members 8971 --test-buyers 3059 --test-role 2489 \
      --history /path/to/temp_history.json
"""

import argparse
import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
BASE_URL = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
    "Content-Type": "application/json",
}

# --- 明鏡コミュニティの固定ID ---
GUILD_ID = "1352073351882870814"          # 明鏡サーバー
LOUNGE_CHANNEL_ID = "1352074201342672947"  # ラウンジ（投稿先）
AFFILIATE_CHANNEL_ID = "1514734016400592936"  # アフィリエイト通知チャンネル
MEIKYO_ROLE_ID = "1489786702032670730"     # 「明鏡」ロール

CUMULATIVE_RE = re.compile(r"累計\s*([\d,]+)\s*部")
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "history", "member_watch_history.json")


class WatchError(Exception):
    """明確な失敗を投げるための例外。"""


def discord_get(url, params=None):
    """GET（429 は retry_after 秒待って再試行）。"""
    while True:
        resp = requests.get(url, headers=HEADERS, params=params)
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 1))
            except Exception:
                retry_after = 1.0
            print(f"[429] レート制限。{retry_after}s 待機して再試行...", file=sys.stderr)
            time.sleep(retry_after + 0.5)
            continue
        return resp


# ---------------------------------------------------------------------------
# a. 参加者数 と 明鏡ロール保持者数
# ---------------------------------------------------------------------------
def fetch_member_and_role_counts():
    """GET /guilds/{id}/members を after ページネーションで全件取得し、
    (参加者総数, 明鏡ロール保持者数) を返す。"""
    all_members = []
    after = "0"
    while True:
        resp = discord_get(
            f"{BASE_URL}/guilds/{GUILD_ID}/members",
            params={"limit": 1000, "after": after},
        )
        if resp.status_code == 403:
            raise WatchError(
                "参加者数を取得できません: GUILD_MEMBERS インテントが未有効です。"
                "\n→ Discord Developer Portal の Bot 設定で「Server Members Intent」をONにしてください。"
            )
        if not resp.ok:
            raise WatchError(
                f"参加者数の取得に失敗（HTTP {resp.status_code}）: {resp.text[:300]}"
            )
        batch = resp.json()
        if not batch:
            break
        all_members.extend(batch)
        if len(batch) < 1000:
            break
        after = batch[-1]["user"]["id"]
        time.sleep(0.5)

    members = len(all_members)
    role = sum(1 for m in all_members if MEIKYO_ROLE_ID in m.get("roles", []))
    return members, role


# ---------------------------------------------------------------------------
# b. 明鏡購入者数（累計）
# ---------------------------------------------------------------------------
def _searchable_text(msg):
    """メッセージの content と embeds（title/description/fields）を1つの文字列に結合する。"""
    parts = [msg.get("content", "") or ""]
    for emb in msg.get("embeds", []) or []:
        parts.append(emb.get("title", "") or "")
        parts.append(emb.get("description", "") or "")
        for field in emb.get("fields", []) or []:
            parts.append(field.get("name", "") or "")
            parts.append(field.get("value", "") or "")
    return "\n".join(parts)


def fetch_buyer_count(max_messages=500):
    """アフィリエイト通知チャンネルを新しい順にページネーションし、
    「累計 N 部」に最初にマッチした数値を返す。見つからなければ None。"""
    before = None
    scanned = 0
    while scanned < max_messages:
        params = {"limit": 100}
        if before:
            params["before"] = before
        resp = discord_get(
            f"{BASE_URL}/channels/{AFFILIATE_CHANNEL_ID}/messages",
            params=params,
        )
        if resp.status_code == 403:
            raise WatchError(
                "明鏡購入者数を取得できません: アフィリエイト通知チャンネルの閲覧権限がありません（403）。"
                "\n→ チャンネル 1514734016400592936 に Bot の閲覧権限を付与してください。"
            )
        if not resp.ok:
            raise WatchError(
                f"アフィリエイト通知の取得に失敗（HTTP {resp.status_code}）: {resp.text[:300]}"
            )
        batch = resp.json()
        if not batch:
            break
        for msg in batch:
            scanned += 1
            m = CUMULATIVE_RE.search(_searchable_text(msg))
            if m:
                return int(m.group(1).replace(",", ""))
            if scanned >= max_messages:
                break
        before = batch[-1]["id"]
        time.sleep(0.5)
    return None


# ---------------------------------------------------------------------------
# c. 履歴管理
# ---------------------------------------------------------------------------
def load_history(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return []


def save_history(path, history):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")


def upsert_today(history, date_str, members, buyers, role):
    """同日エントリがあれば置き換え、なければ末尾に追記する。
    追記後の履歴と、直前エントリ（なければ None）を返す。"""
    entry = {"date": date_str, "members": members, "buyers": buyers, "role": role}
    idx = next((i for i, e in enumerate(history) if e.get("date") == date_str), None)
    if idx is not None:
        prev = history[idx - 1] if idx > 0 else None
        history[idx] = entry
    else:
        prev = history[-1] if history else None
        history.append(entry)
    return history, entry, prev


# ---------------------------------------------------------------------------
# d/e. 差分計算 と 本文組み立て
# ---------------------------------------------------------------------------
def diff_label(current, prev_value):
    """前日比の表記を返す。計算できなければ「（−）」。"""
    if prev_value is None or current is None:
        return "（−）"
    d = current - prev_value
    if d > 0:
        return f"（+{d}名）"
    if d < 0:
        return f"（{d}名）"  # d は負なので符号込み
    return "（±0名）"


def _md(date_str):
    """'2026-07-22' → '7/22'（ゼロ埋めなし）。"""
    y, m, d = date_str.split("-")
    return f"{int(m)}/{int(d)}"


def _disp_width(s):
    """全角を2、半角を1として表示幅を数える。"""
    w = 0
    for ch in s:
        w += 2 if ord(ch) > 0x2E7F else 1
    return w


def _pad(s, width):
    """表示幅 width まで右側を半角スペースで埋める。"""
    return s + " " * max(0, width - _disp_width(s))


def build_recent_table(history, days=7):
    """直近 days 日分の推移テーブル（コードブロック内の中身）を返す。"""
    recent = history[-days:]
    headers = ["日付", "参加者", "購入者", "ロール"]
    rows = [headers]
    for e in recent:
        rows.append([
            _md(e["date"]),
            f"{e['members']:,}" if e.get("members") is not None else "-",
            f"{e['buyers']:,}" if e.get("buyers") is not None else "-",
            f"{e['role']:,}" if e.get("role") is not None else "-",
        ])
    # 列ごとの最大表示幅
    ncol = len(headers)
    widths = [max(_disp_width(row[i]) for row in rows) for i in range(ncol)]
    lines = []
    for row in rows:
        cells = [_pad(row[i], widths[i]) for i in range(ncol)]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def build_body(history, entry, prev):
    date_md = _md(entry["date"])
    members = entry["members"]
    buyers = entry["buyers"]
    role = entry["role"]

    members_diff = diff_label(members, prev.get("members") if prev else None)
    buyers_diff = diff_label(buyers, prev.get("buyers") if prev else None)
    role_diff = diff_label(role, prev.get("role") if prev else None)

    if buyers and buyers > 0 and role is not None:
        pct = round(role / buyers * 100)
        role_pct = f"（購入者の約{pct}%）"
    else:
        role_pct = ""

    members_str = f"{members:,}" if members is not None else "-"
    buyers_str = f"{buyers:,}" if buyers is not None else "-"
    role_str = f"{role:,}" if role is not None else "-"

    table = build_recent_table(history)

    body = (
        f"【メンバー数を観測（{date_md}）】\n"
        f"・参加者：{members_str}名{members_diff}\n"
        f"・明鏡購入者：{buyers_str}名{buyers_diff}\n"
        f"・明鏡ロール：{role_str}名{role_diff}{role_pct}\n"
        f"\n"
        f"■ 直近1週間の推移\n"
        f"```\n"
        f"{table}\n"
        f"```"
    )
    return body


# ---------------------------------------------------------------------------
# f. 投稿
# ---------------------------------------------------------------------------
def post_message(channel_id, content):
    while True:
        resp = requests.post(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=HEADERS,
            json={"content": content},
        )
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 1))
            except Exception:
                retry_after = 1.0
            print(f"[429] レート制限。{retry_after}s 待機して再試行...", file=sys.stderr)
            time.sleep(retry_after + 0.5)
            continue
        if not resp.ok:
            raise WatchError(
                f"投稿に失敗（HTTP {resp.status_code}）: {resp.text[:300]}"
            )
        return


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="明鏡コミュニティ メンバー数観測")
    parser.add_argument("--dry-run", action="store_true", help="投稿せず本文を標準出力に出す")
    parser.add_argument("--history", default=HISTORY_PATH, help="履歴JSONファイルのパス")
    # テスト用（API をスキップしてダミー値を注入。3つすべて指定したときのみ有効）
    parser.add_argument("--test-members", type=int, default=None)
    parser.add_argument("--test-buyers", type=int, default=None)
    parser.add_argument("--test-role", type=int, default=None)
    args = parser.parse_args()

    test_mode = args.test_members is not None or args.test_buyers is not None or args.test_role is not None

    if not test_mode and not DISCORD_BOT_TOKEN:
        print("エラー: DISCORD_BOT_TOKEN が未設定です（.env / GitHub Secrets を確認）。", file=sys.stderr)
        sys.exit(1)

    today_str = datetime.now(JST).strftime("%Y-%m-%d")

    try:
        if test_mode:
            print("[テストモード] API をスキップしてダミー値を使用します。", file=sys.stderr)
            members = args.test_members
            buyers = args.test_buyers
            role = args.test_role
        else:
            members, role = fetch_member_and_role_counts()
            buyers = fetch_buyer_count()
            if buyers is None:
                raise WatchError(
                    "明鏡購入者数（累計）が直近500件のメッセージから見つかりませんでした。"
                    "\n→ 正規表現・チャンネル・スキャン件数を確認してください。"
                )
    except WatchError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    history = load_history(args.history)
    history, entry, prev = upsert_today(history, today_str, members, buyers, role)
    body = build_body(history, entry, prev)

    if args.dry_run:
        print("----- DRY RUN（投稿しません）-----")
        print(body)
        print("----- END DRY RUN -----")
        # dry-run では履歴を書き戻さない（本番のみ永続化する）
        return

    save_history(args.history, history)
    post_message(LOUNGE_CHANNEL_ID, body)
    print(f"投稿完了: 参加者{members} / 購入者{buyers} / ロール{role}")


if __name__ == "__main__":
    main()
