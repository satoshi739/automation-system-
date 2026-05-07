"""
content_queue/instagram/ 内の caption フィールドに生JSONが入っているファイルを修正する。
キャプションとハッシュタグを正しく展開して上書き保存する。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml
from utils import atomic_yaml_write

QUEUE_DIR = Path(__file__).parent / "content_queue" / "instagram"


def _unescape(s: str) -> str:
    """JSON文字列内の \\n を実改行に変換する。"""
    return s.replace("\\n", "\n").replace("\\t", "\t")


def _extract_fields(raw: str) -> dict | None:
    """
    JSONパースが失敗する場合のフォールバック。
    "caption": "..." と "hashtags": "..." を正規表現で直接抽出する。
    """
    cap_m = re.search(
        r'"caption":\s*"(.*?)(?=",\s*\n?\s*"hashtags")',
        raw,
        re.DOTALL,
    )
    tag_m = re.search(
        r'"hashtags":\s*"(.*?)(?=",?\s*\n?\s*(?:"hook"|"?\s*\}))',
        raw,
        re.DOTALL,
    )
    if not cap_m:
        return None
    return {
        "caption": _unescape(cap_m.group(1)),
        "hashtags": _unescape(tag_m.group(1)) if tag_m else "",
    }


def _parse_caption(raw: str) -> dict | None:
    """戦略1: json.loads → 戦略2: 正規表現抽出"""
    for attempt in (raw, re.sub(r'(?<!\\)\n', r'\\n', raw)):
        try:
            return json.loads(attempt)
        except Exception:
            pass
    return _extract_fields(raw)


def repair_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return False

    caption_raw = data.get("caption", "")
    if not (isinstance(caption_raw, str) and caption_raw.strip().startswith("{")):
        return False

    parsed = _parse_caption(caption_raw)
    if not parsed:
        print(f"  [SKIP] 抽出失敗: {path.name}")
        return False

    caption = parsed.get("caption", "").strip()
    hashtags = parsed.get("hashtags", "").strip()
    full_caption = f"{caption}\n\n{hashtags}".strip() if hashtags else caption

    if not full_caption:
        print(f"  [SKIP] キャプション空: {path.name}")
        return False

    data["caption"] = full_caption
    data.pop("needs_review", None)

    atomic_yaml_write(path, data)

    print(f"  [OK] {path.name}")
    print(f"       → {full_caption[:60].replace(chr(10), ' ')}...")
    return True


def main() -> None:
    files = sorted(QUEUE_DIR.glob("*.yaml"))
    fixed = 0
    for p in files:
        if repair_file(p):
            fixed += 1
    print(f"\n完了: {fixed}/{len(files)} 件を修正しました")


if __name__ == "__main__":
    main()
