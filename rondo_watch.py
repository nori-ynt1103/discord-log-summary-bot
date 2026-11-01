"""
Rondo（自前メール配信スタンド）の読者数（一斉配信の実効配信対象数）を取得し、
history/member_watch_history.json の「本日（JST）」エントリに "rondo" キーとしてマージする。

- データ源: GET {RONDO_BASE_URL}/api/admin/scenarios（Authorization: Bearer {RONDO_ADMIN_TOKEN}）
  の応答 JSON の castTargets（整数）。
- member_watch.py の upsert_today() はエントリを丸ごと置き換えるため、必ず member_watch.py の
  後に実行する。本スクリプトは既存エントリへ "rondo" キーを足すだけで、他のキーは触らない。
  本日エントリが無ければ {"date": 今日, "rondo": n} を末尾に追記する。
- 失敗（環境変数未設定・HTTPエラー・タイムアウト・JSON不正・castTargets が整数でない・
  履歴JSONの破損）は stderr に理由を出して exit 1。その場合、履歴ファイルは書き換えない。

使い方:
  python3 rondo_watch.py --dry-run           # 取得して値を表示するだけ（履歴に書かない）
  python3 rondo_watch.py                     # 取得 → 本日エントリに rondo をマージして保存
  python3 rondo_watch.py --history /path/to/history.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

JST = timezone(timedelta(hours=9))
HISTORY_PATH = os.path.join(os.path.dirname(__file__), "history", "member_watch_history.json")
TIMEOUT_SEC = 20


class RondoWatchError(Exception):
    """明確な失敗を投げるための例外。"""


def fetch_cast_targets():
    """Rondo の castTargets（一斉配信の実効配信対象数）を整数で返す。"""
    base_url = (os.getenv("RONDO_BASE_URL") or "").strip().rstrip("/")
    token = (os.getenv("RONDO_ADMIN_TOKEN") or "").strip()
    missing = [n for n, v in (("RONDO_BASE_URL", base_url), ("RONDO_ADMIN_TOKEN", token)) if not v]
    if missing:
        raise RondoWatchError(f"環境変数が未設定です: {', '.join(missing)}（.env / GitHub Secrets を確認）")

    url = f"{base_url}/api/admin/scenarios"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT_SEC,
        )
    except requests.Timeout:
        raise RondoWatchError(f"Rondo API がタイムアウトしました（{TIMEOUT_SEC}秒）: {url}")
    except requests.RequestException as e:
        raise RondoWatchError(f"Rondo API への接続に失敗しました: {url}（{type(e).__name__}）")

    if not resp.ok:
        raise RondoWatchError(f"Rondo API がエラーを返しました（HTTP {resp.status_code}）: {url}")

    try:
        data = resp.json()
    except ValueError:
        raise RondoWatchError(f"Rondo API の応答が JSON ではありません: {resp.text[:200]!r}")

    if not isinstance(data, dict) or "castTargets" not in data:
        raise RondoWatchError("Rondo API の応答に castTargets がありません。")
    value = data["castTargets"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RondoWatchError(f"castTargets が0以上の整数ではありません: {value!r}")
    return value


def load_history_strict(path):
    """履歴を読む。ファイルが無ければ []。JSON が壊れていれば上書き事故を防ぐため例外。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError as e:
            raise RondoWatchError(f"履歴JSONの解析に失敗しました（書き換えずに中止）: {e}")
    if not isinstance(history, list):
        raise RondoWatchError("履歴JSONの形式が配列ではありません（書き換えずに中止）。")
    return history


def save_history(path, history):
    """member_watch.py と同じ形式（indent=2・ensure_ascii=False・末尾改行）で保存する。
    途中失敗で履歴を壊さないよう、一時ファイルに書いてから置き換える。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def merge_rondo(history, date_str, value):
    """本日エントリに rondo キーを足す（他のキーは保持）。無ければ追記。
    返り値: (history, entry, appended)"""
    entry = next((e for e in history if isinstance(e, dict) and e.get("date") == date_str), None)
    if entry is not None:
        entry["rondo"] = value
        return history, entry, False
    entry = {"date": date_str, "rondo": value}
    history.append(entry)
    return history, entry, True


def main():
    parser = argparse.ArgumentParser(description="Rondo 読者数（配信対象）の観測")
    parser.add_argument("--dry-run", action="store_true", help="取得して値を表示するだけ（履歴に書かない）")
    parser.add_argument("--history", default=HISTORY_PATH, help="履歴JSONファイルのパス")
    args = parser.parse_args()

    today_str = datetime.now(JST).strftime("%Y-%m-%d")
    try:
        value = fetch_cast_targets()
        print(f"[Rondo] castTargets（配信対象）: {value:,}名（{today_str}）")
        if args.dry_run:
            print("----- DRY RUN（履歴に書きません）-----")
            return
        history = load_history_strict(args.history)
        history, entry, appended = merge_rondo(history, today_str, value)
        save_history(args.history, history)
    except RondoWatchError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)

    how = "新規エントリとして追記" if appended else "本日エントリにマージ"
    print(f"保存完了: {args.history} に {how}（{entry}）")


if __name__ == "__main__":
    main()
