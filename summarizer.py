"""
LLM（Claude API）を使った日報要約モジュール
- メッセージが大量の場合はチャンク分割して処理
"""

import os
import textwrap

import anthropic

MODEL = "claude-haiku-4-5-20251001"  # コスト効率重視。精度を上げるなら claude-sonnet-4-6 へ
MAX_TOKENS_PER_REQUEST = 8000  # 1リクエストで扱うメッセージの最大トークン目安（概算）
CHARS_PER_TOKEN = 2  # 日本語の概算（1トークン≒2文字）

SYSTEM_PROMPT = """
あなたはDiscordコミュニティの運営サポートAIです。
与えられたDiscordメッセージのログを分析し、以下のカテゴリで日報を作成してください。

出力形式（Markdown）:
## 🗣️ 主要なトピックス
- 当日に議論・共有されたメインテーマを箇条書きで

## ✅ 決定事項・アクション
- 決まったこと、今後やることとして明示されたものを箇条書きで
- なければ「なし」

## 📢 共有・お知らせ
- イベント告知、リンク共有、重要なお知らせを箇条書きで
- なければ「なし」

## 💬 その他の動き
- 雑談・軽いやりとりなど、上記に分類しきれない動向を1〜2行で

ルール:
- 個人名は「さん」付けで表記（例: のりさん）
- 事実を忠実に要約し、自分の意見・推測は加えない
- 投稿がなかったカテゴリは「なし」と書く
- 全体を1000文字以内に収める
""".strip()


class Summarizer:
    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY が設定されていません")
        self._client = anthropic.Anthropic(api_key=api_key)

    def _messages_to_text(self, messages: list[dict]) -> str:
        """メッセージリストを要約用テキストに変換する。"""
        lines = []
        for m in messages:
            line = f"[{m['timestamp']}] {m['author']}: {m['content']}"
            if m.get("attachments"):
                line += f" （添付: {m['attachments']}）"
            lines.append(line)
        return "\n".join(lines)

    def _split_into_chunks(self, messages: list[dict]) -> list[list[dict]]:
        """トークン上限を考慮してメッセージをチャンク分割する。"""
        max_chars = MAX_TOKENS_PER_REQUEST * CHARS_PER_TOKEN
        chunks: list[list[dict]] = []
        current_chunk: list[dict] = []
        current_len = 0

        for msg in messages:
            msg_len = len(msg.get("content", "")) + 60  # タイムスタンプ・著者名の概算
            if current_len + msg_len > max_chars and current_chunk:
                chunks.append(current_chunk)
                current_chunk = []
                current_len = 0
            current_chunk.append(msg)
            current_len += msg_len

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _call_api(self, messages_text: str, date_str: str, chunk_info: str = "") -> str:
        user_content = textwrap.dedent(f"""
            以下は {date_str} のDiscordメッセージログです{chunk_info}。
            日報を作成してください。

            --- ログ開始 ---
            {messages_text}
            --- ログ終了 ---
        """).strip()

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text

    def summarize(self, messages: list[dict], date_str: str) -> str:
        """
        メッセージリストを要約して日報テキストを返す。
        大量メッセージの場合はチャンク処理して統合要約する。
        """
        chunks = self._split_into_chunks(messages)

        if len(chunks) == 1:
            text = self._messages_to_text(chunks[0])
            return self._call_api(text, date_str)

        # 複数チャンクの場合: 各チャンクを要約 → 統合要約
        print(f"[Summarizer] メッセージを {len(chunks)} チャンクに分割して処理します")
        partial_summaries = []
        for i, chunk in enumerate(chunks, 1):
            text = self._messages_to_text(chunk)
            info = f"（{len(chunks)} 分割のうち {i} 番目）"
            partial = self._call_api(text, date_str, info)
            partial_summaries.append(partial)

        # 部分要約を統合
        combined = "\n\n---\n\n".join(partial_summaries)
        integration_prompt = textwrap.dedent(f"""
            以下は {date_str} のDiscordログを複数回に分けて要約した結果です。
            これらを統合して、最終的な日報を1つにまとめてください。
            形式は同じ（## セクション + 箇条書き）を維持し、重複を除いてください。

            {combined}
        """).strip()

        response = self._client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": integration_prompt}],
        )
        return response.content[0].text
