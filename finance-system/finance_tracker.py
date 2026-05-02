"""
月次財務トラッカー
- finance-system/logs/YYYY-MM.yaml を管理
- 当月ログが未作成なら template から自動生成
- --report フラグで現在の財務状態をLINEに送信
- 未回収請求書があればアラート

使い方:
    python3 finance_tracker.py              # 当月ログ初期化
    python3 finance_tracker.py --report     # 月次レポートをLINEに送信
    python3 finance_tracker.py --update     # 対話的に数値を更新
"""

from __future__ import annotations

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
_TEMPLATE = _ROOT / "templates" / "monthly-finance-log.yaml"
_LOGS_DIR = _ROOT / "logs"

load_dotenv(_AUTOMATION / ".env")

sys.path.insert(0, str(_AUTOMATION))


def _current_month() -> str:
    return datetime.now().strftime("%Y-%m")


def _log_path(month: str) -> Path:
    return _LOGS_DIR / f"{month}.yaml"


def _load_template() -> dict:
    with open(_TEMPLATE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_log(month: str) -> Optional[dict]:
    p = _log_path(month)
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _save_log(month: str, data: dict) -> None:
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    p = _log_path(month)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f"保存: {p}")


def ensure_current_month_log() -> dict:
    """当月ログが存在しない場合はテンプレートから生成して返す"""
    month = _current_month()
    data = _load_log(month)
    if data is None:
        data = _load_template()
        data["month"] = month
        _save_log(month, data)
        print(f"当月ログを新規作成しました: {month}")
    return data


def _load_all_logs() -> list[dict]:
    """logs/ 配下の全ログを日付昇順で返す"""
    logs = []
    for p in sorted(_LOGS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
            if data:
                logs.append(data)
        except Exception:
            pass
    return logs


def build_report(month: Optional[str] = None) -> str:
    month = month or _current_month()
    data = _load_log(month)
    if data is None:
        return f"⚠️ {month} の財務ログがありません"

    mrr_end = data.get("mrr_end", 0)
    total_excl = data.get("total_revenue_excl_tax", 0)
    gross = data.get("gross_profit", 0)
    overdue_amt = data.get("overdue_amount", 0)
    invoices_overdue = data.get("invoices_overdue", 0)
    churned_mrr = data.get("churned_mrr", 0)
    new_mrr = data.get("new_mrr", 0)

    # 先月比MRR差分
    all_logs = _load_all_logs()
    prev_data = next((l for l in reversed(all_logs) if l.get("month", "") < month), None)
    mrr_diff_str = ""
    if prev_data:
        prev_mrr = prev_data.get("mrr_end", 0)
        diff = mrr_end - prev_mrr
        sign = "+" if diff >= 0 else ""
        mrr_diff_str = f" (先月比 {sign}¥{diff:,})"

    lines = [
        f"📊 財務レポート {month}",
        f"",
        f"MRR（月末）: ¥{mrr_end:,}{mrr_diff_str}",
        f"  新規MRR: +¥{new_mrr:,} / 解約MRR: -¥{churned_mrr:,}",
        f"月合計売上（税抜）: ¥{total_excl:,}",
        f"粗利: ¥{gross:,}",
    ]

    if invoices_overdue > 0:
        lines += ["", f"未回収請求: {invoices_overdue}件 / ¥{overdue_amt:,}"]
        today = date.today()
        overdue_invoices = data.get("overdue_invoices", [])
        if overdue_invoices:
            for inv in overdue_invoices:
                client = inv.get("client", "不明")
                amount = inv.get("amount", 0)
                inv_date = inv.get("invoice_date", "")
                elapsed = ""
                icon = "⚠️"
                if inv_date:
                    try:
                        d = date.fromisoformat(str(inv_date))
                        days = (today - d).days
                        elapsed = f" (請求から{days}日経過)"
                        icon = "🔴" if days > 30 else ("🟡" if days > 14 else "⚠️")
                    except ValueError:
                        pass
                lines.append(f"  {icon} 未回収: {client} ¥{amount:,}{elapsed}")
        else:
            lines.append("  ※ 詳細は overdue_invoices フィールドに記入してください")

    by_brand = data.get("by_brand", {})
    if by_brand:
        lines += ["", "ブランド別:"]
        for brand, vals in by_brand.items():
            m = vals.get("mrr", 0)
            o = vals.get("one_time", 0)
            if m or o:
                lines.append(f"  {brand}: MRR ¥{m:,} / 単発 ¥{o:,}")

    new_clients = data.get("new_clients", [])
    churned_clients = data.get("churned_clients", [])
    if new_clients:
        lines.append(f"\n🆕 新規: {', '.join(str(c) for c in new_clients)}")
    if churned_clients:
        lines.append(f"📤 解約: {', '.join(str(c) for c in churned_clients)}")

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


def alert_overdue() -> None:
    month = _current_month()
    data = _load_log(month)
    if not data or data.get("invoices_overdue", 0) == 0:
        return

    today = date.today()
    lines = [f"⚠️ 未回収請求アラート ({month})"]
    overdue_invoices = data.get("overdue_invoices", [])
    if overdue_invoices:
        for inv in overdue_invoices:
            client = inv.get("client", "不明")
            amount = inv.get("amount", 0)
            inv_date = inv.get("invoice_date", "")
            elapsed = ""
            icon = "⚠️"
            if inv_date:
                try:
                    d = date.fromisoformat(str(inv_date))
                    days = (today - d).days
                    elapsed = f" (請求から{days}日経過)"
                    icon = "🔴" if days > 30 else ("🟡" if days > 14 else "⚠️")
                except ValueError:
                    pass
            lines.append(f"  {icon} {client} ¥{amount:,}{elapsed}")
    else:
        lines.append(f"  {data['invoices_overdue']}件 / ¥{data.get('overdue_amount', 0):,}")

    send_line_report("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="月次レポートをLINEに送信")
    parser.add_argument("--month", default=None, help="対象月 (YYYY-MM)")
    parser.add_argument("--alert-overdue", action="store_true", help="未回収請求があればLINEアラート")
    args = parser.parse_args()

    ensure_current_month_log()

    if args.report:
        report = build_report(args.month)
        print(report)
        send_line_report(report)
    elif args.alert_overdue:
        alert_overdue()
    else:
        print(f"当月ログ確認完了: {_current_month()}")
        print(build_report())
