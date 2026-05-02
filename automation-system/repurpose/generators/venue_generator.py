"""
ラウンジ・夜の店舗向け Instagram/TikTok 投稿生成モジュール。

ブログ不要 — 10の安全な投稿軸（SAFE_THEMES）からテーマを指定して
1投稿（キャプション + ハッシュタグ + 画像プロンプト）を生成する。

使い方:
    python -m repurpose.generators.venue_generator --brand bangkok-lounge --theme anshin
    python -m repurpose.generators.venue_generator --brand bangkok-lounge --rotate
"""

import json
import logging
import os
from pathlib import Path

from repurpose.brand_loader import build_voice_prompt, get_brand_voice

log = logging.getLogger(__name__)

# ── 投稿軸（10本）────────────────────────────────────────────
SAFE_THEMES = {
    "anshin":      "安心できる空間・居心地の良さ",
    "clean":       "清潔感・衛生管理・店内の清潔さ",
    "japanese":    "日本語対応・日本人スタッフの親しみやすさ",
    "calm":        "落ち着いた雰囲気・BGM・インテリアの紹介",
    "nijikai":     "二次会・アフタービジネスの場として",
    "settai":      "接待・ビジネスシーンでの活用",
    "welcome":     "初めての方歓迎・入りやすい雰囲気",
    "pricing":     "料金明瞭・追加料金なし・安心の料金体系",
    "staff":       "スタッフの丁寧な接客・ホスピタリティ",
    "atmosphere":  "顔出しなし雰囲気写真・店内の見せ方",
}

# テーマローテーション管理（プロセスリセットで初期化）
_STATE_FILE = Path(__file__).parent.parent.parent / "config" / "venue_theme_state.json"


def _next_theme_key() -> str:
    keys = list(SAFE_THEMES.keys())
    try:
        if _STATE_FILE.exists():
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            idx = (state.get("index", 0) + 1) % len(keys)
        else:
            idx = 0
    except Exception:
        idx = 0
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps({"index": idx}), encoding="utf-8")
    return keys[idx]


_DUMMY = {
    "type": "instagram_post",
    "theme": "anshin",
    "caption": (
        "バンコクで「安心して過ごせる場所」を探している方へ。\n\n"
        "日本語対応のスタッフが常駐しており、初めての方でもご遠慮なくお越しください。\n"
        "清潔で落ち着いた空間で、ゆっくりとお過ごしいただけます。\n\n"
        "ご予約・お問い合わせはDMまたはプロフィールのリンクから。"
    ),
    "hashtags": [
        "#バンコク", "#バンコクナイト", "#日本語対応", "#バンコクラウンジ",
        "#バンコク在住", "#バンコク旅行", "#バンコクグルメ", "#タイバンコク",
        "#バンコク観光", "#日本人スタッフ",
    ],
    "image_prompt": (
        "Elegant and cozy lounge interior in Bangkok, soft warm lighting, "
        "minimalist Japanese-inspired decor, no people, clean and inviting atmosphere, "
        "shot from a wide angle showing the full space, cinematic style"
    ),
}


class VenuePostGenerator:
    def __init__(self):
        self._api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(self, brand: str = "bangkok-lounge", theme_key: str = None) -> dict:
        """
        指定テーマで Instagram 投稿を1件生成する。

        Args:
            brand: ブランドキー（brands_voice.yaml に定義されていること）
            theme_key: SAFE_THEMES のキー。None なら前回の続きを自動選択（ローテーション）

        Returns:
            {
              "type": "instagram_post",
              "theme": str,
              "caption": str,
              "hashtags": [str],
              "image_prompt": str,
            }
        """
        resolved_theme_key = theme_key or _next_theme_key()
        theme_desc = SAFE_THEMES.get(resolved_theme_key, resolved_theme_key)
        log.info(f"投稿生成: brand={brand} theme={resolved_theme_key} ({theme_desc})")

        if not self._api_key:
            log.warning("ANTHROPIC_API_KEY 未設定 — ダミー投稿を使用します")
            return {**_DUMMY, "theme": resolved_theme_key}

        voice = build_voice_prompt(brand)
        brand_data = get_brand_voice(brand)
        persona = brand_data.get("persona", "")

        system = f"""{voice}

あなたは上記のブランド設定に従い、バンコクの日本語対応ラウンジの Instagram 投稿文を書くライターです。
以下の制約を必ず守ること:
- 売り込み・押しつけ・過剰な宣伝なし
- 性的表現・タイ政治・王室・VAPE・薬物に関する言及は一切しない
- 顔出し写真・個人特定できる情報を投稿しない
- CTA は「フォロー」か「保存」または「DM・プロフ」への自然な誘導のみ"""

        prompt = f"""今回の投稿テーマ: 「{theme_desc}」

このテーマで、Instagram に投稿する日本語のキャプションを作成してください。
バンコクに来る・住む日本人をターゲットにした、安心感・信頼感が伝わる文章を書いてください。

以下の JSON 形式のみで返してください（コードブロック不要）:
{{
  "caption": "投稿本文（150〜200字、改行入り）",
  "hashtags": ["#タグ1", "#タグ2", ... 合計10個],
  "image_prompt": "English description of the ideal photo for this post (cinematic, no people shown, venue atmosphere)"
}}"""

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._api_key)
            message = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            result = json.loads(raw)
            result["type"] = "instagram_post"
            result["theme"] = resolved_theme_key
            return result

        except Exception as e:
            log.error(f"投稿生成エラー: {e} — ダミーデータにフォールバック")
            return {**_DUMMY, "theme": resolved_theme_key}


if __name__ == "__main__":
    import argparse
    import sys
    import yaml

    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="ラウンジ向けInstagram投稿生成")
    parser.add_argument("--brand", default="bangkok-lounge", help="ブランドキー")
    parser.add_argument("--theme", choices=list(SAFE_THEMES.keys()), help="投稿テーマ（省略時はローテーション）")
    parser.add_argument("--rotate", action="store_true", help="ローテーションで次のテーマを使用")
    parser.add_argument("--list-themes", action="store_true", help="利用可能なテーマ一覧を表示")
    args = parser.parse_args()

    if args.list_themes:
        print("利用可能なテーマ:")
        for k, v in SAFE_THEMES.items():
            print(f"  {k:12} — {v}")
        sys.exit(0)

    gen = VenuePostGenerator()
    result = gen.generate(brand=args.brand, theme_key=args.theme)

    print("\n" + "="*55)
    print(f"テーマ: {result['theme']} — {SAFE_THEMES.get(result['theme'], '')}")
    print("="*55)
    print(f"\n【キャプション】\n{result['caption']}")
    print(f"\n【ハッシュタグ】\n{' '.join(result['hashtags'])}")
    print(f"\n【画像プロンプト】\n{result['image_prompt']}")
    print("="*55)
