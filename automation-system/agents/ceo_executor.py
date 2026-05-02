"""
AI CEO Executor
===============
Claude Sonnet を使って AI CEO が現在のシステム状態を分析し、
各専門エージェントへタスクを自律的に割り当てる。

スケジューラーから呼ばれる:
  - 毎朝 05:30: 全ブランドの状態を分析し当日タスクを生成
  - 毎時 (オプション): キュー残量が少ない場合に補充

エントリーポイント:
  from agents.ceo_executor import run_ceo_dispatch
  run_ceo_dispatch()
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic
import org_database as db
from agents.task_service import create_task

log = logging.getLogger(__name__)

CEO_AGENT_ID = "ai-ceo"
CEO_MODEL    = "claude-sonnet-4-6"

# ── システム状態収集 ────────────────────────────────────────────

def _gather_system_state() -> dict[str, Any]:
    """DB から現在のシステム状態をまとめて返す"""
    agents        = db.list_ai_agents(active_only=True)
    task_counts   = db.get_task_counts_all_agents()
    queued_tasks  = db.list_tasks(status="queued",  limit=30)
    idle_tasks    = db.list_tasks(status="idle",    limit=20)
    running_tasks = db.list_tasks(status="running", limit=20)
    failed_tasks  = db.list_tasks(status="failed",  limit=10)
    escalations   = db.list_escalations(status="open")
    brands        = db.list_brands()

    return {
        "timestamp":    datetime.now().isoformat(),
        "brands":       [{"id": b["id"], "name": b["name"], "slug": b["slug"]} for b in brands],
        "agents":       [
            {
                "id":         a["id"],
                "name":       a.get("name", a["id"]),
                "agent_type": a.get("agent_type", ""),
                "model":      a.get("model", ""),
                "counts":     task_counts.get(a["id"], {}),
            }
            for a in agents
        ],
        "task_counts": {
            "queued":  len(queued_tasks),
            "idle":    len(idle_tasks),
            "running": len(running_tasks),
            "failed":  len(failed_tasks),
        },
        "queued_tasks": [
            {"id": t["id"], "title": t["title"], "agent_id": t.get("assigned_to_agent_id"), "priority": t.get("priority")}
            for t in queued_tasks[:10]
        ],
        "failed_tasks": [
            {"id": t["id"], "title": t["title"], "error": t.get("error_message", "")[:120]}
            for t in failed_tasks
        ],
        "open_escalations": [
            {"id": e["id"], "reason": e.get("reason", "")[:120]}
            for e in escalations
        ],
    }


# ── CEOが使うツール定義 ─────────────────────────────────────────

CEO_TOOLS = [
    {
        "name": "create_agent_task",
        "description": (
            "Create a task and assign it to a specific agent. "
            "Use this to dispatch work to content agents, sales agents, analytics agents, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short task title (Japanese OK)"
                },
                "agent_id": {
                    "type": "string",
                    "description": "Target agent ID (e.g. agent-content-upj, agent-sales, agent-analytics, agent-blog, agent-ops, agent-content-dsc, agent-content-cfj, agent-content-bpg)"
                },
                "description": {
                    "type": "string",
                    "description": "Detailed instructions for the agent. Be specific about what to do."
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority 1(critical)-10(low). Default 5.",
                    "minimum": 1,
                    "maximum": 10
                },
                "brand_slug": {
                    "type": "string",
                    "description": "Target brand slug (upj, dsc, cfj, bangkok-peach, satoshi-blog) — leave empty for cross-brand tasks"
                },
                "mode": {
                    "type": "string",
                    "enum": ["full_auto", "semi_auto", "human_approval_required"],
                    "description": "Execution mode. full_auto=no human review, semi_auto=agent runs but human can override, human_approval_required=pause for president approval"
                },
            },
            "required": ["title", "agent_id", "description"]
        }
    },
    {
        "name": "send_president_notification",
        "description": "Send an important message to President Satoshi via LINE (use for escalations, budget approvals, strategic decisions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Message content in Japanese. Be concise and actionable."
                },
                "urgency": {
                    "type": "string",
                    "enum": ["high", "medium", "low"],
                    "description": "Urgency level"
                }
            },
            "required": ["message", "urgency"]
        }
    },
    {
        "name": "dispatch_done",
        "description": "Call this when you have finished dispatching all tasks for this cycle. Summarize what you did.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of decisions made this cycle (Japanese OK)"
                },
                "tasks_created": {
                    "type": "integer",
                    "description": "Number of tasks created"
                }
            },
            "required": ["summary", "tasks_created"]
        }
    }
]


# ── ツールハンドラ ──────────────────────────────────────────────

def _handle_create_agent_task(args: dict, created_ids: list[str]) -> str:
    agent_id   = args.get("agent_id", "")
    title      = args.get("title", "")
    description = args.get("description", "")
    priority   = int(args.get("priority", 5))
    brand_slug = args.get("brand_slug", "")
    mode       = args.get("mode", "semi_auto")

    # brand_id を slug から引く
    brand_id = ""
    if brand_slug:
        brand = db.get_brand_by_slug(brand_slug)
        if brand:
            brand_id = brand["id"]

    # CEO user ID を取得（requested_by として記録）
    ceo_user_id = ""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM ai_agents WHERE id=?", (CEO_AGENT_ID,)
        ).fetchone()
        if row:
            ceo_user_id = row["user_id"]

    task_id = create_task(
        title=title,
        mode=mode,
        assigned_to_agent_id=agent_id,
        requested_by_user_id=ceo_user_id,
        brand_id=brand_id,
        description=description,
        priority=priority,
    )

    # 即座にキューに投入
    from agents.task_service import enqueue
    enqueue(task_id)

    created_ids.append(task_id)
    log.info(f"CEO → task created: {task_id!r} [{agent_id}] {title!r} pri={priority}")
    return f"Task created: {task_id} (assigned to {agent_id})"


def _handle_send_president_notification(args: dict) -> str:
    message = args.get("message", "")
    urgency = args.get("urgency", "medium")
    title   = args.get("title", "")
    requires_approval = args.get("requires_approval", False)

    # ── ダッシュボード承認キューへ書き込み ─────────────────────
    try:
        import uuid as _uuid
        with db.get_conn() as conn:
            conn.execute(
                "INSERT INTO approvals (id, title, description,"
                " requested_by_agent_id, status, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    str(_uuid.uuid4()),
                    title or message[:60],
                    message,
                    CEO_AGENT_ID,
                    "pending",
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        log.info("承認依頼をダッシュボードキューに追加")
    except Exception as e:
        log.warning("承認依頼DB書き込み失敗: %s", e)

    # ── LINE プッシュ ────────────────────────────────────────
    try:
        import os
        from sns.line_api import LINEMessenger
        # 社長通知専用: ALERT_LINE_CHANNEL_ACCESS_TOKEN を優先 (OWNER_LINE_USER_ID のフレンドチャンネル)
        alert_token = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
        messenger = LINEMessenger(token=alert_token) if alert_token else LINEMessenger()
        owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if not owner_id:
            log.warning("OWNER_LINE_USER_ID未設定、LINE通知をスキップ")
            return "LINE通知スキップ（OWNER_LINE_USER_ID未設定）"
        prefix = {"high": "【緊急】", "medium": "【報告】", "low": "【情報】"}.get(urgency, "【報告】")
        approval_note = "\n\n📋 確認: http://localhost:5001/approvals" if requires_approval else ""
        full_msg = f"{prefix} AI CEO より\n\n{message}{approval_note}"
        ok = messenger.push(owner_id, full_msg)
        if ok:
            return "President notification sent via LINE + dashboard"
        return "LINE send attempted (check logs)"
    except Exception as e:
        log.warning(f"LINE push failed: {e}")
        return f"LINE unavailable: {e}"


# ── CEO ディスパッチメインループ ────────────────────────────────

def run_ceo_dispatch(dry_run: bool = False, president_instruction: str = "") -> dict:
    """
    AI CEO が現在のシステム状態を分析し、エージェントへタスクを割り当てる。

    Args:
        dry_run: True の場合 Claude API を呼ばずサンプルタスクを生成して返す
        president_instruction: 社長からの追加指示（空の場合は通常の日次ディスパッチ）

    Returns:
        dict: { tasks_created, summary, agent_decisions }
    """
    log.info(f"=== AI CEO dispatch 開始 (instruction={'あり' if president_instruction else 'なし'}) ===")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key and not dry_run:
        log.warning("ANTHROPIC_API_KEY が未設定。CEO dispatch をスキップ。")
        return {"tasks_created": 0, "summary": "API key missing", "agent_decisions": []}

    state = _gather_system_state()
    log.info(f"システム状態: agents={len(state['agents'])}, queued={state['task_counts']['queued']}, failed={state['task_counts']['failed']}")

    if dry_run:
        return _dry_run_dispatch(state)

    # ── Claude Sonnet で CEO 意思決定 ─────────────────────────────
    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = """あなたは UPJ Autonomous Brand OS の AI CEO です。
President（Satoshi）の代理として全ブランドの日次運営を自律的に統括します。

## ブランド一覧
- upjapan (UPJ): 事業設計・コンサルティング。Instagram/Threads/Facebook/Twitter/WordPress
- dsc-marketing (DSC): SNS集客支援。全チャンネル（Instagram/Threads/Facebook/Twitter/TikTok/YouTube/LINE/WordPress）
- cashflowsupport (CFJ): ファクタリング・資金繰り相談。Instagram/Facebook/LINE/WordPress ※金融コンプライアンス厳守
- bangkok-peach (BPG): バンコク拠点の国際事業。Instagram/Threads/Facebook/Twitter/TikTok/LINE/WordPress ※日英タイ3言語
- satoshi-blog: Satoshi個人ブログ。WordPress SEO記事

## エージェント一覧
- agent-content-upj: UPJブランドのSNSコンテンツ生成・投稿
- agent-content-dsc: DSCブランドの全チャンネルコンテンツ生成・投稿
- agent-content-cfj: CFJブランドのコンテンツ生成（金融コンプライアンス対応）
- agent-content-bpg: Bangkok Peachの多言語コンテンツ生成
- agent-blog: Satoshiブログの記事生成・SEO最適化
- agent-sales: 全ブランドのリード管理・フォローアップ
- agent-analytics: 全ブランドのパフォーマンス分析・レポート
- agent-ops: システム運用・監視・バックアップ

## あなたの役割（今日のサイクル）
1. 現在のシステム状態を把握する
2. 各ブランドで今日必要なコンテンツ投稿タスクを生成する
3. 失敗したタスクを分析して再試行または対応タスクを生成する
4. 未解決エスカレーションがあれば President に通知する
5. 分析タスクや営業フォローアップタスクを適切なエージェントに割り当てる
6. dispatch_done を呼んで終了する

## 重要ルール
- CFJのタスクは必ず description に「金融コンプライアンスチェック必須」を含める
- BPGのコンテンツタスクは「日本語・英語・タイ語の3言語で生成」を指示する
- 同じタスクを重複して作らない（既存のqueued/runningタスクを確認する）
- President への通知は緊急・重要なものに限定する（毎回送らない）
- 1サイクルで作るタスクは最大10件程度に絞る
"""

    # 社長指示がある場合は優先タスクセクションを追加
    president_section = ""
    if president_instruction:
        president_section = f"""
## 🎯 社長からの直接指示（最優先で対応してください）
{president_instruction}

この指示を最優先に解釈し、適切なエージェントへのタスクを必ず作成してください。
---
"""

    user_message = f"""現在のシステム状態（{state['timestamp']}）:
{president_section}
## ブランド
{json.dumps(state['brands'], ensure_ascii=False, indent=2)}

## エージェントとタスク件数
{json.dumps(state['agents'], ensure_ascii=False, indent=2)}

## タスク件数サマリー
- キュー中: {state['task_counts']['queued']}件
- アイドル: {state['task_counts']['idle']}件
- 実行中: {state['task_counts']['running']}件
- 失敗: {state['task_counts']['failed']}件

## キュー中タスク（上位10件）
{json.dumps(state['queued_tasks'], ensure_ascii=False, indent=2)}

## 失敗タスク
{json.dumps(state['failed_tasks'], ensure_ascii=False, indent=2)}

## 未解決エスカレーション
{json.dumps(state['open_escalations'], ensure_ascii=False, indent=2)}

---
{f'社長指示を最優先に処理した上で、' if president_instruction else ''}上記の状態を分析し、必要なタスクをエージェントに割り当ててください。
キューがほぼ空の場合は各ブランドのコンテンツ生成タスクを作成してください。
分析が完了したら dispatch_done を呼んで終了してください。"""

    messages = [{"role": "user", "content": user_message}]
    created_ids: list[str] = []
    agent_decisions: list[dict] = []
    final_summary = ""
    tasks_created = 0
    max_iterations = 15

    for iteration in range(max_iterations):
        response = client.messages.create(
            model=CEO_MODEL,
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=CEO_TOOLS,
            messages=messages,
        )

        log.debug(f"CEO iteration {iteration}: stop_reason={response.stop_reason}")

        # アシスタントターンを追加
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        # ツール呼び出しを処理
        tool_results = []
        for block in assistant_content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_args = block.input

            log.info(f"CEO tool: {tool_name} args={json.dumps(tool_args, ensure_ascii=False)[:200]}")

            if tool_name == "create_agent_task":
                result_str = _handle_create_agent_task(tool_args, created_ids)
                tasks_created += 1
                agent_decisions.append({
                    "tool":    tool_name,
                    "agent":   tool_args.get("agent_id"),
                    "title":   tool_args.get("title"),
                    "priority": tool_args.get("priority", 5),
                })

            elif tool_name == "send_president_notification":
                result_str = _handle_send_president_notification(tool_args)
                agent_decisions.append({
                    "tool":    tool_name,
                    "urgency": tool_args.get("urgency"),
                    "message": tool_args.get("message", "")[:80],
                })

            elif tool_name == "dispatch_done":
                final_summary = tool_args.get("summary", "")
                tasks_created = tool_args.get("tasks_created", tasks_created)
                result_str = "dispatch_done acknowledged"
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     result_str,
                })
                messages.append({"role": "user", "content": tool_results})
                log.info(f"CEO dispatch_done: {final_summary}")
                # ループを抜ける
                break
            else:
                result_str = f"Unknown tool: {tool_name}"

            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     result_str,
            })

        else:
            # dispatch_done が呼ばれなかった場合は次のイテレーションへ
            if tool_results:
                messages.append({"role": "user", "content": tool_results})
            continue

        # dispatch_done で break した場合はここへ
        break

    # DB にCEOの実行ログを保存
    try:
        with db.get_conn() as conn:
            import time
            run_id = f"ceo-run-{int(time.time())}"
            conn.execute(
                """INSERT OR IGNORE INTO agent_runs
                   (id, task_id, agent_id, status, started_at, completed_at, log)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    run_id, "", CEO_AGENT_ID, "completed",
                    state["timestamp"], datetime.now().isoformat(),
                    json.dumps({
                        "summary":      final_summary,
                        "tasks_created": tasks_created,
                        "decisions":    agent_decisions,
                    }, ensure_ascii=False),
                ),
            )
    except Exception as e:
        log.debug(f"CEO run log保存スキップ: {e}")

    log.info(f"=== AI CEO dispatch 完了: {tasks_created}タスク作成 ===")
    return {
        "tasks_created":   tasks_created,
        "summary":         final_summary,
        "agent_decisions": agent_decisions,
        "created_task_ids": created_ids,
    }


# ── Dry Run（APIキーなしでテスト） ──────────────────────────────

def _dry_run_dispatch(state: dict) -> dict:
    """APIキーなしでサンプルタスクを生成（開発・テスト用）"""
    log.info("CEO dry_run: サンプルタスクを生成")
    created_ids: list[str] = []
    sample_tasks = [
        {
            "title":       "UPJ Instagram投稿コンテンツ生成",
            "agent_id":    "agent-content-upj",
            "description": "今日のUPJ Instagram投稿を1件生成してキューに追加してください。テーマ: 事業設計のポイント。",
            "priority":    5,
            "brand_slug":  "upj",
            "mode":        "semi_auto",
        },
        {
            "title":       "DSC Threads・Twitter投稿生成",
            "agent_id":    "agent-content-dsc",
            "description": "DSc Marketing のThreads・Twitter用投稿を各1件生成してください。テーマ: SNS集客のコツ。",
            "priority":    5,
            "brand_slug":  "dsc",
            "mode":        "semi_auto",
        },
        {
            "title":       "全ブランド週次パフォーマンスレポート",
            "agent_id":    "agent-analytics",
            "description": "全ブランドの今週のSNSパフォーマンスを取得し、週次レポートを生成してください。",
            "priority":    6,
            "brand_slug":  "",
            "mode":        "full_auto",
        },
    ]

    for t in sample_tasks:
        try:
            task_id = create_task(
                title=t["title"],
                mode=t["mode"],
                assigned_to_agent_id=t["agent_id"],
                brand_id="",
                description=t["description"],
                priority=t["priority"],
            )
            from agents.task_service import enqueue
            enqueue(task_id)
            created_ids.append(task_id)
            log.info(f"dry_run task: {task_id} [{t['agent_id']}] {t['title']!r}")
        except Exception as e:
            log.error(f"dry_run task 作成失敗: {e}")

    return {
        "tasks_created":    len(created_ids),
        "summary":          "Dry run: サンプルタスクを生成しました",
        "agent_decisions":  [{"title": t["title"], "agent": t["agent_id"]} for t in sample_tasks],
        "created_task_ids": created_ids,
    }


# ── CLI 実行 ────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    parser = argparse.ArgumentParser(description="AI CEO Executor")
    parser.add_argument("--dry-run", action="store_true", help="Claude API を呼ばずテスト実行")
    args = parser.parse_args()

    result = run_ceo_dispatch(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
