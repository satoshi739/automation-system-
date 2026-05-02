"""
コンテンツバリデーター

チェック項目:
1. forbidden_keyword  — config/forbidden_keywords.yaml のキーワードが含まれていないか
2. structure          — 各プラットフォームの文字数・枚数制約
3. cta                — {{cta:xxx}} 形式が使われている場合、xxx が有効キーか
4. critical_trigger   — ブランド固有の即時停止キーワード（brands_voice.yaml の critical_triggers）
                         検出時は errors ではなく critical_errors に追加し human_review_required=True

戻り値: ValidationResult(ok, errors, human_review_required, critical_errors)
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, List, Tuple

import yaml

_ROOT = Path(__file__).parent.parent
_FORBIDDEN_KEYWORDS_PATH = _ROOT / "config" / "forbidden_keywords.yaml"
_BRANDS_VOICE_PATH = _ROOT / "config" / "brands_voice.yaml"
_VALID_CTA_KEYS = {"save", "follow", "comment", "engagement"}

_CONSTRAINTS = {
    "x_thread": {
        "main_tweet_max": 140,
        "thread_item_max": 280,
    },
    "instagram_carousel": {
        "slides_max": 8,
    },
    "instagram_story": {
        "slides_max": 5,
    },
    "facebook_post": {
        "body_min": 400,
        "body_max": 600,
    },
    "instagram_post": {
        "caption_max": 2200,
    },
}


@dataclass
class ValidationError:
    content_id: str
    rule: str
    message: str
    value: str = ""


@dataclass
class ValidationResult:
    ok: bool
    errors: List[dict] = field(default_factory=list)
    human_review_required: bool = False
    critical_errors: List[dict] = field(default_factory=list)

    def add(self, err: ValidationError):
        self.errors.append(err.__dict__)
        self.ok = False

    def add_critical(self, err: ValidationError):
        """critical_trigger 検出 → human_review_required フラグを立て critical_errors に追加"""
        self.critical_errors.append(err.__dict__)
        self.human_review_required = True
        self.ok = False


def _load_forbidden_keywords() -> List[str]:
    if not _FORBIDDEN_KEYWORDS_PATH.exists():
        return []
    with open(_FORBIDDEN_KEYWORDS_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("keywords", [])


def _load_critical_triggers(brand: str) -> List[dict]:
    """brands_voice.yaml から指定ブランドの critical_triggers リストを返す"""
    if not _BRANDS_VOICE_PATH.exists():
        return []
    with open(_BRANDS_VOICE_PATH, encoding="utf-8") as f:
        voices = yaml.safe_load(f) or {}
    return voices.get(brand, {}).get("critical_triggers", [])


def _iter_strings(obj: Any, path: str = "") -> Generator[Tuple[str, str], None, None]:
    """dict/list を再帰的に辿り (パス, 文字列値) を yield する"""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _iter_strings(v, f"{path}[{i}]")


class ContentValidator:
    def __init__(self):
        self.forbidden = _load_forbidden_keywords()

    def validate(self, review_data: dict, brand: str = None) -> ValidationResult:
        """
        review_data 全体をバリデーションする。
        brand を指定するとブランド固有の critical_triggers もチェックする。
        brand が None のときは review_data['brand'] を自動取得。
        """
        resolved_brand = brand or review_data.get("brand", "")
        critical_triggers = _load_critical_triggers(resolved_brand)

        result = ValidationResult(ok=True)
        self._check_forbidden(review_data, result)
        self._check_structure(review_data, result)
        self._check_cta(review_data, result)
        if critical_triggers:
            self._check_critical(review_data, critical_triggers, result)
        return result

    # ──────────────────────────────────────────────
    # 1. 禁止キーワードチェック
    # ──────────────────────────────────────────────

    def _check_forbidden(self, data: dict, result: ValidationResult):
        if not self.forbidden:
            return
        for path, text in _iter_strings(data):
            for kw in self.forbidden:
                if kw in text:
                    result.add(ValidationError(
                        content_id=path.split(".")[0] if "." in path else path,
                        rule="forbidden_keyword",
                        message=f'禁止キーワード "{kw}" が含まれています',
                        value=path,
                    ))

    # ──────────────────────────────────────────────
    # 2. 構造チェック
    # ──────────────────────────────────────────────

    def _check_structure(self, data: dict, result: ValidationResult):
        for item in data.get("contents", []):
            t = item.get("type", "")
            cid = item.get("id", t)

            if t == "x_thread":
                tweet = item.get("main_tweet", "")
                if len(tweet) > _CONSTRAINTS["x_thread"]["main_tweet_max"]:
                    result.add(ValidationError(
                        cid, "structure",
                        f"main_tweet が {len(tweet)} 字（上限 140 字）",
                        tweet[:50],
                    ))
                for i, t_item in enumerate(item.get("thread", [])):
                    if len(t_item) > _CONSTRAINTS["x_thread"]["thread_item_max"]:
                        result.add(ValidationError(
                            cid, "structure",
                            f"thread[{i}] が {len(t_item)} 字（上限 280 字）",
                            t_item[:50],
                        ))

            elif t == "instagram_carousel":
                slides = item.get("slides", [])
                if len(slides) > _CONSTRAINTS["instagram_carousel"]["slides_max"]:
                    result.add(ValidationError(
                        cid, "structure",
                        f"スライドが {len(slides)} 枚（上限 8 枚）",
                    ))

            elif t == "instagram_story":
                slides = item.get("slides", [])
                if len(slides) > _CONSTRAINTS["instagram_story"]["slides_max"]:
                    result.add(ValidationError(
                        cid, "structure",
                        f"スライドが {len(slides)} 枚（上限 5 枚）",
                    ))

            elif t == "facebook_post":
                body = item.get("body", "")
                length = len(body)
                lo = _CONSTRAINTS["facebook_post"]["body_min"]
                hi = _CONSTRAINTS["facebook_post"]["body_max"]
                if not (lo <= length <= hi):
                    result.add(ValidationError(
                        cid, "structure",
                        f"本文が {length} 字（{lo}〜{hi} 字の範囲外）",
                        body[:50],
                    ))

            elif t == "instagram_post":
                caption = item.get("caption", "")
                cap_max = _CONSTRAINTS["instagram_post"]["caption_max"]
                if len(caption) > cap_max:
                    result.add(ValidationError(
                        cid, "structure",
                        f"caption が {len(caption)} 字（上限 {cap_max} 字）",
                        caption[:50],
                    ))

    # ──────────────────────────────────────────────
    # 3. CTA キー参照チェック
    # ──────────────────────────────────────────────

    def _check_cta(self, data: dict, result: ValidationResult):
        pattern = re.compile(r"\{\{cta:([^}]+)\}\}")
        for path, text in _iter_strings(data):
            for match in pattern.finditer(text):
                key = match.group(1).strip()
                if key not in _VALID_CTA_KEYS:
                    result.add(ValidationError(
                        content_id=path.split(".")[0] if "." in path else path,
                        rule="cta",
                        message=f'無効な CTA キー "{{{{cta:{key}}}}}" — 有効値: {sorted(_VALID_CTA_KEYS)}',
                        value=key,
                    ))

    # ──────────────────────────────────────────────
    # 4. Critical トリガーチェック（ブランド固有・即時停止）
    # ──────────────────────────────────────────────

    def _check_critical(
        self,
        data: dict,
        triggers: List[dict],
        result: ValidationResult,
    ):
        """
        brands_voice.yaml の critical_triggers に含まれるキーワードを検出したら
        human_review_required = True にして critical_errors に追加する。
        通常の errors には追加しない（別経路で処理するため）。
        """
        # contents のみチェック（analysis・メタデータはスキップ）
        contents_only = {"contents": data.get("contents", [])}

        for path, text in _iter_strings(contents_only):
            for trigger in triggers:
                kw = trigger.get("keyword", "")
                if kw and kw in text:
                    result.add_critical(ValidationError(
                        content_id=path.split(".")[0] if "." in path else path,
                        rule="critical_trigger",
                        message=f'⚠️ 即時停止キーワード "{kw}" — {trigger.get("reason", "")}',
                        value=path,
                    ))
