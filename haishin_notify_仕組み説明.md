# イケハヤさん新着通知の仕組み

イケハヤさんが各媒体に新しく出したものを、明鏡サーバーのチャンネルへ「AIのりすけ」名義で1通にまとめて知らせる。

- 本体：`haishin_notify.py`（標準ライブラリのみ）
- 媒体設定：`haishin_sources.json`
- 実行：`.github/workflows/haishin_notify.yml`（GitHub Actions）

## 何を通知するか

| 媒体 | 表示 |
|---|---|
| Substack（記事・音声。有料/無料を区別） | Substack（無料）／Substack（有料）／Substack音声（無料）／Substack音声（有料） |
| stand.fm | stand.fm（Spotify・Voicy の番組リンクを添える。この2つは巡回しない） |
| YouTube（イケハヤ大学2.0・AIで稼ぐラジオ） | YouTube |
| note | note |
| Brain | Brain（新教材） |
| X記事（長文記事だけ。日常の投稿は通知しない） | X |

通知しないもの：Xの日常投稿・Instagram・メルマガ・LISTEN。

**注意（2026-11-12確認）：GitHub Actions からは Substack（API・RSSとも403。Cloudflareの確認画面）と Brain（403。地域制限）が取得できない。** この2つは毎回「取得失敗」の警告になり、既読も進まない（他の媒体は動く）。

## うざくしないための決まり

- 1回の実行で投稿は最大1通。新着ゼロなら投稿しない。
- 「読む（note・Substack記事・X記事・Brain）」と「聴く／観る（stand.fm・Substack音声・YouTube）」の2グループに分け、同じグループ内の同じタイトルは媒体をまたいで1項目に束ねる。通知済みのものは48時間覚えておき、同じグループの別媒体で後から来ても通知しない（音声を通知済みでも、同じタイトルの記事は通知する）。
- YouTube の Shorts（RSS のリンクが `/shorts/`）は通知しない（既読にはする）。`haishin_sources.json` の `exclude_shorts` で切り替える。
- 公開から48時間を超えたものは通知しない。
- 1通は最大8項目・2000字以内。束ねた後で15項目を超えたら異常とみなし、通知せず既読にだけする。
- 媒体を初めて読んだ回は通知せず、既読にするだけ（過去分が流れないように）。

## 二重投稿を防ぐ順序

①取得 → ②新着判定 → ③既読を `haishin-state` ブランチへ push → ④Discordへ投稿。
push に失敗したら投稿しない。投稿に失敗したときは既読が進んだままになる（1通を取りこぼしても、同じ通知が流れ続けるよりはよい）。そのときワークフローは失敗で終わる。

取得に失敗した媒体は既読を進めない（次回また判定する）。全媒体が失敗したらワークフローは失敗で終わる。

## 既読の置き場

`haishin-state` ブランチの `haishin_seen.json`（main には置かない）。中身：

- `sources`：媒体ごとの既読ID（Substack は記事URL、Brain は記事ID）
- `recent_titles`：通知済みタイトル（48時間）
- `x`：XのユーザーID・`since_id`・最後に読みに行った時刻

既読をやり直したいときは、このブランチを削除すれば次回は「初回」として既読化だけ行う。

## 止め方

- 定期実行を止める：`haishin_notify.yml` の `schedule:` の2行をコメントに戻して push する。または GitHub の Actions 画面でこのワークフローを Disable にする。
- 媒体を1つだけ止める：`haishin_sources.json` のその媒体の `enabled` を `false` にする。

## X記事のトークンの入れ方

X API v2 の Bearer トークンを、リポジトリの Settings → Secrets and variables → Actions に `X_BEARER_TOKEN` という名前で登録する。
未登録なら「X記事：未設定のためスキップ」とログに出て、他の媒体はふつうに動く。
X は従量課金のため、読みに行くのは1時間に1回まで。初回は5件だけ読んで基準を記録し、通知はしない。

## 手動での確認

Actions 画面から「Haishin Notify」を Run workflow で実行する。

- `dry_run`（既定 true）：投稿せず既読も保存しない。取得結果・投稿予定の文面・チャンネルの確認結果をログに出す。
- `replay_hours`：dry_run のときだけ有効。既読を無視して直近N時間を新着扱いにした文面を作る（束ね方の確認用）。
- `channel_id`：投稿先（既定は明鏡サーバーの通知先チャンネル）。

手元では `python3 haishin_notify.py --dry-run --replay-hours 36` で同じ確認ができる（トークン不要）。
