"""
Real Service — DBから実データを取得してダッシュボードに渡す。
mock_service.py の各関数と同じシグネチャを持つ。
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

import org_database as db

log = logging.getLogger(__name__)

_OS_CFG_PATH = Path(__file__).parent.parent / "config" / "os_config.yaml"

# ── os_config.yaml からエージェントメタをキャッシュ ────────────────

def _load_agent_meta() -> dict[str, dict]:
    """agent_id → {name, icon, description} マップを返す"""
    meta: dict[str, dict] = {
        "ai-ceo": {"name": "AI CEO", "icon": "🏢", "description": "全ブランド統括"},
    }
    try:
        cfg = yaml.safe_load(_OS_CFG_PATH.read_text(encoding="utf-8"))
        for a in cfg.get("agents", []):
            aid  = a.get("id", "")
            name = a.get("name", aid)
            role = a.get("role", "")
            meta[aid] = {
                "name":        name,
                "icon":        _role_icon(role),
                "description": a.get("description", "")[:60],
            }
    except Exception as e:
        log.warning(f"os_config.yaml 読み込み失敗: {e}")
    return meta


def _role_icon(role: str) -> str:
    role_l = role.lower()
    if "content" in role_l or "コンテンツ" in role_l:
        return "📸"
    if "blog" in role_l or "記事" in role_l:
        return "✍️"
    if "sales" in role_l or "営業" in role_l:
        return "💰"
    if "analytics" in role_l or "分析" in role_l:
        return "📊"
    if "ops" in role_l or "運用" in role_l:
        return "⚙️"
    return "🤖"


# YAML で定義されたエージェントIDのみ扱う（seed_org の旧UUIDエージェントを除外）
YAML_AGENT_IDS = {
    "ai-ceo",
    "agent-content-upj", "agent-content-dsc", "agent-content-cfj", "agent-content-bpg",
    "agent-blog", "agent-sales", "agent-analytics", "agent-ops",
}

# ブランドスラッグ → 表示名・カラー
BRAND_META = {
    "upj":          {"name": "UPJ",  "color": "#5b8af5", "link": "/brands/upjapan"},
    "upjapan":      {"name": "UPJ",  "color": "#5b8af5", "link": "/brands/upjapan"},
    "dsc":          {"name": "DSC",  "color": "#34d399", "link": "/brands/dsc-marketing"},
    "dsc-marketing":{"name": "DSC",  "color": "#34d399", "link": "/brands/dsc-marketing"},
    "cfj":          {"name": "CFJ",  "color": "#fbbf24", "link": "/brands/cashflowsupport"},
    "cashflowsupport":{"name":"CFJ", "color": "#fbbf24", "link": "/brands/cashflowsupport"},
    "bangkok-peach":{"name": "BPG",  "color": "#f472b6", "link": "/brands/bangkok-peach"},
    "satoshi-blog": {"name": "Blog", "color": "#a78bfa", "link": "/brands/satoshi-blog"},
}


def _status_badge(counts: dict) -> str:
    if counts.get("failed", 0) > 2:
        return "alert"
    if counts.get("failed", 0) > 0:
        return "warn"
    if counts.get("running", 0) > 0:
        return "ok"
    if counts.get("queued", 0) > 0:
        return "ok"
    return "idle"


def get_task_queue() -> list:
    """キュー中・実行中タスクを返す（最大15件）"""
    tasks = []
    for status in ("running", "queued", "waiting_approval", "escalated"):
        tasks += db.list_tasks(status=status, limit=5)

    result = []
    for t in tasks[:15]:
        priority_val = t.get("priority", 5)
        if priority_val <= 2:
            pri_label = "high"
        elif priority_val <= 5:
            pri_label = "mid"
        else:
            pri_label = "low"

        result.append({
            "id":       t["id"][:8],
            "priority": pri_label,
            "task":     t.get("title", ""),
            "agent":    t.get("assigned_to_agent_id", ""),
            "status":   t.get("status", ""),
            "eta":      t.get("scheduled_at", "") or "—",
        })
    return result


def get_bottlenecks() -> list:
    """失敗・ブロック中タスクからボトルネックを抽出"""
    failed  = db.list_tasks(status="failed",  limit=5)
    blocked = db.list_tasks(status="blocked", limit=5)

    result = []
    for t in failed:
        err = t.get("error_message", "") or ""
        result.append({
            "area":   t.get("assigned_to_agent_id", "不明"),
            "issue":  f"[失敗] {t.get('title', '')} — {err[:60]}",
            "impact": "high",
        })
    for t in blocked:
        result.append({
            "area":   t.get("assigned_to_agent_id", "不明"),
            "issue":  f"[ブロック] {t.get('title', '')}",
            "impact": "mid",
        })

    if not result:
        result.append({
            "area":   "システム",
            "issue":  "現在ボトルネックはありません",
            "impact": "low",
        })
    return result


def get_escalations() -> list:
    """未解決エスカレーション一覧"""
    escs = db.list_escalations(status="open")
    result = []
    for e in escs:
        result.append({
            "to":       "社長",
            "urgency":  "high",
            "item":     e.get("reason", "")[:80],
            "deadline": "確認必要",
        })
    if not result:
        result.append({
            "to":       "—",
            "urgency":  "low",
            "item":     "現在エスカレーションはありません",
            "deadline": "—",
        })
    return result


def get_agent_status() -> list:
    """YAMLで定義されたエージェントのステータス一覧"""
    agent_meta = _load_agent_meta()
    counts     = db.get_task_counts_all_agents()
    result     = []

    # YAML定義順で表示
    ordered_ids = [
        "agent-content-upj", "agent-content-dsc", "agent-content-cfj", "agent-content-bpg",
        "agent-blog", "agent-sales", "agent-analytics", "agent-ops",
    ]

    for aid in ordered_ids:
        meta = agent_meta.get(aid, {"name": aid, "icon": "🤖", "description": ""})
        c    = counts.get(aid, {})
        runs = db.list_runs_for_agent(aid, limit=1)

        last_run_str = "未実行"
        if runs:
            started = runs[0].get("started_at", "")
            if started:
                try:
                    dt   = datetime.fromisoformat(started)
                    diff = datetime.now() - dt
                    secs = int(diff.total_seconds())
                    if secs < 3600:
                        last_run_str = f"{secs // 60}分前"
                    elif secs < 86400:
                        last_run_str = f"{secs // 3600}時間前"
                    else:
                        last_run_str = f"{secs // 86400}日前"
                except Exception:
                    last_run_str = started[:16]

        detail_parts = []
        if c.get("running"):
            detail_parts.append(f"実行中{c['running']}件")
        if c.get("queued"):
            detail_parts.append(f"待機{c['queued']}件")
        if c.get("completed"):
            detail_parts.append(f"完了{c['completed']}件")
        if c.get("failed"):
            detail_parts.append(f"失敗{c['failed']}件")

        result.append({
            "name":     meta["name"],
            "icon":     meta["icon"],
            "status":   _status_badge(c),
            "last_run": last_run_str,
            "next_run": "スケジューラー依存",
            "detail":   "、".join(detail_parts) or "タスクなし",
        })

    return result


def get_brand_status() -> dict:
    """各ブランドのタスク状況"""
    brands = db.list_brands()
    result = {}

    for b in brands:
        slug = b["slug"]
        meta = BRAND_META.get(slug, {"name": slug, "color": "#888", "link": f"/brands/{slug}"})

        # そのブランドに割り当てられたタスク件数を取得
        with db.get_conn() as conn:
            completed_today = conn.execute(
                """SELECT COUNT(*) FROM agent_tasks
                   WHERE brand_id=? AND status='completed'
                   AND date(updated_at)=date('now')""",
                (b["id"],),
            ).fetchone()[0]
            queued = conn.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE brand_id=? AND status='queued'",
                (b["id"],),
            ).fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE brand_id=? AND status='failed'",
                (b["id"],),
            ).fetchone()[0]

        if failed > 0:
            health = "alert"
        elif queued == 0 and completed_today == 0:
            health = "warn"
        else:
            health = "good"

        result[slug] = {
            "name":         meta["name"],
            "color":        meta["color"],
            "posts_today":  completed_today,
            "posts_week":   0,  # 詳細分析は analytics agent に任せる
            "target_week":  10,
            "leads_active": 0,
            "media_left":   0,
            "health":       health,
            "link":         meta["link"],
        }

    return result


def get_ceo_priorities() -> list:
    """AI CEO の本日の優先事項（最新のCEO実行ログから取得）"""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                """SELECT log FROM agent_runs
                   WHERE agent_id='ai-ceo' AND status='completed'
                   ORDER BY completed_at DESC LIMIT 1""",
            ).fetchone()
        if not row or not row["log"]:
            return _default_priorities()

        data = json.loads(row["log"])
        decisions = data.get("decisions", [])
        if not decisions:
            return _default_priorities()

        result = []
        for i, d in enumerate(decisions[:5], 1):
            title = d.get("title", d.get("message", ""))
            agent = d.get("agent", "")
            result.append({
                "order":     i,
                "focus":     title[:40] if title else "—",
                "rationale": f"担当: {agent}" if agent else "今日の重点タスク",
            })
        return result

    except Exception as e:
        log.debug(f"get_ceo_priorities error: {e}")
        return _default_priorities()


def _default_priorities() -> list:
    task_counts = {
        "queued":  len(db.list_tasks(status="queued",  limit=1)),
        "failed":  len(db.list_tasks(status="failed",  limit=1)),
        "running": len(db.list_tasks(status="running", limit=1)),
    }
    return [
        {"order": 1, "focus": f"キュー中タスク: {task_counts['queued']}件",  "rationale": "順次実行中"},
        {"order": 2, "focus": f"実行中タスク: {task_counts['running']}件",   "rationale": "エージェント稼働中"},
        {"order": 3, "focus": f"失敗タスク: {task_counts['failed']}件",      "rationale": "要確認・再試行"},
        {"order": 4, "focus": "朝のディスパッチ: 毎朝5:30自動実行",          "rationale": "AI CEOが各エージェントにタスクを割り当て"},
    ]


def get_ceo_to_president() -> list:
    """CEOから社長への報告事項（エスカレーション + CEO最新ログ）"""
    result = []

    escs = db.list_escalations(status="open")
    for e in escs[:3]:
        result.append({
            "icon":    "🚨",
            "item":    e.get("reason", "")[:60],
            "context": "エスカレーション — 確認・対応必要",
        })

    # 失敗タスクを報告
    failed = db.list_tasks(status="failed", limit=3)
    for t in failed:
        result.append({
            "icon":    "❌",
            "item":    f"タスク失敗: {t.get('title', '')}",
            "context": (t.get("error_message") or "エラー詳細を確認してください")[:80],
        })

    if not result:
        result.append({
            "icon":    "✅",
            "item":    "現在、社長への報告事項はありません",
            "context": "全システム正常稼働中",
        })

    return result


def get_pending_approvals() -> list:
    """承認待ちタスク一覧"""
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT a.id, a.title, a.task_id, a.status, a.created_at
               FROM approvals a
               WHERE a.status='pending'
               ORDER BY a.created_at DESC LIMIT 10""",
        ).fetchall()

    result = []
    for r in rows:
        result.append({
            "id":          r["id"][:8],
            "title":       r["title"],
            "task_id":     r["task_id"],
            "status":      r["status"],
            "created_at":  r["created_at"][:16] if r["created_at"] else "—",
        })

    return result


def get_morning_brief() -> dict:
    """朝のブリーフ（本日の統計）"""
    with db.get_conn() as conn:
        completed_today = conn.execute(
            """SELECT COUNT(*) FROM agent_tasks
               WHERE status='completed' AND date(updated_at)=date('now')""",
        ).fetchone()[0]
        total_queued = conn.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE status='queued'",
        ).fetchone()[0]
        total_failed = conn.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE status='failed'",
        ).fetchone()[0]
        total_agents = conn.execute(
            "SELECT COUNT(*) FROM ai_agents WHERE is_active=1",
        ).fetchone()[0]

    now = datetime.now()
    return {
        "date":             now.strftime("%Y年%m月%d日"),
        "time":             now.strftime("%H:%M"),
        "completed_today":  completed_today,
        "queued":           total_queued,
        "failed":           total_failed,
        "active_agents":    total_agents,
        "status":           "alert" if total_failed > 3 else "warn" if total_failed > 0 else "ok",
        "headline":         f"本日 {completed_today}件完了 / {total_queued}件待機中",
    }
