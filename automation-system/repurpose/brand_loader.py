"""
ブランド設定ローダー

brands_voice.yaml から指定ブランドの声・人格設定を読み込む。
不明なブランドはデフォルト設定を返す（エラーにしない）。
"""

import logging
from functools import lru_cache
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_VOICE_PATH = _CONFIG_DIR / "brands_voice.yaml"

_DEFAULT_VOICE = {
    "persona": "フレンドリーなSNSライター。読みやすく・分かりやすく・役に立つ情報を発信する",
    "tone": "casual",
    "writing_style": [
        "読みやすい短文",
        "具体的な表現",
        "自然な話し言葉",
    ],
    "topics": ["副業", "ビジネス", "ライフスタイル"],
    "vocabulary": [],
    "avoid": [],
    "cta_style": "保存・フォロー訴求",
    "example_tone": "今日から使える情報をお伝えします。",
}


@lru_cache(maxsize=None)
def _load_all_voices() -> dict:
    if not _VOICE_PATH.exists():
        log.warning(f"brands_voice.yaml が見つかりません: {_VOICE_PATH}")
        return {}
    with open(_VOICE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_brand_voice(brand: str) -> dict:
    """
    ブランド名に対応する声・人格設定を返す。
    未定義のブランドはデフォルト設定を返す。
    """
    all_voices = _load_all_voices()
    if brand not in all_voices:
        log.warning(f"brands_voice.yaml に '{brand}' が未定義 — デフォルト設定を使用")
        return _DEFAULT_VOICE.copy()
    return all_voices[brand]


def build_voice_prompt(brand: str) -> str:
    """
    ブランドの声設定を Claude プロンプト用の文字列に変換する。
    各 generator のシステムプロンプトに追記して使う。
    """
    v = get_brand_voice(brand)

    style_lines = "\n".join(f"  - {s}" for s in v.get("writing_style", []))
    avoid_lines = "\n".join(f"  - {a}" for a in v.get("avoid", [])) or "  （制約なし）"
    vocab_lines = "、".join(v.get("vocabulary", [])) or "（指定なし）"
    extra_notes = ""
    for note_key in ("compliance_note", "multilingual_note"):
        if v.get(note_key):
            extra_notes += f"\n⚠️ {v[note_key]}"

    return f"""
## ブランド設定（必ず従うこと）
- ブランド人格: {v.get('persona', '')}
- トーン: {v.get('tone', 'casual')}
- 文体ルール:
{style_lines}
- 使うべき語彙・キーワード: {vocab_lines}
- 避けること:
{avoid_lines}
- CTA方針: {v.get('cta_style', '')}
- このブランドの文体例: 「{v.get('example_tone', '')}」{extra_notes}
""".strip()
