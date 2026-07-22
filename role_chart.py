"""
明鏡ロール保有者の推移グラフを生成し、アフィリエイト通知チャンネルへ画像投稿するスクリプト。

- history/member_watch_history.json から直近7日分の role 値（null は除外）を読み、
  折れ線グラフ PNG を生成する。
- 生成後、アフィリエイト通知チャンネル（1514734016400592936）へ multipart で添付投稿する。
- グラフ生成/投稿の失敗は明確なエラーで exit 1（黙殺しない）。

配色・フォーム・軸・ダークテーマ対応は dataviz スキルの設計指針に従う（ダークサーフェス上の
単一系列ライン。系列が1本なので凡例は置かず、タイトルが系列名を兼ねる。各点に直接ラベル）。

使い方:
  python3 role_chart.py --dry-run    # PNG 生成のみ（投稿しない）
  python3 role_chart.py              # PNG 生成 → アフィリエイト通知チャンネルへ投稿
  python3 role_chart.py --out /path/to/out.png --dry-run
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.ticker import FuncFormatter

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
JST = timezone(timedelta(hours=9))
BASE_URL = "https://discord.com/api/v10"
AFFILIATE_CHANNEL_ID = "1514734016400592936"  # アフィリエイト通知チャンネル（投稿先）

HISTORY_PATH = os.path.join(os.path.dirname(__file__), "history", "member_watch_history.json")
DEFAULT_OUT = os.path.join(os.path.dirname(__file__), "role_chart.png")

# --- dataviz カラー（ダークサーフェス / palette.md 準拠）---
SURFACE = "#1a1a19"      # ダークチャートサーフェス
SERIES = "#3987e5"       # 系列1 blue（dark step）
INK_PRIMARY = "#ffffff"  # 主要インク
INK_SECOND = "#c3c2b7"   # 二次インク（値ラベル）
INK_MUTED = "#898781"    # 軸/目盛ラベル
GRID = "#2c2c2a"         # グリッド（ヘアライン）
BASELINE = "#383835"     # 軸/ベースライン

# CI（ubuntu）優先 → ローカル Mac フォールバックの順で探す
FONT_CANDIDATES = [
    "Noto Sans CJK JP",
    "Noto Sans CJK JP Regular",
    "IPAexGothic",
    "IPAGothic",
    "Hiragino Sans",
    "Hiragino Kaku Gothic ProN",
    "Hiragino Kaku Gothic Pro",
    "YuGothic",
    "Yu Gothic",
    "Arial Unicode MS",
]


class ChartError(Exception):
    """明確な失敗を投げるための例外。"""


def pick_font():
    """利用可能な日本語フォント名を1つ選ぶ。豆腐化を避けるため、
    実際に font_manager に登録されているものだけを候補にする。見つからなければ ChartError。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in FONT_CANDIDATES:
        if name in available:
            return name
    raise ChartError(
        "日本語フォントが見つかりませんでした（豆腐化を防ぐため中止）。"
        "\n→ CI では fonts-noto-cjk を、ローカルでは Hiragino 系を利用可能にしてください。"
        f"\n  探した候補: {', '.join(FONT_CANDIDATES)}"
    )


def _md(date_str):
    """'2026-07-17' → '7/17'（ゼロ埋めなし）。"""
    y, m, d = date_str.split("-")
    return f"{int(m)}/{int(d)}"


def load_recent_role_series(path, days=7):
    """履歴から role が非 null のエントリを日付順に読み、直近 days 件を返す。
    返り値: [(date_str, role_int), ...]。空なら ChartError。"""
    if not os.path.exists(path):
        raise ChartError(f"履歴ファイルが見つかりません: {path}")
    with open(path, encoding="utf-8") as f:
        try:
            history = json.load(f)
        except json.JSONDecodeError as e:
            raise ChartError(f"履歴JSONの解析に失敗しました: {e}")

    points = [
        (e["date"], int(e["role"]))
        for e in history
        if e.get("role") is not None and e.get("date")
    ]
    points.sort(key=lambda p: p[0])
    points = points[-days:]
    if not points:
        raise ChartError(
            "role が非 null の履歴エントリが1件もありません（グラフを描けません）。"
        )
    return points


def render_chart(points, out_path):
    """折れ線グラフ PNG を out_path に書き出す。"""
    font_name = pick_font()
    plt.rcParams["font.family"] = font_name
    plt.rcParams["axes.unicode_minus"] = False

    labels = [_md(d) for d, _ in points]
    values = [v for _, v in points]
    x = list(range(len(points)))

    # 1280x720 @ dpi 100
    fig, ax = plt.subplots(figsize=(12.8, 7.2), dpi=100)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    # 折れ線（2px）＋マーカー（>=8px）。単一系列なので凡例なし。
    ax.plot(
        x, values,
        color=SERIES, linewidth=2,
        marker="o", markersize=9,
        markerfacecolor=SERIES, markeredgecolor=SURFACE, markeredgewidth=2,
        zorder=3, clip_on=False,
    )

    # 各点に値ラベル（カンマ区切り・二次インク）
    for xi, vi in zip(x, values):
        ax.annotate(
            f"{vi:,}",
            xy=(xi, vi), xytext=(0, 14), textcoords="offset points",
            ha="center", va="bottom",
            color=INK_SECOND, fontsize=13,
        )

    # タイトル（系列名を兼ねる）
    ax.set_title(
        "明鏡ロール保有者の推移",
        color=INK_PRIMARY, fontsize=24, fontweight="bold",
        pad=22, loc="left",
    )

    # 軸
    ax.set_xticks(x)
    ax.set_xticklabels(labels, color=INK_MUTED, fontsize=14)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{int(v):,}"))
    ax.tick_params(axis="y", colors=INK_MUTED, labelsize=13)
    ax.tick_params(axis="x", colors=INK_MUTED, length=0)

    # Y レンジに余白（ラベルが上に出るぶん少し広めに）
    vmin, vmax = min(values), max(values)
    span = max(vmax - vmin, 1)
    ax.set_ylim(vmin - span * 0.35, vmax + span * 0.45)
    ax.set_xlim(-0.4, len(points) - 0.6)

    # グリッド（水平ヘアラインのみ）＋控えめな軸
    ax.grid(axis="y", color=GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE)
    ax.spines["bottom"].set_linewidth(1.5)

    fig.subplots_adjust(left=0.09, right=0.97, top=0.86, bottom=0.10)
    try:
        fig.savefig(out_path, facecolor=SURFACE)
    except Exception as e:
        raise ChartError(f"PNG の書き出しに失敗しました: {e}")
    finally:
        plt.close(fig)
    return out_path


def post_chart(channel_id, image_path, content):
    """PNG を multipart でチャンネルへ添付投稿する（429 リトライ対応）。"""
    if not DISCORD_BOT_TOKEN:
        raise ChartError("DISCORD_BOT_TOKEN が未設定です（.env / GitHub Secrets を確認）。")
    url = f"{BASE_URL}/channels/{channel_id}/messages"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    filename = os.path.basename(image_path)
    payload = {"content": content, "attachments": [{"id": 0, "filename": filename}]}

    while True:
        with open(image_path, "rb") as fh:
            files = {
                "payload_json": (None, json.dumps(payload), "application/json"),
                "files[0]": (filename, fh, "image/png"),
            }
            resp = requests.post(url, headers=headers, files=files)
        if resp.status_code == 429:
            try:
                retry_after = float(resp.json().get("retry_after", 1))
            except Exception:
                retry_after = 1.0
            print(f"[429] レート制限。{retry_after}s 待機して再試行...", file=sys.stderr)
            time.sleep(retry_after + 0.5)
            continue
        if not resp.ok:
            raise ChartError(
                f"画像投稿に失敗（HTTP {resp.status_code}）: {resp.text[:300]}"
            )
        return


def main():
    parser = argparse.ArgumentParser(description="明鏡ロール保有者の推移グラフ 生成／投稿")
    parser.add_argument("--dry-run", action="store_true", help="PNG 生成のみ（投稿しない）")
    parser.add_argument("--history", default=HISTORY_PATH, help="履歴JSONファイルのパス")
    parser.add_argument("--out", default=DEFAULT_OUT, help="出力 PNG パス")
    args = parser.parse_args()

    try:
        points = load_recent_role_series(args.history)
        out_path = render_chart(points, args.out)
        latest_date = points[-1][0]
        print(f"[グラフ] 生成完了: {out_path}（{len(points)}点 / 最新 {latest_date}）")

        if args.dry_run:
            print("----- DRY RUN（投稿しません）-----")
            return

        content = f"【明鏡ロール保有者の推移】（{_md(latest_date)}時点）"
        post_chart(AFFILIATE_CHANNEL_ID, out_path, content)
        print(f"投稿完了: アフィリエイト通知チャンネルへ画像を投稿（{content}）")
    except ChartError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
