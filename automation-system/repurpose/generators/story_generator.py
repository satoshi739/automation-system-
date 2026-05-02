"""
Instagram ストーリーズ生成モジュール。
"""

import json
import logging
import os

from repurpose.brand_loader import build_voice_prompt

log = logging.getLogger(__name__)

_DUMMY = {
    "type": "instagram_story",
    "slides": [
        {
            "text": "副業で月10万円\n稼げてる人の共通点",
            "sub_text": "知ってる？",
            "background": "#1a1a2e",
            "sticker": "poll",
            "sticker_text": "知ってた？",
        },
        {
            "text": "答えは\n「仕組み化」",
            "sub_text": "スキルより仕組みが先",
            "background": "#16213e",
            "sticker": "none",
            "sticker_text": "",
        },
        {
            "text": "3ステップで\n今日から始められる",
            "sub_text": "①スキル棚卸し\n②少額スタート\n③データで判断",
            "background": "#0f3460",
            "sticker": "none",
            "sticker_text": "",
        },
        {
            "text": "詳しくはフィードの\nカルーセルをチェック👇",
            "sub_text": "保存もお忘れなく！",
            "background": "#e94560",
            "sticker": "question",
            "sticker_text": "副業で困ってること教えて！",
        },
    ],
}


class StoryGenerator:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(self, analysis: dict, brand: str = "satoshi-blog") -> dict:
        """
        返り値:
        {
          "type": "instagram_story",
          "slides": [
            {
              "text": str,
              "sub_text": str,
              "background": str,   # 16進カラーコード or "gradient"
              "sticker": str,      # poll / question / none
              "sticker_text": str,
            }
          ],
        }
        """
        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミーストーリーズを使用します")
            return _DUMMY.copy()

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            hooks = "\n".join(f"- {h}" for h in analysis.get("hooks", []))
            voice = build_voice_prompt(brand)

            prompt = f"""{voice}

---

以下のブログ分析データをもとに、Instagramストーリーズ（3〜5スライド）を生成してください。
上記ブランド設定のトーン・文体を必ず守ること。

トピック: {analysis.get('topic', '')}
フック:
{hooks}
ベストクオート: {analysis.get('best_quote', '')}

ストーリーズは短く・インパクト重視で。スワイプしてもらうように設計してください。
最初のスライドは興味を引くクイズ/問いかけ、最後はCTA（フィード投稿へ誘導 or フォロー）にしてください。

以下のJSON形式で返してください（マークダウンのコードブロック不要）:
{{
  "slides": [
    {{
      "text": "メインテキスト（短く、改行は\\nで表現）",
      "sub_text": "サブテキスト（空文字列可）",
      "background": "#16進カラーコード（ダーク系推奨）",
      "sticker": "poll か question か none",
      "sticker_text": "stickerがある場合のテキスト（noneなら空文字列）"
    }}
  ]
}}

日本語で生成してください。スライドは3〜5枚。"""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1200,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            result["type"] = "instagram_story"
            return result

        except Exception as e:
            log.error(f"ストーリーズ生成エラー: {e} — ダミーデータにフォールバック")
            return _DUMMY.copy()
