"""
ブログ本文を Claude で構造化分析するモジュール。
"""

import json
import logging
import os

log = logging.getLogger(__name__)

_DUMMY_ANALYSIS = {
    "topic": "副業で月10万円を稼ぐ方法",
    "summary": "物販・デジタルコンテンツ・スキル販売など、スマホ一台で始められる副業の具体的な手順を解説。初心者でも3,000円の元手からスタートできる。",
    "key_points": [
        "自分のスキルを棚卸しして収益化できる分野を特定する",
        "最初は3,000円〜1万円の少額から始めてリスクを最小化する",
        "リサーチツールを使って需要のある商品・サービスを見つける",
        "作業の仕組み化で時間投下を最小化し利益率を高める",
    ],
    "hooks": [
        "毎月同じ給料でいいの？副業で月10万プラスできたら人生変わる",
        "リサーチに3時間かけてたのが30分になった理由",
    ],
    "best_quote": "副業で稼ぐ最大のコツは「小さく始めて素早く改善する」こと。",
    "tone": "casual",
    "target": "beginner",
}


class BlogAnalyzer:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def analyze(self, title: str, body: str) -> dict:
        """
        Claude でブログを分析して構造化データを返す。
        APIキーがなければダミーを返す。
        """
        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミー分析データを使用します")
            return _DUMMY_ANALYSIS.copy()

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)

            prompt = f"""以下のブログ記事を分析して、JSON形式で返してください。

タイトル: {title}

本文:
{body[:4000]}

返すJSONのキー:
- topic: 主テーマ（1行）
- summary: 2〜3文の要約
- key_points: 主要ポイント 3〜5件（文字列の配列）
- hooks: 感情フック 2〜3件（視聴者の痛み・欲求に刺さる言葉、文字列の配列）
- best_quote: 最も引用しやすい一文
- tone: casual / professional / inspiring のいずれか
- target: beginner / intermediate / advanced のいずれか

JSONのみを返してください（マークダウンのコードブロック不要）。"""

            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            # JSONブロックが混入している場合に除去
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            return json.loads(raw)

        except Exception as e:
            log.error(f"ブログ分析エラー: {e} — ダミーデータにフォールバック")
            return _DUMMY_ANALYSIS.copy()
