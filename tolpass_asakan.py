"""
＜分析神の朝刊＞ 生成モジュール（2026-10-10追加）
- OpenSea API v2 からリスト・フロア・オファー・オーナー数・前日のお迎えを取得
- CoinGecko から 1ETH の円価格を取得
- まとめチャンネルにある前号の朝刊を読み、号数と【N日目】カウンタを引き継ぐ
- 文章はAIを使わず、のりが1,300回書いてきた定型文に実データを入れて組み立てる

印のルール（のり定義）
  🕑 = 今のオーナーが2次流通（OpenSea上の売買）で手に入れた
  🌸 = 今のオーナーが誰からも買っていない（ミント・移転で受け取った）
  👴 = 今のオーナーがそのトークンを3回以上出品している（3回未満は（N回目））

単体実行: python tolpass_asakan.py （本文を標準出力に出すだけ。Discordへは投稿しない）
"""

import os
import re
from decimal import Decimal, ROUND_HALF_UP
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

JST = timezone(timedelta(hours=9))
OPENSEA_BASE = "https://api.opensea.io/api/v2"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
DISCORD_BASE = "https://discord.com/api/v10"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
TITLE_MARK = "＜分析神の朝刊＞ #"
OJII_THRESHOLD = 3  # この回数以上の出品で👴

MAX_ATTEMPTS = 3
BACKOFF_SEC = 2
TIMEOUT_SEC = 30


def _get(url: str, **kwargs) -> requests.Response:
    """接続エラー・タイムアウト・429/5xx のときだけリトライするGET。"""
    kwargs.setdefault("timeout", TIMEOUT_SEC)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(url, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.exceptions.HTTPError(f"HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                requests.exceptions.HTTPError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            retryable = status is None or status == 429 or status >= 500
            if attempt == MAX_ATTEMPTS or not retryable:
                raise
            wait = BACKOFF_SEC * (2 ** (attempt - 1))
            print(f"  ⚠ 取得エラー ({attempt}/{MAX_ATTEMPTS}): {e} → {wait}秒後にリトライ", flush=True)
            time.sleep(wait)


# ---------------------------------------------------------------- OpenSea

class OpenSea:
    def __init__(self, slug: str):
        api_key = os.getenv("OPENSEA_API_KEY")
        if not api_key:
            raise RuntimeError("OPENSEA_API_KEY が .env に設定されていません")
        self.slug = slug
        self.headers = {"x-api-key": api_key, "accept": "application/json"}
        self.contract = None

    def _get(self, path: str, **params) -> dict:
        time.sleep(0.3)
        return _get(f"{OPENSEA_BASE}{path}", headers=self.headers, params=params).json()

    def load_contract(self):
        info = self._get(f"/collections/{self.slug}")
        self.contract = info["contracts"][0]["address"].lower()

    def stats(self) -> dict:
        return self._get(f"/collections/{self.slug}/stats")["total"]

    def listings(self) -> dict:
        """有効なETH建てリストを、トークンごとに最安1件へまとめて返す。{token_id: listing}"""
        now = time.time()
        best = {}
        nxt = None
        while True:
            params = {"limit": 200}
            if nxt:
                params["next"] = nxt
            data = self._get(f"/listings/collection/{self.slug}/best", **params)
            for x in data.get("listings", []):
                p = x["protocol_data"]["parameters"]
                price = x["price"]["current"]
                if x.get("status", "ACTIVE") != "ACTIVE" or price["currency"] != "ETH":
                    continue
                if int(p["endTime"]) <= now or int(p["startTime"]) > now:
                    continue
                tid = str(x["asset"]["identifier"])
                eth = int(price["value"]) / 10 ** price["decimals"]
                if tid not in best or eth < best[tid]["eth"]:
                    best[tid] = {"token_id": tid, "eth": eth, "maker": p["offerer"].lower(),
                                 "start": int(p["startTime"])}
            nxt = data.get("next")
            if not nxt:
                break
        return best

    def top_offer(self) -> float:
        """コレクションオファーの1点あたり最高額（WETH）。"""
        now = time.time()
        top = 0.0
        nxt = None
        while True:
            params = {"limit": 100}
            if nxt:
                params["next"] = nxt
            data = self._get(f"/offers/collection/{self.slug}", **params)
            for x in data.get("offers", []):
                p = x["protocol_data"]["parameters"]
                if int(p["endTime"]) <= now:
                    continue
                qty = int(p["consideration"][0]["startAmount"]) or 1
                price = x["price"]
                per_item = int(price["value"]) / 10 ** price["decimals"] / qty
                top = max(top, per_item)
            nxt = data.get("next")
            if not nxt:
                break
        return top

    def sales_since(self, after_ts: int) -> list[dict]:
        out = []
        nxt = None
        while True:
            params = {"event_type": "sale", "after": after_ts, "limit": 200}
            if nxt:
                params["next"] = nxt
            data = self._get(f"/events/collection/{self.slug}", **params)
            out.extend(data.get("asset_events", []))
            nxt = data.get("next")
            if not nxt:
                break
        return out

    def owner_profile(self, token_id: str, owner: str) -> dict:
        """今のオーナーの入手経路（売買か否か）と、入手後の出品回数を返す。
        イベントは新しい順に返るので、オーナーへの移転に行き着くまで遡る。"""
        owner = owner.lower()
        listings = 0
        acquired_ts = None
        bought = False
        sale_ts = set()
        nxt = None
        while True:
            # 指定は "listing" だが、応答では event_type="order"・order_type="listing" で返る
            params = {"event_type": ["listing", "transfer", "sale"], "limit": 200}
            if nxt:
                params["next"] = nxt
            data = self._get(
                f"/events/chain/ethereum/contract/{self.contract}/nfts/{token_id}", **params
            )
            events = data.get("asset_events", [])
            for e in events:
                et = e.get("event_type")
                if et == "sale" and (e.get("buyer") or "").lower() == owner:
                    sale_ts.add(e["event_timestamp"])
                elif et == "transfer" and (e.get("to_address") or "").lower() == owner:
                    if acquired_ts is None or e["event_timestamp"] > acquired_ts:
                        acquired_ts = e["event_timestamp"]
            for e in events:
                if (e.get("event_type") == "order" and e.get("order_type") == "listing"
                        and (e.get("maker") or "").lower() == owner
                        and (acquired_ts is None or e["event_timestamp"] >= acquired_ts)):
                    listings += 1
            nxt = data.get("next")
            if acquired_ts is not None or not nxt:
                break
        if acquired_ts is not None:
            # 売買と移転は同じトランザクションで同時刻に記録される
            bought = any(abs(ts - acquired_ts) <= 60 for ts in sale_ts)
        return {"bought": bought, "listings": max(listings, 1), "acquired_ts": acquired_ts}


def eth_jpy() -> int:
    data = _get(COINGECKO_URL, params={"ids": "ethereum", "vs_currencies": "jpy"}).json()
    return int(round(data["ethereum"]["jpy"]))


# ---------------------------------------------------------------- 前号の読み取り

def fetch_previous(channel_id: int, bot_token: str) -> dict:
    """まとめチャンネルの直近から最新の朝刊を探して、引き継ぐ値を取り出す。"""
    headers = {"Authorization": f"Bot {bot_token}"}
    before = None
    for _ in range(3):  # 直近300件まで
        params = {"limit": 100}
        if before:
            params["before"] = before
        batch = _get(f"{DISCORD_BASE}/channels/{channel_id}/messages",
                     headers=headers, params=params).json()
        if not batch:
            break
        for msg in batch:
            if TITLE_MARK in msg.get("content", ""):
                return parse_previous(msg["content"], msg["timestamp"])
        before = batch[-1]["id"]
        time.sleep(0.5)
    raise RuntimeError("前号の朝刊がチャンネルに見つかりません（号数がずれるため投稿を中止）")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def parse_previous(content: str, iso_ts: str) -> dict:
    def find(pattern, cast=None, default=None):
        m = re.search(pattern, content)
        if not m:
            return default
        return cast(m.group(1)) if cast else m.group(1)

    issue = find(r"＜分析神の朝刊＞ #(\d+)", int)
    if issue is None:
        raise RuntimeError("前号の号数が読み取れません")

    tokens = {}
    for m in re.finditer(r"#(\d+)：\**([\d.]+)ETH", content):
        tokens[m.group(1)] = float(m.group(2))

    list_line = re.search(r"リストは(.*?)$", content, re.M)
    list_line = list_line.group(1) if list_line else ""
    m = re.search(r"動きなし。?【(\d+)日目】", list_line)
    list_still_days = int(m.group(1)) if m else (1 if list_line else None)

    return {
        "issue": issue,
        "posted_at": datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(JST),
        "eth_jpy": find(r"【現在の1ETH：([\d,]+)円】", _num),
        "list_count": find(r"- リスト：(\d+)件", int),
        "owners": find(r"- オーナー：([\d,]+)ウォレット", lambda s: int(_num(s))),
        "tokens": tokens,
        "list_still_days": list_still_days,
        "no_floor_sale_days": find(r"フロアからのお迎えなし。?【(\d+)日目】", int, 0),
    }


# ---------------------------------------------------------------- 本文の組み立て

def fmt_eth(x: float) -> str:
    """これまでの朝刊どおり小数4桁で四捨五入し、末尾の0を落とす（1 → 1.0）。"""
    # OpenSeaの表示と同じ四捨五入（round() は 0.01935 → 0.0193 になるので使わない）
    s = f"{Decimal(str(x)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP):.4f}".rstrip("0")
    return s + "0" if s.endswith(".") else s


def fmt_delta(n: int) -> str:
    return f"（{n:+d}）" if n else ""


def build(slug: str, channel_id: int, bot_token: str, now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    prev = fetch_previous(channel_id, bot_token)
    days = max((now.date() - prev["posted_at"].date()).days, 1)

    os_api = OpenSea(slug)
    os_api.load_contract()
    stats = os_api.stats()
    listings = os_api.listings()
    offer = os_api.top_offer()
    rate = eth_jpy()

    # 前号の取得時刻（6:00）以降の売買。初回は手動号（5:00）からの差分になる
    since = prev["posted_at"].replace(hour=6, minute=0, second=0, microsecond=0)
    if since > prev["posted_at"]:
        since -= timedelta(days=1)
    sales = os_api.sales_since(int(since.timestamp()))
    floor_sales = [s for s in sales if (s.get("payment") or {}).get("symbol") == "ETH"]
    offer_sales = [s for s in sales if (s.get("payment") or {}).get("symbol") == "WETH"]

    rows = []
    for tid, item in listings.items():
        prof = os_api.owner_profile(tid, item["maker"])
        rows.append({**item, **prof, "shown": fmt_eth(item["eth"])})
    # OpenSeaの「Price low to high」と同じく実際の価格の安い順。同額は新しい出品が先
    rows.sort(key=lambda r: (r["eth"], -r["start"]))

    issue = prev["issue"] + 1
    list_count = len(rows)
    floor_eth = float(rows[0]["shown"]) if rows else stats["floor_price"]
    floor_jpy = int(round(floor_eth * rate))
    owners = stats["num_owners"]

    lines = [f"# ＜分析神の朝刊＞ #{issue}", ""]
    if issue % 100 == 0:
        lines += [f"## **今日で{issue:,}日目！！**", ""]
    lines += [
        "## ◆6:00時点のフロア情報",
        f"- フロア：{fmt_eth(floor_eth)}ETH（{floor_jpy:,}円）",
        f"- オファー：{fmt_eth(offer)}WETH",
        f"- リスト：{list_count}件{fmt_delta(list_count - prev['list_count']) if prev['list_count'] is not None else ''}",
        f"- オーナー：{owners:,}ウォレット{fmt_delta(owners - prev['owners']) if prev['owners'] is not None else ''}",
        "",
        f"## 【現在の1ETH：{rate:,}円】",
        "",
        f"## リスト📝 （全{list_count}件）",
        "----------",
    ]
    for r in rows:
        mark = "🕑" if r["bought"] else "🌸"
        count = "👴" if r["listings"] >= OJII_THRESHOLD else f"（{r['listings']}回目）"
        tid = r["token_id"]
        if tid not in prev["tokens"]:
            lines.append(f"**🆕#{tid}：{r['shown']}ETH ▶︎{mark}{count}**")
        elif abs(prev["tokens"][tid] - float(r["shown"])) > 1e-9:
            lines.append(f"#{tid}：**{r['shown']}ETH** ▶︎{mark}{count}")
        else:
            lines.append(f"#{tid}：{r['shown']}ETH ▶︎{mark}{count}")
    lines += ["", "----------", "", "## 【コメント】", ""]

    # イーサ
    if prev["eth_jpy"]:
        # 過去号は四捨五入でなく切り捨て（428,745円 → 42.8万円）
        man = lambda yen: f"{int(yen) // 1000 / 10:.1f}"
        arrow = "↗️" if rate > prev["eth_jpy"] else ("↘️" if rate < prev["eth_jpy"] else "➡️")
        lines.append(f"イーサは {man(prev['eth_jpy'])}万円 → {man(rate)}万円{arrow}")
    lines += [f"オファー価格は、{fmt_eth(offer)}WETH。", ""]

    # リスト件数
    diff = list_count - prev["list_count"] if prev["list_count"] is not None else 0
    if diff == 0:
        still = (prev["list_still_days"] or 0) + days
        lines.append(f"リストは「{list_count}点」のまま動きなし。【{still}日目】")
    else:
        word = "増えて" if diff > 0 else "減って"
        since_txt = ""
        if prev["list_still_days"] and prev["list_still_days"] > 1:
            since_txt = f"{prev['list_still_days'] + days}日ぶりに"
        lines.append(f"リストは{since_txt}{abs(diff)}点{word}「{list_count}点」になりました。")
    if rows and rows[0]["listings"] >= OJII_THRESHOLD:
        lines.append("👴によるフロアハックは続く。")
    lines.append("")

    if rows:
        lines += [f"**{floor_jpy // 1000 * 1000:,}円台**でお迎えできます🙆‍♂️", ""]

    # お迎え
    if floor_sales:
        lines.append(f"フロアから{len(floor_sales)}点お迎えがありました🎉")
    else:
        lines.append(f"フロアからのお迎えなし。【{prev['no_floor_sale_days'] + days}日目】")
    if offer_sales:
        lines.append("ですが、オファーお迎えがあったようですね。" if not floor_sales
                     else "オファーお迎えもあったようですね。")
    lines += ["また新しい出会いがありますように🐿️", "", "### トピック📝", "", "**↓AIのりすけ参照↓**"]
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    servers = json.load(open(os.path.join(os.path.dirname(__file__), "servers.json"), encoding="utf-8"))
    target = next(s for s in servers if s["name"] == "TOLPass")
    slug = (target.get("asakan") or {}).get("opensea_slug", "tolpass")
    print(build(slug, target["summary_channel_id"], os.getenv("DISCORD_BOT_TOKEN")))
