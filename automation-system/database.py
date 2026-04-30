"""
SQLite データベースモジュール
YAMLファイルの代わりに使う安全なデータストア

テーブル:
  - leads          : リード（見込み客）
  - queue_items    : 投稿キュー
  - performance_log: SNSパフォーマンス記録
  - decisions      : 判断待ちキュー
"""

from __future__ import annotations

import json
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "upj.db"


def init_db():
    """データベースとテーブルを初期化する"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript("""
        -- ────────────── リード ──────────────
        CREATE TABLE IF NOT EXISTS leads (
            lead_id     TEXT PRIMARY KEY,
            created_at  TEXT NOT NULL,
            brand       TEXT,
            name        TEXT,
            company     TEXT,
            email       TEXT,
            phone       TEXT,
            line_user_id TEXT,
            stage       TEXT DEFAULT 'L1',
            last_contact TEXT,
            outcome     TEXT,
            next_action TEXT,
            notes       TEXT,
            followup_sent TEXT DEFAULT '[]',
            source      TEXT DEFAULT 'line',
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_leads_brand   ON leads(brand);
        CREATE INDEX IF NOT EXISTS idx_leads_stage   ON leads(stage);
        CREATE INDEX IF NOT EXISTS idx_leads_outcome ON leads(outcome);

        -- ────────────── 投稿キュー ──────────────
        CREATE TABLE IF NOT EXISTS queue_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            brand       TEXT NOT NULL,
            channel     TEXT NOT NULL,
            media_type  TEXT DEFAULT 'image',
            caption     TEXT,
            text        TEXT,
            message     TEXT,
            title       TEXT,
            content     TEXT,
            image_url   TEXT,
            video_url   TEXT,
            hashtags    TEXT,
            scheduled_at TEXT,
            posted      INTEGER DEFAULT 0,
            posted_at   TEXT,
            source      TEXT DEFAULT 'manual',
            topic       TEXT,
            filename    TEXT UNIQUE,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_queue_brand   ON queue_items(brand);
        CREATE INDEX IF NOT EXISTS idx_queue_channel ON queue_items(channel);
        CREATE INDEX IF NOT EXISTS idx_queue_posted  ON queue_items(posted);
        CREATE INDEX IF NOT EXISTS idx_queue_sched   ON queue_items(scheduled_at);

        -- ────────────── パフォーマンスログ ──────────────
        CREATE TABLE IF NOT EXISTS performance_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT,
            platform        TEXT,
            topic           TEXT,
            post_id         TEXT UNIQUE,
            caption_head    TEXT,
            likes           INTEGER DEFAULT 0,
            reach           INTEGER DEFAULT 0,
            comments        INTEGER DEFAULT 0,
            saves           INTEGER DEFAULT 0,
            plays           INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            play_rate       REAL DEFAULT 0,
            posted_hour     INTEGER,
            logged_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_perf_brand    ON performance_log(brand);
        CREATE INDEX IF NOT EXISTS idx_perf_platform ON performance_log(platform);

        -- ────────────── 判断待ち ──────────────
        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT UNIQUE,
            type        TEXT,
            reason      TEXT NOT NULL,
            context     TEXT DEFAULT '{}',
            resolved    INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_dec_resolved ON decisions(resolved);

        -- ────────────── 操作ログ ──────────────
        CREATE TABLE IF NOT EXISTS activity_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            action     TEXT NOT NULL,
            brand      TEXT,
            platform   TEXT,
            detail     TEXT,
            status     TEXT DEFAULT 'ok',
            created_at TEXT NOT NULL
        );

        -- ────────────── MEO: 店舗プロファイル ──────────────
        CREATE TABLE IF NOT EXISTS business_profiles (
            id                  TEXT PRIMARY KEY,
            brand               TEXT,
            location_name       TEXT NOT NULL,
            address             TEXT,
            city                TEXT,
            phone               TEXT,
            website             TEXT,
            gbp_location_id     TEXT UNIQUE,
            avg_rating          REAL DEFAULT 0,
            total_reviews       INTEGER DEFAULT 0,
            unanswered_reviews  INTEGER DEFAULT 0,
            photos_count        INTEGER DEFAULT 0,
            photo_alert         INTEGER DEFAULT 0,
            last_synced_at      TEXT,
            meo_score           INTEGER DEFAULT 0,
            status              TEXT DEFAULT 'active',
            created_at          TEXT NOT NULL,
            updated_at          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bp_brand  ON business_profiles(brand);
        CREATE INDEX IF NOT EXISTS idx_bp_status ON business_profiles(status);

        -- ────────────── MEO: レビュー ──────────────
        CREATE TABLE IF NOT EXISTS reviews (
            id                  TEXT PRIMARY KEY,
            profile_id          TEXT NOT NULL,
            gbp_review_id       TEXT UNIQUE,
            reviewer_name       TEXT,
            reviewer_photo_url  TEXT,
            rating              INTEGER NOT NULL,
            comment             TEXT,
            reply               TEXT,
            reply_updated_at    TEXT,
            status              TEXT DEFAULT 'unanswered',
            created_at          TEXT NOT NULL,
            updated_at          TEXT,
            FOREIGN KEY(profile_id) REFERENCES business_profiles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_rev_profile ON reviews(profile_id);
        CREATE INDEX IF NOT EXISTS idx_rev_status  ON reviews(status);
        CREATE INDEX IF NOT EXISTS idx_rev_rating  ON reviews(rating);

        -- ────────────── MEO: 返信下書き ──────────────
        CREATE TABLE IF NOT EXISTS review_reply_drafts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            review_id   TEXT NOT NULL,
            draft_text  TEXT NOT NULL,
            source      TEXT DEFAULT 'ai',
            approved    INTEGER DEFAULT 0,
            sent        INTEGER DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT,
            FOREIGN KEY(review_id) REFERENCES reviews(id)
        );
        CREATE INDEX IF NOT EXISTS idx_draft_review   ON review_reply_drafts(review_id);
        CREATE INDEX IF NOT EXISTS idx_draft_approved ON review_reply_drafts(approved);

        -- ────────────── MEO: GBP 投稿 ──────────────
        CREATE TABLE IF NOT EXISTS business_profile_posts (
            id           TEXT PRIMARY KEY,
            profile_id   TEXT NOT NULL,
            gbp_post_id  TEXT UNIQUE,
            post_type    TEXT DEFAULT 'STANDARD',
            summary      TEXT,
            cta_type     TEXT,
            cta_url      TEXT,
            media_url    TEXT,
            state        TEXT DEFAULT 'draft',
            scheduled_at TEXT,
            published_at TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT,
            FOREIGN KEY(profile_id) REFERENCES business_profiles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_bpp_profile ON business_profile_posts(profile_id);

        -- ────────────── MEO: インサイト ──────────────
        CREATE TABLE IF NOT EXISTS business_profile_insights (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            profile_id          TEXT NOT NULL,
            period_start        TEXT,
            period_end          TEXT,
            views_search        INTEGER DEFAULT 0,
            views_maps          INTEGER DEFAULT 0,
            actions_website     INTEGER DEFAULT 0,
            actions_directions  INTEGER DEFAULT 0,
            actions_phone       INTEGER DEFAULT 0,
            photos_views        INTEGER DEFAULT 0,
            logged_at           TEXT NOT NULL,
            FOREIGN KEY(profile_id) REFERENCES business_profiles(id)
        );
        CREATE INDEX IF NOT EXISTS idx_bpi_profile ON business_profile_insights(profile_id);

        -- ────────────── AI Agents ──────────────
        CREATE TABLE IF NOT EXISTS agents (
            agent_id    TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            role        TEXT,
            brand       TEXT,
            status      TEXT DEFAULT 'active',
            last_run    TEXT,
            last_result TEXT,
            run_count   INTEGER DEFAULT 0,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_agents_brand  ON agents(brand);
        CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);

        -- ────────────── Asset Brain ──────────────
        CREATE TABLE IF NOT EXISTS assets (
            asset_id         TEXT PRIMARY KEY,
            brand            TEXT NOT NULL,
            location         TEXT,
            asset_type       TEXT NOT NULL DEFAULT 'photo',
            channel_use      TEXT DEFAULT '[]',
            season           TEXT DEFAULT 'all',
            target_audience  TEXT DEFAULT '[]',
            copyright_status TEXT DEFAULT 'owned',
            face_permission  INTEGER DEFAULT 0,
            reusable         INTEGER DEFAULT 1,
            status           TEXT DEFAULT 'active',
            title            TEXT,
            description      TEXT,
            file_path        TEXT,
            thumbnail_url    TEXT,
            file_size        INTEGER,
            duration_sec     INTEGER,
            width            INTEGER,
            height           INTEGER,
            ai_tags          TEXT DEFAULT '[]',
            performance_note TEXT,
            created_at       TEXT NOT NULL,
            updated_at       TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_assets_brand       ON assets(brand);
        CREATE INDEX IF NOT EXISTS idx_assets_type        ON assets(asset_type);
        CREATE INDEX IF NOT EXISTS idx_assets_status      ON assets(status);
        CREATE INDEX IF NOT EXISTS idx_assets_season      ON assets(season);

        CREATE TABLE IF NOT EXISTS asset_tags (
            tag_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'general',
            color    TEXT DEFAULT '#6366f1'
        );

        CREATE TABLE IF NOT EXISTS asset_tag_links (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id TEXT NOT NULL,
            tag_id   INTEGER NOT NULL,
            UNIQUE(asset_id, tag_id),
            FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
            FOREIGN KEY(tag_id)   REFERENCES asset_tags(tag_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_atl_asset ON asset_tag_links(asset_id);
        CREATE INDEX IF NOT EXISTS idx_atl_tag   ON asset_tag_links(tag_id);

        CREATE TABLE IF NOT EXISTS asset_usages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id    TEXT NOT NULL,
            used_in     TEXT,
            channel     TEXT,
            brand       TEXT,
            used_at     TEXT NOT NULL,
            result_note TEXT,
            performance TEXT DEFAULT '{}',
            FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_usages_asset ON asset_usages(asset_id);
        CREATE INDEX IF NOT EXISTS idx_usages_brand ON asset_usages(brand);

        CREATE TABLE IF NOT EXISTS asset_collections (
            collection_id TEXT PRIMARY KEY,
            name          TEXT NOT NULL,
            brand         TEXT,
            description   TEXT,
            asset_ids     TEXT DEFAULT '[]',
            created_at    TEXT NOT NULL,
            updated_at    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_collections_brand ON asset_collections(brand);

        -- ────────────── NoiMos AI: viral_patterns ──────────────
        CREATE TABLE IF NOT EXISTS viral_patterns (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT NOT NULL,
            hook                TEXT,
            problem_framing     TEXT,
            emotional_arc       TEXT,
            cta                 TEXT,
            format_suitability  TEXT DEFAULT '[]',
            risk_flags          TEXT DEFAULT '[]',
            source_type         TEXT,
            source_url          TEXT,
            source_caption      TEXT,
            metrics_likes       INTEGER DEFAULT 0,
            metrics_comments    INTEGER DEFAULT 0,
            metrics_saves       INTEGER DEFAULT 0,
            metrics_views       INTEGER DEFAULT 0,
            notes               TEXT,
            status              TEXT DEFAULT 'draft',
            created_at          TEXT NOT NULL,
            updated_at          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_vp_status ON viral_patterns(status);

        -- ────────────── NoiMos AI: viral_pattern_examples ──────────────
        CREATE TABLE IF NOT EXISTS viral_pattern_examples (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id      INTEGER REFERENCES viral_patterns(id),
            title           TEXT,
            source_platform TEXT,
            source_url      TEXT,
            source_account  TEXT,
            caption         TEXT,
            image_url       TEXT,
            likes           INTEGER DEFAULT 0,
            comments        INTEGER DEFAULT 0,
            saves           INTEGER DEFAULT 0,
            views           INTEGER DEFAULT 0,
            posted_at       TEXT,
            notes           TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_vpe_pattern ON viral_pattern_examples(pattern_id);

        -- ────────────── Campaign Pipeline: campaigns ──────────────
        CREATE TABLE IF NOT EXISTS campaigns (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            brand       TEXT,
            objective   TEXT,
            start_date  TEXT,
            end_date    TEXT,
            status      TEXT DEFAULT 'planning',
            notes       TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_camp_brand  ON campaigns(brand);
        CREATE INDEX IF NOT EXISTS idx_camp_status ON campaigns(status);

        -- ────────────── Campaign Pipeline: content_ideas ──────────────
        CREATE TABLE IF NOT EXISTS content_ideas (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_id      INTEGER REFERENCES viral_patterns(id),
            campaign_id     INTEGER REFERENCES campaigns(id),
            brand           TEXT NOT NULL,
            title           TEXT NOT NULL,
            hook            TEXT,
            body            TEXT,
            cta             TEXT,
            target_formats  TEXT DEFAULT '[]',
            tone            TEXT,
            notes           TEXT,
            status          TEXT DEFAULT 'draft',
            created_by      TEXT DEFAULT 'manual',
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_idea_brand    ON content_ideas(brand);
        CREATE INDEX IF NOT EXISTS idx_idea_campaign ON content_ideas(campaign_id);
        CREATE INDEX IF NOT EXISTS idx_idea_status   ON content_ideas(status);

        -- ────────────── Campaign Pipeline: content_variants ──────────────
        CREATE TABLE IF NOT EXISTS content_variants (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            idea_id      INTEGER REFERENCES content_ideas(id),
            format       TEXT NOT NULL,
            caption      TEXT,
            hashtags     TEXT,
            image_prompt TEXT,
            video_prompt TEXT,
            duration_sec INTEGER,
            notes        TEXT,
            status       TEXT DEFAULT 'draft',
            created_at   TEXT NOT NULL,
            updated_at   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_var_idea   ON content_variants(idea_id);
        CREATE INDEX IF NOT EXISTS idx_var_format ON content_variants(format);
        CREATE INDEX IF NOT EXISTS idx_var_status ON content_variants(status);

        -- ────────────── Campaign Pipeline: prompt_templates ──────────────
        CREATE TABLE IF NOT EXISTS prompt_templates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            type        TEXT,
            template    TEXT NOT NULL,
            variables   TEXT DEFAULT '[]',
            brand       TEXT,
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        );

        -- ────────────── Campaign Pipeline: publishing_jobs ──────────────
        CREATE TABLE IF NOT EXISTS publishing_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            variant_id      INTEGER REFERENCES content_variants(id),
            idea_id         INTEGER REFERENCES content_ideas(id),
            campaign_id     INTEGER REFERENCES campaigns(id),
            brand           TEXT,
            platform        TEXT,
            scheduled_at    TEXT,
            status          TEXT DEFAULT 'pending_approval',
            approval_note   TEXT,
            approved_by     TEXT,
            approved_at     TEXT,
            published_at    TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_pj_status   ON publishing_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_pj_brand    ON publishing_jobs(brand);
        CREATE INDEX IF NOT EXISTS idx_pj_campaign ON publishing_jobs(campaign_id);

        -- ────────────── Story Autopilot: social_accounts ──────────────
        CREATE TABLE IF NOT EXISTS social_accounts (
            id              TEXT PRIMARY KEY,
            brand           TEXT NOT NULL,
            platform        TEXT NOT NULL DEFAULT 'instagram',
            account_id      TEXT,
            account_name    TEXT,
            account_type    TEXT DEFAULT 'business',
            access_token    TEXT,
            token_expires_at TEXT,
            ig_user_id      TEXT,
            page_id         TEXT,
            provider        TEXT DEFAULT 'mock',
            status          TEXT DEFAULT 'active',
            validated_at    TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sa_brand    ON social_accounts(brand);
        CREATE INDEX IF NOT EXISTS idx_sa_platform ON social_accounts(platform);

        -- ────────────── Story Autopilot: story_templates ──────────────
        CREATE TABLE IF NOT EXISTS story_templates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT NOT NULL,
            name            TEXT NOT NULL,
            description     TEXT,
            story_type      TEXT DEFAULT 'promotion',
            run_mode        TEXT DEFAULT 'semi_auto',
            active_days     TEXT DEFAULT '["mon","tue","wed","thu","fri","sat","sun"]',
            run_time        TEXT DEFAULT '09:00',
            frame_count     INTEGER DEFAULT 3,
            topic_prompt    TEXT,
            asset_source    TEXT DEFAULT 'asset_brain',
            asset_tags      TEXT DEFAULT '[]',
            extra_rules     TEXT DEFAULT '{}',
            is_active       INTEGER DEFAULT 1,
            last_run_at     TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_st_brand  ON story_templates(brand);
        CREATE INDEX IF NOT EXISTS idx_st_active ON story_templates(is_active);

        -- ────────────── Story Autopilot: story_runs ──────────────
        CREATE TABLE IF NOT EXISTS story_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id         INTEGER REFERENCES story_templates(id),
            brand               TEXT NOT NULL,
            run_mode            TEXT NOT NULL,
            status              TEXT DEFAULT 'pending',
            story_type          TEXT,
            topic               TEXT,
            frames_json         TEXT DEFAULT '[]',
            caption             TEXT,
            hashtags            TEXT,
            asset_id            TEXT,
            publishing_job_id   INTEGER REFERENCES publishing_jobs(id),
            social_account_id   TEXT REFERENCES social_accounts(id),
            ig_media_id         TEXT,
            ig_permalink        TEXT,
            approval_note       TEXT,
            approved_by         TEXT,
            approved_at         TEXT,
            published_at        TEXT,
            error_message       TEXT,
            created_at          TEXT NOT NULL,
            updated_at          TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sr_brand    ON story_runs(brand);
        CREATE INDEX IF NOT EXISTS idx_sr_status   ON story_runs(status);
        CREATE INDEX IF NOT EXISTS idx_sr_template ON story_runs(template_id);

        -- ────────────── Story Autopilot: social_posts ──────────────
        CREATE TABLE IF NOT EXISTS social_posts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT NOT NULL,
            platform        TEXT NOT NULL DEFAULT 'instagram',
            post_type       TEXT NOT NULL DEFAULT 'feed',
            social_account_id TEXT REFERENCES social_accounts(id),
            story_run_id    INTEGER REFERENCES story_runs(id),
            publishing_job_id INTEGER REFERENCES publishing_jobs(id),
            ig_media_id     TEXT UNIQUE,
            ig_permalink    TEXT,
            caption         TEXT,
            hashtags        TEXT,
            media_url       TEXT,
            thumbnail_url   TEXT,
            frames_json     TEXT DEFAULT '[]',
            scheduled_at    TEXT,
            published_at    TEXT,
            status          TEXT DEFAULT 'draft',
            provider        TEXT DEFAULT 'mock',
            provider_response TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sp_brand   ON social_posts(brand);
        CREATE INDEX IF NOT EXISTS idx_sp_type    ON social_posts(post_type);
        CREATE INDEX IF NOT EXISTS idx_sp_status  ON social_posts(status);

        -- ────────────── Story Autopilot: social_insights ──────────────
        CREATE TABLE IF NOT EXISTS social_insights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            social_post_id  INTEGER REFERENCES social_posts(id),
            brand           TEXT,
            platform        TEXT,
            post_type       TEXT,
            period_start    TEXT,
            period_end      TEXT,
            impressions     INTEGER DEFAULT 0,
            reach           INTEGER DEFAULT 0,
            replies         INTEGER DEFAULT 0,
            exits           INTEGER DEFAULT 0,
            taps_forward    INTEGER DEFAULT 0,
            taps_back       INTEGER DEFAULT 0,
            shares          INTEGER DEFAULT 0,
            likes           INTEGER DEFAULT 0,
            comments        INTEGER DEFAULT 0,
            saves           INTEGER DEFAULT 0,
            video_views     INTEGER DEFAULT 0,
            engagement_rate REAL DEFAULT 0,
            logged_at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_si_post  ON social_insights(social_post_id);
        CREATE INDEX IF NOT EXISTS idx_si_brand ON social_insights(brand);

        -- ────────────── Blog Auto Growth: blog_projects ──────────────
        CREATE TABLE IF NOT EXISTS blog_projects (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT NOT NULL,
            title           TEXT NOT NULL,
            source_type     TEXT DEFAULT 'sns',
            source_id       TEXT,
            source_platform TEXT,
            source_caption  TEXT,
            engagement_score INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'candidate',
            assigned_to     TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bp_brand  ON blog_projects(brand);
        CREATE INDEX IF NOT EXISTS idx_bp_status ON blog_projects(status);

        -- ────────────── Blog Auto Growth: blog_drafts ──────────────
        CREATE TABLE IF NOT EXISTS blog_drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id      INTEGER REFERENCES blog_projects(id),
            brand           TEXT NOT NULL,
            title           TEXT NOT NULL,
            slug            TEXT,
            outline_json    TEXT DEFAULT '[]',
            body            TEXT,
            seo_keywords    TEXT DEFAULT '[]',
            meta_description TEXT,
            status          TEXT DEFAULT 'draft',
            word_count      INTEGER DEFAULT 0,
            created_by      TEXT DEFAULT 'ai',
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bd_brand   ON blog_drafts(brand);
        CREATE INDEX IF NOT EXISTS idx_bd_status  ON blog_drafts(status);
        CREATE INDEX IF NOT EXISTS idx_bd_project ON blog_drafts(project_id);

        -- ────────────── Blog Auto Growth: blog_publish_jobs ──────────────
        CREATE TABLE IF NOT EXISTS blog_publish_jobs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            draft_id        INTEGER REFERENCES blog_drafts(id),
            brand           TEXT,
            platform        TEXT DEFAULT 'wordpress',
            scheduled_at    TEXT,
            published_at    TEXT,
            status          TEXT DEFAULT 'pending',
            external_url    TEXT,
            notes           TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_bpj_draft  ON blog_publish_jobs(draft_id);
        CREATE INDEX IF NOT EXISTS idx_bpj_status ON blog_publish_jobs(status);

        -- ────────────── Analytics: daily_briefs ──────────────
        CREATE TABLE IF NOT EXISTS daily_briefs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brief_date      TEXT NOT NULL,
            mood            TEXT DEFAULT 'good',
            summary         TEXT,
            highlights_json TEXT DEFAULT '[]',
            kpis_json       TEXT DEFAULT '{}',
            generated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_db_date ON daily_briefs(brief_date);

        -- ────────────── Analytics: ai_recommendations ──────────────
        CREATE TABLE IF NOT EXISTS ai_recommendations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT,
            category        TEXT,
            priority        TEXT DEFAULT 'mid',
            title           TEXT NOT NULL,
            body            TEXT,
            action_url      TEXT,
            dismissed       INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ar_brand    ON ai_recommendations(brand);
        CREATE INDEX IF NOT EXISTS idx_ar_priority ON ai_recommendations(priority);

        -- ────────────── Analytics: performance_snapshots ──────────────
        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            snap_date       TEXT NOT NULL,
            brand           TEXT,
            platform        TEXT,
            metric_key      TEXT NOT NULL,
            metric_value    REAL DEFAULT 0,
            delta_pct       REAL DEFAULT 0,
            note            TEXT,
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ps_brand ON performance_snapshots(brand);
        CREATE INDEX IF NOT EXISTS idx_ps_date  ON performance_snapshots(snap_date);

        -- ────────────── Analytics: anomaly_alerts ──────────────
        CREATE TABLE IF NOT EXISTS anomaly_alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT,
            platform        TEXT,
            metric          TEXT,
            expected_value  REAL,
            actual_value    REAL,
            delta_pct       REAL,
            severity        TEXT DEFAULT 'warn',
            message         TEXT,
            resolved        INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL,
            resolved_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_aa_brand    ON anomaly_alerts(brand);
        CREATE INDEX IF NOT EXISTS idx_aa_severity ON anomaly_alerts(severity);
        CREATE INDEX IF NOT EXISTS idx_aa_resolved ON anomaly_alerts(resolved);

        -- ────────────── Analytics: strategy_notes ──────────────
        CREATE TABLE IF NOT EXISTS strategy_notes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT,
            author          TEXT DEFAULT 'ai_ceo',
            category        TEXT,
            note            TEXT NOT NULL,
            pinned          INTEGER DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sn_brand  ON strategy_notes(brand);
        CREATE INDEX IF NOT EXISTS idx_sn_pinned ON strategy_notes(pinned);

        -- ────────────── 監査ログ ──────────────
        CREATE TABLE IF NOT EXISTS audit_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            user_name   TEXT,
            action      TEXT NOT NULL,
            resource    TEXT,
            resource_id TEXT,
            detail      TEXT DEFAULT '{}',
            ip_address  TEXT,
            user_agent  TEXT,
            status      TEXT DEFAULT 'ok',
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_user     ON audit_logs(user_id);
        CREATE INDEX IF NOT EXISTS idx_audit_action   ON audit_logs(action);
        CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_logs(resource);
        CREATE INDEX IF NOT EXISTS idx_audit_created  ON audit_logs(created_at);

        -- ────────────── 通知 ──────────────
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            body        TEXT,
            link        TEXT,
            is_read     INTEGER DEFAULT 0,
            priority    TEXT DEFAULT 'normal',
            source      TEXT DEFAULT 'system',
            created_at  TEXT NOT NULL,
            read_at     TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_notif_user    ON notifications(user_id);
        CREATE INDEX IF NOT EXISTS idx_notif_read    ON notifications(is_read);
        CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at);

        -- ────────────── コメント ──────────────
        CREATE TABLE IF NOT EXISTS comments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            resource    TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            author_id   TEXT,
            author_name TEXT NOT NULL,
            body        TEXT NOT NULL,
            created_at  TEXT NOT NULL,
            updated_at  TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_comments_resource ON comments(resource, resource_id);
        CREATE INDEX IF NOT EXISTS idx_comments_author   ON comments(author_id);

        -- ────────────── 添付ファイル ──────────────
        CREATE TABLE IF NOT EXISTS attachments (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            resource    TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            file_name   TEXT NOT NULL,
            file_path   TEXT NOT NULL,
            file_size   INTEGER DEFAULT 0,
            mime_type   TEXT,
            uploaded_by TEXT,
            created_at  TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attach_resource ON attachments(resource, resource_id);
        """)
    log.info(f"データベース初期化完了: {DB_PATH}")


@contextmanager
def get_conn():
    """SQLite接続のコンテキストマネージャー（自動コミット・ロールバック）"""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # 並列アクセスを安全に
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════
# LEADS
# ══════════════════════════════════════════

def upsert_lead(data: dict) -> str:
    """リードを作成または更新。lead_id を返す。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lid = data.get("lead_id") or _new_lead_id()
    followup = json.dumps(data.get("followup_sent", []))
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO leads
                (lead_id,created_at,brand,name,company,email,phone,
                 line_user_id,stage,last_contact,outcome,next_action,
                 notes,followup_sent,source,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lead_id) DO UPDATE SET
                brand=excluded.brand, name=excluded.name,
                company=excluded.company, email=excluded.email,
                phone=excluded.phone, line_user_id=excluded.line_user_id,
                stage=excluded.stage, last_contact=excluded.last_contact,
                outcome=excluded.outcome, next_action=excluded.next_action,
                notes=excluded.notes, followup_sent=excluded.followup_sent,
                updated_at=excluded.updated_at
        """, (
            lid, data.get("created_at", now[:10]),
            data.get("brand"), data.get("name"), data.get("company"),
            data.get("email"), data.get("phone"), data.get("line_user_id"),
            data.get("stage", "L1"), data.get("last_contact"),
            data.get("outcome"), data.get("next_action"),
            data.get("notes"), followup,
            data.get("source", "line"), now,
        ))
    return lid


def get_lead(lead_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["followup_sent"] = json.loads(d.get("followup_sent") or "[]")
    return d


def list_leads(brand: str = "", stage: str = "", outcome: str = "active",
               limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM leads WHERE 1=1"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    if stage:
        sql += " AND stage=?"; params.append(stage)
    if outcome == "active":
        sql += " AND (outcome IS NULL OR outcome='')"
    elif outcome:
        sql += " AND outcome=?"; params.append(outcome)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["followup_sent"] = json.loads(d.get("followup_sent") or "[]")
        result.append(d)
    return result


def update_lead_stage(lead_id: str, stage: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE leads SET stage=?, updated_at=? WHERE lead_id=?",
            (stage, now, lead_id)
        )


def _new_lead_id() -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE lead_id LIKE ?", (f"{today}-%",)
        ).fetchone()[0]
    return f"{today}-{count+1:03d}"


# ══════════════════════════════════════════
# QUEUE
# ══════════════════════════════════════════

def enqueue(data: dict) -> int:
    """投稿をキューに追加。id を返す。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hashtags = data.get("hashtags")
    if isinstance(hashtags, list):
        hashtags = " ".join(hashtags)
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO queue_items
                (brand,channel,media_type,caption,text,message,title,content,
                 image_url,video_url,hashtags,scheduled_at,posted,source,
                 topic,filename,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)
        """, (
            data.get("brand"), data.get("channel"),
            data.get("media_type", "image"),
            data.get("caption"), data.get("text"),
            data.get("message"), data.get("title"), data.get("content"),
            data.get("image_url"), data.get("video_url"),
            hashtags, data.get("scheduled_at"),
            data.get("source", "manual"), data.get("topic"),
            data.get("filename"), now,
        ))
    return cur.lastrowid


def list_queue(brand: str = "", channel: str = "",
               pending_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM queue_items WHERE 1=1"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    if channel:
        sql += " AND channel=?"; params.append(channel)
    if pending_only:
        sql += " AND posted=0"
    sql += " ORDER BY COALESCE(scheduled_at, created_at) ASC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def next_pending(brand: str, channel: str) -> dict | None:
    """次の投稿可能アイテムを返す（scheduled_at が未来のものはスキップ）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        row = conn.execute("""
            SELECT * FROM queue_items
            WHERE brand=? AND channel=? AND posted=0
              AND (scheduled_at IS NULL OR scheduled_at <= ?)
            ORDER BY COALESCE(scheduled_at, created_at) ASC
            LIMIT 1
        """, (brand, channel, now)).fetchone()
    return dict(row) if row else None


def mark_posted(item_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE queue_items SET posted=1, posted_at=? WHERE id=?",
            (now, item_id)
        )


def delete_queue_item(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM queue_items WHERE id=?", (item_id,))


def count_pending(brand: str = "", channel: str = "") -> int:
    sql = "SELECT COUNT(*) FROM queue_items WHERE posted=0"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    if channel:
        sql += " AND channel=?"; params.append(channel)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


# ══════════════════════════════════════════
# PERFORMANCE LOG
# ══════════════════════════════════════════

def log_performance(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO performance_log
                (brand,platform,topic,post_id,caption_head,
                 likes,reach,comments,saves,plays,
                 engagement_rate,play_rate,posted_hour,logged_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("brand"), data.get("platform"),
            data.get("topic"), data.get("post_id"),
            data.get("caption_head","")[:80],
            data.get("metrics",{}).get("likes", 0),
            data.get("metrics",{}).get("reach", 0),
            data.get("metrics",{}).get("comments", 0),
            data.get("metrics",{}).get("saves", 0),
            data.get("metrics",{}).get("plays", 0),
            data.get("engagement_rate", 0),
            data.get("play_rate", 0),
            data.get("posted_hour"),
            data.get("logged_at", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ))


def get_performance_summary_db(brand: str, platform: str = "instagram",
                                limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM performance_log
            WHERE brand=? AND platform=?
            ORDER BY logged_at DESC LIMIT ?
        """, (brand, platform, limit)).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# DECISIONS
# ══════════════════════════════════════════

def add_decision(reason: str, type_: str = "要確認",
                 context: dict | None = None, filename: str = "") -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not filename:
        filename = f"decision_{now.replace(' ','_').replace(':','')}.yaml"
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT OR IGNORE INTO decisions (filename,type,reason,context,created_at)
            VALUES (?,?,?,?,?)
        """, (filename, type_, reason, json.dumps(context or {}), now))
    return cur.lastrowid


def list_decisions(resolved: bool = False) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM decisions WHERE resolved=? ORDER BY created_at DESC",
            (1 if resolved else 0,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["context"] = json.loads(d.get("context") or "{}")
        result.append(d)
    return result


def resolve_decision(decision_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE decisions SET resolved=1, resolved_at=? WHERE id=?",
            (now, decision_id)
        )


# ══════════════════════════════════════════
# ACTIVITY LOG
# ══════════════════════════════════════════

def log_activity(action: str, brand: str = "", platform: str = "",
                 detail: str = "", status: str = "ok"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO activity_log (action,brand,platform,detail,status,created_at)
            VALUES (?,?,?,?,?,?)
        """, (action, brand, platform, detail, status, now))


def list_activity(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# STATS
# ══════════════════════════════════════════

def get_stats() -> dict:
    """ダッシュボード用の統計を一括取得"""
    with get_conn() as conn:
        ig_pending   = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE channel='instagram' AND posted=0"
        ).fetchone()[0]
        line_pending = conn.execute(
            "SELECT COUNT(*) FROM queue_items WHERE channel='line' AND posted=0"
        ).fetchone()[0]
        leads_active = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE (outcome IS NULL OR outcome='') AND stage!='L5'"
        ).fetchone()[0]
        leads_contracted = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE outcome='contracted'"
        ).fetchone()[0]
        leads_total  = conn.execute(
            "SELECT COUNT(*) FROM leads"
        ).fetchone()[0]
        decisions_open = conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE resolved=0"
        ).fetchone()[0]
        # 今月の新規リード
        this_month = datetime.now().strftime("%Y-%m")
        leads_new_month = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at LIKE ?",
            (f"{this_month}%",)
        ).fetchone()[0]
        # パイプライン
        funnel = {}
        for stage in ("L1","L2","L3","L4","L5"):
            funnel[stage] = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE stage=? AND (outcome IS NULL OR outcome='')",
                (stage,)
            ).fetchone()[0]

    cvr = round(leads_contracted / leads_total * 100, 1) if leads_total > 0 else 0
    return {
        "ig_pending":      ig_pending,
        "line_pending":    line_pending,
        "leads_active":    leads_active,
        "leads_contracted":leads_contracted,
        "leads_new_month": leads_new_month,
        "decisions_open":  decisions_open,
        "cvr":             cvr,
        "mrr":             0,
        "funnel":          funnel,
    }


def get_monthly_leads(months: int = 6) -> dict:
    """月別リード数"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as cnt
            FROM leads
            GROUP BY month
            ORDER BY month DESC
            LIMIT ?
        """, (months,)).fetchall()
    data = list(reversed(rows))
    return {
        "labels": [r["month"] for r in data],
        "values": [r["cnt"]   for r in data],
    }


# ══════════════════════════════════════════
# BACKUP
# ══════════════════════════════════════════

def backup_db(backup_dir: str = ""):
    """データベースをバックアップ"""
    import shutil
    bdir = Path(backup_dir) if backup_dir else DB_PATH.parent / "backups"
    bdir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = bdir / f"upj_{ts}.db"
    shutil.copy2(DB_PATH, dest)
    # 古いバックアップを削除（30日以上前）
    cutoff = datetime.now().timestamp() - 30 * 86400
    for f in bdir.glob("upj_*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink()
    log.info(f"バックアップ完了: {dest}")
    return str(dest)


# ══════════════════════════════════════════
# MIGRATION: YAML → SQLite
# ══════════════════════════════════════════

def migrate_from_yaml():
    """既存のYAMLファイルをSQLiteに移行する（初回のみ）"""
    import yaml
    base = Path(__file__).parent

    migrated = {"leads": 0, "queue": 0, "performance": 0, "decisions": 0}

    # リード移行
    leads_dir = base.parent / "sales-system" / "leads"
    if leads_dir.exists():
        for f in leads_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and "lead_id" in data:
                    upsert_lead(data)
                    migrated["leads"] += 1
            except Exception as e:
                log.warning(f"リード移行スキップ {f.name}: {e}")

    # キュー移行
    queue_root = base / "content_queue"
    if queue_root.exists():
        for yaml_file in queue_root.rglob("*.yaml"):
            if "calendar" in str(yaml_file):
                continue
            try:
                data = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
                if data and isinstance(data, dict) and "brand" in data:
                    data["filename"] = yaml_file.name
                    enqueue(data)
                    migrated["queue"] += 1
            except Exception as e:
                log.warning(f"キュー移行スキップ {yaml_file.name}: {e}")

    # パフォーマンスログ移行
    perf_path = base / "logs" / "performance_log.yaml"
    if perf_path.exists():
        try:
            items = yaml.safe_load(perf_path.read_text(encoding="utf-8")) or []
            for item in items:
                log_performance(item)
                migrated["performance"] += 1
        except Exception as e:
            log.warning(f"パフォーマンスログ移行エラー: {e}")

    # 判断待ち移行
    decision_dir = base / "decision_queue"
    if decision_dir.exists():
        for f in decision_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and not data.get("resolved"):
                    add_decision(
                        reason=data.get("reason",""),
                        type_=data.get("type","要確認"),
                        context=data.get("context",{}),
                        filename=f.name,
                    )
                    migrated["decisions"] += 1
            except Exception as e:
                log.warning(f"判断待ち移行スキップ {f.name}: {e}")

    log.info(f"移行完了: {migrated}")
    return migrated


# ══════════════════════════════════════════
# AGENTS
# ══════════════════════════════════════════

def upsert_agent(data: dict):
    """Agentのステータスを登録または更新"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO agents (agent_id, name, role, brand, status, last_run, last_result, run_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                name=excluded.name, role=excluded.role, brand=excluded.brand,
                status=excluded.status, last_run=excluded.last_run,
                last_result=excluded.last_result,
                run_count=run_count + excluded.run_count,
                updated_at=excluded.updated_at
        """, (
            data["agent_id"], data["name"], data.get("role"),
            data.get("brand"), data.get("status", "active"),
            data.get("last_run"), data.get("last_result"),
            data.get("run_count", 0), now,
        ))


def get_agent(agent_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM agents WHERE agent_id=?", (agent_id,)).fetchone()
    return dict(row) if row else None


def list_agents() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY agent_id").fetchall()
    return [dict(r) for r in rows]


def update_agent_run(agent_id: str, result: str, status: str = "active"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO agents (agent_id, name, role, brand, status, last_run, last_result, run_count, updated_at)
            VALUES (?, ?, '', '', ?, ?, ?, 1, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                status=excluded.status, last_run=excluded.last_run,
                last_result=excluded.last_result,
                run_count=run_count + 1, updated_at=excluded.updated_at
        """, (agent_id, agent_id, status, now, result[:200], now))


# ══════════════════════════════════════════
# ASSET BRAIN
# ══════════════════════════════════════════

def _new_asset_id() -> str:
    today = datetime.now().strftime("%Y%m%d")
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE asset_id LIKE ?", (f"A{today}%",)
        ).fetchone()[0]
    return f"A{today}{count+1:04d}"


def upsert_asset(data: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    aid = data.get("asset_id") or _new_asset_id()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO assets
                (asset_id,brand,location,asset_type,channel_use,season,
                 target_audience,copyright_status,face_permission,reusable,
                 status,title,description,file_path,thumbnail_url,
                 file_size,duration_sec,width,height,ai_tags,performance_note,
                 created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id) DO UPDATE SET
                brand=excluded.brand, location=excluded.location,
                asset_type=excluded.asset_type, channel_use=excluded.channel_use,
                season=excluded.season, target_audience=excluded.target_audience,
                copyright_status=excluded.copyright_status,
                face_permission=excluded.face_permission, reusable=excluded.reusable,
                status=excluded.status, title=excluded.title,
                description=excluded.description, file_path=excluded.file_path,
                thumbnail_url=excluded.thumbnail_url, file_size=excluded.file_size,
                duration_sec=excluded.duration_sec, width=excluded.width,
                height=excluded.height, ai_tags=excluded.ai_tags,
                performance_note=excluded.performance_note, updated_at=excluded.updated_at
        """, (
            aid, data["brand"], data.get("location"),
            data.get("asset_type", "photo"),
            json.dumps(data.get("channel_use", [])),
            data.get("season", "all"),
            json.dumps(data.get("target_audience", [])),
            data.get("copyright_status", "owned"),
            int(bool(data.get("face_permission", False))),
            int(bool(data.get("reusable", True))),
            data.get("status", "active"),
            data.get("title"), data.get("description"),
            data.get("file_path"), data.get("thumbnail_url"),
            data.get("file_size"), data.get("duration_sec"),
            data.get("width"), data.get("height"),
            json.dumps(data.get("ai_tags", [])),
            data.get("performance_note"),
            data.get("created_at", now[:10]), now,
        ))
    return aid


def get_asset(asset_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
    if not row:
        return None
    return _parse_asset(dict(row))


def _parse_asset(d: dict) -> dict:
    for f in ("channel_use", "target_audience", "ai_tags"):
        d[f] = json.loads(d.get(f) or "[]")
    return d


def list_assets(brand: str = "", asset_type: str = "", channel: str = "",
                season: str = "", status: str = "active",
                tag_id: int = 0, q: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT DISTINCT a.* FROM assets a"
    params: list = []
    if tag_id:
        sql += " JOIN asset_tag_links atl ON atl.asset_id=a.asset_id AND atl.tag_id=?"
        params.append(tag_id)
    sql += " WHERE 1=1"
    if brand:
        sql += " AND a.brand=?"; params.append(brand)
    if asset_type:
        sql += " AND a.asset_type=?"; params.append(asset_type)
    if channel:
        sql += " AND a.channel_use LIKE ?"; params.append(f"%{channel}%")
    if season and season != "all":
        sql += " AND (a.season=? OR a.season='all')"; params.append(season)
    if status:
        sql += " AND a.status=?"; params.append(status)
    if q:
        like = f"%{q}%"
        sql += " AND (a.title LIKE ? OR a.description LIKE ? OR a.ai_tags LIKE ?)"
        params += [like, like, like]
    sql += " ORDER BY a.created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_parse_asset(dict(r)) for r in rows]


def delete_asset(asset_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM assets WHERE asset_id=?", (asset_id,))


def ensure_tag(name: str, category: str = "general", color: str = "#6366f1") -> int:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO asset_tags (name,category,color) VALUES (?,?,?)",
            (name, category, color)
        )
        row = conn.execute("SELECT tag_id FROM asset_tags WHERE name=?", (name,)).fetchone()
    return row[0]


def list_tags() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.*, COUNT(l.asset_id) as asset_count
            FROM asset_tags t
            LEFT JOIN asset_tag_links l ON l.tag_id=t.tag_id
            GROUP BY t.tag_id ORDER BY asset_count DESC, t.name
        """).fetchall()
    return [dict(r) for r in rows]


def get_asset_tags(asset_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT t.* FROM asset_tags t
            JOIN asset_tag_links l ON l.tag_id=t.tag_id
            WHERE l.asset_id=?
        """, (asset_id,)).fetchall()
    return [dict(r) for r in rows]


def add_asset_tag(asset_id: str, tag_name: str, category: str = "general",
                  color: str = "#6366f1") -> int:
    tid = ensure_tag(tag_name, category, color)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO asset_tag_links (asset_id,tag_id) VALUES (?,?)",
            (asset_id, tid)
        )
    return tid


def remove_asset_tag(asset_id: str, tag_id: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM asset_tag_links WHERE asset_id=? AND tag_id=?",
            (asset_id, tag_id)
        )


def record_asset_usage(asset_id: str, channel: str = "", brand: str = "",
                       used_in: str = "", result_note: str = "",
                       performance: dict | None = None) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO asset_usages
                (asset_id,used_in,channel,brand,used_at,result_note,performance)
            VALUES (?,?,?,?,?,?,?)
        """, (asset_id, used_in, channel, brand, now,
              result_note, json.dumps(performance or {})))
    return cur.lastrowid


def get_asset_usages(asset_id: str, limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM asset_usages WHERE asset_id=?
            ORDER BY used_at DESC LIMIT ?
        """, (asset_id, limit)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["performance"] = json.loads(d.get("performance") or "{}")
        result.append(d)
    return result


def upsert_collection(data: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cid = data.get("collection_id") or f"C{now.replace('-','').replace(' ','').replace(':','')[:14]}"
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO asset_collections
                (collection_id,name,brand,description,asset_ids,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(collection_id) DO UPDATE SET
                name=excluded.name, brand=excluded.brand,
                description=excluded.description, asset_ids=excluded.asset_ids,
                updated_at=excluded.updated_at
        """, (cid, data["name"], data.get("brand"), data.get("description"),
              json.dumps(data.get("asset_ids", [])), now[:10], now))
    return cid


def list_collections(brand: str = "") -> list[dict]:
    sql = "SELECT * FROM asset_collections WHERE 1=1"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    sql += " ORDER BY created_at DESC"
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["asset_ids"] = json.loads(d.get("asset_ids") or "[]")
        result.append(d)
    return result


def get_asset_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM assets WHERE status='active'").fetchone()[0]
        by_type: dict = {}
        for t in ("photo", "video", "template", "script"):
            by_type[t] = conn.execute(
                "SELECT COUNT(*) FROM assets WHERE asset_type=? AND status='active'", (t,)
            ).fetchone()[0]
        by_brand: dict = {}
        rows = conn.execute("""
            SELECT brand, COUNT(*) as cnt FROM assets WHERE status='active'
            GROUP BY brand
        """).fetchall()
        for r in rows:
            by_brand[r["brand"]] = r["cnt"]
        needs_review = conn.execute(
            "SELECT COUNT(*) FROM assets WHERE status='review_needed'"
        ).fetchone()[0]
        total_usages = conn.execute("SELECT COUNT(*) FROM asset_usages").fetchone()[0]
    return {
        "total": total, "by_type": by_type, "by_brand": by_brand,
        "needs_review": needs_review, "total_usages": total_usages,
    }


# ══════════════════════════════════════════
# NoiMos AI — VIRAL PATTERNS
# ══════════════════════════════════════════

def create_viral_pattern(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO viral_patterns
                (title,hook,problem_framing,emotional_arc,cta,
                 format_suitability,risk_flags,source_type,source_url,
                 source_caption,metrics_likes,metrics_comments,metrics_saves,
                 metrics_views,notes,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["title"], data.get("hook"), data.get("problem_framing"),
            data.get("emotional_arc"), data.get("cta"),
            json.dumps(data.get("format_suitability", [])),
            json.dumps(data.get("risk_flags", [])),
            data.get("source_type"), data.get("source_url"),
            data.get("source_caption"),
            data.get("metrics_likes", 0), data.get("metrics_comments", 0),
            data.get("metrics_saves", 0), data.get("metrics_views", 0),
            data.get("notes"), data.get("status", "draft"), now, now,
        ))
    return cur.lastrowid


def get_viral_pattern(pid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM viral_patterns WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["format_suitability"] = json.loads(d.get("format_suitability") or "[]")
    d["risk_flags"]         = json.loads(d.get("risk_flags") or "[]")
    return d


def list_viral_patterns(status: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM viral_patterns WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["format_suitability"] = json.loads(d.get("format_suitability") or "[]")
        d["risk_flags"]         = json.loads(d.get("risk_flags") or "[]")
        result.append(d)
    return result


def update_viral_pattern(pid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scalar_fields = ["hook","problem_framing","emotional_arc","cta","notes","status",
                     "source_type","source_url","source_caption","title",
                     "metrics_likes","metrics_comments","metrics_saves","metrics_views"]
    json_fields = ["format_suitability","risk_flags"]
    sets, params = [], []
    for f in scalar_fields:
        if f in data:
            sets.append(f"{f}=?"); params.append(data[f])
    for f in json_fields:
        if f in data:
            sets.append(f"{f}=?"); params.append(json.dumps(data[f]))
    if not sets:
        return
    sets.append("updated_at=?"); params.append(now)
    params.append(pid)
    with get_conn() as conn:
        conn.execute(f"UPDATE viral_patterns SET {','.join(sets)} WHERE id=?", params)


def add_pattern_example(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO viral_pattern_examples
                (pattern_id,title,source_platform,source_url,source_account,
                 caption,image_url,likes,comments,saves,views,posted_at,notes,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["pattern_id"], data.get("title"), data.get("source_platform"),
            data.get("source_url"), data.get("source_account"),
            data.get("caption"), data.get("image_url"),
            data.get("likes", 0), data.get("comments", 0),
            data.get("saves", 0), data.get("views", 0),
            data.get("posted_at"), data.get("notes"), now,
        ))
    return cur.lastrowid


def list_pattern_examples(pattern_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM viral_pattern_examples WHERE pattern_id=? ORDER BY created_at DESC",
            (pattern_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════
# Campaign Pipeline — CAMPAIGNS
# ══════════════════════════════════════════

def create_campaign(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO campaigns
                (title,brand,objective,start_date,end_date,status,notes,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            data["title"], data.get("brand"), data.get("objective"),
            data.get("start_date"), data.get("end_date"),
            data.get("status", "planning"), data.get("notes"), now, now,
        ))
    return cur.lastrowid


def get_campaign(cid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    return dict(row) if row else None


def list_campaigns(brand: str = "", status: str = "", limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM campaigns WHERE 1=1"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_campaign(cid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = ["title","brand","objective","start_date","end_date","status","notes"]
    sets, params = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f}=?"); params.append(data[f])
    if not sets:
        return
    sets.append("updated_at=?"); params.append(now)
    params.append(cid)
    with get_conn() as conn:
        conn.execute(f"UPDATE campaigns SET {','.join(sets)} WHERE id=?", params)


# ══════════════════════════════════════════
# Campaign Pipeline — CONTENT IDEAS
# ══════════════════════════════════════════

def create_content_idea(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO content_ideas
                (pattern_id,campaign_id,brand,title,hook,body,cta,
                 target_formats,tone,notes,status,created_by,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data.get("pattern_id"), data.get("campaign_id"),
            data["brand"], data["title"],
            data.get("hook"), data.get("body"), data.get("cta"),
            json.dumps(data.get("target_formats", [])),
            data.get("tone"), data.get("notes"),
            data.get("status", "draft"), data.get("created_by", "manual"),
            now, now,
        ))
    return cur.lastrowid


def get_content_idea(iid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM content_ideas WHERE id=?", (iid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["target_formats"] = json.loads(d.get("target_formats") or "[]")
    return d


def list_content_ideas(brand: str = "", campaign_id: int = 0,
                        status: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM content_ideas WHERE 1=1"
    params: list = []
    if brand:
        sql += " AND brand=?"; params.append(brand)
    if campaign_id:
        sql += " AND campaign_id=?"; params.append(campaign_id)
    if status:
        sql += " AND status=?"; params.append(status)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["target_formats"] = json.loads(d.get("target_formats") or "[]")
        result.append(d)
    return result


def update_content_idea(iid: int, data: dict):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fields = ["brand","title","hook","body","cta","tone","notes","status","campaign_id"]
    sets, params = [], []
    for f in fields:
        if f in data:
            sets.append(f"{f}=?"); params.append(data[f])
    if "target_formats" in data:
        sets.append("target_formats=?"); params.append(json.dumps(data["target_formats"]))
    if not sets:
        return
    sets.append("updated_at=?"); params.append(now)
    params.append(iid)
    with get_conn() as conn:
        conn.execute(f"UPDATE content_ideas SET {','.join(sets)} WHERE id=?", params)


# ══════════════════════════════════════════
# Campaign Pipeline — CONTENT VARIANTS
# ══════════════════════════════════════════

def create_content_variant(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO content_variants
                (idea_id,format,caption,hashtags,image_prompt,video_prompt,
                 duration_sec,notes,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["idea_id"], data["format"],
            data.get("caption"), data.get("hashtags"),
            data.get("image_prompt"), data.get("video_prompt"),
            data.get("duration_sec"), data.get("notes"),
            data.get("status", "draft"), now, now,
        ))
    return cur.lastrowid


def get_content_variant(vid: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM content_variants WHERE id=?", (vid,)).fetchone()
    return dict(row) if row else None


def list_content_variants(idea_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM content_variants WHERE idea_id=? ORDER BY format,created_at",
            (idea_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_variant_status(vid: int, status: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE content_variants SET status=?,updated_at=? WHERE id=?",
            (status, now, vid)
        )


# ══════════════════════════════════════════
# Campaign Pipeline — PUBLISHING JOBS
# ══════════════════════════════════════════

def create_publishing_job(data: dict) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO publishing_jobs
                (variant_id,idea_id,campaign_id,brand,platform,
                 scheduled_at,status,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            data.get("variant_id"), data.get("idea_id"), data.get("campaign_id"),
            data.get("brand"), data.get("platform"),
            data.get("scheduled_at"),
            data.get("status", "pending_approval"), now, now,
        ))
    return cur.lastrowid


def list_publishing_jobs(status: str = "", brand: str = "",
                          limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM publishing_jobs WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status=?"; params.append(status)
    if brand:
        sql += " AND brand=?"; params.append(brand)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def update_job_status(jid: int, status: str, note: str = "", approved_by: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            UPDATE publishing_jobs
            SET status=?, approval_note=?, approved_by=?, updated_at=?
            WHERE id=?
        """, (status, note, approved_by, now, jid))


# ══════════════════════════════════════════
# Campaign Pipeline — STATS
# ══════════════════════════════════════════

def get_noimos_stats() -> dict:
    with get_conn() as conn:
        patterns_total    = conn.execute("SELECT COUNT(*) FROM viral_patterns").fetchone()[0]
        patterns_extracted = conn.execute(
            "SELECT COUNT(*) FROM viral_patterns WHERE status='extracted'"
        ).fetchone()[0]
        ideas_total       = conn.execute("SELECT COUNT(*) FROM content_ideas").fetchone()[0]
        ideas_approved    = conn.execute(
            "SELECT COUNT(*) FROM content_ideas WHERE status='approved'"
        ).fetchone()[0]
        variants_total    = conn.execute("SELECT COUNT(*) FROM content_variants").fetchone()[0]
        campaigns_active  = conn.execute(
            "SELECT COUNT(*) FROM campaigns WHERE status='active'"
        ).fetchone()[0]
        pending_approvals = conn.execute(
            "SELECT COUNT(*) FROM publishing_jobs WHERE status='pending_approval'"
        ).fetchone()[0]
    return {
        "patterns_total":     patterns_total,
        "patterns_extracted": patterns_extracted,
        "ideas_total":        ideas_total,
        "ideas_approved":     ideas_approved,
        "variants_total":     variants_total,
        "campaigns_active":   campaigns_active,
        "pending_approvals":  pending_approvals,
    }


# ══════════════════════════════════════════
# AUDIT LOGS
# ══════════════════════════════════════════

def write_audit(action: str, resource: str = "", resource_id: str = "",
                user_id: str = "", user_name: str = "",
                detail: dict | None = None, status: str = "ok",
                ip_address: str = "", user_agent: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO audit_logs
                (user_id,user_name,action,resource,resource_id,
                 detail,ip_address,user_agent,status,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (
            user_id or None, user_name or None,
            action, resource or None, resource_id or None,
            json.dumps(detail or {}),
            ip_address or None, user_agent or None,
            status, now,
        ))


def list_audit_logs(resource: str = "", user_id: str = "",
                    action: str = "", limit: int = 200,
                    offset: int = 0) -> list[dict]:
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    params: list = []
    if resource:
        sql += " AND resource=?"; params.append(resource)
    if user_id:
        sql += " AND user_id=?"; params.append(user_id)
    if action:
        sql += " AND action LIKE ?"; params.append(f"%{action}%")
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except Exception:
            d["detail"] = {}
        result.append(d)
    return result


def count_audit_logs() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0]


# ══════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════

def push_notification(title: str, body: str = "", link: str = "",
                      user_id: str = "", type_: str = "info",
                      priority: str = "normal", source: str = "system") -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO notifications
                (user_id,type,title,body,link,is_read,priority,source,created_at)
            VALUES (?,?,?,?,?,0,?,?,?)
        """, (
            user_id or None, type_, title,
            body or None, link or None,
            priority, source, now,
        ))
    return cur.lastrowid


def list_notifications(user_id: str = "", unread_only: bool = False,
                       limit: int = 50) -> list[dict]:
    sql = "SELECT * FROM notifications WHERE 1=1"
    params: list = []
    if user_id:
        sql += " AND (user_id=? OR user_id IS NULL)"; params.append(user_id)
    if unread_only:
        sql += " AND is_read=0"
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_unread_notifications(user_id: str = "") -> int:
    sql = "SELECT COUNT(*) FROM notifications WHERE is_read=0"
    params: list = []
    if user_id:
        sql += " AND (user_id=? OR user_id IS NULL)"; params.append(user_id)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def mark_notification_read(notification_id: int):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "UPDATE notifications SET is_read=1,read_at=? WHERE id=?",
            (now, notification_id)
        )


def mark_all_notifications_read(user_id: str = ""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = "UPDATE notifications SET is_read=1,read_at=? WHERE is_read=0"
    params: list = [now]
    if user_id:
        sql += " AND (user_id=? OR user_id IS NULL)"; params.append(user_id)
    with get_conn() as conn:
        conn.execute(sql, params)


# ══════════════════════════════════════════
# COMMENTS
# ══════════════════════════════════════════

def add_comment(resource: str, resource_id: str, body: str,
                author_name: str, author_id: str = "") -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO comments (resource,resource_id,author_id,author_name,body,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?)
        """, (resource, resource_id, author_id or None, author_name, body, now, now))
    return cur.lastrowid


def list_comments(resource: str, resource_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM comments WHERE resource=? AND resource_id=?
            ORDER BY created_at ASC
        """, (resource, resource_id)).fetchall()
    return [dict(r) for r in rows]


def delete_comment(comment_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM comments WHERE id=?", (comment_id,))


# ══════════════════════════════════════════
# ATTACHMENTS
# ══════════════════════════════════════════

def add_attachment(resource: str, resource_id: str, file_name: str,
                   file_path: str, mime_type: str = "",
                   file_size: int = 0, uploaded_by: str = "") -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO attachments
                (resource,resource_id,file_name,file_path,file_size,mime_type,uploaded_by,created_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (resource, resource_id, file_name, file_path, file_size,
              mime_type or None, uploaded_by or None, now))
    return cur.lastrowid


def list_attachments(resource: str, resource_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM attachments WHERE resource=? AND resource_id=?
            ORDER BY created_at DESC
        """, (resource, resource_id)).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("データベース初期化中...")
    init_db()
    print("YAMLデータを移行中...")
    result = migrate_from_yaml()
    print(f"移行完了: {result}")
    print(f"データベース: {DB_PATH}")
