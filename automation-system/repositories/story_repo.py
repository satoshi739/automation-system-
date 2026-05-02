"""
Story Autopilot — Repository Layer

story_templates / story_runs / social_accounts / social_posts の CRUD。
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
import database as db

log = logging.getLogger(__name__)
_NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _get_fernet():
    """TOKEN_ENCRYPTION_KEY 環境変数から Fernet インスタンスを返す。未設定なら None。"""
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as e:
        log.warning("Fernet初期化失敗（TOKEN_ENCRYPTION_KEY が不正）: %s", e)
        return None


def _encrypt_token(plain: str) -> str:
    """アクセストークンを暗号化。KEY未設定時は平文のまま（警告を出す）。"""
    if not plain:
        return plain
    f = _get_fernet()
    if f is None:
        log.warning("TOKEN_ENCRYPTION_KEY未設定 — access_token を平文で保存します")
        return plain
    return f.encrypt(plain.encode()).decode()


def _decrypt_token(cipher: str) -> str:
    """暗号化されたトークンを復号。KEY未設定・復号失敗時は元の文字列を返す。"""
    if not cipher:
        return cipher
    f = _get_fernet()
    if f is None:
        return cipher
    try:
        return f.decrypt(cipher.encode()).decode()
    except Exception:
        return cipher


# ═══════════════════════════════════════════════════════
# Social Accounts
# ═══════════════════════════════════════════════════════

class SocialAccountRepo:

    def list(self, brand: str = "", platform: str = "instagram") -> list[dict]:
        sql = "SELECT * FROM social_accounts WHERE 1=1"
        params: list = []
        if brand:
            sql += " AND brand=?"; params.append(brand)
        if platform:
            sql += " AND platform=?"; params.append(platform)
        sql += " ORDER BY created_at DESC"
        with db.get_conn() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def _decrypt_row(self, row: dict) -> dict:
        if row.get("access_token"):
            row["access_token"] = _decrypt_token(row["access_token"])
        return row

    def get(self, account_id: str) -> Optional[dict]:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM social_accounts WHERE id=?", (account_id,)).fetchone()
        return self._decrypt_row(dict(row)) if row else None

    def list(self, brand: str = "", platform: str = "instagram") -> list[dict]:
        sql = "SELECT * FROM social_accounts WHERE 1=1"
        params: list = []
        if brand:
            sql += " AND brand=?"; params.append(brand)
        if platform:
            sql += " AND platform=?"; params.append(platform)
        sql += " ORDER BY created_at DESC"
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        return [self._decrypt_row(r) for r in rows]

    def upsert(self, data: dict) -> str:
        now = _NOW()
        aid = data.get("id") or f"sa_{data['brand']}_{data.get('platform','instagram')}"
        raw_token = data.get("access_token", "")
        encrypted_token = _encrypt_token(raw_token) if raw_token else None
        with db.get_conn() as conn:
            conn.execute("""
                INSERT INTO social_accounts
                    (id, brand, platform, account_id, account_name, account_type,
                     access_token, ig_user_id, page_id, provider, status,
                     validated_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    account_name=excluded.account_name,
                    account_id=excluded.account_id,
                    access_token=COALESCE(excluded.access_token, access_token),
                    ig_user_id=excluded.ig_user_id,
                    page_id=excluded.page_id,
                    provider=excluded.provider,
                    status=excluded.status,
                    validated_at=excluded.validated_at,
                    updated_at=excluded.updated_at
            """, (
                aid,
                data["brand"],
                data.get("platform", "instagram"),
                data.get("account_id"),
                data.get("account_name"),
                data.get("account_type", "business"),
                encrypted_token,
                data.get("ig_user_id"),
                data.get("page_id"),
                data.get("provider", "mock"),
                data.get("status", "active"),
                data.get("validated_at"),
                data.get("created_at", now),
                now,
            ))
        return aid

    def seed_mock(self) -> None:
        """ブランドごとにモックアカウントを投入（冪等）。"""
        brands = [
            ("upjapan",         "UP JAPAN",            "17841412345678901"),
            ("dsc-marketing",   "DSc Marketing",       "17841498765432101"),
            ("cashflowsupport", "Cash Flow Support",   "17841411122334401"),
            ("satoshi-blog",    "Satoshi Life Blog",   "17841455566778901"),
            ("bangkok-peach",   "Bangkok Peach Group", "17841477788990011"),
        ]
        for brand, name, ig_uid in brands:
            self.upsert({
                "id":           f"sa_{brand}_instagram",
                "brand":        brand,
                "platform":     "instagram",
                "account_name": name,
                "ig_user_id":   ig_uid,
                "provider":     "mock",
                "status":       "active",
                "validated_at": _NOW(),
            })


# ═══════════════════════════════════════════════════════
# Story Templates
# ═══════════════════════════════════════════════════════

class StoryTemplateRepo:

    def list(self, brand: str = "", active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM story_templates WHERE 1=1"
        params: list = []
        if brand:
            sql += " AND brand=?"; params.append(brand)
        if active_only:
            sql += " AND is_active=1"
        sql += " ORDER BY id DESC"
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["active_days"] = json.loads(r.get("active_days") or "[]")
            r["asset_tags"]  = json.loads(r.get("asset_tags")  or "[]")
            r["extra_rules"] = json.loads(r.get("extra_rules") or "{}")
        return rows

    def get(self, tmpl_id: int) -> Optional[dict]:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM story_templates WHERE id=?", (tmpl_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["active_days"] = json.loads(d.get("active_days") or "[]")
        d["asset_tags"]  = json.loads(d.get("asset_tags")  or "[]")
        d["extra_rules"] = json.loads(d.get("extra_rules") or "{}")
        return d

    def create(self, data: dict) -> int:
        now = _NOW()
        with db.get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO story_templates
                    (brand, name, description, story_type, run_mode,
                     active_days, run_time, frame_count, topic_prompt,
                     asset_source, asset_tags, extra_rules, is_active, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data["brand"],
                data["name"],
                data.get("description", ""),
                data.get("story_type", "promotion"),
                data.get("run_mode", "semi_auto"),
                json.dumps(data.get("active_days", ["mon","tue","wed","thu","fri","sat","sun"])),
                data.get("run_time", "09:00"),
                data.get("frame_count", 3),
                data.get("topic_prompt", ""),
                data.get("asset_source", "asset_brain"),
                json.dumps(data.get("asset_tags", [])),
                json.dumps(data.get("extra_rules", {})),
                1 if data.get("is_active", True) else 0,
                now, now,
            ))
            return cur.lastrowid

    def update(self, tmpl_id: int, data: dict) -> None:
        now = _NOW()
        fields = []
        vals = []
        for col in ["name","description","story_type","run_mode","run_time","frame_count",
                    "topic_prompt","asset_source","is_active"]:
            if col in data:
                fields.append(f"{col}=?")
                vals.append(data[col])
        for col_json in [("active_days","active_days"), ("asset_tags","asset_tags"), ("extra_rules","extra_rules")]:
            col, key = col_json
            if key in data:
                fields.append(f"{col}=?")
                vals.append(json.dumps(data[key]))
        if not fields:
            return
        fields.append("updated_at=?"); vals.append(now)
        vals.append(tmpl_id)
        with db.get_conn() as conn:
            conn.execute(f"UPDATE story_templates SET {', '.join(fields)} WHERE id=?", vals)

    def delete(self, tmpl_id: int) -> None:
        with db.get_conn() as conn:
            conn.execute("DELETE FROM story_templates WHERE id=?", (tmpl_id,))

    def touch_last_run(self, tmpl_id: int) -> None:
        with db.get_conn() as conn:
            conn.execute("UPDATE story_templates SET last_run_at=? WHERE id=?", (_NOW(), tmpl_id))

    def seed_mock(self) -> None:
        """デモ用テンプレートを投入（冪等: 同 brand+name があればスキップ）。"""
        demos = [
            {
                "brand": "dsc-marketing",
                "name":  "DSc 月〜金 プロモ",
                "description": "採用支援の告知を月〜金に自動投稿",
                "story_type": "promotion",
                "run_mode":   "semi_auto",
                "active_days": ["mon","tue","wed","thu","fri"],
                "run_time":   "09:00",
                "topic_prompt": "今週のDSc採用支援サービスのハイライトをストーリー3枚で告知してください。",
                "asset_source": "asset_brain",
                "asset_tags":   ["採用", "HR", "DSc"],
            },
            {
                "brand": "upjapan",
                "name":  "UPJ 週末エンゲージメント",
                "description": "土日限定のエンゲージメント施策",
                "story_type": "poll",
                "run_mode":   "human_approval_required",
                "active_days": ["sat","sun"],
                "run_time":   "10:00",
                "topic_prompt": "UPJのサービスに関するアンケート形式のストーリーを作成してください。",
                "asset_source": "custom",
                "asset_tags":   [],
            },
            {
                "brand": "cashflowsupport",
                "name":  "CSF 毎日 全自動",
                "description": "資金繰り情報を毎日全自動投稿",
                "story_type": "info",
                "run_mode":   "full_auto",
                "active_days": ["mon","tue","wed","thu","fri","sat","sun"],
                "run_time":   "08:00",
                "topic_prompt": "中小企業向けキャッシュフロー管理の豆知識をストーリーで発信。",
                "asset_source": "asset_brain",
                "asset_tags":   ["finance", "中小企業"],
            },
        ]
        with db.get_conn() as conn:
            existing = {(r["brand"], r["name"]) for r in conn.execute(
                "SELECT brand, name FROM story_templates"
            ).fetchall()}
        for d in demos:
            if (d["brand"], d["name"]) not in existing:
                self.create(d)


# ═══════════════════════════════════════════════════════
# Story Runs
# ═══════════════════════════════════════════════════════

class StoryRunRepo:

    def list(
        self,
        brand: str = "",
        status: str = "",
        template_id: Optional[int] = None,
        limit: int = 50,
    ) -> list[dict]:
        sql = "SELECT * FROM story_runs WHERE 1=1"
        params: list = []
        if brand:
            sql += " AND brand=?"; params.append(brand)
        if status:
            sql += " AND status=?"; params.append(status)
        if template_id is not None:
            sql += " AND template_id=?"; params.append(template_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with db.get_conn() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        for r in rows:
            r["frames_json"] = json.loads(r.get("frames_json") or "[]")
        return rows

    def get(self, run_id: int) -> Optional[dict]:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM story_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["frames_json"] = json.loads(d.get("frames_json") or "[]")
        return d

    def create(self, data: dict) -> int:
        now = _NOW()
        with db.get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO story_runs
                    (template_id, brand, run_mode, status, story_type, topic,
                     frames_json, caption, hashtags, asset_id,
                     social_account_id, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("template_id"),
                data["brand"],
                data.get("run_mode", "semi_auto"),
                data.get("status", "pending"),
                data.get("story_type", "promotion"),
                data.get("topic", ""),
                json.dumps(data.get("frames_json", [])),
                data.get("caption", ""),
                data.get("hashtags", ""),
                data.get("asset_id"),
                data.get("social_account_id"),
                now, now,
            ))
            return cur.lastrowid

    def update_status(
        self,
        run_id: int,
        status: str,
        *,
        ig_media_id: str = "",
        ig_permalink: str = "",
        error_message: str = "",
        approval_note: str = "",
        approved_by: str = "",
    ) -> None:
        now = _NOW()
        extra = {}
        if ig_media_id:   extra["ig_media_id"]   = ig_media_id
        if ig_permalink:  extra["ig_permalink"]   = ig_permalink
        if error_message: extra["error_message"]  = error_message
        if approval_note: extra["approval_note"]  = approval_note
        if approved_by:   extra["approved_by"]    = approved_by
        if status == "approved":
            extra["approved_at"] = now
        if status in ("published", "failed"):
            if status == "published":
                extra["published_at"] = now

        sets = ["status=?", "updated_at=?"]
        vals: list = [status, now]
        for k, v in extra.items():
            sets.append(f"{k}=?"); vals.append(v)
        vals.append(run_id)
        with db.get_conn() as conn:
            conn.execute(f"UPDATE story_runs SET {', '.join(sets)} WHERE id=?", vals)

    def count_by_status(self, brand: str = "") -> dict:
        sql = "SELECT status, COUNT(*) as cnt FROM story_runs"
        params: list = []
        if brand:
            sql += " WHERE brand=?"; params.append(brand)
        sql += " GROUP BY status"
        with db.get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    def seed_mock(self) -> None:
        """デモ用 run データを投入（既に 5件以上あればスキップ）。"""
        with db.get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM story_runs").fetchone()[0]
        if cnt >= 5:
            return

        import random
        from datetime import timedelta

        samples = [
            ("dsc-marketing", "semi_auto", "pending_approval", "採用支援の告知", "promotion"),
            ("dsc-marketing", "semi_auto", "published",        "AIで変わる採用", "promotion"),
            ("upjapan",       "human_approval_required", "pending_approval", "GW特別企画", "poll"),
            ("cashflowsupport","full_auto","published",       "資金繰り豆知識", "info"),
            ("dsc-marketing", "semi_auto", "rejected",        "お盆休みのお知らせ", "event"),
            ("upjapan",       "human_approval_required","published","サービス紹介", "behind"),
        ]
        frames = [
            {"emoji":"📣","headline":"タイトル","subtext":"サブテキスト","bg":"purple-blue","type":"hook"},
            {"emoji":"💡","headline":"詳細","subtext":"説明テキスト","bg":"green-teal","type":"detail"},
            {"emoji":"✅","headline":"行動喚起","subtext":"詳しくはプロフへ","bg":"orange-red","type":"cta","button":"詳細を見る"},
        ]
        for brand, mode, status, topic, stype in samples:
            self.create({
                "brand": brand,
                "run_mode": mode,
                "status": status,
                "story_type": stype,
                "topic": topic,
                "frames_json": frames,
                "caption": f"#{brand} #Instagram #ストーリー",
                "hashtags": "#自動投稿 #AIマーケティング",
                "social_account_id": f"sa_{brand}_instagram",
            })


# ═══════════════════════════════════════════════════════
# Social Insights
# ═══════════════════════════════════════════════════════

class SocialInsightRepo:

    def log(self, data: dict) -> None:
        now = _NOW()
        with db.get_conn() as conn:
            conn.execute("""
                INSERT INTO social_insights
                    (social_post_id, brand, platform, post_type,
                     period_start, period_end,
                     impressions, reach, replies, exits,
                     taps_forward, taps_back, shares,
                     likes, comments, saves, video_views,
                     engagement_rate, logged_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("social_post_id"),
                data.get("brand"),
                data.get("platform", "instagram"),
                data.get("post_type", "story"),
                data.get("period_start"),
                data.get("period_end"),
                data.get("impressions", 0),
                data.get("reach", 0),
                data.get("replies", 0),
                data.get("exits", 0),
                data.get("taps_forward", 0),
                data.get("taps_back", 0),
                data.get("shares", 0),
                data.get("likes", 0),
                data.get("comments", 0),
                data.get("saves", 0),
                data.get("video_views", 0),
                data.get("engagement_rate", 0.0),
                now,
            ))

    def summary_by_brand(self, brand: str, days: int = 28) -> dict:
        with db.get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as story_count,
                    COALESCE(SUM(impressions), 0)  as total_impressions,
                    COALESCE(SUM(reach), 0)        as total_reach,
                    COALESCE(SUM(replies), 0)      as total_replies,
                    COALESCE(AVG(engagement_rate), 0) as avg_engagement
                FROM social_insights
                WHERE brand=? AND post_type='story'
                  AND logged_at >= datetime('now', ?)
            """, (brand, f"-{days} days")).fetchone()
        return dict(row) if row else {}
