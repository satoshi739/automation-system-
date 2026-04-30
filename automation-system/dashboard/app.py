"""
管理ダッシュボード v2
起動: python dashboard/app.py
ブラウザで http://localhost:8080 を開く
"""

import os
import sys
import json
import logging
import secrets
import time as _time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from collections import defaultdict

import yaml
from flask import Flask, jsonify, redirect, render_template, request, session, url_for, send_from_directory
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent / ".env")

# MOCK_MODE=true の場合はモックデータを使う（開発・デモ用）
if os.environ.get("MOCK_MODE", "false").lower() == "true":
    from dashboard import mock_service as svc
else:
    from dashboard import real_service as svc

import database as db
from repositories.asset_repo import (
    seed_mock_data, get_recommended_by_channel,
    get_recommended_by_brand, get_missing_alerts,
)

app = Flask(__name__)
# セッション用シークレットキー（.envの FLASK_SECRET_KEY で上書き可）
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
log = logging.getLogger(__name__)

ROOT         = Path(__file__).parent.parent.parent
AUTO         = Path(__file__).parent.parent
LEADS_DIR    = ROOT / "sales-system" / "leads"
FINANCE_DIR  = ROOT / "finance-system" / "logs"
PROJECTS_DIR = ROOT / "project-system" / "projects"
DECISION_DIR = AUTO / "decision_queue"
IG_QUEUE     = AUTO / "content_queue" / "instagram"
LINE_QUEUE   = AUTO / "content_queue" / "line"
QUEUE_ROOT   = AUTO / "content_queue"
LOGS_DIR     = AUTO / "logs"
BRANDS_CFG    = AUTO / "config" / "brands.yaml"
OS_CFG        = AUTO / "config" / "os_config.yaml"

INBOX_DIR     = AUTO.parent / "media" / "inbox"
PROCESSED_DIR = AUTO.parent / "media" / "processed"
PERF_LOG_PATH = AUTO / "logs" / "performance_log.yaml"
CALENDAR_DIR  = AUTO / "content_queue" / "calendar"

PLATFORMS = ["instagram","threads","facebook","twitter","youtube","tiktok","line","wordpress"]
PLATFORM_ICONS = {
    "instagram":"📷","threads":"🧵","facebook":"📘","twitter":"𝕏","youtube":"▶️",
    "tiktok":"🎵","line":"📱","wordpress":"🌐",
}

# ── 認証 ──────────────────────────────────────────────────

@app.before_request
def require_login():
    """DASHBOARD_PASSWORD が設定されている場合、ログイン必須"""
    pw = os.environ.get("DASHBOARD_PASSWORD", "")
    if not pw:
        return  # パスワード未設定 → 認証スキップ
    # ログイン不要なパス
    exempt = ("/login", "/static", "/favicon.ico", "/health", "/webhook")
    if any(request.path.startswith(e) for e in exempt):
        return
    if not session.get("logged_in"):
        return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        pw_input = request.form.get("password", "")
        pw_env   = os.environ.get("DASHBOARD_PASSWORD", "")
        if pw_env and pw_input == pw_env:
            session["logged_in"] = True
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)
        error = "パスワードが違います"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── ヘルスチェック ─────────────────────────────────────────

_HEARTBEAT = Path(__file__).parent.parent / "logs" / "scheduler.heartbeat"


@app.route("/health")
def health():
    try:
        stats = db.get_stats()
        hb_age, hb_status = None, "unknown"
        if _HEARTBEAT.exists():
            hb_age = int(_time.time() - _HEARTBEAT.stat().st_mtime)
            hb_status = "ok" if hb_age < 600 else "dead"
        return jsonify({
            "status": "ok", "db": "ok", "stats": stats,
            "scheduler": {"status": hb_status, "heartbeat_age_sec": hb_age},
        })
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.context_processor
def inject_globals():
    """全テンプレートに共通変数を注入"""
    unread_count = 0
    try:
        unread_count = db.count_unread_notifications()
    except Exception:
        pass
    try:
        hb_ok = _HEARTBEAT.exists() and (_time.time() - _HEARTBEAT.stat().st_mtime) < 600
    except Exception:
        hb_ok = False
    return {
        "nav_brands":              load_brands(),
        "nav_platform_icons":      PLATFORM_ICONS,
        "unread_notif_count":      unread_count,
        "scheduler_status":        "稼働中" if hb_ok else "停止",
        "scheduler_status_ok":     hb_ok,
    }


def load_brands() -> dict:
    if not BRANDS_CFG.exists():
        return {}
    return yaml.safe_load(BRANDS_CFG.read_text(encoding="utf-8")).get("brands", {})


def load_os_config() -> dict:
    if not OS_CFG.exists():
        return {}
    return yaml.safe_load(OS_CFG.read_text(encoding="utf-8")) or {}


# ── ユーティリティ ────────────────────────────────────────

def load_yamls(d: Path) -> list[dict]:
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.yaml"), reverse=True):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            if data:
                data["_file"] = f.name
                out.append(data)
        except Exception:
            pass
    return out


def save_yaml(d: Path, name: str, data: dict):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def log_tail(name: str, n=60) -> list[str]:
    p = LOGS_DIR / name
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-n:]


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# ── 統計ヘルパー（DB版）────────────────────────────────────

def get_stats() -> dict:
    """SQLiteから全統計を取得（YAMLファイルは不使用）"""
    s = db.get_stats()
    # MRRは財務YAMLから補完（まだDBにない）
    finances = load_yamls(FINANCE_DIR)
    s["mrr"] = sum(f.get("mrr_end", 0) for f in finances[-1:]) or 0
    return s


def get_funnel_data() -> dict:
    stats = db.get_stats()
    funnel = dict(stats.get("funnel", {}))
    # 'lost' は別途カウント
    with db.get_conn() as conn:
        lost = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE outcome='lost'"
        ).fetchone()[0]
    funnel["lost"] = lost
    return funnel


def get_monthly_leads() -> dict:
    """直近6ヶ月の月別リード数（DB版）"""
    data = db.get_monthly_leads(6)
    # DBに無い月は0で補完
    months = []
    now = datetime.now()
    for i in range(5, -1, -1):
        d = now - timedelta(days=30*i)
        months.append(d.strftime("%Y-%m"))
    month_map = dict(zip(data["labels"], data["values"]))
    return {"labels": months, "values": [month_map.get(m, 0) for m in months]}


def get_channel_data() -> dict:
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT COALESCE(source,'other') as ch, COUNT(*) as cnt
            FROM leads GROUP BY ch
        """).fetchall()
    if not rows:
        return {"labels": [], "values": []}
    labels = [r["ch"] for r in rows]
    values = [r["cnt"] for r in rows]
    return {"labels": labels, "values": values}


# ── ページ ────────────────────────────────────────────────

@app.route("/president")
def president_dashboard():
    from dashboard.mock_service import (
        get_morning_brief, get_priority_actions, get_pending_approvals,
        get_danger_alerts, get_brand_status, get_agent_status,
        get_recent_runs, get_unreplied, get_media_shortage,
        get_post_shortage, get_blog_candidates,
    )
    return render_template("president.html",
        brief=get_morning_brief(),
        priority_actions=get_priority_actions(),
        pending_approvals=get_pending_approvals(),
        danger_alerts=get_danger_alerts(),
        brand_status=get_brand_status(),
        agents=get_agent_status(),
        recent_runs=get_recent_runs(),
        unreplied=get_unreplied(),
        media_shortage=get_media_shortage(),
        post_shortage=get_post_shortage(),
        blog_candidates=get_blog_candidates(),
    )


@app.route("/ceo")
def ceo_dashboard():
    from dashboard.real_service import (
        get_morning_brief, get_brand_status, get_agent_status,
        get_task_queue, get_bottlenecks, get_escalations,
        get_ceo_priorities, get_ceo_to_president, get_pending_approvals,
    )
    from dashboard.mock_service import (
        get_ai_recommendations, get_anomaly_alerts, get_strategy_notes,
        get_performance_snapshot,
    )
    return render_template("ceo.html",
        brief=get_morning_brief(),
        brand_status=get_brand_status(),
        agents=get_agent_status(),
        task_queue=get_task_queue(),
        bottlenecks=get_bottlenecks(),
        escalations=get_escalations(),
        ceo_priorities=get_ceo_priorities(),
        to_president=get_ceo_to_president(),
        pending_approvals=get_pending_approvals(),
        recommendations=get_ai_recommendations(),
        anomaly_alerts=get_anomaly_alerts(),
        strategy_notes=get_strategy_notes(),
        perf_snapshot=get_performance_snapshot(),
    )


# ── Blog Auto Growth ─────────────────────────────────────────

@app.route("/blog")
def blog_candidates():
    from dashboard.mock_service import get_blog_projects
    projects = get_blog_projects()
    return render_template("blog_candidates.html", projects=projects)


@app.route("/blog/<int:draft_id>")
def blog_draft_detail(draft_id):
    from dashboard.mock_service import get_blog_draft_detail
    draft = get_blog_draft_detail(draft_id)
    return render_template("blog_draft_detail.html", draft=draft)


# ── AI Chief of Staff ─────────────────────────────────────────

@app.route("/chief-of-staff")
def chief_of_staff():
    from dashboard.mock_service import (
        get_ai_recommendations, get_anomaly_alerts, get_strategy_notes,
        get_performance_snapshot, get_daily_briefs_history,
    )
    return render_template("chief_of_staff.html",
        recommendations=get_ai_recommendations(),
        anomaly_alerts=get_anomaly_alerts(),
        strategy_notes=get_strategy_notes(),
        perf_snapshot=get_performance_snapshot(),
        brief_history=get_daily_briefs_history(),
    )


@app.route("/daily-briefs")
def daily_briefs():
    from dashboard.mock_service import get_daily_briefs_history
    briefs = get_daily_briefs_history()
    return render_template("daily_briefs.html", briefs=briefs)


@app.route("/anomaly-alerts")
def anomaly_alerts():
    from dashboard.mock_service import get_anomaly_alerts
    alerts = get_anomaly_alerts()
    return render_template("anomaly_alerts.html", alerts=alerts)


@app.route("/system-alerts")
def system_alerts():
    import re
    alerts_path = Path(__file__).parent.parent / "logs" / "alerts.log"
    lines = []
    if alerts_path.exists():
        lines = alerts_path.read_text(encoding="utf-8").splitlines()[-100:]
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'\[(.+?)\] \[(.+?)\] (.+)', line)
        if m:
            entries.append({
                "time": m.group(1),
                "source": m.group(2),
                "message": m.group(3),
            })
        else:
            entries.append({
                "time": "-",
                "source": "-",
                "message": line,
            })
    return render_template("system_alerts.html", entries=entries)


@app.route("/performance-snapshot")
def performance_snapshot():
    from dashboard.mock_service import get_performance_snapshot
    snap = get_performance_snapshot()
    return render_template("performance_snapshot.html", snap=snap)


@app.route("/")
def index():
    stats    = get_stats()
    recent   = db.list_leads(outcome="active", limit=6)
    decisions= db.list_decisions(resolved=False)[:5]
    funnel   = get_funnel_data()
    monthly  = get_monthly_leads()
    channels = get_channel_data()
    now_str  = datetime.now().strftime("%Y年%m月%d日（%A）%H:%M")
    return render_template("index.html",
        stats=stats, recent=recent, decisions=decisions,
        funnel=funnel, monthly=monthly, channels=channels,
        now=now_str, ai=ai_available())


@app.route("/leads")
def leads_page():
    sf      = request.args.get("stage","")
    brand_f = request.args.get("brand","")
    leads   = db.list_leads(brand=brand_f, stage=sf, outcome="active", limit=200)
    return render_template("leads.html", leads=leads, stage_filter=sf, brand_filter=brand_f)


@app.route("/leads/kanban")
def leads_kanban():
    all_leads = db.list_leads(outcome="active", limit=500)
    kanban = {"L1":[],"L2":[],"L3":[],"L4":[]}
    for l in all_leads:
        s = l.get("stage","L1")
        if s in kanban:
            kanban[s].append(l)
    return render_template("leads_kanban.html", kanban=kanban,
                           now_date=datetime.now().strftime("%Y-%m-%d"))


@app.route("/leads/<lead_id>", methods=["GET","POST"])
def lead_detail(lead_id):
    if request.method == "POST":
        existing = db.get_lead(lead_id) or {}
        for f in ["stage","next_action","next_action_date","notes","outcome","lost_reason",
                  "current_situation","goals","budget_range","concerns"]:
            v = request.form.get(f)
            if v is not None:
                existing[f] = v
        existing["lead_id"]      = lead_id
        existing["last_contact"] = datetime.now().strftime("%Y-%m-%d")
        db.upsert_lead(existing)
        # YAMLも更新（スケジューラーとの後方互換）
        path = LEADS_DIR / f"{lead_id}.yaml"
        if path.exists():
            save_yaml(LEADS_DIR, f"{lead_id}.yaml", existing)
        return redirect(url_for("leads_page"))
    lead = db.get_lead(lead_id)
    if not lead:
        # YAMLファイルから試みる（未移行データ対応）
        path = LEADS_DIR / f"{lead_id}.yaml"
        if not path.exists():
            return "Not found", 404
        lead = yaml.safe_load(path.read_text(encoding="utf-8"))
        db.upsert_lead(lead)  # 遅延移行
    return render_template("lead_detail.html", lead=lead, lead_id=lead_id, ai=ai_available())


@app.route("/queue")
def queue_page():
    # DBからキューを取得
    brands = load_brands()
    queue_data = {}
    for brand_id in brands:
        queue_data[brand_id] = {}
        for platform in PLATFORMS:
            items = db.list_queue(brand=brand_id, channel=platform, pending_only=True)
            # DBになければYAMLから読む（後方互換）
            if not items:
                q_dir = QUEUE_ROOT / brand_id / platform
                items = [i for i in load_yamls(q_dir) if not i.get("posted")]
            if items:
                queue_data[brand_id][platform] = items
    # 旧キューも表示（後方互換）
    ig_queue   = load_yamls(IG_QUEUE)
    line_queue = load_yamls(LINE_QUEUE)
    return render_template("queue.html",
        queue_data=queue_data, brands=brands,
        ig_queue=ig_queue, line_queue=line_queue,
        platform_icons=PLATFORM_ICONS)


@app.route("/queue/add", methods=["GET","POST"])
def queue_add():
    brands = load_brands()
    if request.method == "POST":
        brand   = request.form.get("brand","dsc-marketing")
        ch      = request.form.get("channel","instagram")
        ts      = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        entry   = {"brand":brand,"channel":ch,"posted":False,"source":"dashboard"}

        if ch == "instagram":
            mt = request.form.get("media_type","image")
            entry["media_type"] = mt
            entry["caption"]    = request.form.get("caption","")
            entry["image_url" if mt!="reel" else "video_url"] = request.form.get("image_url","") or request.form.get("video_url","")
        elif ch == "threads":
            entry["text"]      = request.form.get("text","")
            entry["image_url"] = request.form.get("image_url","")
        elif ch == "facebook":
            entry["text"]      = request.form.get("fb_text","")
            entry["image_url"] = request.form.get("fb_image_url","")
        elif ch == "twitter":
            entry["text"]      = request.form.get("text","")
        elif ch == "youtube":
            entry["title"]       = request.form.get("title","")
            entry["description"] = request.form.get("description","")
            entry["video_url"]   = request.form.get("video_url","")
            entry["tags"]        = request.form.get("tags","").split(",")
        elif ch == "tiktok":
            entry["title"]     = request.form.get("title","")
            entry["video_url"] = request.form.get("video_url","")
        elif ch == "line":
            entry["message"]   = request.form.get("message","")
            entry["image_url"] = request.form.get("image_url","")
        elif ch == "wordpress":
            entry["title"]       = request.form.get("title","")
            entry["content"]     = request.form.get("content","")
            entry["status"]      = request.form.get("wp_status","draft")
            entry["image_url"]   = request.form.get("image_url","")

        # 投稿予約日時（空なら自動投稿に委ねる）
        sched = request.form.get("schedule_at", "").strip()
        if sched:
            entry["scheduled_at"] = sched

        # DBに保存（主ストア）
        entry["filename"] = f"{ts}_manual.yaml"
        db.enqueue(entry)
        db.log_activity("queue_add", brand=brand, platform=ch,
                        detail=f"手動追加: {entry.get('caption','')[:40] or entry.get('title','')[:40] or entry.get('message','')[:40]}")
        # YAMLにも保存（スケジューラーとの後方互換）
        save_yaml(QUEUE_ROOT / brand / ch, f"{ts}_manual.yaml", entry)
        if ch == "instagram": save_yaml(IG_QUEUE, f"{ts}_manual.yaml", entry)
        if ch == "line":      save_yaml(LINE_QUEUE, f"{ts}_manual.yaml", entry)
        return redirect(url_for("queue_page"))
    return render_template("queue_add.html", ai=ai_available(), brands=brands,
                           platform_icons=PLATFORM_ICONS)


@app.route("/calendar")
def calendar_page():
    brands   = load_brands()
    events   = []
    brand_colors = {bid: b.get("color","#6366f1") for bid, b in brands.items()}
    platform_icons = PLATFORM_ICONS

    for bid, brand in brands.items():
        color = brand.get("color", "#6366f1")
        for p in PLATFORMS:
            if not brand.get("channels", {}).get(p):
                continue
            items = load_yamls(QUEUE_ROOT / bid / p)
            for item in items:
                fname   = item.get("_file", "")
                # ファイル名から日付推定（YYYY-MM-DD_HHmmSS_*.yaml）
                date_str = fname[:10] if len(fname) >= 10 else datetime.now().strftime("%Y-%m-%d")
                content  = item.get("caption") or item.get("text") or item.get("title") or item.get("message") or ""
                posted   = item.get("posted", False)
                events.append({
                    "title":           f"{platform_icons.get(p,'')} {content[:28]}{'…' if len(content)>28 else ''}",
                    "start":           date_str,
                    "backgroundColor": "#34d399" if posted else color,
                    "borderColor":     "#34d399" if posted else color,
                    "textColor":       "#fff",
                    "extendedProps": {
                        "brand_id":      bid,
                        "brand_name":    brand.get("name_short", bid),
                        "platform":      p,
                        "platform_icon": platform_icons.get(p, ""),
                        "content":       content,
                        "posted":        posted,
                        "file":          fname,
                    }
                })

    return render_template("calendar.html",
        events=events, brand_colors=brand_colors)


@app.route("/generate")
def generate_page():
    if not ai_available():
        return render_template("no_api.html", feature="AI生成", key="ANTHROPIC_API_KEY")
    return render_template("generate.html")


@app.route("/agents")
def agents_page():
    """AI OS — CEO + Agents ステータスページ"""
    os_cfg   = load_os_config()
    brands   = load_brands()
    # DBからエージェント実行履歴を取得してYAML定義とマージ
    db_agents = {a["agent_id"]: a for a in db.list_agents()}
    agents_cfg = os_cfg.get("agents", [])
    agents_merged = []
    for ag in agents_cfg:
        db_rec = db_agents.get(ag["id"], {})
        agents_merged.append({
            **ag,
            "last_run":    db_rec.get("last_run"),
            "last_result": db_rec.get("last_result"),
            "run_count":   db_rec.get("run_count", 0),
            "db_status":   db_rec.get("status", ag.get("status", "active")),
            "brand_color": brands.get(ag.get("brand") or "", {}).get("color", "#6366f1"),
            "brand_short": brands.get(ag.get("brand") or "", {}).get("name_short", "全ブランド"),
        })
    return render_template("agents.html",
        os_cfg=os_cfg, agents=agents_merged,
        president=os_cfg.get("president", {}),
        ai_ceo=os_cfg.get("ai_ceo", {}),
        ai=ai_available())


@app.route("/analytics")
def analytics():
    funnel   = get_funnel_data()
    monthly  = get_monthly_leads()
    channels = get_channel_data()
    stats    = get_stats()
    finances = load_yamls(FINANCE_DIR)
    mrr_history = {"labels":[], "values":[]}
    for f in finances[-6:]:
        mrr_history["labels"].append(f.get("month",""))
        mrr_history["values"].append(f.get("mrr_end", 0))

    # ブランド別サイトアクセス（GA4）
    brands = load_brands()
    brand_traffic = {}
    for bid, b in brands.items():
        ga = _get_ga_data(bid)
        ov = ga.get("overview", {})
        brand_traffic[bid] = {
            "name":     b.get("name_short", bid),
            "color":    b.get("color", "#6366f1"),
            "url":      b.get("url", ""),
            "sessions": ov.get("sessions", 0),
            "pageviews": ov.get("pageviews", 0),
            "users":    ov.get("users", 0),
            "avg_duration": ov.get("avg_duration", 0),
            "bounce_rate":  ov.get("bounce_rate", 0),
            "configured": bool(ov.get("sessions", None) is not None and not ov.get("error")),
        }

    return render_template("analytics.html",
        funnel=funnel, monthly=monthly, channels=channels,
        stats=stats, mrr_history=mrr_history, brand_traffic=brand_traffic)


@app.route("/brands")
def brands_page():
    brands = load_brands()
    brand_stats = {}
    for bid, bcfg in brands.items():
        pending = sum(
            len([i for i in load_yamls(QUEUE_ROOT/bid/p) if not i.get("posted")])
            for p in PLATFORMS
        )
        brand_stats[bid] = {"pending": pending, **_get_brand_cockpit(bid)}
    return render_template("brands.html", brands=brands, brand_stats=brand_stats,
                           platform_icons=PLATFORM_ICONS)


@app.route("/brands/<brand_id>")
def brand_detail(brand_id):
    brands = load_brands()
    brand = brands.get(brand_id)
    if not brand:
        return "Brand not found", 404

    # チャンネル別キュー（テンプレートが期待するフォーマットで構築）
    platforms = {}
    for p in PLATFORMS:
        if brand.get("channels", {}).get(p):
            items = load_yamls(QUEUE_ROOT / brand_id / p)
            platforms[p] = {
                "icon":          PLATFORM_ICONS.get(p, ""),
                "posts":         items,
                "pending_count": sum(1 for i in items if not i.get("posted")),
            }

    # アナリティクス（GA4 / Search Console）
    ga_data  = _get_ga_data(brand_id)
    gsc_data = _get_gsc_data(brand_id)

    # WordPressの下書き一覧
    wp_drafts = _get_wp_drafts(brand_id)

    # ── 運転席: ブランド別ステータスサマリー ──
    cockpit = _get_brand_cockpit(brand_id)

    return render_template("brand_detail.html",
        brand_id=brand_id, brand=brand,
        platforms=platforms,
        ga_data=ga_data, gsc_data=gsc_data,
        wp_drafts=wp_drafts,
        cockpit=cockpit,
        platform_icons=PLATFORM_ICONS)


def _get_brand_cockpit(brand_id: str) -> dict:
    """ブランド運転席用のサマリーデータを集約する"""
    data = {}

    # ── 投稿キュー状況 ──
    total_pending = 0
    for p in PLATFORMS:
        items = load_yamls(QUEUE_ROOT / brand_id / p)
        total_pending += sum(1 for i in items if not i.get("posted"))
    data["queue_pending"] = total_pending

    # ── ストーリー状況 ──
    try:
        from repositories.story_repo import StoryRunRepo, StoryTemplateRepo
        run_repo = StoryRunRepo()
        tmpl_repo = StoryTemplateRepo()
        story_pending = run_repo.count_by_status(brand_id, "pending_approval")
        story_published = run_repo.count_by_status(brand_id, "published")
        templates_active = len([t for t in tmpl_repo.list(brand_id=brand_id) if t.get("is_active")])
        data["story_pending"] = story_pending
        data["story_published"] = story_published
        data["story_templates_active"] = templates_active
    except Exception:
        data["story_pending"] = 0
        data["story_published"] = 0
        data["story_templates_active"] = 0

    # ── MEO状況 ──
    try:
        from repositories.meo_repo import list_profiles, list_reviews, count_unanswered, get_latest_insights
        profiles = [p for p in list_profiles() if p.get("brand_id") == brand_id]
        if profiles:
            profile = profiles[0]
            unanswered = count_unanswered()
            insights = get_latest_insights(profile["id"]) or {}
            data["meo_score"] = profile.get("meo_score", 0)
            data["meo_unanswered"] = unanswered
            data["meo_avg_rating"] = insights.get("avg_rating", 0)
            data["meo_reviews_total"] = insights.get("reviews_total", 0)
        else:
            data["meo_score"] = None
    except Exception:
        data["meo_score"] = None

    # ── 承認待ちコンテンツ ──
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM publishing_jobs WHERE brand=? AND status='pending_approval'",
                (brand_id,)
            ).fetchone()
            data["publishing_pending"] = row[0] if row else 0
    except Exception:
        data["publishing_pending"] = 0

    # ── ブログ候補 ──
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM blog_drafts WHERE brand_id=? AND status='draft'",
                (brand_id,)
            ).fetchone()
            data["blog_drafts"] = row[0] if row else 0
    except Exception:
        data["blog_drafts"] = 0

    # ── 自動化エージェント稼働状況 ──
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN status='online' THEN 1 ELSE 0 END) FROM agents"
            ).fetchone()
            data["agents_total"] = rows[0] or 0
            data["agents_online"] = rows[1] or 0
    except Exception:
        data["agents_total"] = 0
        data["agents_online"] = 0

    return data


def _get_ga_data(brand_id: str) -> dict:
    """GA4データを取得（APIキー未設定の場合は空）"""
    env_key = f"{brand_id.upper().replace('-','_')}_GA4_PROPERTY_ID"
    if not os.environ.get(env_key):
        return {}
    try:
        from sns.analytics import GA4Client
        client = GA4Client(env_key)
        overview = client.get_overview(28)
        series   = client.get_daily_series(28)
        pages    = client.get_top_pages(28, 5)
        return {"overview": overview, "series": series, "pages": pages}
    except Exception as e:
        return {"error": str(e)}


def _get_gsc_data(brand_id: str) -> dict:
    """Search Consoleデータを取得"""
    env_key = f"{brand_id.upper().replace('-','_')}_GSC_SITE_URL"
    if not os.environ.get(env_key):
        return {}
    try:
        from sns.analytics import SearchConsoleClient
        client = SearchConsoleClient(env_key)
        return {
            "overview": client.get_overview(28),
            "queries":  client.get_top_queries(28, 10),
        }
    except Exception as e:
        return {"error": str(e)}


def _get_wp_drafts(brand_id: str) -> list:
    """WordPress下書き一覧を取得"""
    env_key = f"{brand_id.upper().replace('-','_')}_WP_URL"
    if not os.environ.get(env_key):
        return []
    try:
        from sns.wordpress import WordPressPoster
        wp = WordPressPoster(brand_id)
        return wp.get_posts("draft", 5)
    except Exception as e:
        return []


@app.route("/brands/<brand_id>/publish_draft/<int:post_id>", methods=["POST"])
def publish_wp_draft(brand_id, post_id):
    """WordPress下書きを公開"""
    try:
        from sns.wordpress import WordPressPoster
        WordPressPoster(brand_id).publish_post(post_id)
    except Exception as e:
        pass
    return redirect(url_for("brand_detail", brand_id=brand_id))


@app.route("/api/analytics/<brand_id>")
def api_analytics(brand_id):
    return jsonify({
        "ga":  _get_ga_data(brand_id),
        "gsc": _get_gsc_data(brand_id),
    })


@app.route("/decisions")
def decisions_page():
    decisions = db.list_decisions(resolved=False)
    return render_template("decisions.html", decisions=decisions)


@app.route("/decisions/resolve/<int:decision_id>", methods=["POST"])
def resolve_decision(decision_id):
    db.resolve_decision(decision_id)
    return redirect(url_for("decisions_page"))


@app.route("/decisions/resolve_file/<filename>", methods=["POST"])
def resolve_decision_file(filename):
    """後方互換: ファイル名ベースで判断待ちを解決"""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM decisions WHERE filename=?", (filename,)
        ).fetchone()
    if row:
        db.resolve_decision(row["id"])
    # YAMLファイルも更新
    path = DECISION_DIR / filename
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data.update(resolved=True,
                        resolved_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        resolved_note=request.form.get("note",""))
            save_yaml(DECISION_DIR, filename, data)
        except Exception:
            pass
    return redirect(url_for("decisions_page"))


@app.route("/logs")
def logs_page():
    import re as _re

    alerts_path = Path(__file__).parent.parent / "logs" / "alerts.log"
    rotate_history = []
    if alerts_path.exists():
        _pat = _re.compile(
            r'\[(.+?)\] \[log_rotate\] (\d+) files rotated, ([\d.]+)MB archived, (\d+) old archives deleted'
        )
        for line in alerts_path.read_text(encoding="utf-8").splitlines():
            m = _pat.search(line)
            if m:
                rotate_history.append({
                    "time":    m.group(1),
                    "files":   int(m.group(2)),
                    "mb":      float(m.group(3)),
                    "deleted": int(m.group(4)),
                })
    rotate_history = list(reversed(rotate_history[-20:]))

    archive_dir = Path(__file__).parent.parent / "logs" / "archive"
    archive_snap = {"dirs": 0, "total_mb": 0.0}
    if archive_dir.exists():
        dirs = [d for d in archive_dir.iterdir() if d.is_dir()]
        archive_snap["dirs"] = len(dirs)
        total_bytes = sum(
            f.stat().st_size
            for d in dirs for f in d.iterdir() if f.is_file()
        )
        archive_snap["total_mb"] = total_bytes / 1_048_576

    return render_template("logs.html",
        scheduler_log =log_tail("scheduler.log"),
        morning_log   =log_tail("morning.log"),
        server_log    =log_tail("server.log"),
        rotate_history=rotate_history,
        archive_snap  =archive_snap)


@app.route("/audit-logs")
def audit_logs_page():
    page     = int(request.args.get("page", 1))
    per_page = 50
    offset   = (page - 1) * per_page
    resource = request.args.get("resource", "")
    action   = request.args.get("action", "")
    logs     = db.list_audit_logs(resource=resource, action=action,
                                   limit=per_page, offset=offset)
    total    = db.count_audit_logs()
    return render_template("audit_logs.html",
        logs=logs, page=page, per_page=per_page,
        total=total, resource=resource, action=action,
        total_pages=max(1, (total + per_page - 1) // per_page))


@app.route("/notifications")
def notifications_page():
    notifs = db.list_notifications(limit=100)
    return render_template("notifications.html", notifs=notifs)


@app.route("/api/notifications/mark-read", methods=["POST"])
def api_notif_mark_read():
    d    = request.get_json(force=True) or {}
    nid  = d.get("id")
    if nid:
        db.mark_notification_read(int(nid))
    else:
        db.mark_all_notifications_read()
    return jsonify({"ok": True, "unread": db.count_unread_notifications()})


@app.route("/api/notifications")
def api_notifications():
    notifs  = db.list_notifications(limit=20)
    unread  = db.count_unread_notifications()
    return jsonify({"notifs": notifs, "unread": unread})


# ── API ──────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    s = get_stats()
    s["updated_at"] = datetime.now().isoformat()
    s["ai_enabled"] = ai_available()
    return jsonify(s)


@app.route("/api/ceo/dispatch", methods=["POST"])
def api_ceo_dispatch():
    """AI CEO ディスパッチを手動トリガー（ダッシュボードから即時実行）"""
    try:
        body = request.get_json(silent=True, force=True) or {}
        is_dry = body.get("dry_run", False)

        from agents.ceo_executor import run_ceo_dispatch
        result = run_ceo_dispatch(dry_run=is_dry)
        return jsonify({"ok": True, **result})
    except Exception as e:
        log.error(f"CEO dispatch API error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ceo/instruct", methods=["POST"])
def api_ceo_instruct():
    """社長 → AI CEO への直接指示。CEOが解釈してエージェントにタスクを割り当てる。"""
    try:
        body = request.get_json(silent=True, force=True) or {}
        instruction = (body.get("instruction") or "").strip()
        if not instruction:
            return jsonify({"ok": False, "error": "instruction is required"}), 400

        from agents.ceo_executor import run_ceo_dispatch
        result = run_ceo_dispatch(president_instruction=instruction)
        return jsonify({"ok": True, **result})
    except Exception as e:
        log.error(f"CEO instruct API error: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/ceo/status")
def api_ceo_status():
    """AI CEO の最新実行状態を返す"""
    import org_database as org_db
    import json as _json
    try:
        with org_db.get_conn() as conn:
            row = conn.execute(
                """SELECT output_data, completed_at, status
                   FROM agent_runs WHERE agent_id='ai-ceo'
                   ORDER BY completed_at DESC LIMIT 1""",
            ).fetchone()
        if not row:
            return jsonify({"last_run": None, "tasks_created": 0, "summary": "まだ実行されていません"})
        data = _json.loads(row["output_data"] or "{}")
        return jsonify({
            "last_run":      row["completed_at"],
            "status":        row["status"],
            "tasks_created": data.get("tasks_created", 0),
            "summary":       data.get("summary", ""),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/leads/stage", methods=["POST"])
def api_lead_stage():
    """カンバンのドラッグ&ドロップ後のステージ更新"""
    d        = request.get_json()
    lead_id  = d.get("lead_id","")
    new_stage= d.get("stage","")
    lead = db.get_lead(lead_id)
    if not lead:
        return jsonify({"ok":False,"error":"not found"}), 404
    db.update_lead_stage(lead_id, new_stage)
    # YAMLファイルも更新（後方互換）
    path = LEADS_DIR / f"{lead_id}.yaml"
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            data["stage"] = new_stage
            data["last_contact"] = datetime.now().strftime("%Y-%m-%d")
            save_yaml(LEADS_DIR, f"{lead_id}.yaml", data)
        except Exception:
            pass
    return jsonify({"ok":True})


@app.route("/api/ai/generate_post", methods=["POST"])
def api_generate_post():
    if not ai_available():
        return jsonify({"error":"ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_instagram_post
    d = request.get_json()
    try:
        result = generate_instagram_post(
            topic =d.get("topic",""),
            target=d.get("target",""),
            tone  =d.get("tone","実務的"),
            brand =d.get("brand","dsc-marketing"),
            extra =d.get("extra",""),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/generate_line", methods=["POST"])
def api_generate_line():
    if not ai_available():
        return jsonify({"error":"ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_line_message
    d = request.get_json()
    try:
        msg = generate_line_message(
            topic  =d.get("topic",""),
            brand  =d.get("brand","dsc-marketing"),
            purpose=d.get("purpose","集客・認知"),
        )
        return jsonify({"message": msg})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/queue/delete/<brand_id>/<platform>/<filename>", methods=["POST"])
def queue_delete(brand_id, platform, filename):
    """キューアイテムを削除（DB + YAML）"""
    import re
    # パストラバーサル対策
    if not re.match(r'^[\w\-. ]+$', filename):
        return "Invalid filename", 400
    # DBから削除
    with db.get_conn() as conn:
        conn.execute("DELETE FROM queue_items WHERE brand=? AND channel=? AND filename=?",
                     (brand_id, platform, filename))
    # YAMLファイルも削除
    path = QUEUE_ROOT / brand_id / platform / filename
    if path.exists() and str(path).startswith(str(QUEUE_ROOT)):
        path.unlink()
    return redirect(url_for("queue_page"))


@app.route("/api/ai/suggest_topics/<brand_id>", methods=["POST"])
def api_suggest_topics(brand_id):
    """今日のトピックをAIが3つ提案"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    brands = load_brands()
    brand = brands.get(brand_id, {})
    from dashboard.ai import BRAND_CONTEXTS
    import anthropic
    brand_ctx = BRAND_CONTEXTS.get(brand_id, "")
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    today = datetime.now().strftime("%Y年%m月%d日")
    prompt = f"""{brand_ctx}
今日（{today}）のSNS投稿に最適なテーマを3つ提案してください。
旬のビジネストレンド、季節感、ターゲットの関心に合わせてください。

JSON形式で返してください:
{{"topics": ["テーマ1（20文字以内）", "テーマ2（20文字以内）", "テーマ3（20文字以内）"]}}
JSONのみ返す。"""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=200,
            messages=[{"role":"user","content":prompt}]
        )
        import json as _json
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].lstrip("json").strip()
        data = _json.loads(raw)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/generate_all/<brand_id>", methods=["POST"])
def api_generate_all(brand_id):
    """全プラットフォーム一括生成"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_all_platforms
    d = request.get_json()
    topic = d.get("topic", "")
    extra = d.get("extra", "")
    if not topic:
        return jsonify({"error": "トピックを入力してください"}), 400
    try:
        result = generate_all_platforms(topic=topic, brand=brand_id, extra_context=extra)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/generate_reel/<brand_id>", methods=["POST"])
def api_generate_reel(brand_id):
    """リール用スライド画像を生成してキューに保存"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400

    from dashboard.ai import generate_reel_script
    from sns.image_generator import generate_reel_slides, save_slides, slides_to_video

    brands = load_brands()
    brand  = brands.get(brand_id, {})
    brand_color = brand.get("color", "#5b8af5")
    brand_name  = brand.get("name_short", brand_id)

    d     = request.get_json()
    topic = d.get("topic", "")
    reel_data = d.get("reel")  # AI生成済みのreel dictがあれば使う

    if not reel_data:
        if not topic:
            return jsonify({"error": "トピックを入力してください"}), 400
        reel_data = generate_reel_script(topic, brand_id)

    try:
        slides = generate_reel_slides(
            title       = reel_data.get("title", topic),
            points      = reel_data.get("points", []),
            cta         = reel_data.get("cta", "詳しくはプロフリンクから"),
            brand_color = brand_color,
            brand_name  = brand_name,
        )
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        prefix = f"{brand_id}_{ts}_reel"
        paths  = save_slides(slides, prefix)

        # 動画変換を試みる
        video_path = slides_to_video(paths, prefix)

        slide_urls = [str(p.relative_to(AUTO)) for p in paths]
        return jsonify({
            "ok": True,
            "slides": slide_urls,
            "video": str(video_path.relative_to(AUTO)) if video_path else None,
            "reel_data": reel_data,
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/queue/save_all/<brand_id>", methods=["POST"])
def api_save_all_to_queue(brand_id):
    """一括生成したコンテンツを全プラットフォームのキューに保存"""
    brands = load_brands()
    brand  = brands.get(brand_id, {})
    if not brand:
        return jsonify({"error": "Brand not found"}), 404

    d        = request.get_json()
    content  = d.get("content", {})
    ts       = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    saved    = []
    enabled  = brand.get("channels", {})

    platform_map = {
        "instagram": lambda c: {
            "media_type": "image",
            "caption":    f"{c['caption']}\n\n{c['hashtags']}",
            "image_url":  c.get("image_url", ""),
        },
        "threads": lambda c: {"text": c["text"]},
        "facebook": lambda c: {"text": c["text"]},
        "twitter":  lambda c: {"text": c["text"]},
        "line":     lambda c: {"message": c["message"]},
        "wordpress": lambda c: {
            "title":   c["title"],
            "content": c["content"],
            "status":  "draft",
        },
    }

    for platform, builder in platform_map.items():
        if not enabled.get(platform):
            continue
        platform_content = content.get(platform)
        if not platform_content:
            continue
        try:
            entry = builder(platform_content)
            entry.update({"brand": brand_id, "channel": platform, "posted": False, "source": "ai_bulk"})
            entry["filename"] = f"{ts}_ai_bulk.yaml"
            db.enqueue(entry)
            save_yaml(QUEUE_ROOT / brand_id / platform, f"{ts}_ai_bulk.yaml", entry)
            saved.append(platform)
        except Exception as e:
            pass  # スキップして続行

    return jsonify({"ok": True, "saved": saved})


@app.route("/api/ai/lead_reply/<lead_id>")
def api_lead_reply(lead_id):
    if not ai_available():
        return jsonify({"error":"ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_lead_reply
    lead = db.get_lead(lead_id)
    if not lead:
        return jsonify({"error":"not found"}), 404
    try:
        reply = generate_lead_reply(lead)
        return jsonify({"reply": reply})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/settings/<brand_id>", methods=["GET", "POST"])
def settings_page(brand_id):
    """ブランド別API設定ページ"""
    brands = load_brands()
    brand  = brands.get(brand_id)
    if not brand:
        return "Brand not found", 404

    env_path = AUTO / ".env"
    saved = False
    error = None

    # 現在の.envを読み込む
    def read_env() -> dict:
        if not env_path.exists():
            return {}
        result = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
        return result

    if request.method == "POST":
        try:
            env_data = read_env()
            # フォームの値を更新（空でないもののみ）
            for key, val in request.form.items():
                val = val.strip()
                # 空欄の場合でもキーは保持（既存値を消さないため空なら既存値を使う）
                if val:
                    env_data[key] = val
                elif key not in env_data:
                    env_data[key] = ""

            # .envファイルに書き戻す
            lines = []
            written = set()
            # 既存の構造を保持しながら更新
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        lines.append(line)
                        continue
                    if "=" in stripped:
                        k = stripped.split("=")[0].strip()
                        if k in env_data:
                            lines.append(f"{k}={env_data[k]}")
                            written.add(k)
                        else:
                            lines.append(line)
                    else:
                        lines.append(line)
            # 新規キーを追記
            for k, v in env_data.items():
                if k not in written:
                    lines.append(f"{k}={v}")
                    written.add(k)

            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            # プロセスの環境変数も更新
            for k, v in env_data.items():
                if v:
                    os.environ[k] = v
            saved = True
        except Exception as e:
            error = str(e)

    env_data = read_env()

    # 接続ステータスチェック（キーが設定されているか）
    prefix = brand_id.upper().replace("-", "_")
    status = {
        "anthropic":  bool(os.environ.get("ANTHROPIC_API_KEY") or env_data.get("ANTHROPIC_API_KEY")),
        "meta":       bool(env_data.get(f"{prefix}_META_ACCESS_TOKEN")),
        "twitter":    bool(env_data.get(f"{prefix}_TWITTER_API_KEY")),
        "line":       bool(env_data.get("LINE_CHANNEL_ACCESS_TOKEN") or env_data.get(f"LINE_CHANNEL_ACCESS_TOKEN_{prefix.split('_')[0]}")),
        "wordpress":  bool(env_data.get(f"{prefix}_WP_APP_PASSWORD")),
        "youtube":    bool(env_data.get(f"{prefix}_YOUTUBE_CHANNEL_ID")),
        "tiktok":     bool(env_data.get(f"{prefix}_TIKTOK_ACCESS_TOKEN")),
        "google":     bool(env_data.get(f"{prefix}_GA4_PROPERTY_ID") or (AUTO / "credentials.json").exists()),
    }

    return render_template("settings.html",
        brand_id=brand_id, brand=brand,
        env=env_data, status=status,
        saved=saved, error=error)


@app.route("/api/test_connection/<brand_id>/<conn_type>", methods=["POST"])
def api_test_connection(brand_id, conn_type):
    """API接続テスト"""
    prefix = brand_id.upper().replace("-", "_")

    if conn_type == "meta":
        token = os.environ.get(f"{prefix}_META_ACCESS_TOKEN", "")
        ig_id = os.environ.get(f"{prefix}_INSTAGRAM_ACCOUNT_ID", "")
        if not token:
            return jsonify({"ok": False, "error": "META_ACCESS_TOKENが設定されていません"})
        try:
            import urllib.request
            url = f"https://graph.facebook.com/v19.0/me?access_token={token}"
            with urllib.request.urlopen(url, timeout=8) as r:
                data = json.loads(r.read())
            return jsonify({"ok": True, "detail": f"ユーザー: {data.get('name', data.get('id', ''))}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:100]})

    elif conn_type == "twitter":
        try:
            import tweepy
            client = tweepy.Client(
                consumer_key=os.environ.get(f"{prefix}_TWITTER_API_KEY"),
                consumer_secret=os.environ.get(f"{prefix}_TWITTER_API_SECRET"),
                access_token=os.environ.get(f"{prefix}_TWITTER_ACCESS_TOKEN"),
                access_token_secret=os.environ.get(f"{prefix}_TWITTER_ACCESS_SECRET"),
            )
            me = client.get_me()
            return jsonify({"ok": True, "detail": f"@{me.data.username}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:100]})

    elif conn_type == "wordpress":
        try:
            sys.path.insert(0, str(AUTO))
            from sns.wordpress import WordPressPoster
            wp = WordPressPoster(brand_id)
            posts = wp.get_posts("draft", 1)
            return jsonify({"ok": True, "detail": f"接続OK（下書き{len(posts)}件）"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:100]})

    elif conn_type == "line":
        try:
            token_key = "LINE_CHANNEL_ACCESS_TOKEN" if brand_id == "dsc-marketing" else f"LINE_CHANNEL_ACCESS_TOKEN_{prefix.split('_')[0]}"
            token = os.environ.get(token_key, "")
            if not token:
                return jsonify({"ok": False, "error": "LINE_CHANNEL_ACCESS_TOKENが設定されていません"})
            import urllib.request
            req = urllib.request.Request(
                "https://api.line.me/v2/bot/info",
                headers={"Authorization": f"Bearer {token}"}
            )
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            return jsonify({"ok": True, "detail": f"Bot名: {data.get('displayName','')}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:100]})

    elif conn_type == "google":
        cred_path = AUTO / "credentials.json"
        if not cred_path.exists():
            return jsonify({"ok": False, "error": "credentials.json が見つかりません"})
        try:
            import json as _json
            cred = _json.loads(cred_path.read_text())
            email = cred.get("client_email", "不明")
            return jsonify({"ok": True, "detail": f"サービスアカウント: {email}"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)[:100]})

    return jsonify({"ok": False, "error": "Unknown connection type"})


@app.route("/settings")
def settings_index():
    """設定トップ（ブランド選択）"""
    brands = load_brands()
    return render_template("brands.html", brands=brands,
                           brand_stats={}, platform_icons=PLATFORM_ICONS,
                           settings_mode=True)


@app.route("/media/<path:filename>")
def serve_media(filename):
    """生成されたメディアファイルを配信"""
    media_dir = AUTO / "generated_media"
    return send_from_directory(str(media_dir), filename)


@app.route("/inbox")
def inbox_page():
    """写真インボックスページ"""
    brands = load_brands()
    inbox_data = {}
    for bid in brands:
        inbox_dir = INBOX_DIR / bid
        processed_dir = PROCESSED_DIR / bid
        files = sorted(inbox_dir.glob("*")) if inbox_dir.exists() else []
        media_exts = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".mp4", ".mov", ".m4v"}
        inbox_files = [
            {
                "name": f.name,
                "size": round(f.stat().st_size / 1024, 1),
                "is_video": f.suffix.lower() in {".mp4", ".mov", ".m4v"},
                "brand": bid,
            }
            for f in files if f.is_file() and f.suffix.lower() in media_exts
        ]
        processed_count = len(list(processed_dir.glob("*"))) if processed_dir.exists() else 0
        inbox_data[bid] = {
            "files": inbox_files,
            "processed_count": processed_count,
            "path": str(INBOX_DIR / bid),
        }
    return render_template("inbox.html", inbox_data=inbox_data, brands=brands)


@app.route("/api/railway/sync", methods=["POST"])
def api_railway_sync():
    """現在の .env を Railway 本番環境変数に同期する"""
    import urllib.request as _req
    import json as _json

    token      = os.environ.get("RAILWAY_API_TOKEN", "")
    project_id = os.environ.get("RAILWAY_PROJECT_ID", "")
    service_id = os.environ.get("RAILWAY_SERVICE_ID", "")

    if not token:
        return jsonify({"ok": False, "error": "RAILWAY_API_TOKEN が設定されていません。設定を保存してから再試行してください。"})
    if not project_id:
        return jsonify({"ok": False, "error": "RAILWAY_PROJECT_ID が設定されていません。"})
    if not service_id:
        return jsonify({"ok": False, "error": "RAILWAY_SERVICE_ID が設定されていません。"})

    # .env を読み込む（RAILWAY_* 自身は除外）
    env_path = AUTO / ".env"
    skip_keys = {"RAILWAY_API_TOKEN"}
    variables = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            if k and k not in skip_keys and v.strip():
                variables[k] = v.strip()

    def _gql(query, vars_=None):
        body = _json.dumps({"query": query, "variables": vars_ or {}}).encode()
        r = _req.Request(
            "https://backboard.railway.app/graphql/v2",
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with _req.urlopen(r, timeout=15) as res:
            return _json.loads(res.read())

    try:
        # production 環境の ID を取得
        env_resp = _gql(
            """query P($id:String!){project(id:$id){environments{edges{node{id name}}}}}""",
            {"id": project_id},
        )
        environments = env_resp["data"]["project"]["environments"]["edges"]
        env_id = None
        for e in environments:
            if e["node"]["name"].lower() in ("production", "prod"):
                env_id = e["node"]["id"]
                break
        if not env_id and environments:
            env_id = environments[0]["node"]["id"]
        if not env_id:
            return jsonify({"ok": False, "error": "Railway の environment が見つかりません。"})

        # 変数を一括 upsert
        _gql(
            """mutation U($input:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$input)}""",
            {"input": {
                "projectId":     project_id,
                "environmentId": env_id,
                "serviceId":     service_id,
                "variables":     variables,
            }},
        )

        log.info(f"Railway sync: {len(variables)}件の環境変数を同期")
        return jsonify({"ok": True, "synced": len(variables)})

    except Exception as e:
        log.error(f"Railway sync error: {e}")
        return jsonify({"ok": False, "error": str(e)[:200]})


@app.route("/api/inbox/process", methods=["POST"])
def api_inbox_process():
    """インボックス手動処理トリガー"""
    d = request.get_json() or {}
    brand = d.get("brand")
    dry_run = d.get("dry_run", False)
    try:
        from sns.photo_importer import process_inbox
        count = process_inbox(brand=brand, dry_run=dry_run)
        return jsonify({"ok": True, "processed": count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/performance")
def performance_page():
    """パフォーマンス分析ページ"""
    brands = load_brands()
    perf_data = {}
    if PERF_LOG_PATH.exists():
        try:
            from sns.performance import get_engagement_report, get_top_performing_posts
            for bid in brands:
                report = get_engagement_report(bid, days=28)
                top_posts = get_top_performing_posts(bid, "instagram", limit=5, days=90)
                perf_data[bid] = {"report": report, "top_posts": top_posts}
        except Exception as e:
            log.error(f"performance data error: {e}")
    return render_template("performance.html", brands=brands, perf_data=perf_data)


@app.route("/weekly")
def weekly_page():
    """週次AIカレンダービューア"""
    brands = load_brands()
    calendars = {}
    if CALENDAR_DIR.exists():
        for f in sorted(CALENDAR_DIR.glob("*.yaml"), reverse=True):
            try:
                cal = yaml.safe_load(f.read_text(encoding="utf-8"))
                if cal:
                    brand_key = cal.get("brand", f.stem.split("_")[1] if "_" in f.stem else "unknown")
                    if brand_key not in calendars:
                        cal["_file"] = f.name
                        calendars[brand_key] = cal
            except Exception:
                pass
    return render_template("weekly.html", brands=brands, calendars=calendars, ai=ai_available())


@app.route("/api/queue/schedule_week/<brand_id>", methods=["POST"])
def api_schedule_week(brand_id):
    """週次カレンダーのアイテムを予約キューに一括登録"""
    brands = load_brands()
    if brand_id not in brands:
        return jsonify({"error": "Brand not found"}), 404

    d     = request.get_json() or {}
    items = d.get("items", [])  # [{date, time, platform, caption_draft, hashtags, topic, format}]

    scheduled = 0
    for i, item in enumerate(items):
        platform    = item.get("platform", "instagram")
        date_str    = item.get("date", "")
        time_str    = item.get("time", "12:00")
        scheduled_at = f"{date_str} {time_str}"
        caption     = item.get("caption_draft", item.get("caption", "")).strip()
        hashtags    = item.get("hashtags", "").strip()
        topic       = item.get("topic", "")
        fmt         = item.get("format", "image")

        # ファイル名: 日付_時刻_プラットフォーム.yaml（重複防止に連番）
        fname = f"{date_str}_{time_str.replace(':','')}_{platform}_{i:02d}.yaml"

        base = {"brand": brand_id, "channel": platform, "posted": False,
                "source": "weekly_calendar", "topic": topic, "scheduled_at": scheduled_at}

        if platform == "instagram":
            full_caption = f"{caption}\n\n{hashtags}".strip() if hashtags else caption
            entry = {**base, "media_type": fmt, "caption": full_caption, "image_url": ""}
            save_yaml(QUEUE_ROOT / brand_id / platform, fname, entry)
            save_yaml(IG_QUEUE, fname, entry)
        elif platform == "line":
            entry = {**base, "message": item.get("message_draft", caption)}
            save_yaml(QUEUE_ROOT / brand_id / platform, fname, entry)
            save_yaml(LINE_QUEUE, fname, entry)
        else:
            full_caption = f"{caption}\n\n{hashtags}".strip() if hashtags else caption
            entry = {**base, "text": full_caption}
            save_yaml(QUEUE_ROOT / brand_id / platform, fname, entry)

        scheduled += 1

    return jsonify({"ok": True, "scheduled": scheduled})


@app.route("/api/ai/blog_post", methods=["POST"])
def api_generate_blog_post():
    """個人ブログ記事をAIで生成（+ オプションでWordPressに下書き保存）"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_blog_post
    d = request.get_json() or {}
    topic       = d.get("topic", "")
    style       = d.get("style", "体験談・実践寄り")
    word_count  = int(d.get("word_count", 1200))
    save_to_wp  = d.get("save_to_wp", False)   # Trueで下書き保存
    publish     = d.get("publish", False)       # Trueで即公開

    if not topic:
        return jsonify({"error": "topic は必須です"}), 400
    try:
        post = generate_blog_post(topic=topic, style=style, word_count=word_count)

        wp_result = None
        if save_to_wp:
            from sns.wordpress import WordPressPoster
            wp = WordPressPoster(brand="satoshi-blog")
            status = "publish" if publish else "draft"
            wp_result = wp.create_post(
                title=post["title"],
                content=post["content_html"],
                status=status,
            )

        # キューにも保存
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        entry = {
            "brand": "satoshi-blog", "channel": "wordpress",
            "title": post["title"], "content": post["content_html"],
            "status": "publish" if publish else "draft",
            "meta_description": post.get("meta_description", ""),
            "tags": post.get("tags", []),
            "posted": bool(wp_result and wp_result.get("status") in ("draft","publish","published")),
            "source": "ai_blog",
        }
        save_yaml(QUEUE_ROOT / "satoshi-blog" / "wordpress", f"{ts}_blog.yaml", entry)

        return jsonify({"ok": True, "post": post, "wp": wp_result})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/ai/weekly_calendar/<brand_id>", methods=["POST"])
def api_generate_weekly_calendar(brand_id):
    """週次カレンダーをAIで生成してYAMLに保存"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_weekly_calendar, save_weekly_calendar
    d = request.get_json() or {}
    try:
        calendar = generate_weekly_calendar(brand=brand_id, week_start=d.get("week_start"))
        saved_path = save_weekly_calendar(calendar, brand=brand_id)
        return jsonify({"ok": True, "path": str(saved_path), "calendar": calendar})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/trending_topics/<brand_id>", methods=["POST"])
def api_trending_topics(brand_id):
    """トレンドトピックをAIがリサーチして提案"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import research_trending_topics
    try:
        topics = research_trending_topics(brand=brand_id, n=5)
        return jsonify({"ok": True, "topics": topics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/generate_variants/<brand_id>", methods=["POST"])
def api_generate_variants(brand_id):
    """3バリアント生成＋AI自動選択"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_instagram_post_variants
    d = request.get_json() or {}
    try:
        result = generate_instagram_post_variants(
            topic=d.get("topic", ""),
            target=d.get("target", "中小企業経営者"),
            tone=d.get("tone", "実務的"),
            brand=brand_id,
            extra=d.get("extra", ""),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/reel_rich/<brand_id>", methods=["POST"])
def api_reel_rich(brand_id):
    """豪華リール台本（BGM・テロップ・シーン割り）"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_reel_script_rich
    d = request.get_json() or {}
    try:
        result = generate_reel_script_rich(
            topic=d.get("topic", ""),
            brand=brand_id,
            duration_sec=int(d.get("duration_sec", 30)),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/shorts/<brand_id>", methods=["POST"])
def api_shorts(brand_id):
    """YouTube Shorts専用コンテンツ生成"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_shorts_content
    d = request.get_json() or {}
    try:
        result = generate_shorts_content(topic=d.get("topic", ""), brand=brand_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/tiktok/<brand_id>", methods=["POST"])
def api_tiktok_content(brand_id):
    """TikTok専用コンテンツ生成"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_tiktok_content
    d = request.get_json() or {}
    try:
        result = generate_tiktok_content(topic=d.get("topic", ""), brand=brand_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── リール・ストーリー ────────────────────────────────────

@app.route("/reels")
def reels_page():
    brands = load_brands()
    return render_template("reels.html", brands=brands, ai=ai_available())


@app.route("/stories")
def stories_page():
    brands = load_brands()
    return render_template("stories.html", brands=brands, ai=ai_available())


@app.route("/api/ai/reel_v2/<brand_id>", methods=["POST"])
def api_reel_v2(brand_id):
    """リール台本 v2（スタイル・ナレーション付き）"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_reel_script_v2
    d = request.get_json() or {}
    try:
        result = generate_reel_script_v2(
            topic=d.get("topic", ""),
            brand=brand_id,
            duration=int(d.get("duration", 30)),
            style=d.get("style", "教育系"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai/story/<brand_id>", methods=["POST"])
def api_story(brand_id):
    """ストーリーコンテンツ生成"""
    if not ai_available():
        return jsonify({"error": "ANTHROPIC_API_KEY未設定"}), 400
    from dashboard.ai import generate_story_content
    d = request.get_json() or {}
    try:
        result = generate_story_content(
            topic=d.get("topic", ""),
            brand=brand_id,
            story_type=d.get("story_type", "promotion"),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/queue/save_reel/<brand_id>", methods=["POST"])
def api_save_reel(brand_id):
    """生成したリール台本をキューに保存"""
    brands = load_brands()
    if brand_id not in brands:
        return jsonify({"error": "Brand not found"}), 404
    d    = request.get_json() or {}
    data = d.get("reel", {})
    ts   = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    entry = {
        "brand": brand_id, "channel": "instagram", "media_type": "reel",
        "title": data.get("title", ""), "caption": data.get("caption", ""),
        "hashtags": data.get("hashtags", ""), "video_url": "",
        "source": "ai_reel", "topic": d.get("topic", ""),
        "filename": f"{ts}_reel.yaml",
        "reel_script": data,
    }
    db.enqueue(entry)
    save_yaml(QUEUE_ROOT / brand_id / "instagram", f"{ts}_reel.yaml", entry)
    db.log_activity("queue_add", brand=brand_id, platform="instagram",
                    detail=f"リール追加: {data.get('title','')}")
    return jsonify({"ok": True})


@app.route("/api/queue/save_story/<brand_id>", methods=["POST"])
def api_save_story(brand_id):
    """生成したストーリーをキューに保存"""
    brands = load_brands()
    if brand_id not in brands:
        return jsonify({"error": "Brand not found"}), 404
    d    = request.get_json() or {}
    data = d.get("story", {})
    ts   = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    entry = {
        "brand": brand_id, "channel": "instagram", "media_type": "story",
        "caption": data.get("caption", ""), "hashtags": data.get("hashtags", ""),
        "source": "ai_story", "topic": d.get("topic", ""),
        "filename": f"{ts}_story.yaml",
        "story_frames": data.get("frames", []),
    }
    db.enqueue(entry)
    save_yaml(QUEUE_ROOT / brand_id / "instagram", f"{ts}_story.yaml", entry)
    db.log_activity("queue_add", brand=brand_id, platform="instagram",
                    detail=f"ストーリー追加: {d.get('topic','')}")
    return jsonify({"ok": True})


# ══════════════════════════════════════════
# ASSET BRAIN
# ══════════════════════════════════════════

@app.route("/assets")
def assets():
    brands = load_brands()
    brand   = request.args.get("brand", "")
    atype   = request.args.get("type", "")
    channel = request.args.get("channel", "")
    season  = request.args.get("season", "")
    status  = request.args.get("status", "active")
    tag_id  = int(request.args.get("tag", 0))
    q       = request.args.get("q", "")

    asset_list = db.list_assets(
        brand=brand, asset_type=atype, channel=channel,
        season=season, status=status, tag_id=tag_id, q=q,
    )
    for a in asset_list:
        a["tags"] = db.get_asset_tags(a["asset_id"])

    tags = db.list_tags()
    stats = db.get_asset_stats()
    brand_ids = list(brands.keys())
    alerts = get_missing_alerts(brand_ids)

    recommended: dict = {}
    if brand:
        for ch in PLATFORMS:
            recs = get_recommended_by_channel(brand, ch, limit=4)
            if recs:
                recommended[ch] = recs

    return render_template(
        "assets.html",
        assets=asset_list, tags=tags, stats=stats,
        brands=brands, alerts=alerts, recommended=recommended,
        filters={"brand": brand, "type": atype, "channel": channel,
                 "season": season, "status": status, "tag": tag_id, "q": q},
        platforms=PLATFORMS, platform_icons=PLATFORM_ICONS,
    )


@app.route("/assets/<asset_id>")
def asset_detail(asset_id):
    asset = db.get_asset(asset_id)
    if not asset:
        return jsonify({"error": "not found"}), 404
    asset["tags"] = db.get_asset_tags(asset_id)
    asset["usages"] = db.get_asset_usages(asset_id)
    return jsonify(asset)


@app.route("/assets", methods=["POST"])
def asset_create():
    data = request.get_json(force=True) or {}
    if not data.get("brand") or not data.get("asset_type"):
        return jsonify({"error": "brand と asset_type は必須"}), 400
    aid = db.upsert_asset(data)
    for tag_name in data.get("tags", []):
        db.add_asset_tag(aid, tag_name)
    db.log_activity("asset_create", brand=data["brand"],
                    detail=f"{data.get('title',aid)} ({data['asset_type']})")
    return jsonify({"ok": True, "asset_id": aid})


@app.route("/assets/<asset_id>", methods=["PATCH"])
def asset_update(asset_id):
    data = request.get_json(force=True) or {}
    existing = db.get_asset(asset_id)
    if not existing:
        return jsonify({"error": "not found"}), 404
    merged = {**existing, **data, "asset_id": asset_id}
    db.upsert_asset(merged)
    return jsonify({"ok": True})


@app.route("/assets/<asset_id>", methods=["DELETE"])
def asset_delete(asset_id):
    db.delete_asset(asset_id)
    return jsonify({"ok": True})


@app.route("/assets/<asset_id>/tags", methods=["POST"])
def asset_tag_add(asset_id):
    data = request.get_json(force=True) or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    tid = db.add_asset_tag(asset_id, name,
                           category=data.get("category", "general"),
                           color=data.get("color", "#6366f1"))
    return jsonify({"ok": True, "tag_id": tid})


@app.route("/assets/<asset_id>/tags/<int:tag_id>", methods=["DELETE"])
def asset_tag_remove(asset_id, tag_id):
    db.remove_asset_tag(asset_id, tag_id)
    return jsonify({"ok": True})


@app.route("/assets/<asset_id>/usage", methods=["POST"])
def asset_usage_record(asset_id):
    data = request.get_json(force=True) or {}
    uid = db.record_asset_usage(
        asset_id,
        channel=data.get("channel", ""),
        brand=data.get("brand", ""),
        used_in=data.get("used_in", ""),
        result_note=data.get("result_note", ""),
        performance=data.get("performance", {}),
    )
    return jsonify({"ok": True, "usage_id": uid})


# ══════════════════════════════════════════════════════════
# MEO Control Tower & Review Reply Center
# ══════════════════════════════════════════════════════════

import sys as _sys
_sys.path.insert(0, str(AUTO))
from repositories.meo_repo import (
    list_profiles, get_profile, list_reviews,
    count_unanswered, count_low_rating, list_pending_drafts,
    approve_draft, mark_draft_sent, set_review_replied,
    add_draft, sync_from_connector, get_latest_insights,
    list_bp_posts,
)
from connectors.gbp_connector import get_connector as _get_gbp_connector


def _meo_stats() -> dict:
    profiles = list_profiles()
    total_unanswered = count_unanswered()
    total_low_rating = count_low_rating(threshold=2)
    pending_drafts   = len(list_pending_drafts())
    photo_alerts     = sum(1 for p in profiles if p.get("photo_alert"))
    avg_score        = int(sum(p.get("meo_score", 0) for p in profiles) / len(profiles)) if profiles else 0
    return {
        "profiles":       len(profiles),
        "unanswered":     total_unanswered,
        "low_rating":     total_low_rating,
        "pending_drafts": pending_drafts,
        "photo_alerts":   photo_alerts,
        "avg_meo_score":  avg_score,
    }


@app.route("/meo")
def meo_tower():
    profiles = list_profiles()
    stats    = _meo_stats()
    # 各店舗の最新インサイトを付与
    for p in profiles:
        p["insights"] = get_latest_insights(p["id"]) or {}
    return render_template("meo.html", profiles=profiles, stats=stats)


@app.route("/meo/<profile_id>")
def meo_detail(profile_id):
    profile = get_profile(profile_id)
    if not profile:
        return "店舗が見つかりません", 404
    reviews  = list_reviews(profile_id=profile_id)
    posts    = list_bp_posts(profile_id=profile_id)
    insights = get_latest_insights(profile_id) or {}
    # 星ごとの集計
    rating_dist = {i: 0 for i in range(1, 6)}
    for r in reviews:
        rating_dist[r.get("rating", 1)] = rating_dist.get(r.get("rating", 1), 0) + 1
    return render_template(
        "meo_detail.html",
        profile=profile, reviews=reviews, posts=posts,
        insights=insights, rating_dist=rating_dist,
    )


@app.route("/reviews")
def review_center():
    tab      = request.args.get("tab", "unanswered")
    profile_id = request.args.get("profile_id", "")
    profiles = list_profiles()

    if tab == "unanswered":
        reviews = list_reviews(profile_id=profile_id, status="unanswered")
    elif tab == "low_rating":
        reviews = list_reviews(profile_id=profile_id, max_rating=2)
    elif tab == "drafts":
        reviews = list_pending_drafts()
    else:
        reviews = list_reviews(profile_id=profile_id)

    stats = _meo_stats()
    return render_template(
        "review_center.html",
        reviews=reviews, tab=tab, profiles=profiles,
        selected_profile=profile_id, stats=stats,
    )


@app.route("/api/meo/sync", methods=["POST"])
def api_meo_sync():
    try:
        connector = _get_gbp_connector()
        counts = sync_from_connector(connector)
        db.log_activity("meo_sync", detail=str(counts))
        return jsonify({"ok": True, "counts": counts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/reviews/<review_id>/generate_draft", methods=["POST"])
def api_generate_draft(review_id):
    from repositories.meo_repo import get_review as _get_review
    review = _get_review(review_id)
    if not review:
        return jsonify({"error": "not found"}), 404

    if not ai_available():
        # AI未接続時はテンプレート返信を生成
        rating = review.get("rating", 3)
        name   = review.get("reviewer_name", "お客様")
        if rating >= 4:
            draft = f"{name}様、温かいお言葉をありがとうございます。スタッフ一同、励みになります。またのご来店をお待ちしております。"
        elif rating == 3:
            draft = f"{name}様、ご利用いただきありがとうございます。ご意見を参考に、より良いサービスを目指してまいります。"
        else:
            draft = f"{name}様、ご不満をおかけし大変申し訳ございません。ご指摘の点を真摯に受け止め、改善に努めます。直接ご連絡いただけますと幸いです。"
    else:
        try:
            import anthropic as _ant
            client = _ant.Anthropic()
            rating  = review.get("rating", 3)
            comment = review.get("comment", "（コメントなし）")
            name    = review.get("reviewer_name", "お客様")
            prompt  = (
                f"Googleビジネスプロフィールのレビューへの返信文を作成してください。\n\n"
                f"レビュアー名: {name}\n評価: {rating}星\nコメント: {comment}\n\n"
                f"要件:\n- 丁寧な日本語で200文字以内\n- 感謝または謝罪から始める\n"
                f"- 具体的なアクションに言及\n- 返信文のみ出力（説明不要）"
            )
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            draft = resp.content[0].text.strip()
        except Exception as e:
            err_str = str(e)
            # クレジット不足など API エラー時はテンプレートにフォールバック
            if "credit" in err_str.lower() or "billing" in err_str.lower() or "400" in err_str:
                rating = review.get("rating", 3)
                name   = review.get("reviewer_name", "お客様")
                if rating >= 4:
                    draft = f"{name}様、温かいお言葉をありがとうございます。スタッフ一同、励みになります。またのご来店をお待ちしております。"
                elif rating == 3:
                    draft = f"{name}様、ご利用いただきありがとうございます。ご意見を参考に、より良いサービスを目指してまいります。"
                else:
                    draft = f"{name}様、この度はご不満をおかけし大変申し訳ございません。ご指摘を真摯に受け止め、改善に努めます。直接ご連絡いただけますと幸いです。"
            else:
                return jsonify({"error": err_str}), 500

    draft_id = add_draft(review_id, draft, source="ai")
    return jsonify({"ok": True, "draft_id": draft_id, "draft": draft})


@app.route("/api/reviews/<review_id>/reply", methods=["POST"])
def api_review_reply(review_id):
    data = request.get_json(force=True) or {}
    reply_text = (data.get("reply") or "").strip()
    if not reply_text:
        return jsonify({"error": "reply text required"}), 400

    from repositories.meo_repo import get_review as _get_review
    review = _get_review(review_id)
    if not review:
        return jsonify({"error": "not found"}), 404

    profile = get_profile(review["profile_id"])
    if not profile:
        return jsonify({"error": "profile not found"}), 404

    # GBP に送信（モック or 本番）
    connector = _get_gbp_connector()
    ok = connector.reply_to_review(
        profile["gbp_location_id"], review.get("gbp_review_id", ""), reply_text
    )
    if ok:
        set_review_replied(review_id, reply_text)
        # 下書きがあればsentにする
        draft_id = data.get("draft_id")
        if draft_id:
            mark_draft_sent(int(draft_id))
        db.log_activity("review_reply", brand=profile.get("brand"),
                        detail=f"review_id={review_id}")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "GBP reply failed"}), 500


@app.route("/api/drafts/<int:draft_id>/approve", methods=["POST"])
def api_approve_draft(draft_id):
    approve_draft(draft_id)
    return jsonify({"ok": True})


@app.route("/api/meo/stats")
def api_meo_stats():
    return jsonify(_meo_stats())


@app.route("/api/assets")
def api_assets():
    brand   = request.args.get("brand", "")
    atype   = request.args.get("type", "")
    channel = request.args.get("channel", "")
    q       = request.args.get("q", "")
    assets  = db.list_assets(brand=brand, asset_type=atype, channel=channel, q=q)
    for a in assets:
        a["tags"] = db.get_asset_tags(a["asset_id"])
    return jsonify(assets)


@app.route("/api/assets/stats")
def api_asset_stats():
    return jsonify(db.get_asset_stats())


@app.route("/webhook", methods=["POST"])
def webhook():
    from sns.line_api import LINEMessenger
    from sales.lead_intake import create_lead_from_line, load_lead_by_line_id
    import yaml as _yaml
    from flask import abort
    scenarios_path = AUTO / "config" / "line_scenarios.yaml"
    messenger = LINEMessenger()
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()
    if not messenger.verify_signature(body, signature):
        abort(400)
    data = request.get_json()
    scenarios = _yaml.safe_load(scenarios_path.read_text(encoding="utf-8")) if scenarios_path.exists() else {}
    for event in data.get("events", []):
        event_type = event.get("type")
        user_id = event.get("source", {}).get("userId", "")
        if event_type == "follow":
            welcome = scenarios.get("welcome_message", "ご登録ありがとうございます！")
            messenger.push(user_id, welcome)
        elif event_type == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"]
            reply_token = event.get("replyToken", "")
            existing = load_lead_by_line_id(user_id)
            if not existing:
                profile = messenger.get_profile(user_id)
                create_lead_from_line(user_id, profile.get("displayName", ""), text)
            reply = None
            for item in scenarios.get("keyword_replies", []):
                if any(kw in text for kw in item.get("keywords", [])):
                    reply = item["reply"]
                    break
            if reply:
                messenger.reply(reply_token, reply)
            else:
                messenger.reply(reply_token, "メッセージありがとうございます！\n内容を確認して、担当者からご返信します。\n（平日10:00〜17:00 受付）")
    return "OK"


# ── Agent Workspace ──────────────────────────────────────────────────────────

AGENT_ICONS = {
    "sns_poster":       "📣",
    "line_responder":   "📱",
    "lead_manager":     "🎯",
    "content_creator":  "✍",
    "analytics":        "📊",
    "meo_manager":      "📍",
    "wp_blogger":       "📝",
    "finance":          "💰",
    "ceo":              "🏢",
    "scheduler":        "📅",
    "reviewer":         "⭐",
    "escalation":       "🚨",
}


@app.route("/agent-workspace")
def agent_workspace():
    import org_database as obd
    from agents.orchestrator import get_overview
    try:
        agents = obd.list_ai_agents()
        task_counts = obd.get_task_counts_all_agents()
        for ag in agents:
            ag["task_counts"] = task_counts.get(ag["id"], {})
            ag["brands"] = obd.get_agent_brand_assignments(ag["id"])
        overview = get_overview()
    except Exception as e:
        log.warning(f"agent_workspace data error: {e}")
        agents, overview = [], {}
    return render_template("agent_workspace.html",
        agents=agents, overview=overview, agent_icons=AGENT_ICONS)


@app.route("/agent-workspace/<agent_id>")
def agent_workspace_detail(agent_id):
    import org_database as obd
    agent = obd.get_agent_with_user(agent_id)
    if not agent:
        return "Agent not found", 404
    try:
        capabilities     = obd.get_agent_capabilities_list(agent_id)
        brand_assignments = obd.get_agent_brand_assignments(agent_id)
        tasks_by_status  = {}
        for st in ("idle", "queued", "running", "blocked",
                   "waiting_approval", "completed", "failed", "escalated"):
            tasks_by_status[st] = obd.list_tasks(agent_id=agent_id, status=st, limit=20)
        for task in tasks_by_status.get("waiting_approval", []):
            task["approval"] = obd.get_pending_approval_for_task(task["id"])
        recent_runs  = obd.list_runs_for_agent(agent_id, limit=10)
        escalations  = obd.list_escalations_for_agent(agent_id, status="open")
        all_tasks    = obd.list_tasks(limit=200)
    except Exception as e:
        log.warning(f"agent_detail data error: {e}")
        capabilities, brand_assignments = [], []
        tasks_by_status = {s: [] for s in ("idle", "queued", "running", "blocked",
                                            "waiting_approval", "completed", "failed", "escalated")}
        recent_runs, escalations, all_tasks = [], [], []
    return render_template("agent_detail.html",
        agent=agent,
        capabilities=capabilities,
        brand_assignments=brand_assignments,
        tasks=tasks_by_status,
        recent_runs=recent_runs,
        escalations=escalations,
        all_tasks=all_tasks,
        agent_icons=AGENT_ICONS,
    )


# ── Task API ──────────────────────────────────────────────────────────────────

@app.route("/api/tasks", methods=["POST"])
def api_create_task():
    from agents.task_service import create_task
    d = request.get_json(force=True) or {}
    try:
        task_id = create_task(
            title=d.get("title", ""),
            mode=d.get("mode", "full_auto"),
            assigned_to_agent_id=d.get("agent_id", ""),
            priority=int(d.get("priority", 3)),
            description=d.get("description", ""),
            depends_on=d.get("depends_on", []),
            scheduled_at=d.get("scheduled_at", ""),
            brand_id=d.get("brand_id", ""),
        )
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/<task_id>/start", methods=["POST"])
def api_start_task(task_id):
    from agents.orchestrator import start_task
    run_id = start_task(task_id)
    if run_id:
        return jsonify({"ok": True, "run_id": run_id})
    return jsonify({"ok": False, "error": "起動できませんでした（エージェント未割当または状態不正）"}), 400


@app.route("/api/tasks/<task_id>/complete", methods=["POST"])
def api_complete_task(task_id):
    import org_database as obd
    from agents.orchestrator import complete_task
    d = request.get_json(force=True) or {}
    runs = obd.list_runs_for_task(task_id, limit=1)
    if not runs:
        return jsonify({"ok": False, "error": "実行中のrunが見つかりません"}), 400
    complete_task(task_id, runs[0]["id"],
                  output_data=d.get("output_data"),
                  log_entries=d.get("log_entries"),
                  tokens_used=int(d.get("tokens_used", 0)),
                  cost_usd=float(d.get("cost_usd", 0.0)))
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/fail", methods=["POST"])
def api_fail_task(task_id):
    import org_database as obd
    from agents.orchestrator import fail_task
    d = request.get_json(force=True) or {}
    runs = obd.list_runs_for_task(task_id, limit=1)
    if not runs:
        return jsonify({"ok": False, "error": "実行中のrunが見つかりません"}), 400
    fail_task(task_id, runs[0]["id"], error=d.get("error", "手動で失敗に設定"))
    return jsonify({"ok": True})


@app.route("/api/tasks/<task_id>/request-approval", methods=["POST"])
def api_request_approval(task_id):
    from agents.orchestrator import request_approval
    d = request.get_json(force=True) or {}
    try:
        approval_id = request_approval(
            task_id=task_id,
            title=d.get("title", "承認リクエスト"),
            description=d.get("description", ""),
            approver_user_ids=d.get("approver_user_ids"),
            expires_in_hours=int(d.get("expires_in_hours", 48)),
        )
        return jsonify({"ok": True, "approval_id": approval_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/tasks/<task_id>/approve", methods=["POST"])
def api_approve_task_route(task_id):
    from agents.orchestrator import approve_task
    d = request.get_json(force=True) or {}
    ok = approve_task(task_id,
                      approver_user_id=d.get("approver_user_id", ""),
                      comment=d.get("comment", ""))
    return jsonify({"ok": ok})


@app.route("/api/tasks/<task_id>/reject", methods=["POST"])
def api_reject_task_route(task_id):
    from agents.orchestrator import reject_task
    d = request.get_json(force=True) or {}
    ok = reject_task(task_id,
                     approver_user_id=d.get("approver_user_id", ""),
                     comment=d.get("comment", ""))
    return jsonify({"ok": ok})


@app.route("/api/tasks/<task_id>/escalate", methods=["POST"])
def api_escalate_task(task_id):
    import org_database as obd
    from agents.orchestrator import escalate
    d = request.get_json(force=True) or {}
    task = obd.get_task(task_id)
    agent_id = task.get("assigned_to_agent_id", "") if task else ""
    esc_id = escalate(task_id,
                      reason=d.get("reason", "手動エスカレーション"),
                      agent_id=agent_id,
                      context=d.get("context", {}))
    return jsonify({"ok": True, "escalation_id": esc_id})


@app.route("/api/tasks/<task_id>/requeue", methods=["POST"])
def api_requeue_task(task_id):
    from agents.orchestrator import requeue_task
    ok = requeue_task(task_id)
    return jsonify({"ok": ok})


@app.route("/api/tasks/<task_id>/force-queue", methods=["POST"])
def api_force_queue(task_id):
    from agents.task_service import transition
    ok = transition(task_id, "queued")
    return jsonify({"ok": ok})


# ── Escalation API ────────────────────────────────────────────────────────────

@app.route("/api/escalations/<escalation_id>/resolve", methods=["POST"])
def api_resolve_escalation(escalation_id):
    from agents.orchestrator import resolve_escalation
    d = request.get_json(force=True) or {}
    ok = resolve_escalation(escalation_id,
                            note=d.get("note", ""),
                            requeue=d.get("requeue", True))
    return jsonify({"ok": ok})


# ── Task approve（社長直接承認） ──────────────────────────────────────────────

@app.route("/api/tasks/<task_id>/approve", methods=["POST"])
def api_task_approve(task_id):
    try:
        import org_database as obd
        obd.update_task_status(task_id, "idle")
        db.write_audit("task_approved", "agent_task", task_id,
                       user_name="社長", detail={"action": "approved_via_dashboard"})
        db.push_notification(
            title="タスク承認済み",
            body=f"タスク ({task_id[:8]}…) が承認されました。エージェントが実行します。",
            link="/agent-workspace",
            type_="success",
        )
    except Exception as e:
        log.warning(f"task_approve error: {e}")
    return redirect(url_for("agent_workspace_page"))


# ── Orchestrator API ──────────────────────────────────────────────────────────

@app.route("/api/orchestrator/tick", methods=["POST"])
def api_orchestrator_tick():
    from agents.orchestrator import tick
    try:
        summary = tick()
        return jsonify({"ok": True, "summary": summary})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/orchestrator/seed", methods=["POST"])
def api_orchestrator_seed():
    try:
        import seed_org
        seed_org.seed()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/orchestrator/overview")
def api_orchestrator_overview():
    from agents.orchestrator import get_overview
    try:
        return jsonify(get_overview())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def startup():
    """アプリ起動時の初期化処理（gunicorn 起動時もここで初期化される）"""
    # 必要なディレクトリを作成
    for d in [LEADS_DIR, FINANCE_DIR, PROJECTS_DIR, DECISION_DIR,
              IG_QUEUE, LINE_QUEUE, QUEUE_ROOT, LOGS_DIR, CALENDAR_DIR,
              INBOX_DIR, PROCESSED_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    # DBを初期化
    db.init_db()
    # os_config.yaml のAgent定義をDBに同期（初回登録のみ）
    try:
        os_cfg = load_os_config()
        for ag in os_cfg.get("agents", []):
            existing = db.get_agent(ag["id"])
            if not existing:
                db.upsert_agent({
                    "agent_id": ag["id"],
                    "name":     ag["name"],
                    "role":     ag.get("role", ""),
                    "brand":    ag.get("brand"),
                    "status":   ag.get("status", "active"),
                    "run_count": 0,
                })
        # AI CEO も登録
        ceo = os_cfg.get("ai_ceo", {})
        if ceo.get("id") and not db.get_agent(ceo["id"]):
            db.upsert_agent({
                "agent_id": ceo["id"],
                "name":     ceo.get("name", "CEO Agent"),
                "role":     ceo.get("role", "AI CEO"),
                "brand":    None,
                "status":   ceo.get("status", "active"),
                "run_count": 0,
            })
    except Exception as e:
        log.warning(f"Agents同期スキップ: {e}")
    # YAMLデータをDBに移行（初回のみ）
    try:
        migrated = db.migrate_from_yaml()
        if any(v > 0 for v in migrated.values()):
            log.info(f"YAML→DB移行完了: {migrated}")
    except Exception as e:
        log.warning(f"YAML移行スキップ: {e}")
    # Asset Brain モックデータを投入（空の場合のみ）
    try:
        n = seed_mock_data()
        if n:
            log.info(f"Asset Brain: モックデータ {n} 件投入")
    except Exception as e:
        log.warning(f"Asset Brain シードスキップ: {e}")
    # MEO 初期同期（DBが空の場合のみモックデータを投入）
    try:
        with db.get_conn() as _conn:
            _cnt = _conn.execute("SELECT COUNT(*) FROM business_profiles").fetchone()[0]
        if _cnt == 0:
            from connectors.gbp_connector import MockGBPConnector
            from repositories.meo_repo import sync_from_connector as _meo_sync
            _counts = _meo_sync(MockGBPConnector())
            log.info(f"MEO モックデータ投入: {_counts}")
    except Exception as e:
        log.warning(f"MEO 初期化スキップ: {e}")
    # Org DB（エージェント実行フレームワーク）を初期シード
    try:
        import org_database as obd
        obd.init_org_db()
        obd.seed_default_roles()
        agents = obd.list_ai_agents()
        if not agents:
            import seed_org
            seed_org.seed()
            log.info("Org DB: 初期シード完了")
    except Exception as e:
        log.warning(f"Org DB シードスキップ: {e}")
    # 起動通知（未読が0件の場合のみ）
    try:
        if db.count_unread_notifications() == 0:
            db.push_notification(
                title="Brand OS 起動完了",
                body="ダッシュボードが正常に起動しました。",
                link="/",
                type_="info",
                source="system",
            )
    except Exception as e:
        log.warning(f"起動通知スキップ: {e}")
    log.info("✅ ダッシュボード初期化完了")



# ══════════════════════════════════════════════════════════════
# NoiMos AI — 元ネタアーカイブ & 構造抽出
# ══════════════════════════════════════════════════════════════

FORMATS = ["feed", "story", "reel", "meo", "blog", "line"]
RISK_FLAG_OPTIONS = [
    "著作権リスク", "競合模倣", "ブランド不適合", "炎上可能性",
    "景品表示法", "誇大表現", "コンプライアンス要確認",
]
FORMAT_ICONS = {
    "feed": "🖼", "story": "📸", "reel": "🎬",
    "meo": "📍", "blog": "📝", "line": "📱",
}


@app.route("/noimos")
def noimos_list():
    status_filter = request.args.get("status", "")
    patterns  = db.list_viral_patterns(status=status_filter)
    stats     = db.get_noimos_stats()
    campaigns = db.list_campaigns()
    return render_template("noimos.html",
        patterns=patterns, stats=stats, campaigns=campaigns,
        status_filter=status_filter, format_icons=FORMAT_ICONS,
    )


@app.route("/noimos/new", methods=["GET", "POST"])
def noimos_new():
    brands    = load_brands()
    campaigns = db.list_campaigns()
    if request.method == "POST":
        f          = request.form
        formats    = request.form.getlist("format_suitability")
        risk_flags = request.form.getlist("risk_flags")
        pid = db.create_viral_pattern({
            "title":            f.get("title", "").strip(),
            "source_type":      f.get("source_type", ""),
            "source_url":       f.get("source_url", "").strip(),
            "source_caption":   f.get("source_caption", "").strip(),
            "metrics_likes":    int(f.get("metrics_likes") or 0),
            "metrics_comments": int(f.get("metrics_comments") or 0),
            "metrics_saves":    int(f.get("metrics_saves") or 0),
            "metrics_views":    int(f.get("metrics_views") or 0),
            "hook":             f.get("hook", "").strip(),
            "problem_framing":  f.get("problem_framing", "").strip(),
            "emotional_arc":    f.get("emotional_arc", "").strip(),
            "cta":              f.get("cta", "").strip(),
            "format_suitability": formats,
            "risk_flags":       risk_flags,
            "notes":            f.get("notes", "").strip(),
            "status":           "extracted" if f.get("hook") else "draft",
        })
        db.log_activity("noimos_create", detail=f"パターン登録 #{pid}")
        return redirect(url_for("noimos_detail", pid=pid))
    return render_template("noimos_new.html",
        brands=brands, campaigns=campaigns,
        formats=FORMATS, format_icons=FORMAT_ICONS,
        risk_flag_options=RISK_FLAG_OPTIONS,
        source_types=["instagram", "tiktok", "youtube", "twitter", "blog", "other"],
    )


@app.route("/noimos/<int:pid>")
def noimos_detail(pid):
    pattern  = db.get_viral_pattern(pid)
    if not pattern:
        return redirect(url_for("noimos_list"))
    examples = db.list_pattern_examples(pid)
    all_ideas = db.list_content_ideas()
    ideas_linked = [i for i in all_ideas if i.get("pattern_id") == pid]
    campaigns = db.list_campaigns()
    brands    = load_brands()
    return render_template("noimos_detail.html",
        pattern=pattern, examples=examples, ideas_linked=ideas_linked,
        campaigns=campaigns, brands=brands,
        formats=FORMATS, format_icons=FORMAT_ICONS,
        risk_flag_options=RISK_FLAG_OPTIONS,
    )


@app.route("/noimos/<int:pid>/extract", methods=["POST"])
def noimos_extract(pid):
    f          = request.form
    formats    = request.form.getlist("format_suitability")
    risk_flags = request.form.getlist("risk_flags")
    db.update_viral_pattern(pid, {
        "hook":             f.get("hook", "").strip(),
        "problem_framing":  f.get("problem_framing", "").strip(),
        "emotional_arc":    f.get("emotional_arc", "").strip(),
        "cta":              f.get("cta", "").strip(),
        "format_suitability": formats,
        "risk_flags":       risk_flags,
        "notes":            f.get("notes", "").strip(),
        "status":           "extracted",
    })
    db.log_activity("noimos_extract", detail=f"構造抽出 #{pid}")
    return redirect(url_for("noimos_detail", pid=pid))


@app.route("/noimos/<int:pid>/add_example", methods=["POST"])
def noimos_add_example(pid):
    f = request.form
    db.add_pattern_example({
        "pattern_id":      pid,
        "title":           f.get("title", "").strip(),
        "source_platform": f.get("source_platform", ""),
        "source_url":      f.get("source_url", "").strip(),
        "source_account":  f.get("source_account", "").strip(),
        "caption":         f.get("caption", "").strip(),
        "likes":           int(f.get("likes") or 0),
        "comments":        int(f.get("comments") or 0),
        "saves":           int(f.get("saves") or 0),
        "views":           int(f.get("views") or 0),
        "posted_at":       f.get("posted_at", ""),
        "notes":           f.get("notes", "").strip(),
    })
    return redirect(url_for("noimos_detail", pid=pid))


@app.route("/noimos/<int:pid>/convert", methods=["GET", "POST"])
def noimos_convert(pid):
    pattern  = db.get_viral_pattern(pid)
    if not pattern:
        return redirect(url_for("noimos_list"))
    brands    = load_brands()
    campaigns = db.list_campaigns()
    if request.method == "POST":
        f              = request.form
        target_formats = request.form.getlist("target_formats")
        campaign_id    = int(f.get("campaign_id") or 0) or None
        iid = db.create_content_idea({
            "pattern_id":     pid,
            "campaign_id":    campaign_id,
            "brand":          f.get("brand", ""),
            "title":          f.get("title", "").strip(),
            "hook":           f.get("hook", "").strip(),
            "body":           f.get("body", "").strip(),
            "cta":            f.get("cta", "").strip(),
            "target_formats": target_formats,
            "tone":           f.get("tone", "").strip(),
            "notes":          f.get("notes", "").strip(),
            "status":         "draft",
        })
        for fmt in target_formats:
            db.create_content_variant({
                "idea_id": iid,
                "format":  fmt,
                "caption": f.get(f"caption_{fmt}", "").strip() or None,
                "hashtags":f.get(f"hashtags_{fmt}", "").strip() or None,
                "status":  "draft",
            })
        db.log_activity("noimos_convert",
                        detail=f"ブランド変換 pattern#{pid}→idea#{iid}")
        return redirect(url_for("idea_detail", iid=iid))
    return render_template("noimos_convert.html",
        pattern=pattern, brands=brands, campaigns=campaigns,
        formats=FORMATS, format_icons=FORMAT_ICONS,
    )


# ══════════════════════════════════════════════════════════════
# Campaign Pipeline — アイデア & バリアント
# ══════════════════════════════════════════════════════════════

@app.route("/ideas")
def ideas_list():
    brand_filter    = request.args.get("brand", "")
    status_filter   = request.args.get("status", "")
    campaign_filter = int(request.args.get("campaign_id") or 0)
    ideas     = db.list_content_ideas(brand=brand_filter, status=status_filter,
                                       campaign_id=campaign_filter)
    campaigns = db.list_campaigns()
    brands    = load_brands()
    stats     = db.get_noimos_stats()
    return render_template("ideas.html",
        ideas=ideas, campaigns=campaigns, brands=brands, stats=stats,
        brand_filter=brand_filter, status_filter=status_filter,
        campaign_filter=campaign_filter, format_icons=FORMAT_ICONS,
    )


@app.route("/ideas/<int:iid>")
def idea_detail(iid):
    idea     = db.get_content_idea(iid)
    if not idea:
        return redirect(url_for("ideas_list"))
    variants = db.list_content_variants(iid)
    pattern  = db.get_viral_pattern(idea["pattern_id"]) if idea.get("pattern_id") else None
    campaign = db.get_campaign(idea["campaign_id"]) if idea.get("campaign_id") else None
    all_jobs = db.list_publishing_jobs()
    jobs_for = [j for j in all_jobs if j.get("idea_id") == iid]
    campaigns = db.list_campaigns()
    brands    = load_brands()
    return render_template("idea_detail.html",
        idea=idea, variants=variants, pattern=pattern,
        campaign=campaign, campaigns=campaigns, brands=brands,
        jobs_for=jobs_for, formats=FORMATS, format_icons=FORMAT_ICONS,
    )


@app.route("/ideas/<int:iid>/variants", methods=["POST"])
def idea_add_variant(iid):
    f = request.form
    db.create_content_variant({
        "idea_id":      iid,
        "format":       f.get("format", "feed"),
        "caption":      f.get("caption", "").strip(),
        "hashtags":     f.get("hashtags", "").strip(),
        "image_prompt": f.get("image_prompt", "").strip(),
        "video_prompt": f.get("video_prompt", "").strip(),
        "duration_sec": int(f.get("duration_sec") or 0) or None,
        "notes":        f.get("notes", "").strip(),
    })
    return redirect(url_for("idea_detail", iid=iid))


@app.route("/ideas/<int:iid>/approve", methods=["POST"])
def idea_approve(iid):
    db.update_content_idea(iid, {"status": "approved"})
    db.log_activity("idea_approve", detail=f"アイデア承認 #{iid}")
    return redirect(url_for("idea_detail", iid=iid))


@app.route("/ideas/<int:iid>/reject", methods=["POST"])
def idea_reject(iid):
    db.update_content_idea(iid, {"status": "rejected"})
    return redirect(url_for("ideas_list"))


@app.route("/ideas/<int:iid>/request_publish", methods=["POST"])
def idea_request_publish(iid):
    idea = db.get_content_idea(iid)
    if not idea:
        return redirect(url_for("ideas_list"))
    f   = request.form
    vid = int(f.get("variant_id") or 0) or None
    db.create_publishing_job({
        "variant_id":  vid,
        "idea_id":     iid,
        "campaign_id": idea.get("campaign_id"),
        "brand":       idea.get("brand"),
        "platform":    f.get("platform", ""),
        "scheduled_at":f.get("scheduled_at", ""),
        "status":      "pending_approval",
    })
    db.log_activity("publish_request", brand=idea.get("brand"),
                    detail=f"承認依頼 idea#{iid}")
    return redirect(url_for("publishing_queue"))


# ══════════════════════════════════════════════════════════════
# Campaign Pipeline — キャンペーン
# ══════════════════════════════════════════════════════════════

@app.route("/campaigns")
def campaigns_list():
    brand_filter  = request.args.get("brand", "")
    status_filter = request.args.get("status", "")
    campaigns = db.list_campaigns(brand=brand_filter, status=status_filter)
    brands    = load_brands()
    stats     = db.get_noimos_stats()
    for c in campaigns:
        c["ideas"] = db.list_content_ideas(campaign_id=c["id"])
    return render_template("campaigns.html",
        campaigns=campaigns, brands=brands, stats=stats,
        brand_filter=brand_filter, status_filter=status_filter,
    )


@app.route("/campaigns/new", methods=["POST"])
def campaigns_new():
    f = request.form
    cid = db.create_campaign({
        "title":      f.get("title", "").strip(),
        "brand":      f.get("brand", ""),
        "objective":  f.get("objective", ""),
        "start_date": f.get("start_date", ""),
        "end_date":   f.get("end_date", ""),
        "notes":      f.get("notes", "").strip(),
        "status":     "planning",
    })
    db.log_activity("campaign_create", detail=f"キャンペーン作成 #{cid}")
    return redirect(url_for("campaigns_list"))


@app.route("/campaigns/<int:cid>")
def campaign_detail(cid):
    campaign = db.get_campaign(cid)
    if not campaign:
        return redirect(url_for("campaigns_list"))
    ideas = db.list_content_ideas(campaign_id=cid)
    jobs  = [j for j in db.list_publishing_jobs() if j.get("campaign_id") == cid]
    brands = load_brands()
    return render_template("campaign_detail.html",
        campaign=campaign, ideas=ideas, jobs=jobs,
        brands=brands, format_icons=FORMAT_ICONS,
    )


@app.route("/campaigns/<int:cid>/update", methods=["POST"])
def campaign_update(cid):
    f = request.form
    db.update_campaign(cid, {
        "title":      f.get("title", "").strip(),
        "objective":  f.get("objective", ""),
        "status":     f.get("status", "planning"),
        "start_date": f.get("start_date", ""),
        "end_date":   f.get("end_date", ""),
        "notes":      f.get("notes", "").strip(),
    })
    return redirect(url_for("campaign_detail", cid=cid))


# ══════════════════════════════════════════════════════════════
# Campaign Pipeline — 承認キュー
# ══════════════════════════════════════════════════════════════

@app.route("/publishing")
def publishing_queue():
    status_filter = request.args.get("status", "pending_approval")
    filter_arg    = "" if status_filter == "all" else status_filter
    jobs   = db.list_publishing_jobs(status=filter_arg)
    brands = load_brands()
    stats  = db.get_noimos_stats()
    enriched = []
    for j in jobs:
        j2 = dict(j)
        if j.get("idea_id"):
            j2["_idea"] = db.get_content_idea(j["idea_id"])
        if j.get("variant_id"):
            j2["_variant"] = db.get_content_variant(j["variant_id"])
        enriched.append(j2)
    return render_template("publishing.html",
        jobs=enriched, brands=brands, stats=stats,
        status_filter=status_filter,
    )


@app.route("/publishing/<int:jid>/approve", methods=["POST"])
def publishing_approve(jid):
    note = request.form.get("note", "")
    db.update_job_status(jid, "approved", note=note, approved_by="human")
    db.log_activity("publish_approve", detail=f"承認 job#{jid}")
    return redirect(url_for("publishing_queue"))


@app.route("/publishing/<int:jid>/reject", methods=["POST"])
def publishing_reject(jid):
    note = request.form.get("note", "")
    db.update_job_status(jid, "rejected", note=note, approved_by="human")
    db.log_activity("publish_reject", detail=f"却下 job#{jid}")
    return redirect(url_for("publishing_queue"))


@app.route("/api/noimos/stats")
def api_noimos_stats():
    return jsonify(db.get_noimos_stats())


# ══════════════════════════════════════════════════════════════
# Story Autopilot
# ══════════════════════════════════════════════════════════════

def _story_repos():
    from repositories.story_repo import StoryTemplateRepo, StoryRunRepo, SocialAccountRepo, SocialInsightRepo
    return StoryTemplateRepo(), StoryRunRepo(), SocialAccountRepo(), SocialInsightRepo()


@app.route("/story-autopilot")
def story_autopilot():
    """Story Autopilot ダッシュボード。"""
    tmpl_repo, run_repo, acct_repo, insight_repo = _story_repos()
    brands = load_brands()

    brand_f = request.args.get("brand", "")

    templates = tmpl_repo.list(brand=brand_f, active_only=False)
    recent_runs = run_repo.list(brand=brand_f, limit=20)

    # ステータス別件数
    status_counts = run_repo.count_by_status(brand=brand_f)
    pending_count = status_counts.get("pending_approval", 0)

    # ブランド別インサイトサマリー
    insight_summary = {}
    for bid in brands:
        insight_summary[bid] = insight_repo.summary_by_brand(bid)

    return render_template(
        "story_autopilot.html",
        templates=templates,
        recent_runs=recent_runs,
        status_counts=status_counts,
        pending_count=pending_count,
        brands=brands,
        brand_filter=brand_f,
        insight_summary=insight_summary,
    )


@app.route("/story-autopilot/templates")
def story_templates_page():
    tmpl_repo, _, _, _ = _story_repos()
    brands = load_brands()
    brand_f = request.args.get("brand", "")
    templates = tmpl_repo.list(brand=brand_f)
    return render_template("story_templates.html",
                           templates=templates, brands=brands, brand_filter=brand_f)


@app.route("/story-autopilot/templates/new", methods=["GET", "POST"])
def story_template_new():
    tmpl_repo, _, _, _ = _story_repos()
    brands = load_brands()
    if request.method == "POST":
        days = request.form.getlist("active_days") or ["mon","tue","wed","thu","fri","sat","sun"]
        tmpl_repo.create({
            "brand":        request.form["brand"],
            "name":         request.form["name"],
            "description":  request.form.get("description", ""),
            "story_type":   request.form.get("story_type", "promotion"),
            "run_mode":     request.form.get("run_mode", "semi_auto"),
            "active_days":  days,
            "run_time":     request.form.get("run_time", "09:00"),
            "frame_count":  int(request.form.get("frame_count", 3)),
            "topic_prompt": request.form.get("topic_prompt", ""),
            "asset_source": request.form.get("asset_source", "asset_brain"),
            "asset_tags":   [t.strip() for t in request.form.get("asset_tags","").split(",") if t.strip()],
            "is_active":    bool(request.form.get("is_active")),
        })
        return redirect(url_for("story_templates_page"))
    return render_template("story_template_detail.html",
                           template=None, brands=brands, is_new=True)


@app.route("/story-autopilot/templates/<int:tmpl_id>", methods=["GET", "POST"])
def story_template_detail(tmpl_id):
    tmpl_repo, _, _, _ = _story_repos()
    brands = load_brands()
    tmpl = tmpl_repo.get(tmpl_id)
    if not tmpl:
        return "Template not found", 404
    if request.method == "POST":
        days = request.form.getlist("active_days") or tmpl["active_days"]
        tmpl_repo.update(tmpl_id, {
            "name":         request.form.get("name", tmpl["name"]),
            "description":  request.form.get("description", ""),
            "story_type":   request.form.get("story_type", "promotion"),
            "run_mode":     request.form.get("run_mode", "semi_auto"),
            "active_days":  days,
            "run_time":     request.form.get("run_time", "09:00"),
            "frame_count":  int(request.form.get("frame_count", 3)),
            "topic_prompt": request.form.get("topic_prompt", ""),
            "asset_source": request.form.get("asset_source", "asset_brain"),
            "asset_tags":   [t.strip() for t in request.form.get("asset_tags","").split(",") if t.strip()],
            "is_active":    1 if request.form.get("is_active") else 0,
        })
        return redirect(url_for("story_templates_page"))
    return render_template("story_template_detail.html",
                           template=tmpl, brands=brands, is_new=False)


@app.route("/story-autopilot/templates/<int:tmpl_id>/delete", methods=["POST"])
def story_template_delete(tmpl_id):
    tmpl_repo, _, _, _ = _story_repos()
    tmpl_repo.delete(tmpl_id)
    return redirect(url_for("story_templates_page"))


@app.route("/story-autopilot/candidates")
def story_candidates():
    _, run_repo, _, _ = _story_repos()
    brands = load_brands()
    brand_f  = request.args.get("brand", "")
    status_f = request.args.get("status", "pending_approval")
    runs = run_repo.list(brand=brand_f, status=status_f, limit=100)
    counts = run_repo.count_by_status(brand=brand_f)
    return render_template("story_candidates.html",
                           runs=runs, counts=counts,
                           brands=brands, brand_filter=brand_f, status_filter=status_f)


@app.route("/story-autopilot/runs")
def story_runs_history():
    _, run_repo, _, _ = _story_repos()
    brands = load_brands()
    brand_f = request.args.get("brand", "")
    runs = run_repo.list(brand=brand_f, limit=100)
    counts = run_repo.count_by_status(brand=brand_f)
    return render_template("story_candidates.html",
                           runs=runs, counts=counts,
                           brands=brands, brand_filter=brand_f, status_filter="",
                           show_all=True)


# ── Story Autopilot API ───────────────────────────────

@app.route("/api/story-autopilot/generate", methods=["POST"])
def api_story_autopilot_generate():
    """テンプレートからストーリー候補を生成してDBに保存する。"""
    data = request.get_json(force=True) or {}
    tmpl_id  = data.get("template_id")
    brand    = data.get("brand", "")
    topic    = data.get("topic", "")
    run_mode = data.get("run_mode", "semi_auto")
    story_type = data.get("story_type", "promotion")

    tmpl_repo, run_repo, acct_repo, _ = _story_repos()

    tmpl = tmpl_repo.get(tmpl_id) if tmpl_id else None
    if tmpl:
        brand      = brand or tmpl["brand"]
        run_mode   = tmpl["run_mode"]
        story_type = tmpl["story_type"]
        if not topic and tmpl.get("topic_prompt"):
            topic = tmpl["topic_prompt"]

    # AI生成を試みる（APIキーがない場合はモックフレームを使う）
    frames = []
    caption = ""
    hashtags = ""
    if ai_available() and topic:
        try:
            import anthropic
            client = anthropic.Anthropic()
            prompt = f"""
あなたはInstagramストーリー専門のコンテンツクリエイターです。
以下のトピックで3枚のストーリーフレームを作成してください。
タイプ: {story_type}
トピック: {topic}
ブランド: {brand}

JSON形式で返してください:
{{
  "frames": [
    {{"emoji":"絵文字","headline":"見出し(20字以内)","subtext":"本文(40字以内)","bg":"purple-blue","type":"hook"}},
    {{"emoji":"絵文字","headline":"見出し","subtext":"本文","bg":"green-teal","type":"detail"}},
    {{"emoji":"絵文字","headline":"CTA","subtext":"本文","bg":"orange-red","type":"cta","button":"ボタンテキスト"}}
  ],
  "caption": "キャプション本文",
  "hashtags": "#タグ1 #タグ2 #タグ3"
}}
"""
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=800,
                messages=[{"role":"user","content":prompt}],
            )
            import re
            raw = msg.content[0].text
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                parsed = json.loads(m.group())
                frames   = parsed.get("frames", [])
                caption  = parsed.get("caption", "")
                hashtags = parsed.get("hashtags", "")
        except Exception as e:
            log.warning(f"Story AI生成エラー: {e}")

    if not frames:
        frames = [
            {"emoji": "📣", "headline": f"{topic[:20] or 'ストーリー'}", "subtext": "詳細はプロフィールへ", "bg": "purple-blue", "type": "hook"},
            {"emoji": "💡", "headline": "ポイント解説", "subtext": "もっと知りたい方はDMへ", "bg": "green-teal", "type": "detail"},
            {"emoji": "✅", "headline": "今すぐチェック", "subtext": "プロフィールのリンクから", "bg": "orange-red", "type": "cta", "button": "詳しく見る"},
        ]

    # アカウントを探す
    accts = acct_repo.list(brand=brand)
    acct_id = accts[0]["id"] if accts else None

    initial_status = "pending_approval" if run_mode in ("semi_auto","human_approval_required") else "pending"

    run_id = run_repo.create({
        "template_id":       tmpl_id,
        "brand":             brand,
        "run_mode":          run_mode,
        "status":            initial_status,
        "story_type":        story_type,
        "topic":             topic,
        "frames_json":       frames,
        "caption":           caption,
        "hashtags":          hashtags,
        "social_account_id": acct_id,
    })
    if tmpl_id:
        tmpl_repo.touch_last_run(tmpl_id)

    run = run_repo.get(run_id)
    return jsonify({"ok": True, "run_id": run_id, "status": initial_status, "run": run})


@app.route("/api/story-autopilot/runs/<int:run_id>/approve", methods=["POST"])
def api_story_run_approve(run_id):
    _, run_repo, acct_repo, _ = _story_repos()
    run = run_repo.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404

    note = (request.get_json(force=True) or {}).get("note", "")
    run_repo.update_status(run_id, "approved", approval_note=note, approved_by="human")

    if run.get("run_mode") == "full_auto" or not note:
        # 承認後に自動で publish へ
        _publish_story_run(run_id, run, acct_repo)

    db.log_activity("story_approve", brand=run["brand"], detail=f"story_run#{run_id} 承認")
    return jsonify({"ok": True})


@app.route("/api/story-autopilot/runs/<int:run_id>/reject", methods=["POST"])
def api_story_run_reject(run_id):
    _, run_repo, _, _ = _story_repos()
    run = run_repo.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    note = (request.get_json(force=True) or {}).get("note", "")
    run_repo.update_status(run_id, "rejected", approval_note=note, approved_by="human")
    db.log_activity("story_reject", brand=run["brand"], detail=f"story_run#{run_id} 却下")
    return jsonify({"ok": True})


@app.route("/api/story-autopilot/runs/<int:run_id>/publish", methods=["POST"])
def api_story_run_publish(run_id):
    _, run_repo, acct_repo, _ = _story_repos()
    run = run_repo.get(run_id)
    if not run:
        return jsonify({"error": "not found"}), 404
    result = _publish_story_run(run_id, run, acct_repo)
    return jsonify(result)


def _publish_story_run(run_id: int, run: dict, acct_repo) -> dict:
    """Story を connector 経由で公開し、DB を更新する。"""
    from connectors.meta_connector import get_meta_connector
    _, run_repo, _, _ = _story_repos()

    acct = acct_repo.get(run.get("social_account_id") or "") or {}
    ig_uid    = acct.get("ig_user_id", run["brand"])
    provider  = acct.get("provider", "auto")  # autoならトークン有無で自動切替

    connector = get_meta_connector(provider)
    try:
        # モック: ダミー画像URLで publish_story を呼ぶ
        result = connector.publish_story(ig_uid, media_url="https://placehold.co/1080x1920/png")
        if result.get("error"):
            run_repo.update_status(run_id, "failed", error_message=result["error"])
            return {"ok": False, "error": result["error"]}
        run_repo.update_status(
            run_id, "published",
            ig_media_id=result.get("ig_media_id", ""),
            ig_permalink=result.get("permalink", ""),
        )
        db.log_activity("story_publish", brand=run["brand"],
                        detail=f"story_run#{run_id} 公開 ig={result.get('ig_media_id','')}")
        return {"ok": True, "ig_media_id": result.get("ig_media_id"), "permalink": result.get("permalink")}
    except Exception as e:
        run_repo.update_status(run_id, "failed", error_message=str(e))
        return {"ok": False, "error": str(e)}


@app.route("/api/story-autopilot/accounts/seed", methods=["POST"])
def api_story_accounts_seed():
    _, _, acct_repo, _ = _story_repos()
    acct_repo.seed_mock()
    return jsonify({"ok": True, "message": "モックアカウントをシードしました"})


@app.route("/api/story-autopilot/templates/seed", methods=["POST"])
def api_story_templates_seed():
    tmpl_repo, run_repo, acct_repo, _ = _story_repos()
    acct_repo.seed_mock()
    tmpl_repo.seed_mock()
    run_repo.seed_mock()
    return jsonify({"ok": True, "message": "デモデータをシードしました"})


@app.route("/api/story-autopilot/accounts/<brand>")
def api_story_accounts(brand):
    _, _, acct_repo, _ = _story_repos()
    accts = acct_repo.list(brand=brand)
    return jsonify(accts)


@app.route("/api/story-autopilot/insights/<brand>")
def api_story_insights(brand):
    _, _, _, insight_repo = _story_repos()
    days = int(request.args.get("days", 28))
    return jsonify(insight_repo.summary_by_brand(brand, days))


startup()

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(AUTO / "logs" / "dashboard.log"), encoding="utf-8"),
        ]
    )
    startup()
    port = int(os.environ.get("PORT", os.environ.get("DASHBOARD_PORT", 8080)))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"\n✅ ダッシュボード起動: http://localhost:{port}")
    if os.environ.get("DASHBOARD_PASSWORD"):
        print("🔒 認証有効 (DASHBOARD_PASSWORD 設定済み)")
    print()
    app.run(host="0.0.0.0", port=port, debug=debug)
