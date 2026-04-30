"""
Lightweight Orchestrator
Drives the task lifecycle and handles approvals, escalations, and downstream unblocking.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import org_database as db
from agents.task_service import (
    transition, enqueue, get_runnable_tasks,
    unblock_downstream, needs_president_approval,
)
from agents.assignment_service import find_best_agent, assign, auto_assign

log = logging.getLogger(__name__)


# ── Main orchestration cycle ───────────────────────────────────

def tick(execute: bool = False) -> dict:
    """
    One orchestration step:
      1. idle tasks  → queued | blocked
      2. queued+runnable tasks → assign agent (if unassigned)
      3. execute=True のとき agent_executor.run_next() で実行まで行う
    Returns a summary dict.
    """
    summary = {"enqueued": 0, "blocked": 0, "assigned": 0, "executed": 0}

    # Step 1: advance idle tasks
    for t in db.list_tasks(status="idle", limit=50):
        if enqueue(t["id"]):
            refreshed = db.get_task(t["id"])
            if refreshed:
                if refreshed["status"] == "queued":
                    summary["enqueued"] += 1
                elif refreshed["status"] == "blocked":
                    summary["blocked"] += 1

    # Step 2: assign agents to unassigned runnable tasks
    for t in get_runnable_tasks(limit=20):
        if not t.get("assigned_to_agent_id"):
            if auto_assign(t["id"]):
                summary["assigned"] += 1

    # Step 3: execute (optional)
    if execute:
        from agents.agent_executor import run_next
        results = run_next(limit=5)
        summary["executed"] = len(results)

    log.info(f"Orchestrator tick complete: {summary}")
    return summary


# ── Task lifecycle actions ─────────────────────────────────────

def start_task(task_id: str) -> Optional[str]:
    """
    Mark the task as running and open a run record.
    Auto-assigns an agent if none is set.
    Returns run_id or None.
    """
    task = db.get_task(task_id)
    if not task:
        return None
    agent_id = task.get("assigned_to_agent_id") or auto_assign(task_id)
    if not agent_id:
        log.warning(f"start_task: no agent available for {task_id}")
        return None
    if not transition(task_id, "running"):
        return None
    run_id = db.start_run(task_id, agent_id)
    log.info(f"Task {task_id} started → run {run_id}")
    return run_id


def complete_task(task_id: str, run_id: str,
                  output_data: dict | None = None,
                  log_entries: list | None = None,
                  tokens_used: int = 0, cost_usd: float = 0.0):
    """Complete task + run, then unblock any downstream tasks."""
    db.finish_run(run_id, status="completed",
                  log_entries=log_entries or [],
                  tokens_used=tokens_used, cost_usd=cost_usd)
    transition(task_id, "completed", output_data=output_data)
    unblock_downstream(task_id)
    log.info(f"Task {task_id} completed")


def fail_task(task_id: str, run_id: str, error: str,
              auto_escalate: bool = True):
    """Mark task + run as failed. Optionally auto-escalate."""
    db.finish_run(run_id, status="failed", error_message=error)
    transition(task_id, "failed", error_message=error)
    if auto_escalate:
        task = db.get_task(task_id)
        agent_id = task.get("assigned_to_agent_id", "") if task else ""
        escalate(task_id,
                 reason=f"タスクが失敗しました: {error}",
                 agent_id=agent_id,
                 context={"error": error})
    log.info(f"Task {task_id} failed: {error}")


def requeue_task(task_id: str) -> bool:
    """Retry a failed or escalated task by moving it back to queued."""
    return enqueue(task_id) or transition(task_id, "queued")


# ── Approval flow ──────────────────────────────────────────────

def request_approval(task_id: str, title: str, description: str = "",
                     approver_user_ids: list[str] | None = None,
                     expires_in_hours: int = 48) -> str:
    """
    Pause task (→ waiting_approval) and create an approval record.
    Automatically prepends the Human President when the task needs their sign-off.
    Returns approval_id.
    """
    task = db.get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    expires_at = (
        datetime.now() + timedelta(hours=expires_in_hours)
    ).strftime("%Y-%m-%d %H:%M:%S")

    approvers = list(approver_user_ids or [])
    if needs_president_approval(task):
        pid = _get_president_user_id()
        if pid and pid not in approvers:
            approvers.insert(0, pid)

    if not approvers:
        # Default: route to AI CEO
        ceo_id = _get_ceo_user_id()
        if ceo_id:
            approvers = [ceo_id]

    approval_id = db.create_approval(
        task_id=task_id,
        title=title,
        requested_by_agent_id=task.get("assigned_to_agent_id", ""),
        description=description,
        approver_user_ids=approvers,
        expires_at=expires_at,
    )
    transition(task_id, "waiting_approval")
    log.info(f"Approval {approval_id} created for task {task_id}")
    return approval_id


def approve_task(task_id: str, approver_user_id: str,
                 comment: str = "") -> bool:
    """Approve the pending approval for this task."""
    approval = _get_pending_approval(task_id)
    if not approval:
        log.warning(f"No pending approval for task {task_id}")
        return False
    db.decide_approval_step(approval["id"], approver_user_id, "approved", comment)
    # Re-check overall approval status
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM approvals WHERE id=?", (approval["id"],)
        ).fetchone()
    if row and row["status"] == "approved":
        transition(task_id, "running")
    log.info(f"Task {task_id} approved by {approver_user_id}")
    return True


def reject_task(task_id: str, approver_user_id: str,
                comment: str = "") -> bool:
    """Reject the pending approval for this task."""
    approval = _get_pending_approval(task_id)
    if not approval:
        return False
    db.decide_approval_step(approval["id"], approver_user_id, "rejected", comment)
    transition(task_id, "failed",
               error_message=f"承認却下: {comment or '理由なし'}")
    log.info(f"Task {task_id} rejected by {approver_user_id}")
    return True


# ── Escalation flow ────────────────────────────────────────────

def escalate(task_id: str, reason: str,
             agent_id: str = "",
             context: dict | None = None) -> str:
    """Create escalation record and move task → escalated."""
    president_id = _get_president_user_id()
    esc_id = db.create_escalation(
        task_id=task_id, reason=reason,
        agent_id=agent_id,
        escalated_to_user_id=president_id or "",
        context=context or {},
    )
    transition(task_id, "escalated")
    log.info(f"Task {task_id} escalated → {esc_id}")
    return esc_id


def resolve_escalation(escalation_id: str, note: str = "",
                        requeue: bool = True) -> bool:
    """Resolve an open escalation; optionally re-queue the task."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT task_id FROM escalations WHERE id=? AND status='open'",
            (escalation_id,),
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """UPDATE escalations
               SET status='resolved', resolved_at=?, resolution_note=?
               WHERE id=?""",
            (now, note, escalation_id),
        )
        task_id = row["task_id"]
    if requeue:
        enqueue(task_id)
    log.info(f"Escalation {escalation_id} resolved (requeue={requeue})")
    return True


# ── CEO overview ───────────────────────────────────────────────

def get_overview() -> dict:
    """Aggregate task + agent stats for the AI CEO dashboard."""
    today = datetime.now().strftime("%Y-%m-%d")
    with db.get_conn() as conn:
        status_counts: dict[str, int] = {}
        for st in ("idle", "queued", "running", "blocked",
                   "waiting_approval", "completed", "failed", "escalated"):
            status_counts[st] = conn.execute(
                "SELECT COUNT(*) FROM agent_tasks WHERE status=?", (st,)
            ).fetchone()[0]

        open_escalations = conn.execute(
            "SELECT COUNT(*) FROM escalations WHERE status='open'"
        ).fetchone()[0]

        pending_approvals = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE status='pending'"
        ).fetchone()[0]

        active_agents = conn.execute(
            "SELECT COUNT(*) FROM ai_agents WHERE is_active=1"
        ).fetchone()[0]

        today_created = conn.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE created_at LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]

        today_completed = conn.execute(
            "SELECT COUNT(*) FROM agent_tasks WHERE completed_at LIKE ?",
            (f"{today}%",),
        ).fetchone()[0]

        # Blocked tasks + what's blocking them
        blocked_detail = conn.execute(
            """SELECT at.id, at.title, at.priority,
                      GROUP_CONCAT(bt.title, ' | ') AS blocker_titles
               FROM agent_tasks at
               JOIN agent_task_dependencies dep ON dep.task_id = at.id
               JOIN agent_tasks bt ON bt.id = dep.depends_on_task_id
               WHERE at.status='blocked'
                 AND bt.status NOT IN ('completed','failed')
               GROUP BY at.id
               LIMIT 10""",
        ).fetchall()

        # Tasks needing president approval
        president_tasks = conn.execute(
            """SELECT id, title, priority, mode FROM agent_tasks
               WHERE status='waiting_approval'
                 AND (mode='human_approval_required' OR priority <= 2)
               LIMIT 10""",
        ).fetchall()

    total = status_counts.get("completed", 0) + status_counts.get("failed", 0)
    return {
        "status_counts":     status_counts,
        "open_escalations":  open_escalations,
        "pending_approvals": pending_approvals,
        "active_agents":     active_agents,
        "today_created":     today_created,
        "today_completed":   today_completed,
        "blocked_detail":    [dict(r) for r in blocked_detail],
        "president_tasks":   [dict(r) for r in president_tasks],
        "throughput_pct":    round(today_completed / max(today_created, 1) * 100),
        "success_rate":      round(
            status_counts.get("completed", 0) / max(total, 1) * 100
        ),
    }


# ── Internal helpers ───────────────────────────────────────────

def _get_president_user_id() -> Optional[str]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE role_id='owner' LIMIT 1"
        ).fetchone()
        if not row:
            # フォールバック: humanユーザーの最初の1件
            row = conn.execute(
                "SELECT id FROM users WHERE user_type='human' LIMIT 1"
            ).fetchone()
    return row["id"] if row else None


def _get_ceo_user_id() -> Optional[str]:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM users WHERE role_id='ai_ceo' LIMIT 1"
        ).fetchone()
    return row["id"] if row else None


def _get_pending_approval(task_id: str) -> dict | None:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM approvals WHERE task_id=? AND status='pending' LIMIT 1",
            (task_id,),
        ).fetchone()
    return dict(row) if row else None
