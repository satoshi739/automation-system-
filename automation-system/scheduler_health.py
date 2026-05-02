"""
scheduler_health.py — スケジューラー死活監視モジュール

毎朝 morning_operator.run() から呼ばれ、
以下を確認して異常があれば LINE で警告する:

  1. launchd プロセスが起動しているか
  2. scheduler.heartbeat の最終更新が 10 分以内か
  3. 過去 24 時間で Instagram に最低 1 件投稿されているか

単体実行:
  cd automation-system
  python scheduler_health.py
"""

from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent
HEARTBEAT_FILE = _ROOT / "logs" / "scheduler.heartbeat"
LAUNCHD_LABEL  = "jp.upjapan.scheduler"
QUEUE_DIR      = _ROOT / "content_queue" / "instagram"

# 異常判定しきい値
HEARTBEAT_WARN_MINUTES = 10     # heartbeat が X 分以上古ければ警告
NO_POST_WARN_HOURS     = 24     # X 時間投稿ゼロなら警告


# ──────────────────────────────────────────────────────────
# 個別チェック関数
# ──────────────────────────────────────────────────────────

def _check_launchd() -> tuple[bool, str]:
    """launchd プロセスが起動しているか"""
    try:
        result = subprocess.run(
            ["launchctl", "list", LAUNCHD_LABEL],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, f"launchd ラベル '{LAUNCHD_LABEL}' が見つかりません"
        lines = result.stdout.strip().splitlines()
        # 2行目: PID <tab> ExitStatus <tab> Label
        if len(lines) >= 2:
            pid_col = lines[1].split("\t")[0].strip()
            if pid_col == "-":
                return False, f"スケジューラーの PID が '-'（停止中）"
        return True, "launchd プロセス OK"
    except Exception as exc:
        return False, f"launchctl 確認エラー: {exc}"


def _check_heartbeat() -> tuple[bool, str]:
    """heartbeat ファイルが HEARTBEAT_WARN_MINUTES 以内に更新されているか。
    ファイルの OS 更新時刻で判定するため、プロセスのタイムゾーン差に影響されない。"""
    if not HEARTBEAT_FILE.exists():
        return False, "heartbeat ファイルが存在しません"
    try:
        import time as _time_mod
        mtime = HEARTBEAT_FILE.stat().st_mtime
        age_min = (_time_mod.time() - mtime) / 60
        if age_min > HEARTBEAT_WARN_MINUTES:
            return False, f"heartbeat が {age_min:.0f} 分前（{HEARTBEAT_WARN_MINUTES} 分超え）"
        return True, f"heartbeat OK（{age_min:.1f} 分前）"
    except Exception as exc:
        return False, f"heartbeat 確認エラー: {exc}"


def _check_instagram_posts_24h() -> tuple[bool, str]:
    """過去 24 時間で Instagram に最低 1 件投稿されているか"""
    cutoff = datetime.now() - timedelta(hours=NO_POST_WARN_HOURS)
    posted_count = 0

    # ① DB から確認（優先）
    try:
        import database as db
        with db.get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) FROM queue_items
                WHERE channel='instagram'
                  AND posted=1
                  AND posted_at >= ?
                """,
                (cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchone()
        posted_count = row[0] if row else 0
        if posted_count > 0:
            return True, f"過去 24h Instagram 投稿: {posted_count} 件（DB）"
    except Exception as exc:
        logger.debug("DB確認スキップ（YAML にフォールバック）: %s", exc)

    # ② YAML キューから確認（フォールバック）
    if posted_count == 0 and QUEUE_DIR.exists():
        for f in QUEUE_DIR.glob("*.yaml"):
            try:
                import yaml
                data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
                if not data.get("posted"):
                    continue
                # ファイル名先頭 "YYYY-MM-DD_HHmm_..." から日時を推定
                stem = f.stem  # e.g. "2026-05-02_1200_xxx"
                parts = stem.split("_")
                if len(parts) >= 2:
                    dt_str = f"{parts[0]}_{parts[1]}"
                    try:
                        posted_dt = datetime.strptime(dt_str, "%Y-%m-%d_%H%M")
                        if posted_dt >= cutoff:
                            posted_count += 1
                    except ValueError:
                        pass
            except Exception:
                continue

    if posted_count > 0:
        return True, f"過去 24h Instagram 投稿: {posted_count} 件"

    # ③ アクティビティログから確認
    try:
        import database as db
        activities = db.list_activity(limit=200)
        for act in activities:
            if act.get("action") != "post" or act.get("platform") != "instagram":
                continue
            try:
                ts = datetime.strptime(act["created_at"][:19], "%Y-%m-%d %H:%M:%S")
                if ts >= cutoff:
                    posted_count += 1
            except Exception:
                continue
        if posted_count > 0:
            return True, f"過去 24h Instagram 投稿: {posted_count} 件（activity_log）"
    except Exception:
        pass

    return False, f"過去 {NO_POST_WARN_HOURS}h で Instagram 投稿が 0 件"


# ──────────────────────────────────────────────────────────
# メインチェック関数
# ──────────────────────────────────────────────────────────

def check() -> dict:
    """
    全チェックを実行し結果 dict を返す。
    異常があれば LINE に警告を送る。

    Returns:
        {
            "ok": bool,              # すべて正常なら True
            "issues": list[str],     # 異常メッセージのリスト
            "details": list[str],    # 全チェック結果（正常含む）
        }
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    results = []
    issues  = []

    checks = [
        ("launchd プロセス",     _check_launchd),
        ("heartbeat（動作確認）", _check_heartbeat),
        ("Instagram 投稿有無",   _check_instagram_posts_24h),
    ]

    for label, fn in checks:
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"チェック例外: {exc}"
        results.append(f"{'✅' if ok else '❌'} {label}: {msg}")
        if not ok:
            issues.append(f"{label} — {msg}")
        logger.info("[health] %s: %s", label, msg)

    all_ok = len(issues) == 0

    # 異常があれば LINE 通知
    if issues:
        _send_line_alert(issues, now)

    return {"ok": all_ok, "issues": issues, "details": results}


def _send_line_alert(issues: list[str], timestamp: str) -> None:
    """スケジューラー異常を LINE に通知する"""
    try:
        from sns.line_api import LINEMessenger
        lines = [
            "🚨【スケジューラー異常検知】",
            f"時刻: {timestamp}",
            "",
            f"異常件数: {len(issues)} 件",
        ]
        for i, issue in enumerate(issues, 1):
            lines.append(f"  {i}. {issue}")
        lines += [
            "",
            "▶ 対処: cd automation-system && ./deploy.sh",
        ]
        messenger = LINEMessenger(
            token=os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", ""),
            secret=os.environ.get("ALERT_LINE_CHANNEL_SECRET", ""),
        )
        ok = messenger.push_to_owner("\n".join(lines))
        if ok:
            logger.info("[health] LINE アラート送信完了")
        else:
            logger.warning("[health] LINE アラート送信失敗（トークン未設定の可能性）")
    except Exception as exc:
        logger.error("[health] LINE アラート送信エラー: %s", exc)


# ──────────────────────────────────────────────────────────
# 単体実行
# ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = check()
    print("\n=== スケジューラー ヘルスチェック ===")
    for detail in result["details"]:
        print(f"  {detail}")
    print()
    if result["ok"]:
        print("✅ 全チェック正常")
    else:
        print(f"❌ 異常: {len(result['issues'])} 件")
        for issue in result["issues"]:
            print(f"   - {issue}")
