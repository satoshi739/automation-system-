"""
顧客ヘルスチェッカー
- customer-success-system/accounts/*/health-YYYY-MM.yaml を全スキャン
- at_risk: true または risk_signals がある顧客を検出
- 週次でLINEにサマリーを送信

使い方:
    python3 health_checker.py               # スキャンしてLINEに送信
    python3 health_checker.py --dry-run     # コンソール出力のみ
"""

import argparse
import os
import re
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

_ROOT = Path(__file__).parent
_AUTOMATION = _ROOT.parent / "automation-system"
_ACCOUNTS_DIR = _ROOT / "accounts"

load_dotenv(_AUTOMATION / ".env")
sys.path.insert(0, str(_AUTOMATION))


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _parse_date(value) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _last_contact_days(client: dict) -> Optional[int]:
    """actions_this_month の最終エントリから経過日数を返す。形式: 'YYYY-MM-DD: ...'"""
    actions = client.get("actions_this_month", [])
    if not actions:
        return None
    m = re.match(r"(\d{4}-\d{2}-\d{2})", str(actions[-1]))
    if not m:
        return None
    d = _parse_date(m.group(1))
    return (date.today() - d).days if d else None


def scan_all_clients() -> list[dict]:
    """全クライアントの最新ヘルスシートを読み込む"""
    if not _ACCOUNTS_DIR.exists():
        return []

    clients = []
    for project_dir in sorted(_ACCOUNTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        # 最新月のシートを取得（ファイル名降順）
        sheets = sorted(project_dir.glob("health-*.yaml"), reverse=True)
        if not sheets:
            continue
        try:
            with open(sheets[0], encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data:
                data["_file"] = str(sheets[0])
                clients.append(data)
        except Exception as e:
            print(f"読み込みエラー {sheets[0]}: {e}")

    return clients


def build_report(clients: list[dict]) -> str:
    if not clients:
        return "📋 CSヘルスチェック: 登録クライアントなし"

    at_risk = [c for c in clients if c.get("at_risk") or c.get("risk_signals")]
    ok = [c for c in clients if not c.get("at_risk") and not c.get("risk_signals")]
    overdue = [c for c in clients if c.get("payment_status") == "overdue"]

    lines = [
        f"📋 CSヘルスチェック {_current_month()}",
        f"対象: {len(clients)}社 / 要注意: {len(at_risk)}社 / 滞納: {len(overdue)}社",
    ]

    if at_risk:
        lines += ["", "⚠️ 要注意クライアント:"]
        for c in at_risk:
            name = c.get("client_name", c.get("project_id", "不明"))
            signals = c.get("risk_signals", [])
            satisfaction = c.get("satisfaction", "")
            elapsed = _last_contact_days(c)
            detail = f"  • {name}"
            if signals:
                detail += f" — {', '.join(str(s) for s in signals)}"
            if satisfaction == "low":
                detail += f" [満足度: {satisfaction}]"
            if elapsed is not None:
                detail += f" (最終接触: {elapsed}日前)"
            lines.append(detail)

    if overdue:
        lines += ["", "💴 支払い滞納:"]
        for c in overdue:
            name = c.get("client_name", c.get("project_id", "不明"))
            lines.append(f"  • {name}")

    if ok:
        lines += ["", f"✅ 問題なし: {len(ok)}社"]

    today = date.today()
    touch_overdue = []
    touch_soon = []    # 3日以内
    touch_later = []   # 4日以降
    for c in clients:
        d = _parse_date(c.get("next_touch_date"))
        if d is None:
            continue
        name = c.get("client_name", c.get("project_id", "?"))
        days_left = (d - today).days
        if d <= today:
            touch_overdue.append((name, str(d)))
        elif days_left <= 3:
            touch_soon.append((name, str(d), days_left))
        else:
            touch_later.append((name, str(d)))

    if touch_overdue:
        lines += ["", "🔴 コンタクト期日超過:"]
        for name, d in sorted(touch_overdue, key=lambda x: x[1]):
            lines.append(f"  {d} — {name}")

    if touch_soon:
        lines += ["", "🔔 まもなくコンタクト（3日以内）:"]
        for name, d, days_left in sorted(touch_soon, key=lambda x: x[2]):
            label = "明日" if days_left == 1 else f"{days_left}日後"
            lines.append(f"  {d}（{label}） — {name}")

    if touch_later:
        lines += ["", "📅 次回コンタクト予定:"]
        for name, d in sorted(touch_later, key=lambda x: x[1]):
            lines.append(f"  {d} — {name}")

    return "\n".join(lines)


def send_line_report(message: str) -> None:
    try:
        from sns.line_api import LINEMessenger
        messenger = LINEMessenger()
        messenger.push_to_owner(message)
        print("LINE送信完了")
    except Exception as e:
        print(f"LINE送信エラー（コンソール出力に切り替え）: {e}")
        print(message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="コンソール出力のみ")
    args = parser.parse_args()

    clients = scan_all_clients()
    report = build_report(clients)
    print(report)

    if not args.dry_run:
        send_line_report(report)
