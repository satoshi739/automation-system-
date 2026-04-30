#!/usr/bin/env python3
"""
image_url が空のキューエントリを対象に画像アップロードをリトライする。

WordPress → Google Drive の順で試し、成功したら YAML を更新して
inbox の画像を processed/ に移動する。

使い方:
  python3 retry_image_upload.py           # 全ブランド
  python3 retry_image_upload.py --brand cashflowsupport
  python3 retry_image_upload.py --dry-run
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

ROOT         = Path(__file__).parent
QUEUE_DIR    = ROOT / "content_queue" / "instagram"
INBOX_DIR    = ROOT / "media" / "inbox"
PROCESSED_DIR = ROOT / "media" / "processed"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "dashboard"))

from sns.photo_importer import _upload_to_wordpress_media, _upload_to_drive

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("retry_upload")


def _find_inbox_file(brand: str, original_filename: str) -> Path | None:
    candidates = [
        INBOX_DIR / brand / original_filename,
        INBOX_DIR / original_filename,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def retry_brand(brand: str, dry_run: bool) -> tuple[int, int]:
    """(成功数, 失敗数) を返す。"""
    ok = fail = 0
    pattern = f"*_{brand}_*.yaml"
    yaml_files = sorted(QUEUE_DIR.glob(pattern))
    if not yaml_files:
        logger.info("[%s] 対象ファイルなし", brand)
        return 0, 0

    for yf in yaml_files:
        with open(yf, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue
        if data.get("image_url"):
            continue  # 既にURLあり → スキップ

        orig = data.get("original_filename", "")
        if not orig:
            logger.warning("[%s] original_filename なし: %s", brand, yf.name)
            fail += 1
            continue

        file_path = _find_inbox_file(brand, orig)
        if not file_path:
            logger.warning("[%s] inbox にファイルなし: %s", brand, orig)
            fail += 1
            continue

        logger.info("[%s] アップロード試行: %s", brand, orig)
        if dry_run:
            logger.info("[dry-run] スキップ: %s", yf.name)
            ok += 1
            continue

        public_url = ""
        try:
            public_url = _upload_to_wordpress_media(file_path, brand)
            logger.info("[%s] WP アップロード成功: %s", brand, public_url[:60])
        except Exception as wp_err:
            logger.warning("[%s] WP 失敗: %s → Drive を試みます", brand, wp_err)
            try:
                public_url = _upload_to_drive(file_path, brand)
                logger.info("[%s] Drive アップロード成功: %s", brand, public_url[:60])
            except Exception as drive_err:
                logger.error("[%s] 全アップロード失敗: %s", brand, drive_err)
                fail += 1
                continue

        # YAML 更新
        data["image_url"] = public_url
        data.pop("error", None)
        data.pop("needs_review", None)
        with open(yf, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        logger.info("[%s] YAML 更新: %s", brand, yf.name)

        # processed に移動
        dest_dir = PROCESSED_DIR / brand
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(file_path), dest_dir / file_path.name)
        logger.info("[%s] processed に移動: %s", brand, file_path.name)
        ok += 1

    return ok, fail


def main() -> None:
    parser = argparse.ArgumentParser(description="image_url リトライアップロード")
    parser.add_argument("--brand", default="", help="ブランド名（省略で全ブランド）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    brands = [args.brand] if args.brand else [
        d.name for d in INBOX_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    ]

    total_ok = total_fail = 0
    for b in brands:
        ok, fail = retry_brand(b, args.dry_run)
        total_ok += ok
        total_fail += fail
        logger.info("[%s] 結果: 成功=%d 失敗=%d", b, ok, fail)

    logger.info("=== 完了: 成功=%d 失敗=%d ===", total_ok, total_fail)


if __name__ == "__main__":
    main()
