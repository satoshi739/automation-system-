"""
L5（契約）リードからプロジェクトシートとCSヘルスシートを自動生成する。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))
from utils import atomic_yaml_write
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "automation-system" / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

BASE = Path(__file__).parent.parent
LEADS_DIR = BASE / "sales-system" / "leads"
PROJECTS_DIR = BASE / "project-system" / "projects"
ACCOUNTS_DIR = BASE / "customer-success-system" / "accounts"

BRAND_SHORT: dict[str, str] = {
    "dsc-marketing": "DSC",
    "cashflowsupport": "CSF",
    "upjapan": "UPJ",
    "satoshi": "SAT",
}


def _next_project_seq() -> int:
    """projects/ 配下のディレクトリ数+1 で採番する。README.md は除外する。"""
    existing = [p for p in PROJECTS_DIR.iterdir() if p.is_dir()]
    return len(existing) + 1


def _build_project_id(brand: str, year: int, seq: int) -> str:
    short = BRAND_SHORT.get(brand, brand[:3].upper())
    return f"{short}-{year}-{seq:03d}"


def _load_lead(lead_path: Path) -> dict:
    if not lead_path.exists():
        print(f"ERROR: リードファイルが見つかりません: {lead_path}", file=sys.stderr)
        raise SystemExit(1)
    with lead_path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_project_sheet(lead: dict, project_id: str) -> dict:
    today = date.today().isoformat()
    client_name = lead.get("company") or lead.get("name") or ""
    brand = lead.get("brand", "")

    project_name = lead.get("project_name") or f"{client_name} {brand}支援"

    return {
        "project_id": project_id,
        "brand": brand,
        "type": "",
        "client_name": client_name,
        "client_contact": {
            "name": lead.get("name", ""),
            "email": lead.get("email", ""),
            "phone": lead.get("phone", ""),
            "slack_or_line": lead.get("line_user_id", ""),
        },
        "project_name": project_name,
        "description": "",
        "start_date": today,
        "end_date": "",
        "monthly_renewal": True,
        "monthly_fee_jpy": lead.get("proposed_amount", 0),
        "total_fee_jpy": 0,
        "billing": {
            "billing_cycle": "",
            "last_invoice_date": "",
            "last_payment_date": "",
            "payment_status": "current",
        },
        "phase": "P4",
        "last_update": today,
        "next_milestone": "",
        "next_milestone_date": "",
        "project_manager": "",
        "deliverables": [""],
        "notes": lead.get("notes", ""),
        "issues": "",
    }


def _build_health_sheet(lead: dict, project_id: str) -> dict:
    review_month = date.today().strftime("%Y-%m")
    client_name = lead.get("company") or lead.get("name") or ""
    brand = lead.get("brand", "")

    return {
        "project_id": project_id,
        "brand": brand,
        "client_name": client_name,
        "review_month": review_month,
        "payment_status": "current",
        "meetings_held": 0,
        "sla_breaches": 0,
        "satisfaction": "unknown",
        "relationship_owner_client": lead.get("name", ""),
        "notes_on_relationship": "",
        "at_risk": False,
        "risk_signals": [],
        "actions_this_month": [],
        "next_touch_date": "",
        "notes": "",
    }


def _send_line_notification(project_id: str, client_name: str, brand: str) -> None:
    # ALERT チャンネル（オーナー個人宛）で通知する
    token = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
    owner_user_id = os.environ.get("OWNER_LINE_USER_ID", "")

    if not token or not owner_user_id:
        logger.warning("LINE通知をスキップ: ALERT_LINE_CHANNEL_ACCESS_TOKEN または OWNER_LINE_USER_ID 未設定")
        return

    import requests

    message = (
        f"[intake] プロジェクト起票完了\n"
        f"ID: {project_id}\n"
        f"クライアント: {client_name}\n"
        f"ブランド: {brand}"
    )
    try:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"to": owner_user_id, "messages": [{"type": "text", "text": message}]},
            timeout=30,
        )
        if resp.status_code != 200:
            logger.warning(f"LINE通知エラー: {resp.text}")
        else:
            logger.info("LINE通知送信済み")
    except Exception as e:
        logger.warning(f"LINE通知失敗（プロジェクト生成は成功）: {e}")


def run_intake(lead: dict) -> None:
    brand = lead.get("brand", "unknown")
    today_year = date.today().year
    seq = _next_project_seq()
    project_id = _build_project_id(brand, today_year, seq)

    project_dir = PROJECTS_DIR / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    project_sheet = _build_project_sheet(lead, project_id)
    project_sheet_path = project_dir / "project-sheet.yaml"
    atomic_yaml_write(project_sheet_path, project_sheet)

    review_month = date.today().strftime("%Y-%m")
    account_dir = ACCOUNTS_DIR / project_id
    account_dir.mkdir(parents=True, exist_ok=True)

    health_sheet = _build_health_sheet(lead, project_id)
    health_sheet_path = account_dir / f"health-{review_month}.yaml"
    atomic_yaml_write(health_sheet_path, health_sheet)

    client_name = project_sheet["client_name"]

    print(f"プロジェクト起票完了")
    print(f"  project_id : {project_id}")
    print(f"  クライアント: {client_name}")
    print(f"  ブランド   : {brand}")
    print(f"  プロジェクトシート: {project_sheet_path}")
    print(f"  CSヘルスシート  : {health_sheet_path}")

    _send_line_notification(project_id, client_name, brand)


def _interactive_lead() -> dict:
    print("=== インタラクティブモード: リード情報を入力してください ===")
    today = date.today().isoformat()
    brand_choices = list(BRAND_SHORT.keys())
    print(f"ブランド選択肢: {brand_choices}")
    brand = input("brand: ").strip()
    name = input("担当者名: ").strip()
    company = input("会社名（個人の場合は屋号）: ").strip()
    email = input("email: ").strip()
    phone = input("phone: ").strip()
    notes = input("notes: ").strip()
    proposed_amount_str = input("proposed_amount (0): ").strip()
    try:
        proposed_amount = int(proposed_amount_str) if proposed_amount_str else 0
    except ValueError:
        proposed_amount = 0

    return {
        "brand": brand,
        "name": name,
        "company": company,
        "email": email,
        "phone": phone,
        "notes": notes,
        "proposed_amount": proposed_amount,
        "stage": "L5",
        "created_at": today,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="L5リードからプロジェクト・CSシートを生成する")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--lead-id", help="例: 2026-05-02-001")
    group.add_argument("--lead-file", help="リードYAMLの絶対パス")
    group.add_argument("--interactive", action="store_true", help="対話入力モード")
    args = parser.parse_args()

    if args.lead_id:
        lead_path = LEADS_DIR / f"{args.lead_id}.yaml"
        lead = _load_lead(lead_path)
    elif args.lead_file:
        lead_path = Path(args.lead_file)
        lead = _load_lead(lead_path)
    else:
        lead = _interactive_lead()

    run_intake(lead)


if __name__ == "__main__":
    main()
