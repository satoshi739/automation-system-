"""
リード手動入力スクリプト
既存顧客・新規リードをDBに登録する

使い方:
  python3 scripts/add_lead.py
  python3 scripts/add_lead.py --file ../../sales-system/leads/sample.yaml
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

import database as db


STAGES = {
    "1": ("L1", "初回接触・リスト入り"),
    "2": ("L2", "ヒアリング済み"),
    "3": ("L3", "提案中"),
    "4": ("L4", "クロージング"),
    "5": ("L5", "契約済み"),
}

BRANDS = ["dsc-marketing", "cashflowsupport", "upjapan", "bangkok-peach", "other"]
CHANNELS = ["instagram", "line", "form", "referral", "event", "other"]


def _ask(prompt: str, default: str = "") -> str:
    val = input(f"  {prompt}{f' [{default}]' if default else ''}: ").strip()
    return val or default


def interactive_add() -> dict:
    print("\n━━━ リード登録 ━━━")
    print("Enterで空欄スキップ。必須項目は * 付き\n")

    data = {}
    data["name"]    = _ask("* 氏名 / 会社名")
    if not data["name"]:
        print("氏名は必須です。"); return {}

    data["company"] = _ask("  会社名（個人の場合は空欄）")
    data["email"]   = _ask("  メールアドレス")
    data["phone"]   = _ask("  電話番号")

    print(f"\n  ブランド: {', '.join(f'{i+1}={b}' for i,b in enumerate(BRANDS))}")
    bi = _ask("* ブランド番号", "1")
    data["brand"] = BRANDS[int(bi)-1] if bi.isdigit() and 1 <= int(bi) <= len(BRANDS) else BRANDS[0]

    print(f"\n  チャンネル: {', '.join(f'{i+1}={c}' for i,c in enumerate(CHANNELS))}")
    ci = _ask("  流入チャンネル番号", "1")
    data["channel"] = CHANNELS[int(ci)-1] if ci.isdigit() and 1 <= int(ci) <= len(CHANNELS) else "other"

    print(f"\n  ステージ: {', '.join(f'{k}={v[1]}' for k,v in STAGES.items())}")
    si = _ask("  ステージ番号", "1")
    data["stage"] = STAGES.get(si, ("L1",""))[0]

    data["current_situation"] = _ask("\n  現状・課題メモ")
    data["next_action"]       = _ask("  次のアクション")
    data["next_action_date"]  = _ask("  次のアクション期日 (YYYY-MM-DD)")
    data["notes"]             = _ask("  その他メモ")

    data["outcome"]     = ""
    data["created_at"]  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data["last_contact"]= datetime.now().strftime("%Y-%m-%d")

    return data


def add_from_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data.get("name") and not data.get("company"):
        print(f"ERROR: name または company が必須です: {path}")
        return {}
    if not data.get("created_at"):
        data["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def main():
    parser = argparse.ArgumentParser(description="リード登録ツール")
    parser.add_argument("--file", "-f", help="YAMLファイルから一括登録")
    parser.add_argument("--list", "-l", action="store_true", help="登録済みリードを表示")
    args = parser.parse_args()

    if args.list:
        leads = db.list_leads(limit=50)
        if not leads:
            print("リードがまだ登録されていません。")
            return
        print(f"\n{'ID':20} {'名前':20} {'ブランド':18} {'ステージ':8} {'最終':12}")
        print("─" * 80)
        for l in leads:
            print(f"{l.get('lead_id','')[:18]:20} {str(l.get('name',''))[:18]:20} "
                  f"{str(l.get('brand',''))[:16]:18} {l.get('stage',''):8} "
                  f"{str(l.get('last_contact',''))[:10]:12}")
        return

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"ERROR: ファイルが見つかりません: {path}")
            sys.exit(1)
        data = add_from_yaml(path)
    else:
        data = interactive_add()

    if not data:
        sys.exit(1)

    lead_id = db.upsert_lead(data)
    print(f"\n✓ 登録完了: lead_id = {lead_id}")
    print(f"  名前: {data.get('name','')} / ブランド: {data.get('brand','')} / ステージ: {data.get('stage','')}")


if __name__ == "__main__":
    main()
