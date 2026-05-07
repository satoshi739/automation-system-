from utils import claude_resp_text
"""
X（Twitter）スレッド生成モジュール。
"""

import json
import logging
import os

from repurpose.brand_loader import build_voice_prompt

log = logging.getLogger(__name__)

_DUMMY = {
    "type": "x_thread",
    "main_tweet": "副業で月10万円を稼ぐ方法、知ってますか？実はスキルより「仕組み」が全てです。スマホ一台で今日から始められる具体的な手順を解説👇",
    "thread": [
        "【ステップ1】まず自分のスキルを棚卸し。プログラミング・デザイン・ライティング・物販など、何でも収益化できる時代。得意なこと1つ選ぶだけでOK。",
        "【ステップ2】最初は3,000円〜1万円の少額からスタート。失敗しても痛くない金額で仕組みを学ぶ。大事なのはPDCAを素早く回すこと。",
        "【ステップ3】リサーチツールを活用して需要のある市場を見つける。感覚で動かず、データで判断する習慣をつけるだけで利益率が変わる。",
        "【まとめ】副業で稼ぐ最大のコツは「小さく始めて素早く改善する」こと。月10万はゴールじゃなくてスタートライン。続ければ必ず道が開ける💪\n\n参考になったらフォロー＆保存をお願いします！",
    ],
}


class XGenerator:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(self, analysis: dict, brand: str = "satoshi-blog") -> dict:
        """
        返り値:
        {
          "type": "x_thread",
          "main_tweet": str,   # 140字以内
          "thread": [str],     # 3〜5件（各280字以内）、最後はCTA
        }
        """
        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミーXスレッドを使用します")
            return _DUMMY.copy()

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            key_points = "\n".join(f"- {p}" for p in analysis.get("key_points", []))
            hooks = "\n".join(f"- {h}" for h in analysis.get("hooks", []))
            voice = build_voice_prompt(brand)

            prompt = f"""{voice}

---

以下のブログ分析データをもとに、X（Twitter）スレッドを生成してください。
上記ブランド設定のトーン・文体を必ず守ること。

トピック: {analysis.get('topic', '')}
要約: {analysis.get('summary', '')}
キーポイント:
{key_points}
フック:
{hooks}
ベストクオート: {analysis.get('best_quote', '')}

以下のJSON形式で返してください（マークダウンのコードブロック不要）:
{{
  "main_tweet": "140字以内のメインツイート（フックを使って興味を引く）",
  "thread": [
    "スレッド1（280字以内）",
    "スレッド2（280字以内）",
    "スレッド3（280字以内）",
    "スレッド4（280字以内）※最後はブランドのCTA方針に従う"
  ]
}}

日本語で生成してください。"""

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
            result["type"] = "x_thread"
            return result

        except Exception as e:
            log.error(f"Xスレッド生成エラー: {e} — ダミーデータにフォールバック")
            return _DUMMY.copy()
