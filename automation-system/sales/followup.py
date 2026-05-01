"""
フォローアップ自動送信モジュール
リード起票後の時間経過に応じて、LINEで自動フォローアップを送る
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from sns.line_api import LINEMessenger
from sales.lead_intake import LEADS_DIR

logger = logging.getLogger(__name__)

SCENARIOS_PATH = Path(__file__).parent.parent / "config" / "line_scenarios.yaml"


def _load_followup_messages() -> dict:
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("followup_messages", {})


def _load_schedule() -> dict:
    schedule_path = Path(__file__).parent.parent / "config" / "schedule.yaml"
    with open(schedule_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("followup", {})


def run_followup_check():
    """
    全リードを確認し、フォローアップが必要なものにLINEを送る
    スケジューラーから定期的に呼び出す
    """
    if not LEADS_DIR.exists():
        return

    messenger = LINEMessenger()
    messages = _load_followup_messages()
    schedule = _load_schedule()

    first_h = schedule.get("first_followup_hours", 24)
    second_h = schedule.get("second_followup_hours", 72)
    final_h = schedule.get("final_followup_hours", 168)

    now = datetime.now()
    sent_count = 0

    for lead_file in LEADS_DIR.glob("*.yaml"):
        with open(lead_file, encoding="utf-8") as f:
            lead = yaml.safe_load(f)

        if not isinstance(lead, dict):
            continue

        # 契約済み・失注・LINE IDなし はスキップ
        if lead.get("outcome") in ("contracted", "lost"):
            continue
        if not lead.get("line_user_id"):
            continue

        try:
            created = datetime.strptime(str(lead.get("created_at", "")), "%Y-%m-%d")
        except (ValueError, TypeError):
            logger.warning(f"created_at パース失敗: {lead_file.name}")
            continue
        elapsed_h = (now - created).total_seconds() / 3600
        followup_sent = lead.get("followup_sent", [])

        # どのフォローアップを送るか判定
        msg_key = None
        if elapsed_h >= final_h and "final" not in followup_sent:
            msg_key = "final"
        elif elapsed_h >= second_h and "second" not in followup_sent:
            msg_key = "second"
        elif elapsed_h >= first_h and "first" not in followup_sent:
            msg_key = "first"

        if not msg_key:
            continue

        message = messages.get(msg_key, "")
        if not message:
            continue

        user_id = lead["line_user_id"]
        name = lead.get("name", "")
        # 名前があれば冒頭に挿入
        if name:
            message = f"{name} さん、\n{message}"

        ok = messenger.push(user_id, message)
        if ok:
            followup_sent.append(msg_key)
            lead["followup_sent"] = followup_sent
            lead["last_contact"] = now.strftime("%Y-%m-%d")
            with open(lead_file, "w", encoding="utf-8") as f:
                yaml.dump(lead, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            sent_count += 1
            logger.info(f"フォローアップ送信: {lead['lead_id']} → {msg_key}")

    logger.info(f"フォローアップチェック完了: {sent_count}件送信")
    return sent_count
