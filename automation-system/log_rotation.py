"""
ログローテーション
- 10MB超のログファイルを logs/archive/ に日付付きで退避
- 30日以上前のアーカイブを自動削除
- morning_operator.py から毎朝呼ばれる、または単体実行可能

使い方:
  python3 log_rotation.py
"""

import gzip
import logging
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

LOGS_DIR    = Path(__file__).parent / "logs"
ARCHIVE_DIR = LOGS_DIR / "archive"

MAX_BYTES      = 10 * 1024 * 1024   # 10 MB
RETENTION_DAYS = 30
ROTATE_FILES   = [
    "scheduler.log",
    "scheduler_err.log",
    "server.log",
    "server_err.log",
    "dashboard.log",
    "dashboard_err.log",
    "morning.log",
    "alerts.log",
]


def rotate_if_needed(log_path: Path) -> bool:
    """ファイルが MAX_BYTES を超えていたらアーカイブしてtruncate。"""
    if not log_path.exists() or log_path.stat().st_size < MAX_BYTES:
        return False

    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest  = ARCHIVE_DIR / f"{log_path.stem}_{ts}.log.gz"

    with log_path.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out)

    log_path.write_text("", encoding="utf-8")   # truncate
    logger.info("ローテーション完了: %s → %s", log_path.name, dest.name)
    return True


def purge_old_archives():
    """RETENTION_DAYS 日より古いアーカイブを削除。"""
    if not ARCHIVE_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    deleted = 0
    for f in ARCHIVE_DIR.glob("*.gz"):
        if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
            f.unlink()
            deleted += 1
    if deleted:
        logger.info("古いアーカイブ %d 件を削除しました", deleted)


def run():
    rotated = 0
    for name in ROTATE_FILES:
        if rotate_if_needed(LOGS_DIR / name):
            rotated += 1
    purge_old_archives()
    logger.info("ログローテーション完了: %d ファイルをローテート", rotated)
    return rotated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    run()
