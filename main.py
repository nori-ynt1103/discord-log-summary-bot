"""
Discord 日報生成スクリプト
- servers.json に定義されたサーバーごとにメッセージを取得
- Claude APIで要約して各サーバーの投稿先チャンネルに投稿
GitHub Actionsで毎日定時実行する。
"""

import json
import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from summarizer import Summarizer

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
# DRY_RUN=1 のときはDiscordへの投稿だけをスキップし、要約本文を標準出力に出す。
# 取得・要約までは本番と同じ経路を通るので、本番チャンネルを汚さずに疎通確認できる（2026-08-28追加）
DRY_RUN = os.getenv("DRY_RUN") == "1"
JST = timezone(timedelta(hours=9))
BASE_URL = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
    "Content-Type": "application/json",
}


def load_servers() -> list[dict]:
    """servers.json からサーバー設定を読み込む。"""
    config_path = os.path.join(os.path.dirname(__file__), "servers.json")
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


def datetime_to_snowflake(dt: datetime) -> int:
    """datetimeをDiscord snowflake IDに変換する。"""
    ms = int(dt.timestamp() * 1000)
    return (ms - 1420070400000) << 22


def fetch_messages(channel_id: int, target_date, exclude_user_ids=None) -> list[dict]:
    """指定日（JST）のメッセージを全件取得する。

    exclude_user_ids に入れたユーザーのメッセージは、要約処理に渡す前の
    この時点で捨てる（オプトアウト対応）。除外された本文はLLMへ送られず、
    メモリ上にも残らない。空／未指定なら従来どおり全件を対象にする。
    """
    excluded = {str(uid) for uid in (exclude_user_ids or [])}
    start_jst = datetime(
        target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=JST
    )
    end_jst = start_jst + timedelta(days=1)
    start_utc = start_jst.astimezone(timezone.utc)
    end_utc = end_jst.astimezone(timezone.utc)

    last_id = str(datetime_to_snowflake(end_jst))
    messages = []
    skipped = 0

    while True:
        resp = requests.get(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=HEADERS,
            params={"limit": 100, "before": last_id},
        )
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        reached_start = False
        for msg in batch:
            ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
            if ts < start_utc:
                reached_start = True
                break
            if ts < end_utc:
                if str(msg["author"]["id"]) in excluded:
                    skipped += 1
                    continue
                messages.append({
                    "timestamp": ts.astimezone(JST).strftime("%H:%M"),
                    "author": msg["author"].get("global_name") or msg["author"]["username"],
                    "content": msg["content"],
                    "attachments": " | ".join(a["url"] for a in msg.get("attachments", [])),
                })

        if reached_start:
            break

        last_id = batch[-1]["id"]
        time.sleep(0.5)

    if skipped:
        print(f"  （オプトアウト設定により {skipped} 件を除外）")

    messages.reverse()
    return messages


def get_channel_name(channel_id: int) -> str:
    """チャンネル名を取得する。"""
    resp = requests.get(f"{BASE_URL}/channels/{channel_id}", headers=HEADERS)
    if resp.ok:
        return resp.json().get("name", str(channel_id))
    return str(channel_id)


def _domain_of(url: str) -> str:
    """URLからドメイン名を取り出す（www. は除去。例: https://x.com/... → x.com）。"""
    m = re.search(r"https?://([^/\s?#]+)", url)
    host = m.group(1) if m else url
    return re.sub(r"^www\.", "", host)


def fix_malformed_links(content: str) -> str:
    """リンク形式の乱れを矯正する（suppress_link_cards より前に適用する）。
    a. ラベルがURLになっている [<URL>](<URL>) 形式 → ラベルをドメイン名に置換
    b. Markdownリンクの外にある素URL → [ドメイン名](<URL>) に変換
    c. 既に正しい [日本語ラベル](<URL>) は一切触らない
    """
    # a. ラベルがURL（山括弧付き含む）になっているMarkdownリンクを矯正
    def _fix_label(m):
        target = m.group(2).strip("<>")
        return f"[{_domain_of(target)}](<{target}>)"

    content = re.sub(
        r"\[<?\s*(https?://[^\]\s]+?)\s*>?\]\(<?\s*(https?://[^)\s]+?)\s*>?\)",
        _fix_label,
        content,
    )

    # b. Markdownリンクの外にある素URL（直前が < ( [ でないもの）をリンク形式にする
    def _fix_bare(m):
        url = m.group(1)
        return f"[{_domain_of(url)}](<{url}>)"

    content = re.sub(
        r"(?<![<(\[])(https?://[^\s<>()\[\]]+)",
        _fix_bare,
        content,
    )
    return content


def suppress_link_cards(content: str) -> str:
    """埋め込みカード（リンクプレビュー）を防ぐため、素のURLを < > で囲む。
    既に [text](<url>) や <url> の形になっているURL（直前が < ( [ ）は対象外。"""
    return re.sub(r"(?<![<(\[])(https?://[^\s<>()]+)", r"<\1>", content)


def post_message(channel_id: int, content: str):
    """2000文字制限に対応して分割投稿する。"""
    content = fix_malformed_links(content)
    content = suppress_link_cards(content)

    if DRY_RUN:
        print(f"--- DRY_RUN: 投稿スキップ (channel_id={channel_id}) ---")
        print(content)
        print("--- DRY_RUN: ここまで（Discordへは送信していません） ---")
        return

    chunks = []
    while len(content) > 1900:
        split_at = content.rfind("\n\n", 0, 1900)
        if split_at == -1:
            split_at = 1900
        chunks.append(content[:split_at].strip())
        content = content[split_at:].strip()
    if content:
        chunks.append(content)

    for chunk in chunks:
        resp = requests.post(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=HEADERS,
            json={"content": chunk},
        )
        resp.raise_for_status()
        time.sleep(0.5)


def process_server(server: dict, target_date, summarizer: Summarizer):
    """1サーバー分の取得・要約・投稿を行う。"""
    name = server["name"]
    source_ids = server["source_channel_ids"]
    summary_channel_id = server["summary_channel_id"]
    # 要約されたくないと申し出た人のDiscordユーザーID（servers.json で設定・任意キー）
    exclude_user_ids = server.get("exclude_user_ids", [])
    date_str = target_date.strftime("%Y/%m/%d")

    print(f"\n=== {name} ===")

    all_messages = []
    channel_names = []

    for ch_id in source_ids:
        ch_name = get_channel_name(ch_id)
        channel_names.append(ch_name)
        print(f"#{ch_name} からメッセージ取得中...")

        msgs = fetch_messages(ch_id, target_date, exclude_user_ids)
        for m in msgs:
            m["channel_name"] = ch_name
        all_messages.extend(msgs)
        print(f"  → {len(msgs)} 件")

    if not all_messages:
        post_message(
            summary_channel_id,
            f"📭 **{date_str}** の対象チャンネルに投稿はありませんでした。",
        )
        print("投稿なし")
        return

    print(f"要約生成中... ({len(all_messages)} 件)")
    summary = summarizer.summarize(all_messages, date_str)

    ch_names_str = "、".join(channel_names)
    header = f"📅 **{date_str} の活動まとめ**（対象: {ch_names_str}）\n\n"
    post_message(summary_channel_id, header + summary)
    print("投稿完了")


def main():
    target_date = (datetime.now(JST) - timedelta(days=1)).date()
    print(f"対象日: {target_date.strftime('%Y/%m/%d')}")

    servers = load_servers()
    print(f"対象サーバー: {len(servers)} 件")

    summarizer = Summarizer()

    for server in servers:
        if server["name"].startswith("★"):
            print(f"\nスキップ: {server['name']}（未設定）")
            continue
        process_server(server, target_date, summarizer)


if __name__ == "__main__":
    main()
