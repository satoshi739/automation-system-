from utils import claude_resp_text
"""
Instagram カルーセル（スライド型投稿）生成モジュール。
"""

import json
import logging
import os

from repurpose.brand_loader import build_voice_prompt

log = logging.getLogger(__name__)

_DUMMY = {
    "type": "instagram_carousel",
    "slides": [
        {
            "title": "副業で月10万円\n稼ぐ完全ガイド",
            "body": "スマホ一台で今日から始められる方法を公開",
            "image_prompt": "Flat design illustration of a smartphone with money icons, modern Japanese style, orange and white color scheme",
        },
        {
            "title": "ステップ1\nスキルの棚卸し",
            "body": "得意なことを1つ選ぶだけでOK。プログラミング・デザイン・ライティング・物販など何でも収益化できる時代。",
            "image_prompt": "Person writing a list on notebook, pencil, warm lighting, minimal flat illustration",
        },
        {
            "title": "ステップ2\n少額からスタート",
            "body": "最初は3,000円〜1万円で十分。失敗しても痛くない金額でPDCAを回すことが成功の鍵。",
            "image_prompt": "Coin stacking gradually growing higher, flat illustration, green tones",
        },
        {
            "title": "ステップ3\nデータで判断する",
            "body": "リサーチツールを使って需要のある市場を特定。感覚ではなくデータに基づく判断で利益率が変わる。",
            "image_prompt": "Dashboard with charts and graphs on screen, data analytics flat illustration, blue tones",
        },
        {
            "title": "よくある失敗",
            "body": "いきなり大きく投資・完璧を求めすぎ・リサーチなしで動く。この3つを避けるだけで成功率が大幅アップ。",
            "image_prompt": "Warning signs with X marks, red and white flat illustration, simple icons",
        },
        {
            "title": "まとめ",
            "body": "小さく始めて素早く改善。月10万はゴールじゃなくてスタートライン。",
            "image_prompt": "Person reaching a milestone marker on a road, sunrise background, inspiring flat illustration",
        },
        {
            "title": "もっと詳しく\n知りたい方へ",
            "body": "プロフィールのリンクから無料ガイドをチェック！\nフォロー＆保存もお忘れなく🙌",
            "image_prompt": "Arrow pointing right with Instagram follow button, coral and white flat illustration, CTA design",
        },
    ],
    "caption": "副業で月10万円を稼ぐには「仕組み」が全てです。スキルより先に正しい方法論を身につけることで、最短ルートで結果が出せます。今日からできる3ステップをまとめました📱\n\n保存して後で見返してください💾",
    "hashtags": ["#副業", "#在宅ワーク", "#物販", "#副業初心者", "#スマホ副業", "#月10万", "#副収入", "#せどり"],
}


class CarouselGenerator:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(self, analysis: dict, brand: str = "satoshi-blog") -> dict:
        """
        返り値:
        {
          "type": "instagram_carousel",
          "slides": [{"title": str, "body": str, "image_prompt": str}],  # 6〜8枚
          "caption": str,
          "hashtags": [str],
        }
        """
        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミーカルーセルを使用します")
            return _DUMMY.copy()

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            key_points = "\n".join(f"- {p}" for p in analysis.get("key_points", []))
            voice = build_voice_prompt(brand)

            prompt = f"""{voice}

---

以下のブログ分析データをもとに、Instagramカルーセル投稿を生成してください。
上記ブランド設定のトーン・文体を必ず守ること。

トピック: {analysis.get('topic', '')}
要約: {analysis.get('summary', '')}
キーポイント:
{key_points}
ベストクオート: {analysis.get('best_quote', '')}
フック: {analysis.get('hooks', [''])[0] if analysis.get('hooks') else ''}
ターゲット: {analysis.get('target', 'beginner')}

以下のJSON形式で返してください（マークダウンのコードブロック不要）:
{{
  "slides": [
    {{
      "title": "表紙タイトル（インパクトある短いフレーズ、改行\\nで2行もOK）",
      "body": "表紙サブテキスト（1〜2行）",
      "image_prompt": "English image generation prompt for this slide"
    }},
    ... (合計6〜8枚、最後のスライドはCTAにする)
  ],
  "caption": "投稿キャプション200字程度（日本語）",
  "hashtags": ["#タグ1", "#タグ2", ... 8〜10個]
}}

日本語で生成してください（image_promptのみ英語）。"""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = claude_resp_text(message)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            result["type"] = "instagram_carousel"
            return result

        except Exception as e:
            log.error(f"カルーセル生成エラー: {e} — ダミーデータにフォールバック")
            return _DUMMY.copy()
