# Privileged Intent 申請フォーム 回答集（InfoSearchBot）

- App name: InfoSearchBot
- Application ID: 1493529162554671185
- Privacy Policy: https://nori-official.pages.dev/bot-privacy/
- 最終更新: 2026-07-28（実際の申請フォームの設問に合わせて再構成）

> 提出言語は**英語**を推奨。ポータルの表示は日本語だが、審査担当は英語で読む。
> 各セクションの英文をそのまま該当欄へ貼る。日本語は内容確認用で、提出しない。

---

## 提出前に必ず済ませること

1. **Developer Portal → General Information → Privacy Policy URL** に
   `https://nori-official.pages.dev/bot-privacy/` を登録する。
   下の「プライバシーポリシーはどこで閲覧できますか」で「Botプロフィールからリンク」と
   答えるため、登録前に提出すると虚偽になる。
2. **各サーバーのルール/お知らせチャンネル**にポリシーのリンクを固定（ピン留め）する。
3. **スクリーンショットを撮る**（最後のセクションに撮るべき画像を列挙）。
4. **オプトアウトの実装**（後述の「未解決」を参照）。ここが未実装のまま
   「オプトアウトできる」と答えてはいけない。

---

## アプリケーションの詳細（What does your app do?）

InfoSearchBot is a private community-operations assistant. It runs in 5 Discord servers that
I own or help operate. It is not a public bot: it is not listed in any bot directory, it has
no public invite page, and it is added to servers only by their owner. There is no plan to
distribute it publicly.

It has exactly two features.

**1. Daily activity digest.**
Once per day, the app reads the previous day's messages from a small number of channels that
are explicitly listed in its configuration file (currently one or two channels per server).
It generates a short Markdown summary — the main topics discussed, decisions made, and
announcements — and posts that summary back into a designated summary channel inside the same
server. Nothing leaves the server: the input comes from the server and the output goes back to
the same server. The purpose is so that members who could not follow the server that day can
catch up in 30 seconds, and so that I, as the operator, can see what still needs a response.

**2. Daily community metrics report.**
Once per day, in one server, the app posts a short public report to the community lounge
containing three numbers: the total member count, the cumulative number of people who have
purchased the community's product, and the number of members currently holding the
product-owner role. Each is shown with a day-over-day delta and a 7-day trend table. This is
posted publicly to the members themselves — it is a transparency and growth report for the
community, not internal analytics. A line chart of the same trend is posted alongside it.

The app does not send DMs to members, does not track individual members, does not build
profiles, and takes no action on individual accounts (no kicks, bans, roles, or moderation).
Its only write action is posting these two messages into channels of the same server.

Its data handling is published at https://nori-official.pages.dev/bot-privacy/ (Japanese and
English on one page).

---

## 公開のプライバシーポリシーで、ユーザーにデータ使用について伝えていますか？

**Yes**

## プライバシーポリシーはどこで閲覧できますか？

The privacy policy is linked from the application's profile (registered as the Privacy Policy
URL on the application in the Developer Portal), and it is also pinned in the rules /
information channel of each server the bot runs in, so members can reach it without leaving
Discord. The policy page itself is public and requires no login.

## プライバシーポリシーへのリンク

```
https://nori-official.pages.dev/bot-privacy/
```

---

## どのインテントを申請しますか？

- [x] **Server Members Intent**
- [ ] Presence Intent — 申請しない（不要）
- [x] **Message Content Intent**（現在limited・フルを申請）

---

## Server Members Intent — なぜ必要か

Feature 2 needs an accurate count of how many members currently hold one specific role, and an
accurate total member count.

There is no other way to obtain the role count. I verified every alternative before applying:

- `GET /guilds/{guild.id}/roles`, with and without `with_counts=true`, does not return any
  member count field on the role object.
- `GET /guilds/{guild.id}/roles/{role.id}` likewise returns no member count.
- `approximate_member_count` from `GET /guilds/{guild.id}?with_counts=true` gives only a
  guild-wide approximation, and nothing per-role.
- The audit log (`MEMBER_ROLE_UPDATE`) is not a usable substitute. Over an 8-day sample it
  captured 72 role grants where the true increase was 114, and recorded zero role removals —
  roles granted at join time and members leaving voluntarily are never logged. A ~37% shortfall
  with no visibility into removals cannot produce a correct day-over-day number.

Because of this, the role count is currently typed in by hand every day by me, the server
owner, and the bot has to ask me for it. That has already produced at least one incorrect
report published to the community. The Server Members Intent would let the app compute the
number correctly and automatically, and would let me delete the manual workaround entirely.

I only need aggregate counts. The app does not build or store a member list, does not track
individual members over time, and does not act on individual members. No user IDs, usernames,
avatars, or member records are retained.

---

## Message Content Intent

### ユーザーはメッセージ内容データの追跡をオプトアウトできますか？

**Yes**（2026-07-28に実装済み。`servers.json` の `exclude_user_ids` → `main.py` の
`fetch_messages()` で取得時点に除外。除外分はLLMへ送信されない）

補足として自由記述欄があれば：

Yes. Members can opt out in two ways. First, the bot only reads a small number of channels
that are explicitly listed in its configuration and identified to members in the server rules
channel; conversations in any other channel are never read. Second, a member can request
exclusion — via the contact form linked in the privacy policy or by telling me directly in the
server — and their messages are then filtered out before any processing takes place. Exclusion
is honoured for all future runs, and because no message content is ever stored there is no
historical data to delete.

### メッセージコンテンツデータをプラットフォーム外（Discord外）で保存しますか？

**No**

Message content is never written to disk or to any database. It is held in memory only for the
duration of the daily summarization run and discarded when the run ends. The generated summary
is posted back into Discord and is not retained either. The only thing persisted anywhere is a
small JSON file of daily aggregate numbers, in the form
`{"date": "2026-07-26", "members": 9694, "buyers": 3112, "role": 2567}`, which contains no
message content and no personal data.

Disclosed for completeness, since it involves transmission rather than storage: to generate the
summary, the message text and author display names are sent to Anthropic's Claude API, which is
the model that writes the summary. This is a processing call only — the output returns to the
app and is posted into the same Discord server. Anthropic's API terms apply to that call, and
API data is not used to train their models.

### メッセージコンテンツデータは機械学習またはAIモデルのトレーニングに使用されますか？

**No**

Message content is not used to train any model, mine or anyone else's. I do not train, fine-tune,
or build models. As noted above, message content is sent to a commercial LLM API (Anthropic
Claude) purely for inference to produce the daily summary; under that provider's API terms this
data is not used for model training. Nothing is retained for training purposes on my side.

### なぜMessage Content Intentが必要ですか？

Feature 1 — the daily digest — cannot exist without message content. Summarizing a day's
discussion requires the text of what was said; message metadata alone (author, timestamp,
channel) carries no information about what the conversation was about. There is no API that
returns a summary, a topic, or any content-derived signal without the content itself.

Concretely, the flow is: the app fetches the previous day's messages from the configured
channels, passes the text to an LLM with an instruction to produce a short digest of topics,
decisions, and announcements, and posts that digest into a summary channel in the same server.
Members who were away read one message instead of scrolling hundreds.

Feature 2 also depends on message content in a much narrower way: one channel receives an
automated sales notification from a separate integration, in the form "cumulative N copies".
The app reads that channel to extract that single number for the daily metrics report. Without
message content it cannot read the number, and that metric would be lost.

Scope limits, stated explicitly: the app reads only channels the server owner has listed in its
configuration file. It does not read any other channel, and it does not read DMs between
members.

The one exception, stated for completeness: because the role count in Feature 2 is currently
entered by hand (see the Server Members section above), the app maintains a DM channel with me,
the operator. It sends me a nightly reminder and reads only the number I reply with. No other
DM channel is read by the app. If the Server Members Intent is approved, that manual workaround
and the DM exchange supporting it are removed entirely.

---

## スクリーンショット / 動画（インテントごとに必須）

自分で撮って、リンクを貼る欄。**インテントごとに1点以上**必要。
撮る内容は以下。画像はGoogle Driveの共有リンクか、Cloudflare Pagesへ置いて直リンクでよい。

**Message Content Intent 用**

1. 要約Botが実際に投稿した日次ダイジェスト1件（サーバー名・チャンネル名が写った状態）
2. その要約の元になった会話チャンネル（設定に載せているチャンネルであることが分かる形）
3. `servers.json` の中身（読み取り対象チャンネルを明示的に列挙している設定であることの証拠）

**Server Members Intent 用**

4. ラウンジに投稿された日次指標レポート1件（参加者数／購入者数／ロール保有者数と前日比が写ったもの）
5. ロール推移グラフの投稿1件
6. 補強用：ロール数を手入力するために運営者へ届いているリマインドDM。
   「今この機能が手作業で回っている」ことの証拠になり、インテントの必要性を裏づける

---

## 未解決（提出前に済ませること）

### Privacy Policy URL がポータル未登録

「Botプロフィールからリンクされている」と答える前に、
Developer Portal → General Information → Privacy Policy URL へ登録する。
各サーバーのルール/お知らせチャンネルへのピン留めも同様。

### スクリーンショット未取得

上のセクションの6点を撮ってリンクを用意する。

---

## 実装済み（2026-07-28）

- **オプトアウト**：`servers.json` の任意キー `exclude_user_ids` に
  ユーザーIDを入れると、`main.py` の `fetch_messages()` が取得時点でそのメッセージを捨てる。
  要約リストに入らないためAnthropic APIへも送信されない。キー未設定なら従来と同一挙動。
  手順はREADMEの「要約対象からの除外（オプトアウト）」に記載
- **プライバシーポリシーの実態整合**：§6のDM記述と§4の保存範囲を実装に合わせて訂正済み
