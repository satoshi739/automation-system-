#!/usr/bin/env python3
"""
ログローテーション - 毎日 04:00 に launchd から起動
- 対象ログを logs/archive/YYYY-MM-DD/ にコピーして truncate
- 30日以上古い archive/YYYY-MM-DD/ ディレクトリを削除
- 完了サマリーを alerts.log に記録
"""
from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR    = Path(__file__).parent / "logs"
ARCHIVE_DIR = LOGS_DIR / "archive"
ALERTS_LOG  = LOGS_DIR / "alerts.log"
RETAIN_DAYS = 30

# alerts.log は最後に処理する（truncate 後に write_summary が書き込むため）
# TODO: copy→truncate 間の race condition 対策が必要になったら fcntl ロックを追加する
TARGETS = [
    "morning.log",
    "scheduler_err.log",
    "server_err.log",
    "dashboard.log",
    "dashboard_err.log",
    "alerts.log",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rotate_logs")


def rotate(today: str, dry_run: bool) -> tuple[int, float]:
    """アーカイブ + truncate。(ローテーション数, アーカイブバイト数) を返す。"""
    dest_dir = ARCHIVE_DIR / today
    rotated = 0
    archived_bytes = 0.0

    for name in TARGETS:
        src = LOGS_DIR / name
        if not src.exists() or src.stat().st_size < 1:
            logger.info("スキップ: %s (存在しないか空)", name)
            continue
        dest = dest_dir / name
        size = src.stat().st_size
        if dry_run:
            logger.info("[dry-run] %s → %s (%d B)", name, dest, size)
            rotated += 1
            archived_bytes += size
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            logger.warning("既存アーカイブあり、スキップ: %s", dest)
            continue
        shutil.copy2(src, dest)
        src.write_bytes(b"")   # truncate — O_APPEND fd は次回書き込みで offset 0 から再開
        logger.info("ローテーション完了: %s (%d B) → %s", name, size, dest)
        rotated += 1
        archived_bytes += size

    return rotated, archived_bytes


def purge_old(today_dt: datetime, dry_run: bool) -> int:
    """YYYY-MM-DD 形式の古いアーカイブディレクトリを削除。削除数を返す。"""
    cutoff = today_dt - timedelta(days=RETAIN_DAYS)
    deleted = 0
    for entry in ARCHIVE_DIR.iterdir():
        if not entry.is_dir():
            continue              # フラット形式の手動アーカイブは触らない
        try:
            dt = datetime.strptime(entry.name, "%Y-%m-%d")
        except ValueError:
            continue              # 日付形式でないディレクトリは触らない
        if dt < cutoff:
            if dry_run:
                logger.info("[dry-run] 削除予定: %s", entry.name)
            else:
                shutil.rmtree(entry)
                logger.info("古いアーカイブ削除: %s", entry.name)
            deleted += 1
    return deleted


def write_summary(rotated: int, archived_bytes: float, deleted: int, dry_run: bool) -> None:
    mb = archived_bytes / 1_048_576
    summary = f"{rotated} files rotated, {mb:.1f}MB archived, {deleted} old archives deleted"
    if dry_run:
        logger.info("[dry-run] alerts.log サマリー予定: %s", summary)
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ALERTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] [log_rotate] {summary}\n")
        logger.info("alerts.log にサマリー記録完了")
    except Exception as exc:
        logger.error("alerts.log 書き込み失敗: %s", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="ログローテーション")
    parser.add_argument("--dry-run", action="store_true", help="変更せず確認のみ")
    args = parser.parse_args()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    mode = "[dry-run] " if args.dry_run else ""
    logger.info("=== %sログローテーション開始: %s ===", mode, today)

    rotated, archived_bytes = rotate(today, args.dry_run)
    deleted = purge_old(now, args.dry_run)
    write_summary(rotated, archived_bytes, deleted, args.dry_run)

    logger.info("=== %sログローテーション完了 ===", mode)


if __name__ == "__main__":
    main()
