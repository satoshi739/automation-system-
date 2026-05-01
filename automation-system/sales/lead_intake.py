from __future__ import annotations

"""
リード自動起票モジュール
LINEからの問い合わせ情報を sales-system/leads/ に自動保存する
"""

import os
import re
import logging
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# プロジェクトルートからの相対パス
LEADS_DIR = Path(__file__).parent.parent.parent / "sales-system" / "leads"
TEMPLATE_PATH = Path(__file__).parent.parent.parent / "sales-system" / "templates" / "lead-sheet.yaml"


def _next_lead_id(date_str: str) -> str:
    """その日の連番を生成 (例: 2026-04-05-001)"""
    LEADS_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(LEADS_DIR.glob(f"{date_str}-*.yaml"))
    seq = len(existing) + 1
    return f"{date_str}-{seq:03d}"


def detect_brand(message: str) -> str:
    """メッセージ内容からブランドを推定"""
    msg = message.lower()
    if any(w in msg for w in ["ファクタリング", "資金", "売掛", "キャッシュ"]):
        return "cashflowsupport"
    if any(w in msg for w in ["コンサル", "事業", "戦略", "設計"]):
        return "upjapan"
    return "dsc-marketing"


def create_lead_from_line(user_id: str, display_name: str, message: str, channel: str = "line") -> Path:
    """
    LINEユーザーのメッセージからリードシートを作成する

    Args:
        user_id:      LINE ユーザーID
        display_name: LINEの表示名
        message:      最初のメッセージ内容
        channel:      流入チャネル（デフォルト: line）

    Returns:
        作成したYAMLファイルのPath
    """
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    lead_id = _next_lead_id(date_str)

    lead = {
        "lead_id": lead_id,
        "created_at": date_str,
        "brand": detect_brand(message),
        "name": display_name,
        "company": "",
        "email": "",
        "phone": "",
        "channel": channel,
        "line_user_id": user_id,
        "referred_by": "",
        "stage": "L2",  # LINE登録 = 既にコンタクト済み
        "last_contact": date_str,
        "next_action": "ヒアリング・商談日程調整",
        "next_action_date": "",
        "current_situation": message,
        "goals": "",
        "budget_range": "",
        "timeline": "",
        "decision_maker": "",
        "concerns": "",
        "proposed_plan": "",
        "proposed_amount": 0,
        "proposal_sent_at": "",
        "proposal_url": "",
        "outcome": "",
        "contract_date": "",
        "lost_reason": "",
        "notes": f"LINE経由の自動起票。初回メッセージ: {message[:100]}",
    }

    LEADS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = LEADS_DIR / f"{lead_id}.yaml"
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(lead, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"リード起票完了: {out_path}")
    return out_path


def load_lead_by_line_id(user_id: str) -> dict | None:
    """LINE user_id でリードシートを検索して返す"""
    if not LEADS_DIR.exists():
        return None
    for f in sorted(LEADS_DIR.glob("*.yaml"), reverse=True):
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data and data.get("line_user_id") == user_id:
            return data
    return None


def update_lead_stage(lead_id: str, new_stage: str, note: str = "") -> bool:
    """リードのステージを更新する"""
    path = LEADS_DIR / f"{lead_id}.yaml"
    if not path.exists():
        logger.warning(f"リードが見つかりません: {lead_id}")
        return False

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        logger.warning(f"リードファイルが不正: {path}")
        return False

    data["stage"] = new_stage
    data["last_contact"] = datetime.now().strftime("%Y-%m-%d")
    if note:
        data["notes"] = (data.get("notes") or "") + f"\n{datetime.now().strftime('%Y-%m-%d')}: {note}"

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"リードステージ更新: {lead_id} → {new_stage}")
    return True
