"""
組織・AIガバナンス データベースモジュール

テーブルグループ:
  organization  : organizations / brands / locations / users / roles / user_brand_permissions
  ai_governance : ai_ceo_profiles / ai_agents / agent_capabilities / agent_assignments
                  agent_tasks / agent_task_dependencies / agent_runs
                  escalations / approvals / approval_steps
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.types import (
    AgentTask, TaskMode, TaskStatus,
)

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "upj.db"


# ══════════════════════════════════════════
# CONNECTION
# ══════════════════════════════════════════

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _uid() -> str:
    return str(uuid.uuid4())


# ══════════════════════════════════════════
# MIGRATION / INIT
# ══════════════════════════════════════════

SCHEMA_SQL = """
-- ────────────── organization ──────────────

CREATE TABLE IF NOT EXISTS organizations (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS brands (
    id              TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    name            TEXT NOT NULL,
    slug            TEXT UNIQUE NOT NULL,
    short_name      TEXT,
    color           TEXT,
    url             TEXT,
    description     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_brands_org ON brands(organization_id);

CREATE TABLE IF NOT EXISTS locations (
    id         TEXT PRIMARY KEY,
    brand_id   TEXT NOT NULL REFERENCES brands(id),
    name       TEXT NOT NULL,
    country    TEXT DEFAULT 'JP',
    city       TEXT,
    address    TEXT,
    timezone   TEXT DEFAULT 'Asia/Tokyo',
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_locations_brand ON locations(brand_id);

CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    description TEXT,
    level       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL REFERENCES organizations(id),
    role_id         TEXT REFERENCES roles(id),
    user_type       TEXT NOT NULL DEFAULT 'human',
    name            TEXT NOT NULL,
    email           TEXT,
    avatar_url      TEXT,
    is_active       INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_org  ON users(organization_id);
CREATE INDEX IF NOT EXISTS idx_users_type ON users(user_type);

CREATE TABLE IF NOT EXISTS user_brand_permissions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          TEXT NOT NULL REFERENCES users(id),
    brand_id         TEXT NOT NULL REFERENCES brands(id),
    permission_level TEXT DEFAULT 'read',
    UNIQUE(user_id, brand_id)
);

-- ────────────── ai_governance ──────────────

CREATE TABLE IF NOT EXISTS ai_ceo_profiles (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    reports_to_user_id  TEXT REFERENCES users(id),
    persona             TEXT,
    decision_authority  TEXT DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS ai_agents (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    agent_type   TEXT NOT NULL,
    reports_to_id TEXT REFERENCES users(id),
    model        TEXT DEFAULT 'claude-sonnet-4-6',
    system_prompt TEXT,
    config       TEXT DEFAULT '{}',
    is_active    INTEGER DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_agents_type   ON ai_agents(agent_type);
CREATE INDEX IF NOT EXISTS idx_agents_active ON ai_agents(is_active);

CREATE TABLE IF NOT EXISTS agent_capabilities (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id   TEXT NOT NULL REFERENCES ai_agents(id),
    capability TEXT NOT NULL,
    enabled    INTEGER DEFAULT 1,
    config     TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_caps_agent ON agent_capabilities(agent_id);

CREATE TABLE IF NOT EXISTS agent_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL REFERENCES ai_agents(id),
    brand_id    TEXT NOT NULL REFERENCES brands(id),
    location_id TEXT REFERENCES locations(id),
    is_primary  INTEGER DEFAULT 0,
    created_at  TEXT NOT NULL,
    UNIQUE(agent_id, brand_id)
);
CREATE INDEX IF NOT EXISTS idx_assign_agent ON agent_assignments(agent_id);
CREATE INDEX IF NOT EXISTS idx_assign_brand ON agent_assignments(brand_id);

CREATE TABLE IF NOT EXISTS agent_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    assigned_to_agent_id  TEXT REFERENCES ai_agents(id),
    requested_by_user_id  TEXT REFERENCES users(id),
    brand_id              TEXT REFERENCES brands(id),
    mode                  TEXT NOT NULL DEFAULT 'semi_auto',
    status                TEXT NOT NULL DEFAULT 'idle',
    priority              INTEGER DEFAULT 5,
    input_data            TEXT DEFAULT '{}',
    output_data           TEXT DEFAULT '{}',
    error_message         TEXT,
    scheduled_at          TEXT,
    started_at            TEXT,
    completed_at          TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_agent  ON agent_tasks(assigned_to_agent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON agent_tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_brand  ON agent_tasks(brand_id);
CREATE INDEX IF NOT EXISTS idx_tasks_mode   ON agent_tasks(mode);

CREATE TABLE IF NOT EXISTS agent_task_dependencies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL REFERENCES agent_tasks(id),
    depends_on_task_id  TEXT NOT NULL REFERENCES agent_tasks(id),
    dependency_type     TEXT DEFAULT 'finish_to_start',
    UNIQUE(task_id, depends_on_task_id)
);
CREATE INDEX IF NOT EXISTS idx_deps_task    ON agent_task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_deps_depends ON agent_task_dependencies(depends_on_task_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id            TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL REFERENCES agent_tasks(id),
    agent_id      TEXT NOT NULL REFERENCES ai_agents(id),
    run_number    INTEGER DEFAULT 1,
    status        TEXT NOT NULL DEFAULT 'running',
    log           TEXT,
    tokens_used   INTEGER DEFAULT 0,
    cost_usd      REAL DEFAULT 0,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_task  ON agent_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_runs_agent ON agent_runs(agent_id);

CREATE TABLE IF NOT EXISTS escalations (
    id                    TEXT PRIMARY KEY,
    task_id               TEXT NOT NULL REFERENCES agent_tasks(id),
    agent_id              TEXT REFERENCES ai_agents(id),
    escalated_to_user_id  TEXT REFERENCES users(id),
    reason                TEXT NOT NULL,
    context               TEXT DEFAULT '{}',
    status                TEXT DEFAULT 'open',
    resolved_at           TEXT,
    resolution_note       TEXT,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_esc_task   ON escalations(task_id);
CREATE INDEX IF NOT EXISTS idx_esc_status ON escalations(status);

CREATE TABLE IF NOT EXISTS approvals (
    id                      TEXT PRIMARY KEY,
    task_id                 TEXT NOT NULL REFERENCES agent_tasks(id),
    title                   TEXT NOT NULL,
    description             TEXT,
    requested_by_agent_id   TEXT REFERENCES ai_agents(id),
    status                  TEXT DEFAULT 'pending',
    expires_at              TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_appr_task   ON approvals(task_id);
CREATE INDEX IF NOT EXISTS idx_appr_status ON approvals(status);

CREATE TABLE IF NOT EXISTS approval_steps (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id      TEXT NOT NULL REFERENCES approvals(id),
    step_order       INTEGER NOT NULL,
    approver_user_id TEXT NOT NULL REFERENCES users(id),
    status           TEXT DEFAULT 'pending',
    comment          TEXT,
    decided_at       TEXT,
    UNIQUE(approval_id, step_order)
);
CREATE INDEX IF NOT EXISTS idx_steps_approval ON approval_steps(approval_id);
"""


def init_org_db():
    """組織・AIガバナンステーブルを初期化する"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
    log.info("組織DBスキーマ初期化完了")


# ══════════════════════════════════════════
# organizations
# ══════════════════════════════════════════

def create_organization(name: str, slug: str, description: str = "") -> str:
    oid = _uid()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO organizations (id,name,slug,description,created_at) VALUES (?,?,?,?,?)",
            (oid, name, slug, description, _now()),
        )
        row = conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
    return row["id"]


def get_organization_by_slug(slug: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM organizations WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def list_organizations() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM organizations ORDER BY name").fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# brands
# ══════════════════════════════════════════

def create_brand(organization_id: str, name: str, slug: str,
                 short_name: str = "", color: str = "", url: str = "",
                 description: str = "") -> str:
    bid = _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO brands
               (id,organization_id,name,slug,short_name,color,url,description,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (bid, organization_id, name, slug, short_name, color, url, description, _now()),
        )
        row = conn.execute("SELECT id FROM brands WHERE slug=?", (slug,)).fetchone()
    return row["id"]


def get_brand_by_slug(slug: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM brands WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def list_brands(organization_id: str = "") -> list[dict]:
    sql = "SELECT * FROM brands"
    params: list = []
    if organization_id:
        sql += " WHERE organization_id=?"
        params.append(organization_id)
    sql += " ORDER BY name"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# roles
# ══════════════════════════════════════════

def create_role(name: str, slug: str, level: int = 0, description: str = "") -> str:
    rid = slug  # slug を PK として使う（固定値）
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO roles (id,name,slug,description,level) VALUES (?,?,?,?,?)",
            (rid, name, slug, description, level),
        )
    return rid


# 6種デフォルトロールを確保（起動時に呼び出す）
ROLE_LEVELS = {
    "owner":    200,  # 全権限（オーナー）
    "admin":    150,  # 管理者（設定変更可）
    "operator": 100,  # 運用担当（投稿・承認）
    "editor":    70,  # 編集者（コンテンツ編集のみ）
    "reviewer":  50,  # レビュアー（承認のみ）
    "viewer":    10,  # 閲覧のみ
}

ROLE_DESCS = {
    "owner":    "全権限。組織設定・課金管理を含む",
    "admin":    "管理者。ユーザー管理・設定変更可",
    "operator": "運用担当。投稿・承認・エージェント操作可",
    "editor":   "編集者。コンテンツ作成・編集のみ",
    "reviewer": "レビュアー。承認・コメントのみ",
    "viewer":   "閲覧専用。変更不可",
}


def seed_default_roles():
    """6種デフォルトロールを INSERT OR IGNORE で確保"""
    for slug, level in ROLE_LEVELS.items():
        create_role(ROLE_DESCS.get(slug, slug).split("。")[0], slug, level, ROLE_DESCS.get(slug, ""))


# ── 権限チェックヘルパー ──────────────────────────────────

def get_user_permission_level(user_id: str, brand_id: str = "") -> int:
    """ユーザーの権限レベルを返す。brand_id 指定時はブランド権限を優先。"""
    with get_conn() as conn:
        # まずユーザーのグローバルロールを取得
        row = conn.execute(
            "SELECT r.level FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?",
            (user_id,)
        ).fetchone()
        global_level = row["level"] if row else 0

        if not brand_id:
            return global_level

        # ブランド固有の権限（permission_level は slug として扱う）
        bp = conn.execute(
            "SELECT permission_level FROM user_brand_permissions WHERE user_id=? AND brand_id=?",
            (user_id, brand_id)
        ).fetchone()
        if bp:
            brand_level = ROLE_LEVELS.get(bp["permission_level"], 0)
            return max(global_level, brand_level)
        return global_level


def check_permission(user_id: str, required_slug: str, brand_id: str = "") -> bool:
    """required_slug 以上のロールを持つか確認"""
    required_level = ROLE_LEVELS.get(required_slug, 0)
    user_level = get_user_permission_level(user_id, brand_id)
    return user_level >= required_level


def list_roles() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM roles ORDER BY level DESC").fetchall()
    return [dict(r) for r in rows]


def get_role(slug: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM roles WHERE slug=?", (slug,)).fetchone()
    return dict(row) if row else None


def list_user_permissions(user_id: str) -> list[dict]:
    """ユーザーの全ブランド権限一覧"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ubp.*, b.name as brand_name, b.slug as brand_slug
               FROM user_brand_permissions ubp
               JOIN brands b ON b.id=ubp.brand_id
               WHERE ubp.user_id=?""",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# users
# ══════════════════════════════════════════

def create_user(organization_id: str, name: str, user_type: str = "human",
                role_id: str = "", email: str = "") -> str:
    uid = _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users
               (id,organization_id,role_id,user_type,name,email,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (uid, organization_id, role_id or None, user_type, name, email or None, _now()),
        )
    return uid


def get_user(user_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def list_users(user_type: str = "", organization_id: str = "") -> list[dict]:
    sql = "SELECT * FROM users WHERE 1=1"
    params: list = []
    if user_type:
        sql += " AND user_type=?"; params.append(user_type)
    if organization_id:
        sql += " AND organization_id=?"; params.append(organization_id)
    sql += " ORDER BY name"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def grant_brand_permission(user_id: str, brand_id: str,
                            level: str = "read"):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO user_brand_permissions (user_id,brand_id,permission_level)
               VALUES (?,?,?)
               ON CONFLICT(user_id,brand_id) DO UPDATE SET permission_level=excluded.permission_level""",
            (user_id, brand_id, level),
        )


# ══════════════════════════════════════════
# ai_ceo_profiles
# ══════════════════════════════════════════

def create_ai_ceo_profile(user_id: str, reports_to_user_id: str,
                           persona: dict | None = None,
                           decision_authority: dict | None = None) -> str:
    pid = _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_ceo_profiles
               (id,user_id,reports_to_user_id,persona,decision_authority,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                pid, user_id, reports_to_user_id,
                json.dumps(persona or {}),
                json.dumps(decision_authority or {}),
                _now(),
            ),
        )
    return pid


def get_ai_ceo_profile() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_ceo_profiles LIMIT 1").fetchone()
    if not row:
        return None
    d = dict(row)
    d["persona"] = json.loads(d.get("persona") or "{}")
    d["decision_authority"] = json.loads(d.get("decision_authority") or "{}")
    return d


# ══════════════════════════════════════════
# ai_agents
# ══════════════════════════════════════════

def create_ai_agent(user_id: str, agent_type: str, reports_to_id: str = "",
                    model: str = "claude-sonnet-4-6",
                    system_prompt: str = "", config: dict | None = None,
                    agent_id: str = "") -> str:
    aid = agent_id or _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO ai_agents
               (id,user_id,agent_type,reports_to_id,model,system_prompt,config,created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                aid, user_id, agent_type, reports_to_id or None,
                model, system_prompt, json.dumps(config or {}), _now(),
            ),
        )
        # モデル・システムプロンプトが変わった場合は更新
        conn.execute(
            """UPDATE ai_agents SET model=?, system_prompt=?, updated_at=?
               WHERE id=?""",
            (model, system_prompt, _now(), aid),
        )
    return aid


def get_ai_agent(agent_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM ai_agents WHERE id=?", (agent_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["config"] = json.loads(d.get("config") or "{}")
    return d


def list_ai_agents(active_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM ai_agents"
    params: list = []
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY agent_type"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["config"] = json.loads(d.get("config") or "{}")
        result.append(d)
    return result


def add_agent_capability(agent_id: str, capability: str,
                          config: dict | None = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_capabilities (agent_id,capability,config)
               VALUES (?,?,?)""",
            (agent_id, capability, json.dumps(config or {})),
        )


def assign_agent_to_brand(agent_id: str, brand_id: str,
                           location_id: str = "", is_primary: bool = False):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_assignments
               (agent_id,brand_id,location_id,is_primary,created_at)
               VALUES (?,?,?,?,?)""",
            (agent_id, brand_id, location_id or None, 1 if is_primary else 0, _now()),
        )


# ══════════════════════════════════════════
# agent_tasks
# ══════════════════════════════════════════

def _ensure_agent_exists(agent_id: str) -> None:
    """指定されたエージェントIDがai_agentsに存在しない場合、最低限のレコードを挿入する"""
    if not agent_id:
        return
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT id FROM ai_agents WHERE id=?", (agent_id,)).fetchone()
            if row:
                return
            # 既存 ai ユーザーを再利用するか、新規作成する
            existing_ai = conn.execute(
                "SELECT id FROM users WHERE user_type='ai' LIMIT 1"
            ).fetchone()
            if existing_ai:
                user_id = existing_ai["id"]
            else:
                user_id = _uid()
                org_row = conn.execute("SELECT id FROM organizations LIMIT 1").fetchone()
                org_id = org_row["id"] if org_row else None
                # role_id は NULL 許容（FK 未設定環境でも動く）
                role_row = conn.execute(
                    "SELECT id FROM roles WHERE slug='ai_agent' LIMIT 1"
                ).fetchone()
                role_id = role_row["id"] if role_row else None
                conn.execute(
                    """INSERT OR IGNORE INTO users
                       (id,organization_id,role_id,user_type,name,is_active,created_at)
                       VALUES (?,?,?,'ai',?,1,?)""",
                    (user_id, org_id, role_id, agent_id, _now()),
                )
            conn.execute(
                """INSERT OR IGNORE INTO ai_agents
                   (id,user_id,agent_type,model,system_prompt,config,is_active,created_at)
                   VALUES (?,?,?,?,?,?,1,?)""",
                (agent_id, user_id, agent_id, "claude-haiku-4-5-20251001", "", "{}", _now()),
            )
        log.info(f"エージェント自動作成: {agent_id}")
    except Exception as e:
        log.warning(f"エージェント自動作成スキップ {agent_id}: {e}")


def create_task(
    title: str,
    mode: TaskMode = "semi_auto",
    assigned_to_agent_id: str = "",
    requested_by_user_id: str = "",
    brand_id: str = "",
    description: str = "",
    priority: int = 5,
    input_data: dict | None = None,
    scheduled_at: str = "",
) -> str:
    if assigned_to_agent_id:
        _ensure_agent_exists(assigned_to_agent_id)
    tid = _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO agent_tasks
               (id,title,description,assigned_to_agent_id,requested_by_user_id,
                brand_id,mode,status,priority,input_data,output_data,
                scheduled_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,'idle',?,?,?,?,?,?)""",
            (
                tid, title, description or None,
                assigned_to_agent_id or None,
                requested_by_user_id or None,
                brand_id or None,
                mode, priority,
                json.dumps(input_data or {}),
                "{}",
                scheduled_at or None,
                _now(), _now(),
            ),
        )
    return tid


def update_task_status(task_id: str, status: TaskStatus,
                        error_message: str = "", output_data: dict | None = None):
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """UPDATE agent_tasks
               SET status=?, error_message=?, output_data=COALESCE(?,output_data), updated_at=?,
                   started_at  = CASE WHEN ? = 'running'   AND started_at IS NULL THEN ? ELSE started_at END,
                   completed_at= CASE WHEN ? IN ('completed','failed') THEN ? ELSE completed_at END
               WHERE id=?""",
            (
                status, error_message or None,
                json.dumps(output_data) if output_data else None,
                now,
                status, now,
                status, now,
                task_id,
            ),
        )


def get_task(task_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agent_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["input_data"]  = json.loads(d.get("input_data")  or "{}")
    d["output_data"] = json.loads(d.get("output_data") or "{}")
    return d


def list_tasks(status: str = "", agent_id: str = "",
               brand_id: str = "", limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM agent_tasks WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"; params.append(status)
    if agent_id:
        sql += " AND assigned_to_agent_id=?"; params.append(agent_id)
    if brand_id:
        sql += " AND brand_id=?"; params.append(brand_id)
    sql += " ORDER BY priority ASC, created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["input_data"]  = json.loads(d.get("input_data")  or "{}")
        d["output_data"] = json.loads(d.get("output_data") or "{}")
        result.append(d)
    return result


def add_task_dependency(task_id: str, depends_on_task_id: str,
                         dependency_type: str = "finish_to_start"):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO agent_task_dependencies
               (task_id,depends_on_task_id,dependency_type) VALUES (?,?,?)""",
            (task_id, depends_on_task_id, dependency_type),
        )


def get_task_dependencies(task_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_task_dependencies WHERE task_id=?", (task_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_blocking_tasks(task_id: str) -> list[dict]:
    """task_id に依存している未完了タスクを返す"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT at.* FROM agent_tasks at
               JOIN agent_task_dependencies d ON d.depends_on_task_id = at.id
               WHERE d.task_id=? AND at.status NOT IN ('completed','failed')""",
            (task_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# agent_runs
# ══════════════════════════════════════════

def start_run(task_id: str, agent_id: str) -> str:
    rid = _uid()
    with get_conn() as conn:
        run_number = (
            conn.execute(
                "SELECT COALESCE(MAX(run_number),0)+1 FROM agent_runs WHERE task_id=?",
                (task_id,),
            ).fetchone()[0]
        )
        conn.execute(
            """INSERT INTO agent_runs
               (id,task_id,agent_id,run_number,status,started_at)
               VALUES (?,?,?,?,'running',?)""",
            (rid, task_id, agent_id, run_number, _now()),
        )
    return rid


def finish_run(run_id: str, status: str = "completed",
               log_entries: list | None = None,
               tokens_used: int = 0, cost_usd: float = 0,
               error_message: str = ""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE agent_runs
               SET status=?,log=?,tokens_used=?,cost_usd=?,
                   completed_at=?,error_message=?
               WHERE id=?""",
            (
                status,
                json.dumps(log_entries or []),
                tokens_used, cost_usd,
                _now(), error_message or None,
                run_id,
            ),
        )


# ══════════════════════════════════════════
# escalations
# ══════════════════════════════════════════

def create_escalation(task_id: str, reason: str,
                       agent_id: str = "",
                       escalated_to_user_id: str = "",
                       context: dict | None = None) -> str:
    eid = _uid()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO escalations
               (id,task_id,agent_id,escalated_to_user_id,reason,context,created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                eid, task_id, agent_id or None,
                escalated_to_user_id or None,
                reason, json.dumps(context or {}), _now(),
            ),
        )
    return eid


def list_escalations(status: str = "open") -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE status=? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["context"] = json.loads(d.get("context") or "{}")
        result.append(d)
    return result


# ══════════════════════════════════════════
# approvals
# ══════════════════════════════════════════

def create_approval(task_id: str, title: str,
                     requested_by_agent_id: str = "",
                     description: str = "",
                     approver_user_ids: list[str] | None = None,
                     expires_at: str = "") -> str:
    appr_id = _uid()
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO approvals
               (id,task_id,title,description,requested_by_agent_id,expires_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                appr_id, task_id, title, description or None,
                requested_by_agent_id or None,
                expires_at or None, now, now,
            ),
        )
        for i, uid in enumerate(approver_user_ids or [], start=1):
            conn.execute(
                """INSERT INTO approval_steps
                   (approval_id,step_order,approver_user_id)
                   VALUES (?,?,?)""",
                (appr_id, i, uid),
            )
    return appr_id


def decide_approval_step(approval_id: str, approver_user_id: str,
                          decision: str, comment: str = ""):
    now = _now()
    with get_conn() as conn:
        conn.execute(
            """UPDATE approval_steps
               SET status=?,comment=?,decided_at=?
               WHERE approval_id=? AND approver_user_id=? AND status='pending'""",
            (decision, comment or None, now, approval_id, approver_user_id),
        )
        pending = conn.execute(
            "SELECT COUNT(*) FROM approval_steps WHERE approval_id=? AND status='pending'",
            (approval_id,),
        ).fetchone()[0]
        rejected = conn.execute(
            "SELECT COUNT(*) FROM approval_steps WHERE approval_id=? AND status='rejected'",
            (approval_id,),
        ).fetchone()[0]
        if rejected > 0:
            final_status = "rejected"
        elif pending == 0:
            final_status = "approved"
        else:
            return
        conn.execute(
            "UPDATE approvals SET status=?,updated_at=? WHERE id=?",
            (final_status, now, approval_id),
        )


# ══════════════════════════════════════════
# RICH QUERY HELPERS (agent workspace UI)
# ══════════════════════════════════════════

def get_task_counts_for_agent(agent_id: str) -> dict[str, int]:
    """Task counts by status for a single agent."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT status, COUNT(*) as cnt
               FROM agent_tasks WHERE assigned_to_agent_id=?
               GROUP BY status""",
            (agent_id,),
        ).fetchall()
    return {r["status"]: r["cnt"] for r in rows}


def get_task_counts_all_agents() -> dict[str, dict[str, int]]:
    """task counts by status keyed by agent_id."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT assigned_to_agent_id, status, COUNT(*) as cnt
               FROM agent_tasks
               WHERE assigned_to_agent_id IS NOT NULL
               GROUP BY assigned_to_agent_id, status""",
        ).fetchall()
    result: dict[str, dict[str, int]] = {}
    for r in rows:
        aid = r["assigned_to_agent_id"]
        result.setdefault(aid, {})[r["status"]] = r["cnt"]
    return result


def list_runs_for_agent(agent_id: str, limit: int = 20) -> list[dict]:
    """Recent run records for an agent, joined with task title."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT ar.*, at.title as task_title
               FROM agent_runs ar
               JOIN agent_tasks at ON at.id = ar.task_id
               WHERE ar.agent_id=?
               ORDER BY ar.started_at DESC LIMIT ?""",
            (agent_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["log"] = json.loads(d.get("log") or "[]")
        except Exception:
            d["log"] = []
        result.append(d)
    return result


def list_runs_for_task(task_id: str, limit: int = 10) -> list[dict]:
    """Recent run records for a task."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id=? ORDER BY run_number DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["log"] = json.loads(d.get("log") or "[]")
        except Exception:
            d["log"] = []
        result.append(d)
    return result


def get_agent_capabilities_list(agent_id: str) -> list[str]:
    """Capability names for an agent."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT capability FROM agent_capabilities WHERE agent_id=? AND enabled=1",
            (agent_id,),
        ).fetchall()
    return [r["capability"] for r in rows]


def upsert_agent_capabilities(agent_id: str, capabilities: list[str]) -> None:
    """ケイパビリティを全件置き換えする（冪等）。"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM agent_capabilities WHERE agent_id=?", (agent_id,)
        )
        for cap in capabilities:
            conn.execute(
                """INSERT INTO agent_capabilities (agent_id, capability, enabled, config)
                   VALUES (?,?,1,'{}')""",
                (agent_id, cap),
            )


def get_agent_brand_assignments(agent_id: str) -> list[dict]:
    """Brand assignments with brand metadata."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT aa.is_primary, b.id, b.name, b.slug, b.short_name, b.color
               FROM agent_assignments aa
               JOIN brands b ON b.id = aa.brand_id
               WHERE aa.agent_id=?
               ORDER BY aa.is_primary DESC""",
            (agent_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_approval_for_task(task_id: str) -> dict | None:
    """Pending approval + steps for a task."""
    with get_conn() as conn:
        appr = conn.execute(
            "SELECT * FROM approvals WHERE task_id=? AND status='pending' LIMIT 1",
            (task_id,),
        ).fetchone()
        if not appr:
            return None
        steps = conn.execute(
            """SELECT aps.*, u.name as approver_name
               FROM approval_steps aps
               JOIN users u ON u.id = aps.approver_user_id
               WHERE aps.approval_id=?
               ORDER BY aps.step_order""",
            (appr["id"],),
        ).fetchall()
    d = dict(appr)
    d["steps"] = [dict(s) for s in steps]
    return d


def list_escalations_for_agent(agent_id: str,
                                status: str = "open") -> list[dict]:
    """Open escalations attributed to an agent."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.*, at.title as task_title
               FROM escalations e
               JOIN agent_tasks at ON at.id = e.task_id
               WHERE e.agent_id=? AND e.status=?
               ORDER BY e.created_at DESC""",
            (agent_id, status),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["context"] = json.loads(d.get("context") or "{}")
        except Exception:
            d["context"] = {}
        result.append(d)
    return result


def list_all_escalations_rich(status: str = "open") -> list[dict]:
    """All escalations with task title and agent name."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.*,
                      at.title  AS task_title,
                      u.name    AS agent_user_name,
                      aa.agent_type
               FROM escalations e
               JOIN agent_tasks at ON at.id = e.task_id
               LEFT JOIN ai_agents aa ON aa.id = e.agent_id
               LEFT JOIN users u ON u.id = aa.user_id
               WHERE e.status=?
               ORDER BY e.created_at DESC""",
            (status,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["context"] = json.loads(d.get("context") or "{}")
        except Exception:
            d["context"] = {}
        result.append(d)
    return result


def list_all_approvals_rich(status: str = "pending") -> list[dict]:
    """All approvals with task info and step summary."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT a.*,
                      at.title AS task_title,
                      at.mode  AS task_mode,
                      at.priority AS task_priority
               FROM approvals a
               JOIN agent_tasks at ON at.id = a.task_id
               WHERE a.status=?
               ORDER BY a.created_at DESC""",
            (status,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        with get_conn() as conn2:
            steps = conn2.execute(
                """SELECT aps.*, u.name AS approver_name
                   FROM approval_steps aps
                   JOIN users u ON u.id = aps.approver_user_id
                   WHERE aps.approval_id=?
                   ORDER BY aps.step_order""",
                (d["id"],),
            ).fetchall()
        d["steps"] = [dict(s) for s in steps]
        result.append(d)
    return result


def get_agent_with_user(agent_id: str) -> dict | None:
    """ai_agents joined with users for display name."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT aa.*, u.name AS display_name, u.role_id
               FROM ai_agents aa
               JOIN users u ON u.id = aa.user_id
               WHERE aa.id=?""",
            (agent_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["config"] = json.loads(d.get("config") or "{}")
    return d


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_org_db()
    print("組織DBスキーマ初期化完了")
