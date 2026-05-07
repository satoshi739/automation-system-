from utils import claude_resp_text
"""
Facebook 長文投稿生成モジュール。
"""

import json
import logging
import os

from repurpose.brand_loader import build_voice_prompt

log = logging.getLogger(__name__)

_DUMMY = {
    "type": "facebook_post",
    "body": """毎月同じ給料で満足していますか？

実は今、副業で月10万円を稼いでいる人たちには、ある共通点があります。それは「スキルではなく仕組みを先に作る」という考え方です。

私も最初は何から手をつければいいか全くわかりませんでした。でも、この3つのステップを実践してから、物事がシンプルになりました。

【ステップ1：自分のスキルを棚卸しする】
プログラミング・デザイン・ライティング・物販など、現代では様々なスキルが収益化できます。特別な才能は不要。あなたが「人より少し得意」と思えることを1つ選ぶだけで十分です。

【ステップ2：3,000円から始める】
いきなり大きく投資する必要はありません。失敗しても痛くない金額でスタートして、素早くPDCAを回すことが最短ルートです。

【ステップ3：データで判断する習慣をつける】
感覚で動くのではなく、リサーチツールを使って需要のある市場を特定する。これだけで利益率が大きく変わります。

副業で稼ぐ最大のコツは「小さく始めて素早く改善する」こと。月10万円はゴールではなく、スタートラインです。

詳しい方法はプロフィールのリンクから確認できます。気になった方はコメントで「詳しく！」と教えてください📩""",
}


class FacebookGenerator:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(self, analysis: dict, brand: str = "satoshi-blog") -> dict:
        """
        返り値:
        {
          "type": "facebook_post",
          "body": str,   # 400〜600字の長文投稿。冒頭フック→本論→CTA
        }
        """
        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミーFacebook投稿を使用します")
            return _DUMMY.copy()

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            key_points = "\n".join(f"- {p}" for p in analysis.get("key_points", []))
            hooks = "\n".join(f"- {h}" for h in analysis.get("hooks", []))
            voice = build_voice_prompt(brand)

            prompt = f"""{voice}

---

以下のブログ分析データをもとに、Facebook長文投稿を生成してください。
上記ブランド設定のトーン・文体を必ず守ること。

トピック: {analysis.get('topic', '')}
要約: {analysis.get('summary', '')}
キーポイント:
{key_points}
フック:
{hooks}
ベストクオート: {analysis.get('best_quote', '')}
ターゲット: {analysis.get('target', 'beginner')}

構成:
1. 冒頭フック（読者の痛みや欲求に刺さる問いかけ）
2. 本論（キーポイントを自然な文章で展開、見出し【】を使ってもOK）
3. まとめ（ベストクオートを活かす）
4. CTA（コメント・シェア・リンクへの誘導）

400〜600字程度の日本語で生成してください。
以下のJSON形式で返してください（マークダウンのコードブロック不要）:
{{
  "body": "投稿本文"
}}"""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = claude_resp_text(message)
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            result["type"] = "facebook_post"
            return result

        except Exception as e:
            log.error(f"Facebook投稿生成エラー: {e} — ダミーデータにフォールバック")
            return _DUMMY.copy()
