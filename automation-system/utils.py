from __future__ import annotations

import yaml
from pathlib import Path


def claude_resp_text(resp) -> str:
    """Claude API レスポンスからテキストを安全に抽出する（content が空/None でも安全）"""
    try:
        if resp and resp.content and len(resp.content) > 0:
            return (resp.content[0].text or "").strip()
    except (AttributeError, IndexError):
        pass
    return ""


def safe_int(value, default: int = 0) -> int:
    """ValueError/TypeError を握りつぶして安全に int 変換する"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def atomic_yaml_write(file_path: Path, data: dict, **dump_kwargs) -> None:
    """YAML をアトミックに書き込む（tmp → rename で途中クラッシュによるファイル破損を防止）"""
    dump_kwargs.setdefault("allow_unicode", True)
    dump_kwargs.setdefault("default_flow_style", False)
    dump_kwargs.setdefault("sort_keys", False)
    tmp = Path(file_path).with_suffix(".tmp")
    try:
        tmp.write_text(yaml.dump(data, **dump_kwargs), encoding="utf-8")
        tmp.replace(file_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
