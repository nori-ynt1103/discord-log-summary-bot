# Discord ログ抽出 & 日報生成 Bot

指定チャンネルの前日メッセージをClaudeで要約し、別チャンネルに自動投稿するBot。

## ファイル構成

```
├── bot.py          # メインBot（取得 → 要約 → 投稿）
├── summarizer.py   # Claude API要約
├── requirements.txt
├── .env.example
└── README.md
```

## セットアップ

### 1. Python環境の準備

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Discord Botの作成

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリ作成
2. Bot タブ → Token をコピー
3. Privileged Gateway Intents の **Message Content Intent** をON
4. OAuth2 → URL Generator → Scopes: `bot` `applications.commands`
5. Bot Permissions: `Read Messages/View Channels` `Read Message History` `Send Messages`
6. 生成されたURLでサーバーに招待

### 3. 環境変数の設定

```bash
cp .env.example .env
# .envを編集して各値を設定
```

| 変数名 | 説明 |
|---|---|
| `DISCORD_BOT_TOKEN` | BotのToken |
| `DISCORD_GUILD_ID` | サーバーのID（開発者モードで右クリック → IDをコピー） |
| `SOURCE_CHANNEL_IDS` | 監視するチャンネルのID（カンマ区切りで複数指定可） |
| `SUMMARY_CHANNEL_ID` | 日報を投稿するチャンネルのID |
| `DAILY_HOUR` / `DAILY_MINUTE` | 自動実行の時刻（JST） |
| `ANTHROPIC_API_KEY` | Anthropic APIキー |

### 4. 起動

```bash
python bot.py
```

## 使い方

### 自動実行

毎日 `DAILY_HOUR:DAILY_MINUTE`（デフォルト 8:00 JST）に前日分の日報を自動生成・投稿。

### 手動実行（スラッシュコマンド）

```
/create-summary                    # 昨日の日報を生成
/create-summary date:2026-04-13    # 指定日の日報を生成
```

## 日報の出力例

```
📅 2026/04/13 の活動まとめ（対象: general、announce）

## 🗣️ 主要なトピックス
- イベント開催についての議論
- 新メンバー3名が参加

## ✅ 決定事項・アクション
- 勉強会を4/20に実施することが決定

## 📢 共有・お知らせ
- のりさんより教材の案内

## 💬 その他の動き
- 雑談チャンネルで近況報告が活発
```

## 本番運用

Render.com の無料Workerプランで常時稼働可能。環境変数はRenderの管理画面で設定する。
