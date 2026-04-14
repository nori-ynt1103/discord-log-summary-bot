"""
Discord ログ抽出 & 日報生成 Bot
- 指定チャンネルの前日メッセージをClaudeで要約
- 別チャンネルに日報として自動投稿
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import tasks
from dotenv import load_dotenv

from summarizer import Summarizer

load_dotenv()

# ──────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "0"))

# カンマ区切りで複数チャンネル指定可能
SOURCE_CHANNEL_IDS = [
    int(x.strip())
    for x in os.getenv("SOURCE_CHANNEL_IDS", "").split(",")
    if x.strip()
]
SUMMARY_CHANNEL_ID = int(os.getenv("SUMMARY_CHANNEL_ID", "0"))

# 毎日実行する時刻（JST）
DAILY_HOUR = int(os.getenv("DAILY_HOUR", "8"))
DAILY_MINUTE = int(os.getenv("DAILY_MINUTE", "0"))

RATE_LIMIT_SLEEP = 0.5
JST = timezone(timedelta(hours=9))

# ──────────────────────────────────────────────
# Bot本体
# ──────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True


class SummaryBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.summarizer = Summarizer()

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        daily_task.start(self)

    async def on_ready(self):
        print(f"[Bot] ログイン完了: {self.user} (ID: {self.user.id})")
        print(f"[Bot] 監視チャンネル: {SOURCE_CHANNEL_IDS}")
        print(f"[Bot] 日報投稿先: {SUMMARY_CHANNEL_ID}")
        print(f"[Bot] 毎日 {DAILY_HOUR:02d}:{DAILY_MINUTE:02d} に自動実行")


bot = SummaryBot()


# ──────────────────────────────────────────────
# コアロジック
# ──────────────────────────────────────────────
async def fetch_messages_for_date(
    channel: discord.TextChannel, target_date: datetime.date
) -> list[dict]:
    """指定日（JST）のメッセージを全件取得する。"""
    start_jst = datetime(
        target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=JST
    )
    end_jst = start_jst + timedelta(days=1)

    messages = []
    try:
        async for msg in channel.history(
            after=start_jst.astimezone(timezone.utc),
            before=end_jst.astimezone(timezone.utc),
            limit=None,
            oldest_first=True,
        ):
            messages.append(
                {
                    "timestamp": msg.created_at.astimezone(JST).strftime("%H:%M"),
                    "author": msg.author.display_name,
                    "content": msg.content,
                    "attachments": " | ".join(a.url for a in msg.attachments),
                    "channel_name": channel.name,
                }
            )
            await asyncio.sleep(RATE_LIMIT_SLEEP)
    except discord.Forbidden:
        print(f"[Bot] 権限なし: #{channel.name} ({channel.id})")
    except discord.HTTPException as e:
        print(f"[Bot] HTTP エラー: {e}")

    return messages


async def run_daily_job(client: SummaryBot, target_date=None):
    """前日分メッセージを要約してDiscordに投稿する。"""
    if target_date is None:
        target_date = (datetime.now(JST) - timedelta(days=1)).date()

    date_str = target_date.strftime("%Y/%m/%d")
    print(f"[Bot] 日次ジョブ開始: {date_str}")

    all_messages: list[dict] = []

    for ch_id in SOURCE_CHANNEL_IDS:
        channel = client.get_channel(ch_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(ch_id)
            except Exception as e:
                print(f"[Bot] チャンネル取得エラー (ID: {ch_id}): {e}")
                continue

        print(f"[Bot] #{channel.name} からメッセージ取得中...")
        msgs = await fetch_messages_for_date(channel, target_date)
        print(f"[Bot]   → {len(msgs)} 件")
        all_messages.extend(msgs)

    # 日報投稿先チャンネルを取得
    summary_channel = client.get_channel(SUMMARY_CHANNEL_ID)
    if summary_channel is None:
        try:
            summary_channel = await client.fetch_channel(SUMMARY_CHANNEL_ID)
        except Exception as e:
            print(f"[Bot] 日報投稿先チャンネル取得エラー: {e}")
            return

    if not all_messages:
        await summary_channel.send(
            f"📭 **{date_str}** の対象チャンネルに投稿はありませんでした。"
        )
        return

    print(f"[Bot] 要約生成中... ({len(all_messages)} 件)")
    channel_names = "、".join(sorted({m["channel_name"] for m in all_messages}))
    summary_text = client.summarizer.summarize(all_messages, date_str)

    header = f"📅 **{date_str} の活動まとめ**（対象: {channel_names}）\n\n"
    await post_chunks(summary_channel, header + summary_text)
    print("[Bot] 日報投稿完了")


async def post_chunks(channel: discord.TextChannel, text: str, limit: int = 1900):
    """Discordの2000文字制限に対応して分割投稿する。"""
    if len(text) <= limit:
        await channel.send(text)
        return

    paragraphs = text.split("\n\n")
    chunk = ""
    for para in paragraphs:
        candidate = (chunk + "\n\n" + para).strip() if chunk else para
        if len(candidate) <= limit:
            chunk = candidate
        else:
            if chunk:
                await channel.send(chunk)
                await asyncio.sleep(0.5)
            while len(para) > limit:
                await channel.send(para[:limit])
                await asyncio.sleep(0.5)
                para = para[limit:]
            chunk = para
    if chunk:
        await channel.send(chunk)


# ──────────────────────────────────────────────
# 定期実行タスク
# ──────────────────────────────────────────────
@tasks.loop(hours=1)
async def daily_task(client: SummaryBot):
    now = datetime.now(JST)
    if now.hour == DAILY_HOUR and now.minute < 5:
        await run_daily_job(client)


@daily_task.before_loop
async def before_daily_task():
    await bot.wait_until_ready()


# ──────────────────────────────────────────────
# スラッシュコマンド
# ──────────────────────────────────────────────
@bot.tree.command(name="create-summary", description="指定日（省略時は昨日）の日報を手動生成します")
@app_commands.describe(date="対象日 (YYYY-MM-DD 形式、省略時は昨日)")
async def create_summary(interaction: discord.Interaction, date: str | None = None):
    await interaction.response.defer(thinking=True)

    if date:
        try:
            from datetime import date as date_type
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            await interaction.followup.send(
                "❌ 日付の形式が正しくありません。`YYYY-MM-DD` で入力してください。"
            )
            return
    else:
        target_date = (datetime.now(JST) - timedelta(days=1)).date()

    await interaction.followup.send(
        f"🔄 **{target_date.strftime('%Y/%m/%d')}** の日報を生成します..."
    )
    await run_daily_job(bot, target_date)


# ──────────────────────────────────────────────
# 起動
# ──────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN が設定されていません")
    if not SOURCE_CHANNEL_IDS:
        raise ValueError("SOURCE_CHANNEL_IDS が設定されていません")
    if not SUMMARY_CHANNEL_ID:
        raise ValueError("SUMMARY_CHANNEL_ID が設定されていません")

    bot.run(TOKEN)
