"""
明鏡ロール保有者数の「報告リマインダー」

毎晩 23 時ごろ、のりさんへ DM で「明日の朝刊用にロール数を送ってください」と催促する。
member_watch.py は暫定モード（GUILD_MEMBERS インテント未承認）の間、ロール数を
のりさんの DM 手動入力に頼っている。その送り忘れを防ぐのがこのスクリプトの役目。

- 「まだ投稿に使われていない報告」が届いていれば催促しない（二重催促の防止）。
  当日中に届いた報告でも、その数字が既に当日の履歴へ反映済みなら「明日の朝刊用の数字は
  未報告」とみなして催促する（使い回しで前日比±0になる事故を防ぐため）
- 直近の記録値を本文に添えて、何を送ればいいかを毎回思い出せるようにする
- 定義（DM相手・報告フォーマット・受理範囲）は member_watch.py から import して二重管理しない

使い方:
  python3 role_reminder.py --dry-run   # 送信せず、送る本文と判定結果を表示
  python3 role_reminder.py             # 本番（未報告のときだけDM送信）
  python3 role_reminder.py --force     # 報告済みでも強制送信（動作確認用）
"""

import argparse
import sys
from datetime import datetime

from member_watch import (
    BASE_URL,
    HISTORY_PATH,
    JST,
    NORI_USER_ID,
    DISCORD_BOT_TOKEN,
    WatchError,
    discord_get,
    get_dm_channel_id,
    load_history,
    parse_role_report,
    post_message,
)


def find_report_today(dm_id):
    """当日（JST 0:00 以降）に届いた有効なロール数報告を (値, 送信時刻) で返す。
    なければ None。「最新の有効報告が昨日以前」の場合も未報告とみなす。"""
    resp = discord_get(f"{BASE_URL}/channels/{dm_id}/messages", params={"limit": 20})
    if not resp.ok:
        raise WatchError(
            f"DM履歴の取得に失敗（HTTP {resp.status_code}）: {resp.text[:300]}"
        )

    day_start = datetime.now(JST).replace(hour=0, minute=0, second=0, microsecond=0)
    for msg in resp.json():  # 新しい順
        if str(msg.get("author", {}).get("id")) != NORI_USER_ID:
            continue
        value = parse_role_report(msg.get("content"))
        if value is None:
            continue
        # ここが「最新の有効な報告」。当日中かどうかだけを見る。
        try:
            ts = datetime.fromisoformat(msg["timestamp"]).astimezone(JST)
        except Exception:
            return None
        return (value, ts) if ts >= day_start else None
    return None


def role_recorded_today(history):
    """履歴に記録済みの「当日のロール数」を返す。未記録なら None。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    for entry in history:
        if entry.get("date") == today:
            return entry.get("role")
    return None


def latest_known_role(history):
    """履歴から最後に記録されたロール数を (値, 日付文字列) で返す。なければ None。"""
    for entry in reversed(history):
        if entry.get("role") is not None:
            return entry["role"], entry["date"]
    return None


def build_reminder(history):
    known = latest_known_role(history)
    if known:
        value, date_str = known
        y, m, d = date_str.split("-")
        last_line = f"直近の記録：{int(m)}/{int(d)} 時点で {value:,}名\n"
    else:
        last_line = ""

    return (
        "【明鏡ロール保有者数のご報告おねがいします】\n"
        "\n"
        "明日 5:00 のメンバー数観測に使います。\n"
        "このDMに数字を送るだけでOKです（返信不要）。\n"
        "\n"
        "例：明鏡ロール保有者 2567\n"
        "\n"
        f"{last_line}"
        "※ 今夜のうちに送っておくと、朝の投稿に確実に反映されます。"
    )


def main():
    parser = argparse.ArgumentParser(description="明鏡ロール数の報告リマインダー")
    parser.add_argument("--dry-run", action="store_true", help="送信せず本文と判定を表示")
    parser.add_argument("--force", action="store_true", help="報告済みでも強制送信")
    parser.add_argument("--history", default=HISTORY_PATH, help="履歴JSONファイルのパス")
    args = parser.parse_args()

    if not DISCORD_BOT_TOKEN:
        print("エラー: DISCORD_BOT_TOKEN が未設定です（.env / GitHub Secrets を確認）。", file=sys.stderr)
        sys.exit(1)

    try:
        dm_id = get_dm_channel_id(NORI_USER_ID)
        reported = find_report_today(dm_id)
    except WatchError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    history = load_history(args.history)
    used_today = role_recorded_today(history)

    if reported and not args.force:
        value, ts = reported
        sent = ts.strftime("%m/%d %H:%M")
        # 当日中の報告でも、その数字が既に当日の投稿へ反映済みなら
        # 「明日の朝刊用の数字はまだ来ていない」ので催促する。
        if value == used_today:
            print(
                f"本日の報告 {value:,}名（{sent} JST）は既に本日の履歴へ反映済み。"
                "明日の朝刊用の数字が未報告のため催促します。"
            )
        else:
            print(f"未反映の報告あり（{value:,}名 / {sent} JST）。催促をスキップします。")
            return

    body = build_reminder(history)

    if args.dry_run:
        print("----- DRY RUN（送信しません）-----")
        print(body)
        print("----- END DRY RUN -----")
        return

    try:
        post_message(dm_id, body)
    except WatchError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)
    print("リマインドDMを送信しました。")


if __name__ == "__main__":
    main()
