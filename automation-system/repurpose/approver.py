"""
レビューYAML → 投稿キュー振り分け

使い方:
    python -m repurpose.approver --all             # review/ の approved 全件処理
    python -m repurpose.approver --file xxx.yaml   # 1ファイル指定
    python -m repurpose.approver --all --dry-run   # ドライラン（ファイル移動なし）

フロー:
    review/ の status: approved なファイルを読み込む
    → バリデーション (validators.py)
    → OK : 各コンテンツを content_queue/posting/{platform}/ へ出力
           元ファイルを approved/ へ退避
    → NG : status: rejected + reasons を書き込んで rejected/ へ退避
    ログ: logs/approver/YYYYMMDD.log に追記
    冪等: approved/ または rejected/ に同名があればスキップ
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import yaml

_ROOT = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system"))
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from repurpose.validators import ContentValidator

# ─── ディレクトリ定義 ────────────────────────────────────────
_QUEUE = _ROOT / "content_queue"
REVIEW_DIR        = _QUEUE / "review"
APPROVED_DIR      = _QUEUE / "approved"
REJECTED_DIR      = _QUEUE / "rejected"
HUMAN_REVIEW_DIR  = _QUEUE / "human_review"  # critical_trigger 検出時
POSTING_BASE      = _QUEUE / "posting"
LOG_DIR           = _ROOT / "logs" / "approver"

_PLATFORM_MAP = {
    "x_thread":            "twitter",
    "instagram_carousel":  "instagram",
    "instagram_story":     "instagram",
    "instagram_post":      "instagram",
    "facebook_post":       "facebook",
    "instagram_reel":      "instagram",
}

for _d in [REVIEW_DIR, APPROVED_DIR, REJECTED_DIR, HUMAN_REVIEW_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ─── ロガー設定 ───────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_log = logging.getLogger(__name__)


def _file_logger(date_str: str) -> logging.Logger:
    """日付ごとのファイルロガーを返す"""
    log_path = LOG_DIR / f"{date_str}.log"
    logger = logging.getLogger(f"approver.file.{date_str}")
    if not logger.handlers:
        h = logging.FileHandler(log_path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(h)
        logger.setLevel(logging.INFO)
    return logger


def _already_processed(filename: str) -> bool:
    """approved/ / rejected/ / human_review/ に同名ファイルがあれば True"""
    return (
        (APPROVED_DIR / filename).exists()
        or (REJECTED_DIR / filename).exists()
        or (HUMAN_REVIEW_DIR / filename).exists()
    )


def _write_posting_yaml(item: dict, brand: str, source_file: str, dry_run: bool) -> Path:
    """
    承認済みコンテンツ1件を posting/{platform}/ へ書き出す。
    返り値: 出力先パス（dry_run時は予定パスを返すが書き込まない）
    """
    platform = _PLATFORM_MAP.get(item.get("type", ""), "misc")
    out_dir = POSTING_BASE / platform
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    content_id = item.get("id", item.get("type", "content"))
    out_path = out_dir / f"{timestamp}_{brand}_{content_id}.yaml"

    doc = {
        "status": "queued",
        "platform": platform,
        "brand": brand,
        "source_review": source_file,
        "queued_at": datetime.now().isoformat(timespec="seconds"),
        **{k: v for k, v in item.items() if k not in ("status",)},
    }

    if not dry_run:
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    return out_path


def process_file(path: Path, dry_run: bool = False) -> dict:
    """
    レビューYAML1件を処理する。

    Returns:
        {
            "file": str,
            "action": "approved" | "rejected" | "skipped",
            "posted": [str],   # 出力した posting ファイル名リスト
            "errors": [dict],  # バリデーションエラー
        }
    """
    filename = path.name
    date_str = datetime.now().strftime("%Y%m%d")
    flog = _file_logger(date_str)

    # ─── 冪等チェック ───────────────────────────────────────
    if _already_processed(filename):
        msg = f"[SKIPPED] {filename} — approved/ または rejected/ に既存"
        _log.info(msg)
        flog.info(msg)
        return {"file": filename, "action": "skipped", "posted": [], "errors": []}

    # ─── YAML 読み込み ──────────────────────────────────────
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    top_status = data.get("status", "")
    if top_status != "approved":
        msg = f"[SKIPPED] {filename} — status が '{top_status}'（'approved' のみ処理）"
        _log.info(msg)
        flog.info(msg)
        return {"file": filename, "action": "skipped", "posted": [], "errors": []}

    brand = data.get("brand", "unknown")

    # ─── バリデーション ─────────────────────────────────────
    validator = ContentValidator()
    vresult = validator.validate(data)

    # ─── Critical（即時停止・人間確認必須） ─────────────────
    if vresult.human_review_required:
        data["status"] = "human_review_required"
        data["critical_reasons"] = vresult.critical_errors
        data["human_review_note"] = (
            "⚠️ 自動停止: critical_trigger を検出しました。"
            " 内容を確認し、問題なければ status を 'approved' に変更して再実行してください。"
        )

        if not dry_run:
            dest = HUMAN_REVIEW_DIR / filename
            with open(dest, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            path.unlink()

        reasons = "; ".join(e["message"] for e in vresult.critical_errors[:3])
        msg = f"[HUMAN_REVIEW] {filename} — 即時停止 {len(vresult.critical_errors)} 件: {reasons}"
        _log.warning(msg)
        flog.info(msg)

        return {
            "file": filename,
            "action": "human_review",
            "posted": [],
            "errors": [],
            "critical_errors": vresult.critical_errors,
        }

    # ─── 通常の拒否 ────────────────────────────────────────
    if not vresult.ok:
        data["status"] = "rejected"
        data["rejection_reasons"] = vresult.errors

        if not dry_run:
            dest = REJECTED_DIR / filename
            with open(dest, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            path.unlink()

        msg = (
            f"[REJECTED] {filename} — "
            f"{len(vresult.errors)} エラー: "
            + "; ".join(e["message"] for e in vresult.errors[:3])
        )
        _log.warning(msg)
        flog.info(msg)

        return {"file": filename, "action": "rejected", "posted": [], "errors": vresult.errors}

    # ─── 承認処理: approved コンテンツを posting/ へ ────────
    posted = []
    for item in data.get("contents", []):
        if item.get("status") != "approved":
            continue
        out_path = _write_posting_yaml(item, brand, filename, dry_run)
        posted.append(str(out_path))

    # 元ファイルを approved/ へ退避
    if not dry_run:
        APPROVED_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(APPROVED_DIR / filename))

    platforms = list({_PLATFORM_MAP.get(p, "misc") for p in
                      [i.get("type", "") for i in data.get("contents", []) if i.get("status") == "approved"]})
    dry_tag = " [DRY-RUN]" if dry_run else ""
    msg = (
        f"[APPROVED{dry_tag}] {filename} — "
        f"{len(posted)} 件を posting/ へ出力"
        + (f" ({', '.join(platforms)})" if platforms else "（承認済みコンテンツなし）")
    )
    _log.info(msg)
    flog.info(msg)

    return {"file": filename, "action": "approved", "posted": posted, "errors": []}


def run_all(dry_run: bool = False) -> list:
    """review/ の status: approved なファイルを全件処理"""
    targets = sorted(REVIEW_DIR.glob("*.yaml"))
    if not targets:
        _log.info("review/ に処理対象ファイルがありません")
        return []

    results = []
    for p in targets:
        results.append(process_file(p, dry_run=dry_run))
    return results


def _print_summary(results: list, dry_run: bool):
    approved      = [r for r in results if r["action"] == "approved"]
    rejected      = [r for r in results if r["action"] == "rejected"]
    human_review  = [r for r in results if r["action"] == "human_review"]
    skipped       = [r for r in results if r["action"] == "skipped"]
    dry_tag       = " [DRY-RUN]" if dry_run else ""

    print(f"\n{'='*55}")
    print(f"Approver 実行結果{dry_tag}")
    print(
        f"  承認: {len(approved)} 件  "
        f"拒否: {len(rejected)} 件  "
        f"⚠️ 人間確認必須: {len(human_review)} 件  "
        f"スキップ: {len(skipped)} 件"
    )
    for r in human_review:
        print(f"\n  ⚠️  {r['file']} → human_review/ に移動")
        for e in r.get("critical_errors", []):
            print(f"       [{e['rule']}] {e['message']}")
    for r in rejected:
        print(f"\n  ✗ {r['file']}")
        for e in r["errors"]:
            print(f"      [{e['rule']}] {e['message']}")
    print("="*55)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="レビューYAML → 投稿キュー振り分け")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", metavar="FILENAME", help="review/ 内の特定ファイル名")
    group.add_argument("--all", action="store_true", help="review/ の全 approved ファイルを処理")
    parser.add_argument("--dry-run", action="store_true", help="ファイル移動・書き込みをしない")
    args = parser.parse_args()

    if args.all:
        results = run_all(dry_run=args.dry_run)
    else:
        p = REVIEW_DIR / args.file
        if not p.exists():
            print(f"ファイルが見つかりません: {p}")
            sys.exit(1)
        results = [process_file(p, dry_run=args.dry_run)]

    _print_summary(results, dry_run=args.dry_run)
