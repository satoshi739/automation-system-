"""
プロジェクトダッシュボード
- project-system/projects/*/project-sheet.yaml を全スキャン
- フェーズ・支払い状態・マイルストーン期日を集計
- 週次でLINEにサマリーを送信

使い方:
    python3 project_dashboard.py              # スキャンしてLINEに送信
    python3 project_dashboard.py --dry-run    # コンソール出力のみ
"""

import argparse
import os
import sys
from datetime import datetime, date
from pathlib import Path

import yaml
from dotenv import load_dotenv

STALE_DAYS = 30

_ROOT = Path(__file__).parent
_AUTOMATION = _ROOT.parent / "automation-system"
_PROJECTS_DIR = _ROOT / "projects"

load_dotenv(_AUTOMATION / ".env")
sys.path.insert(0, str(_AUTOMATION))

PHASE_LABELS = {
    "P1": "リード",
    "P2": "提案中",
    "P3": "契約交渉",
    "P4": "進行中",
    "P5": "完了/継続",
}


def load_all_projects() -> list[dict]:
    if not _PROJECTS_DIR.exists():
        return []
    projects = []
    for project_dir in sorted(_PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        sheet = project_dir / "project-sheet.yaml"
        if not sheet.exists():
            continue
        try:
            with open(sheet, encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data:
                data["_file"] = str(sheet)
                data["_mtime"] = datetime.fromtimestamp(sheet.stat().st_mtime).date()
                projects.append(data)
        except Exception as e:
            print(f"読み込みエラー {sheet}: {e}")
    return projects


def build_report(projects: list[dict]) -> str:
    if not projects:
        return "📁 プロジェクトダッシュボード: 案件なし"

    today = date.today()
    active = [p for p in projects if p.get("phase") in ("P2", "P3", "P4")]
    stopped = [p for p in projects if (p.get("billing", {}) or {}).get("payment_status") == "closed"]
    overdue_payment = [
        p for p in projects
        if (p.get("billing", {}) or {}).get("payment_status") == "overdue"
    ]

    # マイルストーン期日チェック（7日以内 + 超過）
    milestone_soon = []
    for p in active:
        d_str = p.get("next_milestone_date", "")
        if d_str:
            try:
                d = datetime.strptime(str(d_str), "%Y-%m-%d").date()
                days_left = (d - today).days
                if days_left <= 7:
                    milestone_soon.append((p, days_left))
            except Exception:
                pass

    # 停滞案件: P4以外かつファイル更新が STALE_DAYS 日以上前
    stale = []
    for p in projects:
        if p.get("phase") in ("P4", "P5"):
            continue
        mtime = p.get("_mtime", today)
        if isinstance(mtime, date) and (today - mtime).days >= STALE_DAYS:
            stale.append(p)

    lines = [
        f"📁 プロジェクトダッシュボード {today.strftime('%Y-%m-%d')}",
        f"総案件: {len(projects)}件 / アクティブ: {len(active)}件 / 停止中: {len(stopped)}件",
    ]

    if active:
        lines += ["", "🔵 アクティブ案件:"]
        for p in active:
            pid = p.get("project_id", "?")
            project_name = p.get("project_name", "?")
            client_name = p.get("client_name", "")
            phase = PHASE_LABELS.get(p.get("phase", ""), p.get("phase", ""))
            client_tag = f" ({client_name})" if client_name and client_name not in project_name else ""
            lines.append(f"  [{phase}] {pid}{client_tag} — {project_name}")

    if milestone_soon:
        lines += ["", "⏰ マイルストーン（7日以内）:"]
        for p, days in sorted(milestone_soon, key=lambda x: x[1]):
            project_name = p.get("project_name", p.get("project_id", "?"))
            client_name = p.get("client_name", "")
            ms = p.get("next_milestone", "")
            d = p.get("next_milestone_date", "")
            if days < 0:
                icon = "💀"
                label = f"期日超過{abs(days)}日"
            elif days == 0:
                icon = "🔴"
                label = "今日"
            elif days == 1:
                icon = "🔴"
                label = "明日"
            elif days <= 3:
                icon = "🟡"
                label = f"{days}日後"
            else:
                icon = "⏰"
                label = f"{days}日後"
            client_tag = f" ({client_name})" if client_name and client_name not in project_name else ""
            lines.append(f"  {icon} {label}: {project_name}{client_tag} — {ms} ({d})")

    if overdue_payment:
        lines += ["", "🚨 支払い問題:"]
        for p in overdue_payment:
            name = p.get("project_name", p.get("project_id", "?"))
            lines.append(f"  • {name}")

    if stale:
        lines += ["", f"⚠️ 停滞案件（{STALE_DAYS}日以上更新なし）:"]
        for p in stale:
            pid = p.get("project_id", "?")
            name = p.get("client_name", "?")
            mtime = p.get("_mtime", today)
            days_stale = (today - mtime).days if isinstance(mtime, date) else "?"
            lines.append(f"  • [{pid}] {name} ({days_stale}日)")

    issues_list = [
        (p.get("project_id", "?"), p.get("issues", ""))
        for p in active if p.get("issues")
    ]
    if issues_list:
        lines += ["", "🚧 懸念事項:"]
        for pid, issue in issues_list:
            lines.append(f"  [{pid}] {issue[:60]}")

    if not (milestone_soon or overdue_payment or stale):
        lines += ["", "✅ 問題なし — 次回は来週月曜"]

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

    projects = load_all_projects()
    report = build_report(projects)
    print(report)

    if not args.dry_run:
        send_line_report(report)
