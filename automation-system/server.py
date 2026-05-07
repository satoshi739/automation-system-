
from __future__ import annotations

"""
LINE Webhook サーバー
- LINEからのメッセージを受け取り、自動返信する
- 新規ユーザーのリードを自動起票する

起動方法:
  python server.py

外部公開が必要:
  ngrok http 5000  （開発・テスト用）
  または Render / Railway にデプロイ（本番用）
"""

import logging
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from flask import Flask, abort, request, send_from_directory

from sales.lead_intake import create_lead_from_line, load_lead_by_line_id
from sns.line_api import LINEMessenger

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
_messenger: LINEMessenger | None = None
_bpg_messenger: LINEMessenger | None = None

SCENARIOS_PATH   = Path(__file__).parent / "config" / "line_scenarios.yaml"
HEARTBEAT_FILE   = Path(__file__).parent / "logs" / "scheduler.heartbeat"
ALERTS_LOG       = Path(__file__).parent / "logs" / "alerts.log"
_DEDUP_FLAG      = Path(__file__).parent / "logs" / ".server_alert_scheduler_dead.sent"
_HEARTBEAT_THRESHOLD = 600   # 10分を超えたら異常
_MONITOR_INTERVAL    = 300   # 5分ごとにチェック


# ── heartbeat 監視 ────────────────────────────────────────────


def _server_alert(message: str) -> None:
    """alerts.log への書き込み + Mac通知 + LINEプッシュ（server_check 発信）。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ALERTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] [server_check] {message}\n")
    except Exception as exc:
        logger.error("alerts.log 書き込み失敗: %s", exc)
    try:
        safe_msg = message.replace('"', "'").replace("\n", " ").replace("\r", " ")[:100]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "server_check alert"'],
            timeout=5, capture_output=True,
        )
    except Exception as exc:
        logger.error("Mac通知失敗: %s", exc)
    try:
        import requests as _req
        token = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
        user_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if token and user_id:
            _req.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"to": user_id, "messages": [{"type": "text", "text": f"[システムアラート]\n{message}"}]},
                timeout=5,
            )
    except Exception as exc:
        logger.error("LINE アラート送信失敗: %s", exc)
    logger.warning("ALERT: %s", message)


def _check_heartbeat() -> None:
    """scheduler.heartbeat の鮮度を確認し、古ければアラートを送る。"""
    if not HEARTBEAT_FILE.exists():
        if not _DEDUP_FLAG.exists():
            _DEDUP_FLAG.touch()
            _server_alert("scheduler停止検知 (heartbeatファイルなし)")
        return

    age = time.time() - HEARTBEAT_FILE.stat().st_mtime
    if age > _HEARTBEAT_THRESHOLD:
        if not _DEDUP_FLAG.exists():
            _DEDUP_FLAG.touch()
            _server_alert(f"scheduler停止検知 (age: {int(age // 60)}分)")
    else:
        # 回復 → dedup フラグを消して次の障害で再アラート可能にする
        if _DEDUP_FLAG.exists():
            _DEDUP_FLAG.unlink()
            logger.info("scheduler heartbeat 回復を確認")


def _heartbeat_monitor_loop() -> None:
    """5分おきに heartbeat をチェックし続けるループ（daemon thread で動く）。"""
    while True:
        try:
            _check_heartbeat()
        except Exception as exc:
            logger.error("heartbeat監視ループで予期せぬ例外: %s", exc)
        time.sleep(_MONITOR_INTERVAL)


# Railway本番では dashboard/app.py の startup() が同等の監視を担う。
# このserver.pyはローカル直接起動（python server.py）専用。
def _start_heartbeat_monitor() -> None:
    t = threading.Thread(
        target=_heartbeat_monitor_loop,
        name="heartbeat-monitor",
        daemon=True,
    )
    t.start()
    logger.info(
        "heartbeat監視を開始しました (threshold=%ds, interval=%ds)",
        _HEARTBEAT_THRESHOLD, _MONITOR_INTERVAL,
    )


def _get_messenger() -> LINEMessenger:
    global _messenger
    if _messenger is None:
        _messenger = LINEMessenger()
    return _messenger


def _get_bpg_messenger() -> LINEMessenger:
    global _bpg_messenger
    if _bpg_messenger is None:
        _bpg_messenger = LINEMessenger(
            token=os.environ.get("BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN", ""),
            secret=os.environ.get("BANGKOK_PEACH_LINE_CHANNEL_SECRET", ""),
        )
    return _bpg_messenger


def _load_scenarios() -> dict:
    with open(SCENARIOS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _find_keyword_reply(message: str, scenarios: dict) -> str | None:
    """メッセージにマッチするキーワード返信を探す"""
    for item in scenarios.get("keyword_replies", []):
        for kw in item.get("keywords", []):
            if kw in message:
                return item["reply"]
    return None


@app.route("/webhook", methods=["POST"])
def webhook():
    # 署名検証
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()
    if not _get_messenger().verify_signature(body, signature):
        logger.warning(
            "署名検証失敗 — body_len=%d sig_prefix=%s",
            len(body),
            signature[:10] if signature else "empty",
        )
        abort(400)

    data = request.get_json()
    if data is None:
        abort(400)
    scenarios = _load_scenarios()

    for event in data.get("events", []):
        event_type = event.get("type")
        source = event.get("source", {})
        user_id = source.get("userId", "")

        # --- 友だち追加 ---
        if event_type == "follow":
            _handle_follow(user_id, scenarios)

        # --- メッセージ受信 ---
        elif event_type == "message" and event.get("message", {}).get("type") == "text":
            text = event["message"].get("text", "")
            reply_token = event.get("replyToken", "")
            _handle_message(user_id, text, reply_token, scenarios)

    return "OK"


@app.route("/webhook/bpg", methods=["POST"])
def webhook_bpg():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()
    if not _get_bpg_messenger().verify_signature(body, signature):
        logger.warning(
            "BPG 署名検証失敗 — body_len=%d sig_prefix=%s",
            len(body),
            signature[:10] if signature else "empty",
        )
        abort(400)

    data = request.get_json()
    if data is None:
        abort(400)
    scenarios = _load_scenarios()

    for event in data.get("events", []):
        event_type = event.get("type")
        user_id = event.get("source", {}).get("userId", "")

        if event_type == "follow":
            _handle_follow(user_id, scenarios, messenger=_get_bpg_messenger())
        elif event_type == "message" and event["message"]["type"] == "text":
            _handle_message(
                user_id, event["message"]["text"],
                event.get("replyToken", ""), scenarios,
                messenger=_get_bpg_messenger(),
            )

    return "OK"


def _handle_follow(user_id: str, scenarios: dict, messenger: LINEMessenger | None = None):
    """友だち追加時の処理"""
    messenger = messenger or _get_messenger()
    profile = messenger.get_profile(user_id)
    display_name = profile.get("displayName", "")

    # ウェルカムメッセージを送信
    welcome = scenarios.get("welcome_message", "ご登録ありがとうございます！")
    messenger.push(user_id, welcome)
    logger.info(f"ウェルカムメッセージ送信: {display_name} ({user_id})")


def _is_owner(user_id: str) -> bool:
    """OWNER_LINE_USER_ID と一致するオーナーか確認"""
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    return bool(owner_id and user_id == owner_id)


def _get_status_message() -> str:
    """オーケストレーターの現在状態をLINE向けサマリーに整形して返す"""
    try:
        import sys
        _base = str(Path(__file__).parent)
        if _base not in sys.path:
            sys.path.insert(0, _base)
        from agents.orchestrator import get_overview

        ov = get_overview()
        sc = ov.get("status_counts", {})
        lines = [
            "📊 システム状態",
            "",
            f"🟢 完了: {sc.get('completed', 0)}件",
            f"🔄 実行中: {sc.get('running', 0)}件",
            f"📥 キュー: {sc.get('queued', 0)}件",
            f"⏳ アイドル: {sc.get('idle', 0)}件",
            f"❌ 失敗: {sc.get('failed', 0)}件",
            f"🚨 エスカレーション: {ov.get('open_escalations', 0)}件",
            f"⏸️ 承認待ち: {ov.get('pending_approvals', 0)}件",
            "",
            f"本日 作成: {ov.get('today_created', 0)}件 / 完了: {ov.get('today_completed', 0)}件",
            f"成功率: {ov.get('success_rate', 0)}%",
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.error("get_status_message エラー: %s", exc)
        return f"⚠️ 状態取得エラー: {exc}"


def _run_ceo_dispatch_async(instruction: str, push_fn) -> None:
    """
    バックグラウンドスレッドで CEO dispatch を実行し、
    完了後に push_fn(message) で結果をプッシュする。
    """
    try:
        import sys
        _base = str(Path(__file__).parent)
        if _base not in sys.path:
            sys.path.insert(0, _base)
        from agents.ceo_executor import run_ceo_dispatch

        result = run_ceo_dispatch(president_instruction=instruction)
        tasks_n  = result.get("tasks_created", 0)
        summary  = result.get("summary", "")
        decisions = result.get("agent_decisions", [])

        lines = ["✅ AI CEO 実行完了", "", f"タスク作成: {tasks_n}件"]
        if summary:
            lines += ["", "サマリー:", summary]
        if decisions:
            lines += ["", "割り当て:"]
            for d in decisions[:5]:
                if d.get("tool") == "create_agent_task":
                    lines.append(f"  • [{d.get('agent','')}] {d.get('title','')}")
        push_fn("\n".join(lines))
    except Exception as exc:
        logger.error("CEO dispatch 非同期エラー: %s", exc, exc_info=True)
        push_fn(f"❌ CEO dispatch エラー:\n{str(exc)[:200]}")


def _handle_owner_message(user_id: str, text: str, reply_token: str, messenger: LINEMessenger) -> None:
    """
    オーナー（サトシ）専用ルート。
      @ceo <指示>  → AI CEO に president_instruction として渡す
      @status      → システム状態サマリーを即返答
      その他        → ヘルプを返す
    """
    stripped = text.strip()

    # ── @ceo: CEO直通指示 ─────────────────────────────────
    if stripped.lower().startswith("@ceo "):
        instruction = stripped[5:].strip()
        if not instruction:
            messenger.reply(reply_token,
                "指示内容を入力してください。\n例: @ceo 今日のDSC Instagram投稿を3件生成して")
            return

        preview = instruction[:50] + ("…" if len(instruction) > 50 else "")
        messenger.reply(reply_token,
            f"🎯 AI CEO に指示を送りました\n\n「{preview}」\n\n処理完了後に結果をお送りします。")

        def _push(msg: str):
            messenger.push(user_id, msg)

        threading.Thread(
            target=_run_ceo_dispatch_async,
            args=(instruction, _push),
            daemon=True,
            name="ceo-dispatch",
        ).start()
        logger.info("CEO dispatch started by owner: %s", preview)

    # ── @status: システム状態確認 ─────────────────────────
    elif stripped.lower().startswith("@status"):
        messenger.reply(reply_token, _get_status_message())
        logger.info("@status requested by owner")

    # ── ヘルプ ────────────────────────────────────────────
    else:
        messenger.reply(reply_token,
            "👤 オーナーモード\n\n"
            "コマンド:\n"
            "• @ceo [指示] — AI CEOに直接指示\n"
            "• @status — システム状態確認\n\n"
            "例:\n"
            "@ceo 今日のUPJ投稿を3件生成して\n"
            "@ceo 失敗タスクをリトライして"
        )


def _handle_message(user_id: str, text: str, reply_token: str, scenarios: dict, messenger: LINEMessenger | None = None):
    """メッセージ受信時の処理"""
    messenger = messenger or _get_messenger()

    # ── オーナーからのメッセージ → CEO直通ルート ──────────
    if _is_owner(user_id):
        _handle_owner_message(user_id, text, reply_token, messenger)
        return

    # ── 一般ユーザー: 既存リードか確認 ───────────────────
    existing = load_lead_by_line_id(user_id)
    if not existing:
        # 新規リード → 自動起票
        profile = messenger.get_profile(user_id)
        display_name = profile.get("displayName", "")
        lead_path = create_lead_from_line(user_id, display_name, text)
        logger.info(f"新規リード起票: {lead_path}")

    # キーワード返信を探す
    reply = _find_keyword_reply(text, scenarios)
    if reply:
        messenger.reply(reply_token, reply)
    else:
        # デフォルト返信
        default_reply = (
            "メッセージありがとうございます！\n"
            "内容を確認して、担当者からご返信します。\n"
            "（平日10:00〜17:00 受付）"
        )
        messenger.reply(reply_token, default_reply)


# ── Nano Banana マルチページ ──────────────────────────────────────

_CONTENT_DIR = Path(__file__).parent / "content_queue" / "instagram"
_REVIEW_DIR  = Path(__file__).parent / "content_queue" / "review"

BRAND_CONFIG = {
    "satoshi-blog":               {"label": "Satoshi Blog",     "color": "#FF6B35", "emoji": "✍️"},
    "bangkok-peach":              {"label": "Bangkok Peach",    "color": "#FF69B4", "emoji": "🌸"},
    "cashflowsupport":            {"label": "CashFlow Support", "color": "#00C896", "emoji": "💰"},
    "dsc-marketing":              {"label": "DSC Marketing",    "color": "#00BFFF", "emoji": "📊"},
    "cfj-marketing":              {"label": "CFJ Marketing",    "color": "#F39C12", "emoji": "📋"},
    "upjapan":                    {"label": "UPJ",              "color": "#9B59B6", "emoji": "🏢"},
    "upj":                        {"label": "UPJ",              "color": "#9B59B6", "emoji": "🏢"},
    "upj-universal-planet-japan": {"label": "UPJ",              "color": "#9B59B6", "emoji": "🏢"},
}


def _brand_cfg(slug: str) -> dict:
    return BRAND_CONFIG.get(slug, {"label": slug, "color": "#888", "emoji": "📁"})


def _load_queue_items() -> list:
    """全YAMLを (filename, data) リストで返す"""
    items = []
    for f in sorted(_CONTENT_DIR.glob("*.yaml")):
        if f.name == "README.md":
            continue
        try:
            with open(f, encoding="utf-8") as fp:
                data = yaml.safe_load(fp)
            if isinstance(data, dict):
                items.append((f.name, data))
        except Exception:
            pass
    return items


def _build_ga4_html(ga4_data: list) -> str:
    if not ga4_data:
        return '<p style="color:#555;font-size:.85rem">GA4データなし</p>'
    rows = ""
    for d in ga4_data:
        color = d["color"]
        label = d["label"]
        emoji = d["emoji"]
        status = d["status"]
        if status == "ok":
            status_html = '<span style="color:#00ff88;font-size:.75rem">● 接続済み</span>'
            sessions   = f'{d["sessions"]:,}'
            pageviews  = f'{d["pageviews"]:,}'
            users      = f'{d["users"]:,}'
            bounce     = f'{d["bounce_rate"]}%'
            dur        = f'{d["avg_duration"]}分'
        elif status == "no_permission":
            status_html = '<span style="color:#ff4466;font-size:.75rem">● 権限なし</span>'
            sessions = pageviews = users = bounce = dur = '<span style="color:#555">—</span>'
        elif status == "unset":
            status_html = '<span style="color:#555;font-size:.75rem">○ 未設定</span>'
            sessions = pageviews = users = bounce = dur = '<span style="color:#555">—</span>'
        else:
            status_html = f'<span style="color:#ff4466;font-size:.75rem">● エラー</span>'
            sessions = pageviews = users = bounce = dur = '<span style="color:#555">—</span>'
        rows += f"""
        <tr>
          <td><span style="color:{color};font-weight:700">{emoji} {label}</span></td>
          <td>{status_html}</td>
          <td style="text-align:right">{sessions}</td>
          <td style="text-align:right">{pageviews}</td>
          <td style="text-align:right">{users}</td>
          <td style="text-align:right">{bounce}</td>
          <td style="text-align:right">{dur}</td>
        </tr>"""
    return f"""<table>
  <tr>
    <th>ブランド</th>
    <th>状態</th>
    <th style="text-align:right">セッション</th>
    <th style="text-align:right">PV</th>
    <th style="text-align:right">ユーザー</th>
    <th style="text-align:right">直帰率</th>
    <th style="text-align:right">平均滞在</th>
  </tr>
  {rows}
</table>"""


_BRAND_GA4_ENV = {
    "upjapan":       "UPJAPAN_GA4_PROPERTY_ID",
    "dsc-marketing": "DSC_MARKETING_GA4_PROPERTY_ID",
    "cashflowsupport": "CASHFLOWSUPPORT_GA4_PROPERTY_ID",
    "satoshi-blog":  "SATOSHI_BLOG_GA4_PROPERTY_ID",
    "bangkok-peach": "BANGKOK_PEACH_GA4_PROPERTY_ID",
}

def _get_ga4_all() -> list:
    """全ブランドのGA4サマリーを返す。接続不可は statusフィールドで表現する。"""
    import os as _os
    results = []
    try:
        from sns.analytics import GA4Client
    except Exception:
        return results
    for slug, env_key in _BRAND_GA4_ENV.items():
        prop_id = _os.environ.get(env_key, "")
        cfg = _brand_cfg(slug)
        entry = {"slug": slug, "label": cfg["label"], "color": cfg["color"], "emoji": cfg["emoji"]}
        if not prop_id:
            entry.update({"status": "unset"})
        else:
            data = GA4Client(property_id_env=env_key).get_overview(28)
            if "error" in data:
                msg = data["error"]
                if "403" in msg:
                    entry.update({"status": "no_permission"})
                else:
                    entry.update({"status": "error", "error_msg": msg[:80]})
            else:
                entry.update({"status": "ok", **data})
        results.append(entry)
    return results


def _get_brand_counts() -> dict:
    from collections import defaultdict
    counts = defaultdict(lambda: {"total": 0, "pending": 0, "needs_review": 0, "posted": 0})
    for _fname, d in _load_queue_items():
        brand = d.get("brand", "unknown")
        counts[brand]["total"] += 1
        if d.get("posted"):
            counts[brand]["posted"] += 1
        elif d.get("needs_review"):
            counts[brand]["needs_review"] += 1
        else:
            counts[brand]["pending"] += 1
    return dict(counts)


_NB_CSS = """
:root {
  --banana:#FFD700;--banana-dim:#c9a900;
  --bg:#0a0a0a;--surface:#111;--surface2:#1a1a1a;
  --text:#f0f0f0;--muted:#888;
  --green:#00ff88;--red:#ff4466;--cyan:#00d4ff;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Segoe UI','Helvetica Neue',Arial,sans-serif;min-height:100vh}
a{color:var(--banana);text-decoration:none}
a:hover{text-decoration:underline}
.header{background:linear-gradient(135deg,#111 0%,#1a1500 100%);border-bottom:2px solid var(--banana);padding:16px 28px;display:flex;align-items:center;gap:12px}
.header-title{font-size:1.3rem;font-weight:900;color:var(--banana);letter-spacing:2px;text-transform:uppercase}
.header-sub{color:var(--muted);font-size:.78rem;margin-top:2px}
.breadcrumbs{padding:10px 28px;background:#0d0d0d;border-bottom:1px solid #1a1a1a;font-size:.82rem;display:flex;align-items:center;gap:6px}
.breadcrumbs a{color:var(--muted)}
.breadcrumbs a:hover{color:var(--banana)}
.breadcrumbs span{color:var(--text)}
.breadcrumbs .sep{color:#333}
.page-body{padding:24px 28px}
.section-title{font-size:.7rem;color:var(--banana-dim);text-transform:uppercase;letter-spacing:2px;border-bottom:1px solid #222;padding-bottom:8px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-bottom:24px}
.card{background:var(--surface);border:1px solid #222;border-radius:10px;padding:18px;position:relative;overflow:hidden;cursor:pointer;transition:border-color .2s}
.card:hover{border-color:var(--banana)}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:var(--banana)}
.card-label{font-size:.68rem;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}
.card-value{font-size:2rem;font-weight:900;color:var(--banana);line-height:1}
.card-sub{font-size:.75rem;color:var(--muted);margin-top:5px}
.brand-tag{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;border-radius:8px;border:1px solid;font-size:.85rem;font-weight:600;cursor:pointer;text-decoration:none;transition:opacity .2s}
.brand-tag:hover{opacity:.8;text-decoration:none}
.tags-row{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:24px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:600}
.badge-pending{background:#1a2a1a;color:var(--green);border:1px solid var(--green)}
.badge-review{background:#2a1a0a;color:#F39C12;border:1px solid #F39C12}
.badge-posted{background:#1a1a2a;color:var(--cyan);border:1px solid var(--cyan)}
.badge-error{background:#2a1a1a;color:var(--red);border:1px solid var(--red)}
table{width:100%;border-collapse:collapse;font-size:.86rem}
th{text-align:left;color:var(--muted);font-size:.68rem;text-transform:uppercase;letter-spacing:1px;padding:7px 10px;border-bottom:1px solid #222}
td{padding:10px 10px;border-bottom:1px solid #191919;vertical-align:top}
tr:hover td{background:#141414}
.caption-preview{color:var(--text);line-height:1.5;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical}
.btn{display:inline-block;padding:8px 18px;border-radius:6px;font-size:.85rem;font-weight:600;border:none;cursor:pointer;transition:opacity .2s}
.btn:hover{opacity:.8}
.btn-approve{background:var(--green);color:#000}
.btn-reject{background:var(--red);color:#fff}
.btn-outline{background:transparent;border:1px solid var(--banana);color:var(--banana)}
.content-box{background:var(--surface);border:1px solid #222;border-radius:10px;padding:20px;margin-bottom:16px}
.content-caption{white-space:pre-wrap;line-height:1.7;font-size:.9rem;color:var(--text)}
.img-preview{max-width:100%;max-height:400px;border-radius:8px;border:1px solid #222;margin:12px 0}
.footer{text-align:center;color:var(--muted);font-size:.72rem;padding:28px;border-top:1px solid #1a1a1a;margin-top:32px}
@media(max-width:600px){.page-body{padding:16px}.header{padding:14px 16px}}
"""


def _nb_page(title: str, body: str, breadcrumbs: list = None) -> str:
    """Nano Banana スタイルのフル HTML ページを返す。"""
    bc_html = ""
    if breadcrumbs:
        parts = []
        for label, url in breadcrumbs:
            if url:
                parts.append(f'<a href="{url}">{label}</a>')
            else:
                parts.append(f'<span>{label}</span>')
        bc_html = f'<div class="breadcrumbs">{"<span class=sep>›</span>".join(parts)}</div>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🍌 {title} — Nano Banana OS</title>
<style>{_NB_CSS}</style>
</head>
<body>
<div class="header">
  <a href="/" style="text-decoration:none;display:flex;align-items:center;gap:12px">
    <span style="font-size:1.8rem">🍌</span>
    <div>
      <div class="header-title">Nano Banana OS</div>
      <div class="header-sub">UPJ Autonomous Brand OS</div>
    </div>
  </a>
  <div style="margin-left:auto;color:var(--muted);font-size:.8rem">{datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div>
{bc_html}
<div class="page-body">
{body}
</div>
<div class="footer">🍌 Nano Banana OS · Claude Sonnet 4.6</div>
</body>
</html>"""


# ─────────────────────────────────────────────────────────────────


@app.route("/", methods=["GET"])
def index():
    from flask import Response
    from pathlib import Path as _Path
    import sys as _sys
    _sys.path.insert(0, str(_Path(__file__).parent))

    # ── データ収集 ──────────────────────────────────────────────
    try:
        from agents.orchestrator import get_overview
        ov = get_overview()
        sc = ov.get("status_counts", {})
        tasks_completed = sc.get("completed", 0)
        tasks_running   = sc.get("running", 0)
        tasks_queued    = sc.get("queued", 0)
        tasks_failed    = sc.get("failed", 0)
    except Exception:
        tasks_completed = tasks_running = tasks_queued = tasks_failed = 0

    try:
        from api_cost_tracker import get_cost_summary
        cost = get_cost_summary(30)
        total_cost  = cost["total_cost_usd"]
        monthly_est = cost["monthly_est_usd"]
        total_tokens = cost["total_tokens"]
        by_agent = cost["by_agent"][:6]
    except Exception:
        total_cost = monthly_est = 0.0
        total_tokens = 0
        by_agent = []

    # Instagramキュー数
    try:
        insta_dir = _Path(__file__).parent / "content_queue" / "instagram"
        insta_count = len(list(insta_dir.glob("*.yaml")))
    except Exception:
        insta_count = 0

    # agent_runs 件数
    try:
        import org_database as _db
        with _db.get_conn() as _conn:
            agent_run_count = _conn.execute("SELECT COUNT(*) as c FROM agent_runs").fetchone()["c"]
    except Exception:
        agent_run_count = 0

    # GA4アナリティクス
    ga4_data = _get_ga4_all()

    # スケジューラー死活
    hb_path = _Path(__file__).parent / "logs" / "scheduler.heartbeat"
    import time as _time
    sched_alive = hb_path.exists() and (_time.time() - hb_path.stat().st_mtime < 700)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _appr_cnt = _approval_count()

    # ── エージェント行生成 ─────────────────────────────────────
    agent_rows = ""
    for a in by_agent:
        m = a["model"].replace("claude-", "").replace("-20251001", "")
        badge = "🟡 Sonnet" if "sonnet" in a["model"] else "🟢 Haiku"
        agent_rows += f"""
        <tr>
          <td>{a['agent_id']}</td>
          <td><span class="badge">{badge}</span></td>
          <td>{a['runs']}回</td>
          <td>{a['tokens']:,}</td>
          <td>${a['cost_usd']:.4f}</td>
        </tr>"""

    sched_color = "#00ff88" if sched_alive else "#ff4466"
    sched_label = "稼働中" if sched_alive else "停止中"

    # ── ブランドタグ生成 ─────────────────────────────────────────
    brand_counts = _get_brand_counts()
    # UPJ系スラッグを統合表示（重複ラベルは最初の1件のみ）
    seen_labels = set()
    brand_tags_html = ""
    for slug, c in sorted(brand_counts.items(), key=lambda x: -x[1]["total"]):
        cfg = _brand_cfg(slug)
        label_key = cfg["label"]
        if label_key in seen_labels:
            continue
        seen_labels.add(label_key)
        # 同じラベルを持つスラッグをまとめてカウント
        total = sum(
            v["total"] for s, v in brand_counts.items()
            if _brand_cfg(s)["label"] == label_key
        )
        pending = sum(
            v["pending"] for s, v in brand_counts.items()
            if _brand_cfg(s)["label"] == label_key
        )
        review = sum(
            v["needs_review"] for s, v in brand_counts.items()
            if _brand_cfg(s)["label"] == label_key
        )
        brand_tags_html += (
            f'<a href="/brands/{slug}" class="brand-tag" '
            f'style="color:{cfg["color"]};border-color:{cfg["color"]}">'
            f'{cfg["emoji"]} {cfg["label"]} <strong>{total}</strong>'
            f'</a>'
        )

    body = f"""
<div class="section-title">システム状態</div>
<div class="grid">
  <div class="card" style="--accent:var(--green)">
    <div class="card-label">完了タスク</div>
    <div class="card-value" style="color:var(--green)">{tasks_completed}</div>
    <div class="card-sub">累計実行済み</div>
  </div>
  <div class="card">
    <div class="card-label">キュー中</div>
    <div class="card-value">{tasks_queued}</div>
    <div class="card-sub">実行待ち</div>
  </div>
  <div class="card">
    <div class="card-label">実行中</div>
    <div class="card-value">{tasks_running}</div>
    <div class="card-sub">処理中タスク</div>
  </div>
  <div class="card">
    <div class="card-label">失敗</div>
    <div class="card-value" style="color:{'var(--red)' if tasks_failed > 0 else 'var(--banana)'}">{tasks_failed}</div>
    <div class="card-sub">要確認タスク</div>
  </div>
  <div class="card">
    <div class="card-label">Instagramキュー</div>
    <div class="card-value" style="color:var(--cyan)">{insta_count}</div>
    <div class="card-sub">投稿待ちコンテンツ</div>
  </div>
  <div class="card">
    <div class="card-label">エージェント実行</div>
    <div class="card-value">{agent_run_count}</div>
    <div class="card-sub">累計ラン数</div>
  </div>
  <a href="/approvals" style="text-decoration:none" id="approvals-card">
  <div class="card" style="border-color:{'#ff4466' if _appr_cnt>0 else '#222'}">
    <div class="card-label">承認待ち</div>
    <div class="card-value" style="color:{'var(--red)' if _appr_cnt>0 else 'var(--banana)'}">{_appr_cnt}</div>
    <div class="card-sub">エスカレーション・レビュー</div>
  </div>
  </a>
  <div class="card">
    <div class="card-label">APIコスト（30日）</div>
    <div class="card-value" style="font-size:1.5rem">${total_cost:.4f}</div>
    <div class="card-sub">月次推計: ${monthly_est:.2f} / {total_tokens:,} tokens</div>
  </div>
  <div class="card">
    <div class="card-label">スケジューラー</div>
    <div class="card-value" style="font-size:1.1rem;margin-top:8px">
      <span style="display:inline-flex;align-items:center;gap:6px;padding:4px 12px;border-radius:20px;background:#1a1a1a;border:1px solid {sched_color};color:{sched_color};font-size:.85rem">
        <span style="width:8px;height:8px;border-radius:50%;background:{sched_color};display:inline-block"></span>
        {sched_label}
      </span>
    </div>
    <div class="card-sub" style="margin-top:10px">5分ごとにハートビート確認</div>
  </div>
</div>

<div class="section-title">ブランド</div>
<div class="tags-row">
{brand_tags_html}
</div>

<div class="section-title">エージェント別APIコスト（過去30日）</div>
<table>
  <tr>
    <th>エージェント</th>
    <th>モデル</th>
    <th>実行数</th>
    <th>トークン</th>
    <th>コスト</th>
  </tr>
  {agent_rows if agent_rows else '<tr><td colspan="5" style="color:#555;text-align:center;padding:20px">データなし</td></tr>'}
</table>

<div class="section-title" style="margin-top:28px">アナリティクス（各事業 / 過去28日）</div>
<div style="font-size:.75rem;color:#555;margin-bottom:12px">
  サービスアカウント: <code style="color:#888">upjapan-drive-bot@gen-lang-client-0671871313.iam.gserviceaccount.com</code>
  — GA4管理画面でこのアカウントを「閲覧者」に追加すると接続されます
</div>
{_build_ga4_html(ga4_data)}
"""

    return Response(_nb_page("指令室", body), mimetype="text/html")


@app.route("/brands/<slug>", methods=["GET"])
def brand_page(slug: str):
    from flask import Response
    cfg = _brand_cfg(slug)
    all_items = _load_queue_items()
    # 同じラベルを持つスラッグをまとめて表示（UPJ系）
    label = cfg["label"]
    items = [
        (fname, d) for fname, d in all_items
        if _brand_cfg(d.get("brand", ""))["label"] == label
    ]

    rows = ""
    for fname, d in items:
        if d.get("posted"):
            badge = '<span class="badge badge-posted">投稿済み</span>'
        elif d.get("needs_review"):
            badge = '<span class="badge badge-review">要レビュー</span>'
        elif d.get("error"):
            badge = '<span class="badge badge-error">エラー</span>'
        else:
            badge = '<span class="badge badge-pending">投稿待ち</span>'

        caption = d.get("caption") or d.get("content") or d.get("text") or ""
        preview = caption[:80] + ("…" if len(caption) > 80 else "")
        scheduled = d.get("scheduled_at") or "—"
        source = d.get("source") or "—"
        rows += (
            f'<tr onclick="location.href=\'/content/instagram/{fname}\'" style="cursor:pointer">'
            f'<td>{badge}</td>'
            f'<td><div class="caption-preview">{preview}</div></td>'
            f'<td style="white-space:nowrap;color:var(--muted)">{scheduled}</td>'
            f'<td style="color:var(--muted)">{source}</td>'
            f'<td><a href="/content/instagram/{fname}" style="font-size:.78rem">詳細</a></td>'
            f'</tr>'
        )

    body = f"""
<h2 style="color:{cfg['color']};font-size:1.4rem;margin-bottom:20px">{cfg['emoji']} {cfg['label']}</h2>
<div style="color:var(--muted);font-size:.82rem;margin-bottom:16px">{len(items)} 件</div>
<table>
  <tr>
    <th>状態</th><th>キャプション</th><th>予定日</th><th>ソース</th><th></th>
  </tr>
  {rows if rows else '<tr><td colspan="5" style="color:#555;text-align:center;padding:20px">コンテンツなし</td></tr>'}
</table>
"""
    return Response(
        _nb_page(cfg["label"], body, breadcrumbs=[("指令室", "/"), (cfg["label"], None)]),
        mimetype="text/html",
    )


@app.route("/content/instagram/<filename>", methods=["GET"])
def content_detail(filename: str):
    from flask import Response
    fpath = _CONTENT_DIR / filename
    if not fpath.exists():
        abort(404)
    with open(fpath, encoding="utf-8") as fp:
        data = yaml.safe_load(fp)

    slug = data.get("brand", "")
    cfg = _brand_cfg(slug)

    if data.get("posted"):
        status_badge = '<span class="badge badge-posted">投稿済み</span>'
    elif data.get("needs_review"):
        status_badge = '<span class="badge badge-review">要レビュー</span>'
    elif data.get("error"):
        status_badge = '<span class="badge badge-error">エラー</span>'
    else:
        status_badge = '<span class="badge badge-pending">投稿待ち</span>'

    img_html = ""
    if data.get("image_url"):
        img_html = f'<img src="{data["image_url"]}" class="img-preview" alt="preview">'

    hashtags_html = ""
    if data.get("hashtags"):
        hashtags_html = f'<div style="margin-top:10px;color:var(--muted);font-size:.82rem">{data["hashtags"]}</div>'

    actions_html = ""
    if not data.get("posted"):
        actions_html = f"""
<div style="display:flex;gap:12px;margin-top:20px">
  <form method="POST" action="/content/instagram/{filename}/approve">
    <button class="btn btn-approve" type="submit">✅ 承認して投稿キューへ</button>
  </form>
  <form method="POST" action="/content/instagram/{filename}/reject">
    <button class="btn btn-reject" type="submit">🗑️ 却下</button>
  </form>
</div>"""

    caption = data.get("caption") or data.get("content") or data.get("text") or ""

    scheduled_box = ""
    if data.get("scheduled_at"):
        scheduled_box = f'<div class="content-box"><div class="section-title">予定日時</div><div>{data["scheduled_at"]}</div></div>'

    error_box = ""
    if data.get("error"):
        error_box = f'<div class="content-box" style="border-color:var(--red)"><div class="section-title" style="color:var(--red)">エラー</div><div style="color:var(--red);font-size:.85rem">{data["error"]}</div></div>'

    body = f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:20px">
  <a href="/brands/{slug}" class="btn btn-outline" style="font-size:.78rem;padding:5px 12px">← {cfg['label']}</a>
  {status_badge}
</div>
<div class="content-box">
  <div class="section-title">投稿キャプション</div>
  <div class="content-caption">{caption}</div>
  {hashtags_html}
</div>
{img_html}
{scheduled_box}
{error_box}
{actions_html}
"""

    return Response(
        _nb_page(
            f"{cfg['emoji']} コンテンツ詳細",
            body,
            breadcrumbs=[("指令室", "/"), (cfg["label"], f"/brands/{slug}"), ("詳細", None)],
        ),
        mimetype="text/html",
    )


@app.route("/content/instagram/<filename>/approve", methods=["POST"])
def content_approve(filename: str):
    from flask import redirect
    fpath = _CONTENT_DIR / filename
    if fpath.exists():
        with open(fpath, encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        data["posted"] = True
        data["needs_review"] = False
        data["approved_at"] = datetime.now().isoformat()
        with open(fpath, "w", encoding="utf-8") as fp:
            yaml.dump(data, fp, allow_unicode=True, default_flow_style=False)
    return redirect(f"/content/instagram/{filename}")


@app.route("/content/instagram/<filename>/reject", methods=["POST"])
def content_reject(filename: str):
    from flask import redirect
    fpath = _CONTENT_DIR / filename
    brand = ""
    if fpath.exists():
        with open(fpath, encoding="utf-8") as fp:
            data = yaml.safe_load(fp)
        brand = data.get("brand", "")
        data["rejected"] = True
        data["needs_review"] = False
        data["rejected_at"] = datetime.now().isoformat()
        with open(fpath, "w", encoding="utf-8") as fp:
            yaml.dump(data, fp, allow_unicode=True, default_flow_style=False)
    return redirect(f"/brands/{brand}" if brand else "/")


# ─────────────────────────────────────────────────────────────────
# 承認待ち・判断待ち（/approvals）
# ─────────────────────────────────────────────────────────────────

def _load_pending_approvals():
    """
    承認待ち・判断待ちアイテムを3種類まとめて返す。

    Returns: {
        "escalations": [...],   # DBエスカレーション
        "approvals":   [...],   # DB承認依頼（CEO→Satoshi）
        "reviews":     [...],   # content_queue/review/ の未承認YAML
    }
    """
    import org_database as _db

    # 1. DBエスカレーション
    escalations = []
    with _db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, task_id, agent_id, reason, context, status, created_at"
            " FROM escalations WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
    for r in rows:
        import json as _json
        ctx = {}
        try:
            ctx = _json.loads(r["context"] or "{}")
        except Exception:
            pass
        escalations.append({
            "id":        r["id"],
            "task_id":   r["task_id"],
            "agent_id":  r["agent_id"],
            "reason":    r["reason"] or "",
            "context":   ctx,
            "created_at": r["created_at"],
        })

    # 2. DB承認依頼（CEO → Satoshi）
    approvals = []
    with _db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, task_id, title, description, requested_by_agent_id,"
            " status, expires_at, created_at"
            " FROM approvals WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
    for r in rows:
        approvals.append(dict(r))

    # 3. review YAML
    reviews = []
    if _REVIEW_DIR.exists():
        for f in sorted(_REVIEW_DIR.glob("*.yaml")):
            try:
                with open(f, encoding="utf-8") as fp:
                    d = yaml.safe_load(fp)
                if isinstance(d, dict) and d.get("status") == "pending_review":
                    reviews.append((f.name, d))
            except Exception:
                pass

    return {"escalations": escalations, "approvals": approvals, "reviews": reviews}


def _approval_count() -> int:
    """トップページに表示する承認待ち件数"""
    try:
        pending = _load_pending_approvals()
        return len(pending["escalations"]) + len(pending["approvals"]) + len(pending["reviews"])
    except Exception:
        return 0


@app.route("/approvals", methods=["GET"])
def approvals_page():
    from flask import Response
    pending = _load_pending_approvals()
    escalations = pending["escalations"]
    approvals   = pending["approvals"]
    reviews     = pending["reviews"]

    total = len(escalations) + len(approvals) + len(reviews)

    # ── エスカレーション行 ─────────────────────────────────────
    esc_rows = ""
    for e in escalations:
        short_reason = e["reason"][:100] + ("…" if len(e["reason"]) > 100 else "")
        agent_short  = e["agent_id"].replace("agent-", "")
        esc_rows += f"""
        <tr>
          <td style="color:var(--muted);font-size:.8rem">{e['created_at'][:16]}</td>
          <td><span class="badge badge-error">{agent_short}</span></td>
          <td style="font-size:.84rem">{short_reason}</td>
          <td>
            <form method="POST" action="/approvals/escalation/{e['id']}/resolve" style="display:inline">
              <button class="btn btn-outline" type="submit" style="font-size:.75rem;padding:4px 10px">✓ 解決済み</button>
            </form>
          </td>
        </tr>"""

    # ── CEO承認依頼行 ──────────────────────────────────────────
    appr_rows = ""
    for a in approvals:
        appr_rows += f"""
        <tr>
          <td style="color:var(--muted);font-size:.8rem">{a['created_at'][:16]}</td>
          <td><span class="badge badge-review">{(a.get('requested_by_agent_id') or 'ai-ceo').replace('agent-','')}</span></td>
          <td>
            <div style="font-weight:600;margin-bottom:3px">{a.get('title','')}</div>
            <div style="font-size:.82rem;color:var(--muted)">{(a.get('description') or '')[:120]}</div>
          </td>
          <td>
            <form method="POST" action="/approvals/approval/{a['id']}/approve" style="display:inline;margin-right:6px">
              <button class="btn btn-approve" type="submit" style="font-size:.75rem;padding:4px 10px">✅ 承認</button>
            </form>
            <form method="POST" action="/approvals/approval/{a['id']}/reject" style="display:inline">
              <button class="btn btn-reject" type="submit" style="font-size:.75rem;padding:4px 10px">🗑️ 却下</button>
            </form>
          </td>
        </tr>"""

    # ── レビューキュー行 ──────────────────────────────────────
    review_rows = ""
    for fname, d in reviews:
        cfg  = _brand_cfg(d.get("brand",""))
        cnt  = len(d.get("contents", []))
        types = ", ".join({c.get("type","") for c in d.get("contents",[])})
        review_rows += f"""
        <tr onclick="location.href='/approvals/review/{fname}'" style="cursor:pointer">
          <td style="color:var(--muted);font-size:.8rem">{d.get('generated_at','')[:16]}</td>
          <td><span style="color:{cfg['color']}">{cfg['emoji']} {cfg['label']}</span></td>
          <td>
            <div style="font-weight:600;margin-bottom:3px">{d.get('source_title','')}</div>
            <div style="font-size:.82rem;color:var(--muted)">{types} · {cnt}件</div>
          </td>
          <td><a href="/approvals/review/{fname}" class="btn btn-outline" style="font-size:.75rem;padding:4px 10px">レビュー →</a></td>
        </tr>"""

    empty_msg = '<tr><td colspan="4" style="color:#555;text-align:center;padding:24px">なし</td></tr>'

    body = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
  <h2 style="font-size:1.3rem;color:var(--banana)">承認待ち・判断待ち</h2>
  <span class="badge {'badge-error' if total > 0 else 'badge-posted'}" style="font-size:.85rem;padding:4px 10px">{total}件</span>
</div>

<div class="section-title" style="margin-top:0">🚨 エスカレーション（エラー・失敗タスク）</div>
<table style="margin-bottom:28px">
  <tr><th>日時</th><th>エージェント</th><th>理由</th><th></th></tr>
  {esc_rows or empty_msg}
</table>

<div class="section-title">🎯 AI CEOからの承認依頼</div>
<p style="font-size:.82rem;color:var(--muted);margin-bottom:12px">
  AIエージェントが調査し、CEO経由でサトシに判断を求めているタスクです。
</p>
<table style="margin-bottom:28px">
  <tr><th>日時</th><th>送信元</th><th>内容</th><th></th></tr>
  {appr_rows or empty_msg}
</table>

<div class="section-title">📝 コンテンツレビューキュー（ブログ→SNS）</div>
<p style="font-size:.82rem;color:var(--muted);margin-bottom:12px">
  ブログ記事から自動生成されたSNS投稿です。承認すると投稿キューへ移動します。
</p>
<table>
  <tr><th>生成日時</th><th>ブランド</th><th>元記事</th><th></th></tr>
  {review_rows or empty_msg}
</table>
"""

    return Response(
        _nb_page("承認待ち", body, breadcrumbs=[("指令室", "/"), ("承認待ち", None)]),
        mimetype="text/html"
    )


@app.route("/approvals/escalation/<esc_id>/resolve", methods=["POST"])
def escalation_resolve(esc_id: str):
    from flask import redirect
    import org_database as _db
    with _db.get_conn() as conn:
        conn.execute(
            "UPDATE escalations SET status='resolved', resolved_at=?, resolution_note=?"
            " WHERE id=?",
            (datetime.now().isoformat(), "Resolved via dashboard", esc_id),
        )
        conn.commit()
    return redirect("/approvals")


@app.route("/approvals/approval/<appr_id>/approve", methods=["POST"])
def approval_approve(appr_id: str):
    from flask import redirect
    import org_database as _db
    with _db.get_conn() as conn:
        conn.execute(
            "UPDATE approvals SET status='approved', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), appr_id),
        )
        conn.commit()
    return redirect("/approvals")


@app.route("/approvals/approval/<appr_id>/reject", methods=["POST"])
def approval_reject(appr_id: str):
    from flask import redirect
    import org_database as _db
    with _db.get_conn() as conn:
        conn.execute(
            "UPDATE approvals SET status='rejected', updated_at=? WHERE id=?",
            (datetime.now().isoformat(), appr_id),
        )
        conn.commit()
    return redirect("/approvals")


@app.route("/approvals/review/<filename>", methods=["GET"])
def review_detail(filename: str):
    from flask import Response
    fpath = _REVIEW_DIR / filename
    if not fpath.exists():
        abort(404)
    with open(fpath, encoding="utf-8") as fp:
        d = yaml.safe_load(fp)

    brand = d.get("brand", "")
    cfg   = _brand_cfg(brand)
    contents = d.get("contents", [])

    # コンテンツカード生成
    content_cards = ""
    for i, c in enumerate(contents):
        ctype  = c.get("type", "")
        status = c.get("status", "")
        text   = c.get("main_tweet") or c.get("caption") or c.get("text") or ""
        if not text:
            continue
        tags = c.get("hashtags", [])
        tag_str = " ".join(tags[:8]) if isinstance(tags, list) else str(tags or "")
        type_badge = {
            "x_thread":          "𝕏 Thread",
            "instagram_carousel": "📷 カルーセル",
            "instagram_story":    "📱 ストーリー",
            "instagram_post":     "📸 投稿",
            "line_message":       "💬 LINE",
        }.get(ctype, ctype)

        content_cards += f"""
        <div class="content-box" style="margin-bottom:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <span class="badge badge-review">{type_badge}</span>
            <span class="badge {'badge-pending' if status=='pending' else 'badge-posted'}">{status}</span>
          </div>
          <div class="content-caption">{text[:600]}{'…' if len(text)>600 else ''}</div>
          {f'<div style="margin-top:8px;color:var(--muted);font-size:.8rem">{tag_str}</div>' if tag_str else ''}
        </div>"""

    # 分析サマリー
    analysis = d.get("analysis", {})
    if isinstance(analysis, str):
        try:
            import json as _j
            analysis = _j.loads(analysis)
        except Exception:
            analysis = {"summary": analysis}
    analysis_html = ""
    if isinstance(analysis, dict) and analysis.get("summary"):
        analysis_html = f"""
        <div class="content-box" style="margin-bottom:20px;border-color:#333">
          <div class="section-title">AIによる分析</div>
          <div style="font-size:.86rem;line-height:1.7;color:var(--muted)">{str(analysis.get('summary',''))[:400]}</div>
        </div>"""

    body = f"""
<div style="display:flex;align-items:center;gap:10px;margin-bottom:20px">
  <a href="/approvals" class="btn btn-outline" style="font-size:.78rem;padding:5px 12px">← 承認待ち一覧</a>
  <span style="color:{cfg['color']};font-weight:600">{cfg['emoji']} {cfg['label']}</span>
  <span class="badge badge-review">要レビュー</span>
</div>

<div class="content-box" style="margin-bottom:16px">
  <div class="section-title">元記事タイトル</div>
  <div style="font-size:1.05rem;font-weight:600">{d.get('source_title','')}</div>
  <div style="font-size:.8rem;color:var(--muted);margin-top:4px">生成日時: {d.get('generated_at','')[:19]}</div>
</div>

{analysis_html}

<div class="section-title">生成されたSNSコンテンツ（{len(contents)}件）</div>
{content_cards or '<div style="color:var(--muted);padding:20px">コンテンツなし</div>'}

<div style="display:flex;gap:12px;margin-top:24px">
  <form method="POST" action="/approvals/review/{filename}/approve">
    <button class="btn btn-approve" type="submit">✅ 全件承認 → 投稿キューへ</button>
  </form>
  <form method="POST" action="/approvals/review/{filename}/reject">
    <button class="btn btn-reject" type="submit">🗑️ 却下</button>
  </form>
</div>
"""

    return Response(
        _nb_page(
            f"レビュー: {d.get('source_title','')}",
            body,
            breadcrumbs=[("指令室","/"), ("承認待ち","/approvals"), ("レビュー",None)]
        ),
        mimetype="text/html"
    )


@app.route("/approvals/review/<filename>/approve", methods=["POST"])
def review_approve(filename: str):
    from flask import redirect
    fpath = _REVIEW_DIR / filename
    if fpath.exists():
        with open(fpath, encoding="utf-8") as fp:
            d = yaml.safe_load(fp)
        d["status"] = "approved"
        d["approved_at"] = datetime.now().isoformat()
        # contents を approved に更新
        for c in d.get("contents", []):
            if c.get("status") == "pending":
                c["status"] = "approved"
        with open(fpath, "w", encoding="utf-8") as fp:
            yaml.dump(d, fp, allow_unicode=True, default_flow_style=False)
        # instagram_post / instagram_carousel → content_queue/instagram へコピー
        brand = d.get("brand", "unknown")
        for c in d.get("contents", []):
            if c.get("type") in ("instagram_post", "instagram_carousel"):
                caption = c.get("caption") or c.get("text") or ""
                if caption:
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out = _CONTENT_DIR / f"{ts}_review_{brand}.yaml"
                    entry = {
                        "brand": brand,
                        "caption": caption,
                        "media_type": "image",
                        "posted": None,
                        "source": "review_approved",
                        "hashtags": " ".join(c.get("hashtags", [])) if isinstance(c.get("hashtags"), list) else c.get("hashtags", ""),
                        "created_at": datetime.now().isoformat(),
                    }
                    with open(out, "w", encoding="utf-8") as fp:
                        yaml.dump(entry, fp, allow_unicode=True, default_flow_style=False)
    return redirect("/approvals")


@app.route("/approvals/review/<filename>/reject", methods=["POST"])
def review_reject(filename: str):
    from flask import redirect
    fpath = _REVIEW_DIR / filename
    if fpath.exists():
        with open(fpath, encoding="utf-8") as fp:
            d = yaml.safe_load(fp)
        d["status"] = "rejected"
        d["rejected_at"] = datetime.now().isoformat()
        with open(fpath, "w", encoding="utf-8") as fp:
            yaml.dump(d, fp, allow_unicode=True, default_flow_style=False)
    return redirect("/approvals")


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


# ═══════════════════════════════════════════════════
# N8N 連携 API
# ═══════════════════════════════════════════════════

def _n8n_auth():
    """N8N_API_KEY ヘッダー認証。未設定時はスキップ（開発用）。"""
    key = os.environ.get("N8N_API_KEY", "")
    if key and request.headers.get("X-Api-Key") != key:
        abort(401)


@app.route("/api/n8n/instagram/queue", methods=["GET"])
def n8n_instagram_queue():
    """N8N用: 投稿可能なInstagramキューを返す（image_urlあり・未投稿）"""
    _n8n_auth()
    from pathlib import Path as _Path
    import yaml as _yaml
    q_dir = _Path(__file__).parent / "content_queue" / "instagram"
    items = []
    for f in sorted(q_dir.glob("*.yaml")):
        try:
            d = _yaml.safe_load(f.read_text(encoding="utf-8"))
            if d.get("posted"):
                continue
            url = (d.get("image_url") or "").strip()
            if not url:
                continue
            items.append({
                "file": f.name,
                "brand": d.get("brand", ""),
                "image_url": url,
                "caption": d.get("caption", ""),
                "media_type": d.get("media_type", "image"),
                "video_url": d.get("video_url", ""),
            })
        except Exception:
            continue
    return jsonify({"items": items, "count": len(items)})


@app.route("/api/n8n/instagram/posted", methods=["POST"])
def n8n_instagram_posted():
    """N8N用: 投稿完了をマーク"""
    _n8n_auth()
    data = request.get_json(force=True) or {}
    filename = data.get("file", "")
    if not filename:
        abort(400)
    from pathlib import Path as _Path
    import yaml as _yaml
    f = _Path(__file__).parent / "content_queue" / "instagram" / filename
    if not f.exists():
        abort(404)
    try:
        d = _yaml.safe_load(f.read_text(encoding="utf-8"))
        d["posted"] = True
        d["posted_at"] = __import__("datetime").datetime.now().isoformat()
        d["ig_media_id"] = data.get("ig_media_id", "")
        import yaml as _yaml2
        f.write_text(_yaml2.dump(d, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        return jsonify({"ok": True, "file": filename})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/n8n/alert", methods=["POST"])
def n8n_alert():
    """N8N用: エラーアラートをLINEに転送"""
    _n8n_auth()
    data = request.get_json(force=True) or {}
    message = data.get("message", "N8N エラー（詳細なし）")
    try:
        import requests as _req
        token   = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
        user_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if token and user_id:
            _req.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"to": user_id, "messages": [{"type": "text", "text": f"[N8N Alert]\n{message}"}]},
                timeout=5,
            )
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/n8n/queue/stats", methods=["GET"])
def n8n_queue_stats():
    """N8N用: キュー統計（ダッシュボード表示用）"""
    _n8n_auth()
    from pathlib import Path as _Path
    import yaml as _yaml
    q_dir = _Path(__file__).parent / "content_queue" / "instagram"
    stats = {"ready": 0, "posted": 0, "stale": 0}
    for f in q_dir.glob("*.yaml"):
        try:
            d = _yaml.safe_load(f.read_text(encoding="utf-8"))
            if d.get("posted"):
                stats["posted"] += 1
            elif (d.get("image_url") or "").strip():
                stats["ready"] += 1
            else:
                stats["stale"] += 1
        except Exception:
            continue
    return jsonify(stats)


_MEDIA_ROOT = Path(__file__).parent / "generated_media" / "reels"

@app.route("/media/<path:filename>", methods=["GET"])
def serve_media(filename: str):
    """生成済みリール動画を公開配信する（Meta API の video_url pull に使用）"""
    if not _MEDIA_ROOT.exists() or not (_MEDIA_ROOT / filename).exists():
        abort(404)
    return send_from_directory(str(_MEDIA_ROOT), filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    _start_heartbeat_monitor()
    logger.info(f"LINE Webhookサーバー起動: port={port}")
    app.run(host="0.0.0.0", port=port, debug=False)
