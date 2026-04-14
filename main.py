"""
Discord 日報生成スクリプト
- 指定チャンネルの前日メッセージを取得
- Claude APIで要約
- 指定チャンネルに投稿
GitHub Actionsで毎日定時実行する。
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from summarizer import Summarizer

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SOURCE_CHANNEL_IDS = [
    int(x.strip())
    for x in os.getenv("SOURCE_CHANNEL_IDS", "").split(",")
    if x.strip()
]
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID", "0"))

JST = timezone(timedelta(hours=9))
BASE_URL = "https://discord.com/api/v10"
HEADERS = {
    "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
    "Content-Type": "application/json",
}


def datetime_to_snowflake(dt: datetime) -> int:
    """datetimeをDiscord snowflake IDに変換する。"""
    ms = int(dt.timestamp() * 1000)
    return (ms - 1420070400000) << 22


def fetch_messages(channel_id: int, target_date) -> list[dict]:
    """指定日（JST）のメッセージを全件取得する。"""
    start_jst = datetime(
        target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=JST
    )
    end_jst = start_jst + timedelta(days=1)
    start_utc = start_jst.astimezone(timezone.utc)
    end_utc = end_jst.astimezone(timezone.utc)

    # 対象日の終端スノーフレークから before で遡る
    last_id = str(datetime_to_snowflake(end_jst))
    messages = []

    print(f"  [DEBUG] start_utc={start_utc}, end_utc={end_utc}")
    print(f"  [DEBUG] before_snowflake={last_id}")

    while True:
        resp = requests.get(
            f"{BASE_URL}/channels/{channel_id}/messages",
            headers=HEADERS,
            params={"limit": 100, "before": last_id},
        )
        print(f"  [DEBUG] API status={resp.status_code}, batch_size={len(resp.json()) if resp.ok else 'error'}")
        resp.raise_for_status()
        batch = resp.json()

        if not batch:
            break

        # before は新しい順で返ってくる
        reached_start = False
        for msg in batch:
            ts = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
            if ts < start_utc:
                reached_start = True
                break
            if ts < end_utc:
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

    messages.reverse()  # 古い順に並べ直す
    return messages


def get_channel_name(channel_id: int) -> str:
    """チャンネル名を取得する。"""
    resp = requests.get(f"{BASE_URL}/channels/{channel_id}", headers=HEADERS)
    if resp.ok:
        return resp.json().get("name", str(channel_id))
    return str(channel_id)


def post_message(channel_id: int, content: str):
    """2000文字制限に対応して分割投稿する。"""
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


def main():
    now_utc = datetime.now(timezone.utc)
    now_jst = datetime.now(JST)
    target_date = (now_jst - timedelta(days=1)).date()
    date_str = target_date.strftime("%Y/%m/%d")
    print(f"現在時刻 UTC: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"現在時刻 JST: {now_jst.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"対象日: {date_str}")

    all_messages = []
    channel_names = []

    for ch_id in SOURCE_CHANNEL_IDS:
        ch_name = get_channel_name(ch_id)
        channel_names.append(ch_name)
        print(f"#{ch_name} からメッセージ取得中...")

        msgs = fetch_messages(ch_id, target_date)
        for m in msgs:
            m["channel_name"] = ch_name
        all_messages.extend(msgs)
        print(f"  → {len(msgs)} 件")

    if not all_messages:
        post_message(
            SUMMARY_CHANNEL_ID,
            f"📭 **{date_str}** の対象チャンネルに投稿はありませんでした。",
        )
        print("投稿なし")
        return

    print(f"要約生成中... ({len(all_messages)} 件)")
    summarizer = Summarizer()
    summary = summarizer.summarize(all_messages, date_str)

    ch_names_str = "、".join(channel_names)
    header = f"📅 **{date_str} の活動まとめ**（対象: {ch_names_str}）\n\n"
    post_message(SUMMARY_CHANNEL_ID, header + summary)
    print("投稿完了")


if __name__ == "__main__":
    main()
