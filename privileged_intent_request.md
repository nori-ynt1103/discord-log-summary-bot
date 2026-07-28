# Privileged Intent 申請文（InfoSearchBot）

- App name: InfoSearchBot
- Application ID: 1493529162554671185
- 申請するインテント: **Server Members Intent**（必須）／**Message Content Intent**（現在limitedで稼働中・継続申請）
- Privacy Policy: https://nori-official.pages.dev/bot-privacy/

> 提出先はDeveloper Portalの該当アプリ → Bot → Privileged Gateway Intents の申請導線、
> またはDiscord Developer Supportのフォーム。設問の並びは変わることがあるので、
> 下記の各見出しを対応する設問欄に貼り分けること。

---

## What does your app do? (アプリの概要)

InfoSearchBot is a private community-operations assistant used in 5 Discord servers that I
own or help operate. It is not a public bot: it is not listed in any bot directory, has no
invite page, and is only added to servers by their owners. It has two features.

**1. Daily activity digest.** Once per day the app reads the previous day's messages from a
small number of explicitly configured channels and posts a short Markdown summary (main
topics, decisions, announcements) back into a summary channel inside the same server. This
helps members who could not read the server that day catch up, and helps the operators see
what needs a response.

**2. Daily community metrics report.** Once per day, in one server, the app posts a short
report to the community lounge showing the total member count, the cumulative number of
people who purchased the community's product, and the number of members holding the
product-owner role, each with a day-over-day delta and a 7-day trend table. This is posted
publicly to the members themselves, as a transparency and growth report for the community.

## Why do you need the Server Members Intent? (必要な理由)

Feature 2 needs an accurate count of how many members currently hold one specific role.

There is no other way to obtain this. I verified the alternatives before applying:

- `GET /guilds/{guild.id}/roles` (with and without `with_counts=true`) does not return any
  member count field.
- `GET /guilds/{guild.id}/roles/{role.id}` likewise returns no member count.
- `approximate_member_count` from `GET /guilds/{guild.id}?with_counts=true` gives only a
  guild-wide approximation and nothing per-role.
- The audit log (`MEMBER_ROLE_UPDATE`) is not usable as a substitute: over an 8-day sample it
  captured 72 role grants where the true increase was 114, and recorded zero role removals,
  because roles granted at join time and members leaving voluntarily are not logged.

Because of this, the role count is currently entered by hand every day by the server owner,
which is error-prone and has already produced incorrect reports. The Server Members Intent
would let the app compute the number correctly and automatically.

I only need aggregate counts. The app does not build a member list, does not track individual
members over time, and does not act on individual members.

## Why do you need the Message Content Intent? (必要な理由)

Feature 1 cannot work without it: summarizing a day's discussion requires the text of the
messages. Feature 2 also reads message content in one channel to extract a single published
number (the community's cumulative sales figure, which a separate integration posts there).

The app only reads channels that the server owner has explicitly listed in its configuration.
It does not read channels outside that list, and it does not read DMs between members.

The one exception, stated for completeness: because the role count is currently entered by hand
(see above), the app has a DM channel with me, the operator. It sends me a nightly reminder and
reads only the number I reply with. No other DM channel is read. If the Server Members Intent is
approved, this manual workaround — and the DM exchange that supports it — is removed entirely.

## How is the data stored and secured? (データの保存とセキュリティ)

**Nothing derived from members is stored persistently.**

- Message content and author display names are held in memory only for the duration of the
  daily summarization run, and are discarded when the run ends. They are never written to disk
  or to any database.
- Member data obtained via the Server Members Intent would be used only to compute two
  integers (total members, role holders). No user IDs, usernames, avatars, or member lists
  would be retained.
- The only data persisted is a JSON file of daily aggregate numbers, in the form
  `{"date": "2026-07-26", "members": 9694, "buyers": 3112, "role": 2567}`. It contains no
  personal data. It is kept in a private repository accessible only to me.
- The app runs as a scheduled GitHub Actions job. The bot token is held in GitHub Actions
  encrypted secrets and is not present in the repository.

Retention: the aggregate counts file is retained indefinitely as a growth record. Any
member-derived data is retained for zero time beyond the run.

## Is data shared with any third party? (第三者への提供)

Yes, for one feature, and I want to be explicit about it.

For the daily activity digest, the message text and author display names of the configured
channels are sent to Anthropic's Claude API to generate the summary. This is a processing
call only: the output is returned to the app and posted back into the same Discord server.
The data is not used to train models, is not sold, is not shared with advertisers, and is not
shared with any other party. Anthropic's API data handling terms apply to that call.

No data is shared with any third party for the community metrics feature, which uses only
aggregate counts.

---

## 日本語版（内容確認用・提出はしない）

**アプリの概要**
のりが所有・運営する5つのDiscordサーバーで使う、非公開のコミュニティ運営補助Bot。
ボットリスト非掲載・招待ページなし・サーバーオーナーが直接導入するのみ。機能は2つ。

1. **日次ダイジェスト**：設定済みの少数のチャンネルの前日分メッセージを読み、
   トピック・決定事項・お知らせの要約を同じサーバー内の要約チャンネルへ投稿する
2. **日次コミュニティ指標レポート**：1サーバーで、参加者数・累計購入者数・
   商品購入者ロール保有者数を前日比と7日推移つきでラウンジへ投稿する（メンバー向けの成長レポート）

**Server Members Intentが必要な理由**
特定ロールの保有者数を正確に数えるため。代替手段は全て検証済みで、
rolesエンドポイントは`with_counts`付きでもmember_countを返さず、
approximate_member_countはサーバー全体の概算のみ、監査ログは8日間で
実測+114に対し72件しか記録されず剥奪は0件だった（参加時付与と自主退会が記録されないため）。
現在は毎日オーナーが手入力しており、実際に誤ったレポートが出ている。

**Message Content Intentが必要な理由**
日次ダイジェストは本文がないと成立しない。指標レポートも1チャンネルから
累計販売数の数値1つを抽出している。読むのは設定に明記されたチャンネルのみ。メンバー同士のDMは読まない。
唯一の例外として、ロール数の手入力を支えるため運営者本人とのDMだけは読む（催促DMを送り、返信の数値のみ抽出）。
インテントが承認されればこの手入力ごと不要になる旨も明記した。

**保存とセキュリティ**
メンバー由来のデータは永続保存しない。メッセージ本文と表示名は要約実行中の
メモリ上のみで、終了時に破棄。ディスクにもDBにも書かない。
保存するのは日次の集計数値JSONのみ（個人データを含まない・privateリポジトリ）。
トークンはGitHub Actionsの暗号化Secretsに保管。

**第三者提供**
あり。日次ダイジェストのため、対象チャンネルのメッセージ本文と表示名を
Anthropic Claude APIへ送信して要約させている。処理呼び出しのみで、出力は
同じDiscordサーバーへ戻すだけ。学習利用・販売・広告利用・他社提供はしていない。
指標レポート側は集計値のみで第三者提供なし。
