from __future__ import annotations

import yaml
from pathlib import Path


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
