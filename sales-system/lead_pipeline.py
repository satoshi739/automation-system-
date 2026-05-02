"""
営業パイプライン監視スクリプト
- leads/ 配下の全YAMLを走査してステージ別集計・期限超過・スタール検出
- 問題があればLINEでオーナーに通知
"""

import os
import sys
import logging
from pathlib import Path
from datetime import date, timedelta

from typing import Optional

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

LEADS_DIR = Path(__file__).parent / "leads"
STALE_DAYS = 14


def _load_leads() -> list[dict]:
    leads = []
    for path in LEADS_DIR.rglob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                leads.append(data)
        except Exception as e:
            logger.warning(f"YAMLスキップ: {path} — {e}")
    return leads


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def analyze(leads: list[dict]) -> dict:
    today = date.today()
    stale_threshold = today - timedelta(days=STALE_DAYS)

    stage_counts: dict[str, int] = {}
    overdue: list[dict] = []
    stale: list[dict] = []
    contracted_this_month = 0

    for lead in leads:
        stage = lead.get("stage", "unknown")
        stage_counts[stage] = stage_counts.get(stage, 0) + 1

        next_action_date = _parse_date(lead.get("next_action_date"))
        if next_action_date and next_action_date <= today:
            overdue.append(lead)

        last_contact = _parse_date(lead.get("last_contact"))
        if last_contact and last_contact < stale_threshold:
            stale.append(lead)

        if lead.get("outcome") == "contracted":
            created_at = _parse_date(lead.get("created_at"))
            if created_at and created_at.year == today.year and created_at.month == today.month:
                contracted_this_month += 1

    return {
        "stage_counts": stage_counts,
        "overdue": overdue,
        "stale": stale,
        "contracted_this_month": contracted_this_month,
    }


def _build_message(result: dict) -> str:
    today = date.today().isoformat()
    stage_counts = result["stage_counts"]
    overdue = result["overdue"]
    stale = result["stale"]

    stage_summary = " ".join(
        f"L{i}:{stage_counts.get(f'L{i}', 0)}件" for i in range(1, 7)
    )

    lines = [f"【営業パイプライン】{today}", stage_summary]

    lines.append(f"⚠️ 期限超過: {len(overdue)}件")
    for lead in overdue:
        name = lead.get("company") or lead.get("name", "不明")
        stage = lead.get("stage", "?")
        nad = lead.get("next_action_date", "")
        lines.append(f"  - {name} ({stage}) next_action_date: {nad}")

    lines.append(f"スタール: {len(stale)}件")
    lines.append(f"今月成約: {result['contracted_this_month']}件")

    return "\n".join(lines)


def _notify(message: str) -> None:
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    if dry_run:
        logger.info(f"[DRY_RUN] LINE通知スキップ:\n{message}")
        return

    try:
        from sns.line_api import LINEMessenger
        messenger = LINEMessenger()
        owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if not owner_id:
            logger.warning("OWNER_LINE_USER_ID 未設定 — LINE通知スキップ")
            return
        messenger.push(owner_id, message)
    except Exception as e:
        logger.error(f"LINE通知エラー: {e}")


def run() -> None:
    if not LEADS_DIR.exists():
        logger.info("leads/ ディレクトリが存在しない — スキップ")
        return

    leads = _load_leads()
    result = analyze(leads)

    stage_counts = result["stage_counts"]
    overdue = result["overdue"]
    stale = result["stale"]

    print(f"=== 営業パイプライン {date.today().isoformat()} ===")
    for stage, count in sorted(stage_counts.items()):
        print(f"  {stage}: {count}件")
    print(f"期限超過: {len(overdue)}件")
    for lead in overdue:
        name = lead.get("company") or lead.get("name", "不明")
        print(f"  - {name} ({lead.get('stage')}) {lead.get('next_action_date')}")
    print(f"スタール({STALE_DAYS}日超): {len(stale)}件")
    print(f"今月成約: {result['contracted_this_month']}件")

    if overdue or stale:
        message = _build_message(result)
        _notify(message)


if __name__ == "__main__":
    run()
