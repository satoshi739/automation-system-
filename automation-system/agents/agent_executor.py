"""
Agent Executor
==============
Takes a queued task, runs the assigned Claude AI agent against it,
handles tool calls, and closes the run record in org_database.

Entry points:
  run(task_id)        — execute a specific task
  run_next(limit=5)   — pick up to N queued tasks and run them
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic

# パスを通す
_BASE = Path(__file__).parent.parent
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

import org_database as db
from agents import orchestrator
from utils import atomic_yaml_write, claude_resp_text

log = logging.getLogger(__name__)

# 起動時に API キーの有無を確認する（実行時 KeyError を未然に防ぐ）
_ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not _ANTHROPIC_KEY:
    log.warning("ANTHROPIC_API_KEY 未設定 — エージェント機能は無効")

# ── ディレクトリ定数 ──────────────────────────────────────────
QUEUE_ROOT   = _BASE / "content_queue"
DECISION_DIR = _BASE / "decision_queue"
LEADS_DIR    = _BASE.parent / "sales-system" / "leads"
CALENDAR_DIR = QUEUE_ROOT / "calendar"

# ── Claude クライアント ──────────────────────────────────────
def _client() -> anthropic.Anthropic | None:
    if not _ANTHROPIC_KEY:
        return None
    return anthropic.Anthropic(api_key=_ANTHROPIC_KEY)


# ════════════════════════════════════════════════════════════
# ツール スキーマ定義
# ════════════════════════════════════════════════════════════

TOOL_SCHEMAS: dict[str, dict] = {

    "generate_post": {
        "name": "generate_post",
        "description": "指定ブランド・プラットフォーム向けの投稿文（キャプション＋ハッシュタグ）を生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":    {"type": "string", "description": "ブランドID (例: dsc-marketing)"},
                "platform": {"type": "string", "description": "投稿先 (instagram/threads/facebook/twitter/line/wordpress)"},
                "topic":    {"type": "string", "description": "投稿テーマ・トピック"},
                "target":   {"type": "string", "description": "ターゲット層"},
                "tone":     {"type": "string", "description": "トーン (例: 親しみやすい・専門的)"},
                "extra":    {"type": "string", "description": "追加指示（省略可）"},
            },
            "required": ["brand", "platform", "topic"],
        },
    },

    "queue_push": {
        "name": "queue_push",
        "description": "生成したコンテンツを投稿キューに追加する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":      {"type": "string", "description": "ブランドID"},
                "platform":   {"type": "string", "description": "プラットフォーム"},
                "caption":    {"type": "string", "description": "投稿本文"},
                "hashtags":   {"type": "string", "description": "ハッシュタグ"},
                "image_url":  {"type": "string", "description": "画像URL（省略可）"},
                "scheduled_at": {"type": "string", "description": "予約投稿日時 ISO形式（省略可）"},
            },
            "required": ["brand", "platform", "caption"],
        },
    },

    "weekly_calendar": {
        "name": "weekly_calendar",
        "description": "週次コンテンツカレンダーを自動生成し保存する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":     {"type": "string", "description": "ブランドID"},
                "week_note": {"type": "string", "description": "今週の重点テーマや注意事項（省略可）"},
            },
            "required": ["brand"],
        },
    },

    "line_broadcast": {
        "name": "line_broadcast",
        "description": "LINE公式アカウントから全フォロワーに一斉配信する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "message": {"type": "string", "description": "配信メッセージ本文"},
            },
            "required": ["brand", "message"],
        },
    },

    "generate_blog_post": {
        "name": "generate_blog_post",
        "description": "ブログ記事（タイトル・本文・SEOメタ）を生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "topic":   {"type": "string", "description": "記事テーマ"},
                "keyword": {"type": "string", "description": "SEOキーワード（省略可）"},
                "length":  {"type": "integer", "description": "目標文字数（省略可、デフォルト1200）"},
            },
            "required": ["brand", "topic"],
        },
    },

    "wordpress_draft": {
        "name": "wordpress_draft",
        "description": "WordPressに記事を下書き保存する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "title":   {"type": "string", "description": "記事タイトル"},
                "content": {"type": "string", "description": "記事本文（HTML or Markdown）"},
                "status":  {"type": "string", "description": "draft or publish（デフォルト: draft）"},
            },
            "required": ["brand", "title", "content"],
        },
    },

    "lead_reply": {
        "name": "lead_reply",
        "description": "リードの問い合わせ内容を読んで返信ドラフトを生成し、LINEまたはメールで送信する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
                "send":    {"type": "boolean", "description": "true=実際に送信, false=ドラフトのみ返す"},
            },
            "required": ["lead_id"],
        },
    },

    "followup_send": {
        "name": "followup_send",
        "description": "指定リードにフォローアップメッセージを送信する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
                "message": {"type": "string", "description": "フォローアップ内容（省略時は自動生成）"},
                "channel": {"type": "string", "description": "line または email（デフォルト: line）"},
            },
            "required": ["lead_id"],
        },
    },

    "stage_update": {
        "name": "stage_update",
        "description": "リードの営業ステージを更新する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
                "stage":   {"type": "string", "description": "新ステージ (new/contacted/qualified/proposal/closed_won/closed_lost)"},
                "note":    {"type": "string", "description": "更新メモ（省略可）"},
            },
            "required": ["lead_id", "stage"],
        },
    },

    "performance_fetch": {
        "name": "performance_fetch",
        "description": "指定ブランドのSNSパフォーマンスサマリーを取得する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":    {"type": "string", "description": "ブランドID"},
                "platform": {"type": "string", "description": "プラットフォーム（省略時: instagram）"},
                "days":     {"type": "integer", "description": "集計日数（省略時: 30）"},
            },
            "required": ["brand"],
        },
    },

    "trend_research": {
        "name": "trend_research",
        "description": "指定ブランド・業界のトレンドトピックをリサーチして返す",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "keyword": {"type": "string", "description": "調査キーワード（省略可）"},
                "count":   {"type": "integer", "description": "提案数（省略時: 5）"},
            },
            "required": ["brand"],
        },
    },

    "ga4_fetch": {
        "name": "ga4_fetch",
        "description": "Google Analytics 4 からセッション・PV・ユーザー数を取得する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "ブランドID"},
                "days":  {"type": "integer", "description": "集計日数（省略時: 28）"},
            },
            "required": ["brand"],
        },
    },

    "scheduler_check": {
        "name": "scheduler_check",
        "description": "スケジューラーの直近実行ログと次回予定を確認する",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "取得件数（省略時: 10）"},
            },
            "required": [],
        },
    },

    "decision_triage": {
        "name": "decision_triage",
        "description": "判断待ちキューの未処理件数と優先度リストを返す",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "取得件数（省略時: 20）"},
            },
            "required": [],
        },
    },

    "db_backup": {
        "name": "db_backup",
        "description": "SQLiteデータベースをバックアップファイルにコピーする",
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "バックアップメモ（省略可）"},
            },
            "required": [],
        },
    },

    # ── SNS 直接投稿 ──────────────────────────────────────────

    "post_to_instagram": {
        "name": "post_to_instagram",
        "description": "Instagram に画像または動画を直接投稿する（キューを経由しない即時投稿）",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":     {"type": "string", "description": "ブランドID"},
                "caption":   {"type": "string", "description": "投稿本文＋ハッシュタグ"},
                "image_url": {"type": "string", "description": "公開アクセス可能な画像URL"},
                "video_url": {"type": "string", "description": "動画URL（リール）"},
                "is_reel":   {"type": "boolean", "description": "true でリール投稿"},
            },
            "required": ["brand", "caption"],
        },
    },

    "post_to_facebook": {
        "name": "post_to_facebook",
        "description": "Facebook ページにテキストまたは画像を投稿する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":     {"type": "string", "description": "ブランドID"},
                "message":   {"type": "string", "description": "投稿テキスト"},
                "image_url": {"type": "string", "description": "画像URL（省略可）"},
            },
            "required": ["brand", "message"],
        },
    },

    "post_to_twitter": {
        "name": "post_to_twitter",
        "description": "Twitter/X にツイートを投稿する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "ブランドID"},
                "text":  {"type": "string", "description": "ツイート本文（280文字以内）"},
            },
            "required": ["brand", "text"],
        },
    },

    "post_to_threads": {
        "name": "post_to_threads",
        "description": "Threads にテキストまたは画像を投稿する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":     {"type": "string", "description": "ブランドID"},
                "text":      {"type": "string", "description": "投稿テキスト"},
                "image_url": {"type": "string", "description": "画像URL（省略可）"},
            },
            "required": ["brand", "text"],
        },
    },

    "post_to_tiktok": {
        "name": "post_to_tiktok",
        "description": "TikTok に動画を投稿する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":     {"type": "string", "description": "ブランドID"},
                "video_url": {"type": "string", "description": "動画URL"},
                "title":     {"type": "string", "description": "タイトル・説明文"},
            },
            "required": ["brand", "video_url", "title"],
        },
    },

    # ── コンテンツ生成（拡張） ────────────────────────────────

    "generate_reel_script": {
        "name": "generate_reel_script",
        "description": "Instagram/TikTok リール用の台本（BGM候補・テロップ・シーン割）を生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":  {"type": "string", "description": "ブランドID"},
                "topic":  {"type": "string", "description": "リールのテーマ"},
                "length": {"type": "integer", "description": "尺（秒）。省略時 30秒"},
            },
            "required": ["brand", "topic"],
        },
    },

    "generate_tiktok_content": {
        "name": "generate_tiktok_content",
        "description": "TikTok 向けのフック・本編・CTA テキストとハッシュタグを生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":  {"type": "string", "description": "ブランドID"},
                "topic":  {"type": "string", "description": "動画のテーマ"},
                "target": {"type": "string", "description": "ターゲット層（省略可）"},
            },
            "required": ["brand", "topic"],
        },
    },

    "generate_story_content": {
        "name": "generate_story_content",
        "description": "Instagram Stories 用のテキストオーバーレイ・スタンプ文言を生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "theme":   {"type": "string", "description": "ストーリーのテーマ"},
                "slides":  {"type": "integer", "description": "スライド枚数（省略時 3）"},
            },
            "required": ["brand", "theme"],
        },
    },

    "generate_shorts_content": {
        "name": "generate_shorts_content",
        "description": "YouTube Shorts 用の台本とタイトル・説明文を生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "ブランドID"},
                "topic": {"type": "string", "description": "動画テーマ"},
            },
            "required": ["brand", "topic"],
        },
    },

    "multilingual_post": {
        "name": "multilingual_post",
        "description": "日本語・英語・タイ語の3言語で投稿文を同時生成する（Bangkok Peach 用）",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":    {"type": "string", "description": "ブランドID"},
                "platform": {"type": "string", "description": "投稿先プラットフォーム"},
                "topic":    {"type": "string", "description": "投稿テーマ"},
                "languages": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "生成言語リスト（省略時: ['ja','en','th']）",
                },
            },
            "required": ["brand", "topic"],
        },
    },

    "compliance_check": {
        "name": "compliance_check",
        "description": "金融・医療・法律関連コンテンツのコンプライアンスチェックを行う（CFJ専用）",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "チェックするテキスト"},
                "brand":   {"type": "string", "description": "ブランドID"},
            },
            "required": ["content", "brand"],
        },
    },

    # ── 営業（拡張） ──────────────────────────────────────────

    "lead_create": {
        "name": "lead_create",
        "description": "新規リードを起票してファイルに保存する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":        {"type": "string", "description": "ブランドID"},
                "name":         {"type": "string", "description": "リード名・会社名"},
                "contact":      {"type": "string", "description": "連絡先（LINE ID / メール / 電話）"},
                "source":       {"type": "string", "description": "流入元（instagram/line/web等）"},
                "inquiry":      {"type": "string", "description": "問い合わせ内容"},
                "line_user_id": {"type": "string", "description": "LINE ユーザーID（省略可）"},
            },
            "required": ["brand", "name", "inquiry"],
        },
    },

    "lead_list": {
        "name": "lead_list",
        "description": "リード一覧を取得する。ステージ・ブランドでフィルタ可能",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":  {"type": "string", "description": "ブランドID（省略時: 全ブランド）"},
                "stage":  {"type": "string", "description": "ステージフィルタ（省略時: 全ステージ）"},
                "limit":  {"type": "integer", "description": "取得件数（省略時: 20）"},
            },
            "required": [],
        },
    },

    "qualify_lead": {
        "name": "qualify_lead",
        "description": "リードの問い合わせ内容を分析し、優先度スコアと対応方針を判定する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
            },
            "required": ["lead_id"],
        },
    },

    "generate_proposal": {
        "name": "generate_proposal",
        "description": "リード情報をもとに提案書ドラフトを生成する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
                "brand":   {"type": "string", "description": "提案するブランドID"},
                "plan":    {"type": "string", "description": "提案プラン名（省略時は自動選定）"},
            },
            "required": ["lead_id", "brand"],
        },
    },

    "escalate_lead": {
        "name": "escalate_lead",
        "description": "重要リードを判断待ちキューに追加し、Satoshi に通知する",
        "input_schema": {
            "type": "object",
            "properties": {
                "lead_id": {"type": "string", "description": "リードID"},
                "reason":  {"type": "string", "description": "エスカレーション理由"},
                "priority": {"type": "string", "description": "urgent / high / normal"},
            },
            "required": ["lead_id", "reason"],
        },
    },

    "line_push": {
        "name": "line_push",
        "description": "特定の LINE ユーザーに個別メッセージを送信する",
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string", "description": "LINE ユーザーID"},
                "message": {"type": "string", "description": "送信メッセージ"},
            },
            "required": ["user_id", "message"],
        },
    },

    # ── 分析（拡張） ──────────────────────────────────────────

    "gsc_fetch": {
        "name": "gsc_fetch",
        "description": "Google Search Console からクリック数・表示回数・上位クエリを取得する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "ブランドID"},
                "days":  {"type": "integer", "description": "集計日数（省略時: 28）"},
            },
            "required": ["brand"],
        },
    },

    "generate_report": {
        "name": "generate_report",
        "description": "SNS・GA4・GSCデータを統合した週次/月次レポートを生成してファイルに保存する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":  {"type": "string", "description": "ブランドID（省略時: 全ブランド）"},
                "period": {"type": "string", "description": "weekly / monthly（省略時: weekly）"},
            },
            "required": [],
        },
    },

    "performance_compare": {
        "name": "performance_compare",
        "description": "複数ブランドのSNSパフォーマンスを比較分析する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "比較するブランドIDリスト（省略時: 全ブランド）",
                },
                "days": {"type": "integer", "description": "集計日数（省略時: 30）"},
            },
            "required": [],
        },
    },

    "seo_research": {
        "name": "seo_research",
        "description": "指定テーマのSEOキーワード・検索意図・競合状況をリサーチする",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand":   {"type": "string", "description": "ブランドID"},
                "topic":   {"type": "string", "description": "リサーチテーマ"},
                "keyword": {"type": "string", "description": "軸キーワード（省略可）"},
            },
            "required": ["brand", "topic"],
        },
    },

    # ── 運用（拡張） ──────────────────────────────────────────

    "queue_check": {
        "name": "queue_check",
        "description": "コンテンツキューの状態（未投稿件数・ブランド別・プラットフォーム別）を確認する",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "ブランドID（省略時: 全ブランド）"},
            },
            "required": [],
        },
    },

    "health_check": {
        "name": "health_check",
        "description": "システム全体のヘルス状態（DB・キュー・スケジューラー・API接続）を確認する",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },

    "cleanup_files": {
        "name": "cleanup_files",
        "description": "投稿済みコンテンツキューや古いログファイルを整理する",
        "input_schema": {
            "type": "object",
            "properties": {
                "days_old": {"type": "integer", "description": "何日以上前のファイルを対象にするか（省略時: 30）"},
                "dry_run":  {"type": "boolean", "description": "true で削除せずリストのみ返す"},
            },
            "required": [],
        },
    },

    "error_alert": {
        "name": "error_alert",
        "description": "エラーや異常を検知して Satoshi の LINE に通知する",
        "input_schema": {
            "type": "object",
            "properties": {
                "message":  {"type": "string", "description": "通知メッセージ"},
                "severity": {"type": "string", "description": "critical / warning / info"},
            },
            "required": ["message"],
        },
    },
}


# ════════════════════════════════════════════════════════════
# ツール ハンドラー
# ════════════════════════════════════════════════════════════

def _h_generate_post(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import (
        generate_instagram_post, generate_all_platforms,
        generate_line_message,
    )
    brand    = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    platform = inp.get("platform", "instagram")
    topic    = inp.get("topic", "")
    target   = inp.get("target", "一般ユーザー")
    tone     = inp.get("tone", "親しみやすい")
    extra    = inp.get("extra", "")

    if platform == "instagram":
        result = generate_instagram_post(topic, target, tone, brand, extra)
    elif platform == "line":
        result = {"message": generate_line_message(topic, brand)}
    else:
        result = generate_all_platforms(topic, target, tone, brand, [platform], extra)
        result = result.get(platform, result)

    log.info(f"generate_post: brand={brand} platform={platform}")
    return {"ok": True, "result": result}


def _h_queue_push(inp: dict, ctx: dict) -> dict:
    import uuid, yaml
    brand    = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    platform = inp.get("platform", "instagram")
    caption  = inp.get("caption", "")
    hashtags = inp.get("hashtags", "")
    image_url = inp.get("image_url", "")
    scheduled_at = inp.get("scheduled_at", "")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    entry = {
        "id":           str(uuid.uuid4())[:8],
        "brand":        brand,
        "platform":     platform,
        "caption":      caption,
        "hashtags":     hashtags,
        "image_url":    image_url,
        "scheduled_at": scheduled_at,
        "posted":       False,
        "source":       "agent",
        "created_at":   datetime.now().isoformat(),
    }
    dest = QUEUE_ROOT / brand / platform
    dest.mkdir(parents=True, exist_ok=True)
    fname = f"{ts}_agent.yaml"
    atomic_yaml_write(dest / fname, entry)

    # instagram キューにも追加
    if platform == "instagram":
        ig_dir = QUEUE_ROOT / "instagram"
        ig_dir.mkdir(parents=True, exist_ok=True)
        atomic_yaml_write(ig_dir / fname, entry)

    log.info(f"queue_push: {brand}/{platform} → {fname}")
    return {"ok": True, "file": fname, "brand": brand, "platform": platform}


def _h_weekly_calendar(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_weekly_calendar, save_weekly_calendar
    brand     = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    week_note = inp.get("week_note", "")
    extra     = f"今週の注意: {week_note}" if week_note else ""
    cal = generate_weekly_calendar(brand=brand)
    path = save_weekly_calendar(cal, brand=brand)
    log.info(f"weekly_calendar: brand={brand} saved to {path}")
    return {"ok": True, "brand": brand, "saved_to": str(path), "calendar": cal}


def _h_line_broadcast(inp: dict, ctx: dict) -> dict:
    from sns.line_api import LINEMessenger
    brand   = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    message = inp.get("message", "")
    if not message:
        return {"ok": False, "error": "message が空です"}
    messenger = LINEMessenger()
    ok = messenger.broadcast(message)
    log.info(f"line_broadcast: brand={brand} ok={ok}")
    return {"ok": ok, "brand": brand, "chars": len(message)}


def _h_generate_blog_post(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_blog_post
    brand      = inp.get("brand", ctx.get("brand_id", "satoshi-blog"))
    topic      = inp.get("topic", "")
    word_count = inp.get("length", 1200)
    result     = generate_blog_post(topic=topic, word_count=word_count)
    log.info(f"generate_blog_post: brand={brand} topic={topic}")
    return {"ok": True, "result": result}


def _h_wordpress_draft(inp: dict, ctx: dict) -> dict:
    from sns.wordpress import WordPressPoster
    brand   = inp.get("brand", ctx.get("brand_id", "satoshi-blog"))
    title   = inp.get("title", "")
    content = inp.get("content", "")
    status  = inp.get("status", "draft")
    wp = WordPressPoster(brand=brand)
    result = wp.create_post(title=title, content=content, status=status)
    log.info(f"wordpress_draft: brand={brand} title={title!r} status={status}")
    return {"ok": True, "result": result}


def _h_lead_reply(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_lead_reply
    lead_id = inp.get("lead_id", "")
    send    = inp.get("send", False)

    lead = db.get_lead(lead_id) if hasattr(db, "get_lead") else None
    if not lead:
        # ファイルから読む
        lead = _load_lead_from_file(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} が見つかりません"}

    draft = generate_lead_reply(lead)

    if send:
        line_uid = lead.get("line_user_id", "")
        if line_uid:
            from sns.line_api import LINEMessenger
            LINEMessenger().push(line_uid, draft)
            log.info(f"lead_reply sent via LINE: lead_id={lead_id}")
        else:
            log.warning(f"lead_reply: LINE user_id なし, 送信スキップ lead_id={lead_id}")

    return {"ok": True, "lead_id": lead_id, "draft": draft, "sent": send}


def _h_followup_send(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_lead_reply
    lead_id = inp.get("lead_id", "")
    message = inp.get("message", "")
    channel = inp.get("channel", "line")

    lead = _load_lead_from_file(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} が見つかりません"}

    if not message:
        message = generate_lead_reply(lead)

    if channel == "line":
        line_uid = lead.get("line_user_id", "")
        if line_uid:
            from sns.line_api import LINEMessenger
            ok = LINEMessenger().push(line_uid, message)
        else:
            ok = False
    else:
        ok = False
        log.warning(f"followup_send: channel={channel} 未対応")

    log.info(f"followup_send: lead_id={lead_id} channel={channel} ok={ok}")
    return {"ok": ok, "lead_id": lead_id, "channel": channel}


def _h_stage_update(inp: dict, ctx: dict) -> dict:
    import yaml
    lead_id = inp.get("lead_id", "")
    stage   = inp.get("stage", "")
    note    = inp.get("note", "")

    lead_path = _find_lead_file(lead_id)
    if not lead_path:
        return {"ok": False, "error": f"Lead {lead_id} が見つかりません"}

    with open(lead_path, encoding="utf-8") as f:
        lead = yaml.safe_load(f)

    old_stage = lead.get("stage", "")
    lead["stage"] = stage
    lead["updated_at"] = datetime.now().isoformat()
    if note:
        lead.setdefault("notes", []).append({
            "at": datetime.now().isoformat(), "note": note
        })

    atomic_yaml_write(lead_path, lead)

    log.info(f"stage_update: lead_id={lead_id} {old_stage} → {stage}")
    return {"ok": True, "lead_id": lead_id, "old_stage": old_stage, "new_stage": stage}


def _h_performance_fetch(inp: dict, ctx: dict) -> dict:
    from sns.performance import get_performance_summary
    brand    = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    platform = inp.get("platform", "instagram")
    days     = inp.get("days", 30)
    summary  = get_performance_summary(brand=brand, platform=platform, days=days)
    return {"ok": True, "brand": brand, "platform": platform, "summary": summary}


def _h_trend_research(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import research_trending_topics
    brand   = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    keyword = inp.get("keyword", "")
    count   = inp.get("count", 5)
    topics  = research_trending_topics(brand=brand, n=count)
    return {"ok": True, "brand": brand, "topics": topics}


def _h_ga4_fetch(inp: dict, ctx: dict) -> dict:
    from sns.analytics import GA4Client
    brand    = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    days     = inp.get("days", 28)
    env_key  = brand.upper().replace("-", "_") + "_GA4_PROPERTY_ID"
    client   = GA4Client(property_id_env=env_key)
    overview = client.get_overview(days=days)
    return {"ok": True, "brand": brand, "days": days, "data": overview}


def _h_scheduler_check(inp: dict, ctx: dict) -> dict:
    limit = inp.get("limit", 10)
    logs_dir = _BASE / "logs"
    entries: list[dict] = []
    if logs_dir.exists():
        import yaml
        for f in sorted(logs_dir.glob("*.yaml"), reverse=True)[:limit]:
            try:
                with open(f, encoding="utf-8") as fh:
                    entries.append(yaml.safe_load(fh) or {})
            except Exception:
                pass
    return {"ok": True, "log_count": len(entries), "recent": entries[:limit]}


def _h_decision_triage(inp: dict, ctx: dict) -> dict:
    import yaml
    limit = inp.get("limit", 20)
    items: list[dict] = []
    if DECISION_DIR.exists():
        for f in sorted(DECISION_DIR.glob("*.yaml"), reverse=True)[:limit]:
            try:
                with open(f, encoding="utf-8") as fh:
                    d = yaml.safe_load(fh) or {}
                    d["_file"] = f.name
                    items.append(d)
            except Exception:
                pass
    urgent = [i for i in items if i.get("priority") in ("high", "urgent")]
    return {
        "ok": True,
        "total": len(items),
        "urgent_count": len(urgent),
        "items": items,
    }


def _h_db_backup(inp: dict, ctx: dict) -> dict:
    note = inp.get("note", "")
    db_path = _BASE / "data" / "automation.db"
    if not db_path.exists():
        db_path = _BASE / "automation.db"
    if not db_path.exists():
        return {"ok": False, "error": "データベースファイルが見つかりません"}

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = _BASE / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"backup_{ts}.db"
    shutil.copy2(db_path, dest)
    log.info(f"db_backup: {dest} note={note!r}")
    return {"ok": True, "backup_file": str(dest), "note": note}


# ── SNS 直接投稿ハンドラー ────────────────────────────────────

def _h_post_to_instagram(inp: dict, ctx: dict) -> dict:
    from sns.instagram import InstagramPoster
    brand     = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    caption   = inp.get("caption", "")
    image_url = inp.get("image_url", "")
    video_url = inp.get("video_url", "")
    is_reel   = inp.get("is_reel", False)
    poster = InstagramPoster()
    if is_reel and video_url:
        result = poster.post_reel(video_url, caption)
    elif image_url:
        result = poster.post_image(image_url, caption)
    else:
        return {"ok": False, "error": "image_url または video_url が必要です"}
    log.info(f"post_to_instagram: brand={brand} result={result}")
    return {"ok": True, "brand": brand, "result": result}


def _h_post_to_facebook(inp: dict, ctx: dict) -> dict:
    from sns.facebook import FacebookPoster
    brand     = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    message   = inp.get("message", "")
    image_url = inp.get("image_url", "")
    poster = FacebookPoster(brand=brand)
    if image_url:
        result = poster.post_image(image_url, message)
    else:
        result = poster.post_text(message)
    log.info(f"post_to_facebook: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_post_to_twitter(inp: dict, ctx: dict) -> dict:
    from sns.twitter import TwitterPoster
    brand  = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    text   = inp.get("text", "")
    poster = TwitterPoster(brand=brand)
    result = poster.tweet(text)
    log.info(f"post_to_twitter: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_post_to_threads(inp: dict, ctx: dict) -> dict:
    from sns.threads import ThreadsPoster
    import os
    brand     = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    text      = inp.get("text", "")
    image_url = inp.get("image_url", "")
    prefix    = brand.upper().replace("-", "_")
    account_id = os.environ.get(f"{prefix}_THREADS_USER_ID", "")
    token      = os.environ.get(f"{prefix}_META_ACCESS_TOKEN", "")
    poster = ThreadsPoster(account_id=account_id, access_token=token)
    if image_url:
        result = poster.post_image(image_url, text)
    else:
        result = poster.post_text(text)
    log.info(f"post_to_threads: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_post_to_tiktok(inp: dict, ctx: dict) -> dict:
    from sns.tiktok import TikTokPoster
    brand     = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    video_url = inp.get("video_url", "")
    title     = inp.get("title", "")
    poster = TikTokPoster(brand=brand)
    result = poster.upload_video_url(video_url, title)
    log.info(f"post_to_tiktok: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


# ── コンテンツ生成（拡張）ハンドラー ─────────────────────────

def _h_generate_reel_script(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_reel_script_rich
    brand  = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    topic  = inp.get("topic", "")
    length = inp.get("length", 30)
    result = generate_reel_script_rich(topic=topic, brand=brand, duration_sec=length)
    log.info(f"generate_reel_script: brand={brand} topic={topic}")
    return {"ok": True, "brand": brand, "result": result}


def _h_generate_tiktok_content(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_tiktok_content
    brand  = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    topic  = inp.get("topic", "")
    target = inp.get("target", "一般ユーザー")
    result = generate_tiktok_content(topic=topic, brand=brand)
    log.info(f"generate_tiktok_content: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_generate_story_content(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_story_content
    brand  = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    theme  = inp.get("theme", "")
    slides = inp.get("slides", 3)
    result = generate_story_content(topic=theme, brand=brand)
    log.info(f"generate_story_content: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_generate_shorts_content(inp: dict, ctx: dict) -> dict:
    from dashboard.ai import generate_shorts_content
    brand  = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    topic  = inp.get("topic", "")
    result = generate_shorts_content(topic=topic, brand=brand)
    log.info(f"generate_shorts_content: brand={brand}")
    return {"ok": True, "brand": brand, "result": result}


def _h_multilingual_post(inp: dict, ctx: dict) -> dict:
    brand     = inp.get("brand", ctx.get("brand_id", "bangkok-peach"))
    platform  = inp.get("platform", "instagram")
    topic     = inp.get("topic", "")
    languages = inp.get("languages", ["ja", "en", "th"])
    client = _client()
    if not client:
        return {"error": "API key not configured"}
    lang_names = {"ja": "日本語", "en": "英語", "th": "タイ語"}
    results = {}
    for lang in languages:
        lang_name = lang_names.get(lang, lang)
        prompt = (
            f"Bangkok Peach Group の{platform}投稿を{lang_name}で書いてください。\n"
            f"テーマ: {topic}\n"
            f"ハッシュタグも含めてください。"
        )
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        results[lang] = claude_resp_text(resp)
    log.info(f"multilingual_post: brand={brand} langs={languages}")
    return {"ok": True, "brand": brand, "platform": platform, "posts": results}


def _h_compliance_check(inp: dict, ctx: dict) -> dict:
    content = inp.get("content", "")
    brand   = inp.get("brand", ctx.get("brand_id", "cashflowsupport"))
    client  = _client()
    if not client:
        return {"error": "API key not configured"}
    prompt = f"""以下のコンテンツの金融・法律コンプライアンスチェックをしてください。

ブランド: {brand}
コンテンツ:
{content}

チェック項目:
- 誇大広告・断定的な利益表現がないか
- 金融商品取引法・貸金業法・景品表示法に抵触しないか
- 「必ず」「確実に」「誰でも」などNG表現がないか
- 審査通過を保証するような表現がないか

結果を「OK」または「NG（理由）」で返してください。NGの場合は修正案も添えてください。"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    result_text = claude_resp_text(resp)
    is_ok = result_text.strip().upper().startswith("OK")
    log.info(f"compliance_check: brand={brand} ok={is_ok}")
    return {"ok": True, "compliant": is_ok, "result": result_text}


# ── 営業（拡張）ハンドラー ────────────────────────────────────

def _h_lead_create(inp: dict, ctx: dict) -> dict:
    import uuid, yaml as _yaml
    brand        = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    name         = inp.get("name", "")
    contact      = inp.get("contact", "")
    source       = inp.get("source", "unknown")
    inquiry      = inp.get("inquiry", "")
    line_user_id = inp.get("line_user_id", "")

    lead_id = str(uuid.uuid4())[:8]
    ts = datetime.now().isoformat()
    lead = {
        "id":           lead_id,
        "brand":        brand,
        "name":         name,
        "contact":      contact,
        "source":       source,
        "inquiry":      inquiry,
        "line_user_id": line_user_id,
        "stage":        "new",
        "created_at":   ts,
        "updated_at":   ts,
        "notes":        [],
    }
    dest_dir = LEADS_DIR / brand
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{lead_id}.yaml"
    atomic_yaml_write(dest_dir / fname, lead)
    log.info(f"lead_create: {brand}/{fname}")
    return {"ok": True, "lead_id": lead_id, "file": fname}


def _h_lead_list(inp: dict, ctx: dict) -> dict:
    import yaml as _yaml
    brand = inp.get("brand", "")
    stage = inp.get("stage", "")
    limit = inp.get("limit", 20)
    leads = []
    search_root = LEADS_DIR / brand if brand else LEADS_DIR
    for f in sorted(search_root.rglob("*.yaml"), reverse=True):
        try:
            with open(f, encoding="utf-8") as fh:
                d = _yaml.safe_load(fh) or {}
            if stage and d.get("stage") != stage:
                continue
            leads.append(d)
            if len(leads) >= limit:
                break
        except Exception:
            pass
    return {"ok": True, "count": len(leads), "leads": leads}


def _h_qualify_lead(inp: dict, ctx: dict) -> dict:
    lead_id = inp.get("lead_id", "")
    lead    = _load_lead_from_file(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} が見つかりません"}
    client = _client()
    if not client:
        return {"error": "API key not configured"}
    prompt = f"""リードの資格判定をしてください。

ブランド: {lead.get('brand')}
問い合わせ内容: {lead.get('inquiry')}
流入元: {lead.get('source')}

以下を JSON で返してください:
{{
  "score": 1〜10（10が最高優先度）,
  "tier": "hot/warm/cold",
  "next_action": "推奨アクション",
  "estimated_budget": "予算感（わかれば）",
  "reason": "判定理由"
}}"""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = claude_resp_text(resp)
    try:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        qual = json.loads(m.group()) if m else {"raw": raw}
    except Exception:
        qual = {"raw": raw}
    log.info(f"qualify_lead: {lead_id} score={qual.get('score')}")
    return {"ok": True, "lead_id": lead_id, "qualification": qual}


def _h_generate_proposal(inp: dict, ctx: dict) -> dict:
    lead_id = inp.get("lead_id", "")
    brand   = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    plan    = inp.get("plan", "")
    lead    = _load_lead_from_file(lead_id)
    if not lead:
        return {"ok": False, "error": f"Lead {lead_id} が見つかりません"}
    client = _client()
    if not client:
        return {"error": "API key not configured"}
    brand_ctx = _brand_context(brand)
    prompt = f"""以下のリード向けに提案書ドラフトを作成してください。

{brand_ctx}
リード情報:
- 名前: {lead.get('name')}
- 問い合わせ: {lead.get('inquiry')}
- プラン希望: {plan or '未指定（最適なものを提案）'}

提案書の構成:
1. 課題の整理
2. 提案内容・プラン
3. 期待効果
4. 料金・契約内容
5. 次のステップ

丁寧・簡潔に、400字以内で。"""
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    proposal = claude_resp_text(resp)
    log.info(f"generate_proposal: lead_id={lead_id} brand={brand}")
    return {"ok": True, "lead_id": lead_id, "proposal": proposal}


def _h_escalate_lead(inp: dict, ctx: dict) -> dict:
    import yaml as _yaml
    lead_id  = inp.get("lead_id", "")
    reason   = inp.get("reason", "")
    priority = inp.get("priority", "high")
    lead     = _load_lead_from_file(lead_id)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    entry = {
        "type":     "lead_escalation",
        "lead_id":  lead_id,
        "lead_name": lead.get("name", "") if lead else "",
        "reason":   reason,
        "priority": priority,
        "created_at": datetime.now().isoformat(),
    }
    DECISION_DIR.mkdir(parents=True, exist_ok=True)
    atomic_yaml_write(DECISION_DIR / f"{ts}_lead_{lead_id}.yaml", entry)
    # Satoshi に LINE 通知
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if owner_id:
        from sns.line_api import LINEMessenger
        LINEMessenger().push(
            owner_id,
            f"⚠️ リードエスカレーション [{priority.upper()}]\n{lead.get('name','')}\n{reason}"
        )
    log.info(f"escalate_lead: {lead_id} priority={priority}")
    return {"ok": True, "lead_id": lead_id, "priority": priority}


def _h_line_push(inp: dict, ctx: dict) -> dict:
    from sns.line_api import LINEMessenger
    user_id = inp.get("user_id", "")
    message = inp.get("message", "")
    if not user_id or not message:
        return {"ok": False, "error": "user_id と message が必要です"}
    ok = LINEMessenger().push(user_id, message)
    log.info(f"line_push: user_id={user_id} ok={ok}")
    return {"ok": ok}


# ── 分析（拡張）ハンドラー ────────────────────────────────────

def _h_gsc_fetch(inp: dict, ctx: dict) -> dict:
    from sns.analytics import SearchConsoleClient
    brand    = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    days     = inp.get("days", 28)
    env_key  = brand.upper().replace("-", "_") + "_GSC_SITE_URL"
    client   = SearchConsoleClient(site_url_env=env_key)
    overview = client.get_overview(days=days)
    queries  = client.get_top_queries(days=days, limit=10)
    return {"ok": True, "brand": brand, "overview": overview, "top_queries": queries}


def _h_generate_report(inp: dict, ctx: dict) -> dict:
    import yaml as _yaml
    brand  = inp.get("brand", "")
    period = inp.get("period", "weekly")
    brands = [brand] if brand else ["dsc-marketing", "upjapan", "cashflowsupport", "bangkok-peach"]
    client = _client()
    if not client:
        return {"error": "API key not configured"}
    summaries = []
    for b in brands:
        try:
            from sns.performance import get_performance_summary
            s = get_performance_summary(brand=b, platform="instagram", days=7 if period=="weekly" else 30)
            summaries.append(f"【{b}】{s}")
        except Exception:
            summaries.append(f"【{b}】データ取得エラー")

    prompt = f"""以下のデータをもとに{period}レポートを作成してください。

{chr(10).join(summaries)}

レポート形式:
- 全体サマリー（3行）
- ブランド別ハイライト
- 改善提案（3点）
- 来週の重点アクション"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    report_text = claude_resp_text(resp)
    ts = datetime.now().strftime("%Y%m%d")
    report_dir = _BASE / "logs" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{ts}_{period}_report.md"
    (report_dir / fname).write_text(report_text, encoding="utf-8")
    log.info(f"generate_report: {fname}")
    return {"ok": True, "period": period, "file": fname, "report": report_text}


def _h_performance_compare(inp: dict, ctx: dict) -> dict:
    from sns.performance import get_performance_summary
    brands = inp.get("brands", ["dsc-marketing", "upjapan", "cashflowsupport", "bangkok-peach"])
    days   = inp.get("days", 30)
    result = {}
    for b in brands:
        try:
            result[b] = get_performance_summary(brand=b, platform="instagram", days=days)
        except Exception as e:
            result[b] = f"エラー: {e}"
    return {"ok": True, "days": days, "comparison": result}


def _h_seo_research(inp: dict, ctx: dict) -> dict:
    brand   = inp.get("brand", ctx.get("brand_id", "dsc-marketing"))
    topic   = inp.get("topic", "")
    keyword = inp.get("keyword", "")
    client  = _client()
    if not client:
        return {"error": "API key not configured"}
    brand_ctx = _brand_context(brand)
    prompt = f"""{brand_ctx}

以下のテーマで SEO キーワードリサーチをしてください。

テーマ: {topic}
軸キーワード: {keyword or '未指定'}

以下を JSON で返してください:
{{
  "main_keywords": ["主要KW×5"],
  "long_tail": ["ロングテールKW×5"],
  "search_intent": "検索意図の説明",
  "recommended_title": "推奨記事タイトル",
  "content_outline": ["見出し案×4"]
}}"""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = claude_resp_text(resp)
    try:
        import re
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        seo = json.loads(m.group()) if m else {"raw": raw}
    except Exception:
        seo = {"raw": raw}
    return {"ok": True, "brand": brand, "topic": topic, "seo": seo}


# ── 運用（拡張）ハンドラー ────────────────────────────────────

def _h_queue_check(inp: dict, ctx: dict) -> dict:
    brand = inp.get("brand", "")
    result: dict = {}
    brands = [brand] if brand else ["dsc-marketing", "upjapan", "cashflowsupport", "bangkok-peach", "satoshi-blog"]
    platforms = ["instagram", "facebook", "twitter", "threads", "tiktok", "line", "wordpress"]
    for b in brands:
        result[b] = {}
        for p in platforms:
            q_dir = QUEUE_ROOT / b / p
            if not q_dir.exists():
                continue
            import yaml as _yaml
            items = []
            for f in q_dir.glob("*.yaml"):
                try:
                    with open(f) as fh:
                        d = _yaml.safe_load(fh) or {}
                    items.append(d)
                except Exception:
                    pass
            pending = [i for i in items if not i.get("posted")]
            if pending:
                result[b][p] = len(pending)
    total = sum(sum(v.values()) for v in result.values())
    return {"ok": True, "total_pending": total, "by_brand": result}


def _h_health_check(inp: dict, ctx: dict) -> dict:
    checks: dict = {}
    # DB
    try:
        import database as main_db
        stats = main_db.get_stats()
        checks["database"] = {"ok": True, "posts": stats.get("total_posts", 0)}
    except Exception as e:
        checks["database"] = {"ok": False, "error": str(e)}
    # ANTHROPIC_API_KEY
    checks["anthropic_api"] = {"ok": bool(os.environ.get("ANTHROPIC_API_KEY"))}
    # LINE
    checks["line_api"] = {"ok": bool(os.environ.get("CASHFLOWSUPPORT_LINE_CHANNEL_ACCESS_TOKEN") or os.environ.get("BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN"))}
    # Meta
    checks["meta_api"] = {"ok": bool(os.environ.get("DSC_MARKETING_META_ACCESS_TOKEN"))}
    # キュー
    try:
        q_result = _h_queue_check({}, ctx)
        checks["content_queue"] = {"ok": True, "pending": q_result.get("total_pending", 0)}
    except Exception as e:
        checks["content_queue"] = {"ok": False, "error": str(e)}

    all_ok = all(v.get("ok", False) for v in checks.values())
    log.info(f"health_check: all_ok={all_ok}")
    return {"ok": all_ok, "checks": checks}


def _h_cleanup_files(inp: dict, ctx: dict) -> dict:
    import time
    days_old = inp.get("days_old", 30)
    dry_run  = inp.get("dry_run", True)
    cutoff   = time.time() - days_old * 86400
    removed  = []
    skipped  = []
    targets  = list((_BASE / "logs").glob("*.yaml")) + list((_BASE / "logs").glob("*.log"))
    for f in targets:
        if f.stat().st_mtime < cutoff:
            if dry_run:
                skipped.append(str(f))
            else:
                f.unlink()
                removed.append(str(f))
    log.info(f"cleanup_files: removed={len(removed)} dry_run={dry_run}")
    return {"ok": True, "removed": removed, "would_remove": skipped, "dry_run": dry_run}


def _h_error_alert(inp: dict, ctx: dict) -> dict:
    from sns.line_api import LINEMessenger
    message  = inp.get("message", "")
    severity = inp.get("severity", "warning")
    icons    = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
    icon     = icons.get(severity, "⚠️")
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if not owner_id:
        return {"ok": False, "error": "OWNER_LINE_USER_ID が未設定です"}
    full_msg = f"{icon} [{severity.upper()}] {message}"
    ok = LINEMessenger().push(owner_id, full_msg)
    log.info(f"error_alert: severity={severity} ok={ok}")
    return {"ok": ok, "severity": severity}


# ── ハンドラーテーブル ─────────────────────────────────────
TOOL_HANDLERS: dict[str, Any] = {
    # コンテンツ生成（基本）
    "generate_post":          _h_generate_post,
    "queue_push":             _h_queue_push,
    "weekly_calendar":        _h_weekly_calendar,
    "line_broadcast":         _h_line_broadcast,
    "generate_blog_post":     _h_generate_blog_post,
    "wordpress_draft":        _h_wordpress_draft,
    # コンテンツ生成（拡張）
    "generate_reel_script":   _h_generate_reel_script,
    "generate_tiktok_content": _h_generate_tiktok_content,
    "generate_story_content": _h_generate_story_content,
    "generate_shorts_content": _h_generate_shorts_content,
    "multilingual_post":      _h_multilingual_post,
    "compliance_check":       _h_compliance_check,
    # SNS 直接投稿
    "post_to_instagram":      _h_post_to_instagram,
    "post_to_facebook":       _h_post_to_facebook,
    "post_to_twitter":        _h_post_to_twitter,
    "post_to_threads":        _h_post_to_threads,
    "post_to_tiktok":         _h_post_to_tiktok,
    # 営業（基本）
    "lead_reply":             _h_lead_reply,
    "followup_send":          _h_followup_send,
    "stage_update":           _h_stage_update,
    # 営業（拡張）
    "lead_create":            _h_lead_create,
    "lead_list":              _h_lead_list,
    "qualify_lead":           _h_qualify_lead,
    "generate_proposal":      _h_generate_proposal,
    "escalate_lead":          _h_escalate_lead,
    "line_push":              _h_line_push,
    # 分析（基本）
    "performance_fetch":      _h_performance_fetch,
    "trend_research":         _h_trend_research,
    "ga4_fetch":              _h_ga4_fetch,
    # 分析（拡張）
    "gsc_fetch":              _h_gsc_fetch,
    "generate_report":        _h_generate_report,
    "performance_compare":    _h_performance_compare,
    "seo_research":           _h_seo_research,
    # 運用（基本）
    "scheduler_check":        _h_scheduler_check,
    "decision_triage":        _h_decision_triage,
    "db_backup":              _h_db_backup,
    # 運用（拡張）
    "queue_check":            _h_queue_check,
    "health_check":           _h_health_check,
    "cleanup_files":          _h_cleanup_files,
    "error_alert":            _h_error_alert,
}


# ════════════════════════════════════════════════════════════
# エージェント設定ローダー
# ════════════════════════════════════════════════════════════

def _load_os_config() -> dict:
    import yaml
    cfg_path = _BASE / "config" / "os_config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _get_agent_config(agent_id: str) -> dict:
    """
    DB の ai_agents レコード + os_config.yaml の tools 定義をマージして返す。
    agent_id は YAML の id（例: agent-content-upj）と一致している前提。
    """
    agent_row = db.get_ai_agent(agent_id)
    if not agent_row:
        raise ValueError(f"Agent {agent_id} が DB に存在しません")

    cfg = _load_os_config()
    yaml_agents: list[dict] = cfg.get("agents", [])

    # YAML ID で直接検索（setup_from_config.py が YAML ID を DB ID として登録する）
    yaml_entry = next((a for a in yaml_agents if a["id"] == agent_id), {})

    # DB の capabilities を優先、なければ YAML の tools を使う
    tools: list[str] = db.get_agent_capabilities_list(agent_id)
    if not tools:
        tools = yaml_entry.get("tools", [])

    # CEO の場合は yaml_agents でなく ai_ceo を参照
    if not yaml_entry and agent_id == cfg.get("ai_ceo", {}).get("id", "ai-ceo"):
        ceo = cfg.get("ai_ceo", {})
        yaml_entry = {
            "role": ceo.get("role", "AI CEO"),
            "description": ceo.get("description", ""),
            "brand": "",
            "tools": [],
        }

    return {
        "id":            agent_id,
        "model":         agent_row.get("model") or yaml_entry.get("model", "claude-haiku-4-5-20251001"),
        "role":          yaml_entry.get("role", "AI Agent"),
        "description":   yaml_entry.get("description", ""),
        "brand":         yaml_entry.get("brand", ""),
        "tools":         tools,
        "system_prompt": agent_row.get("system_prompt") or "",
    }


def _build_system_prompt(agent_cfg: dict, task: dict) -> str:
    brand_id = task.get("brand_id") or agent_cfg.get("brand") or ""
    brand_ctx = _brand_context(brand_id)

    lines = [
        f"あなたは {agent_cfg['role']} です。",
        agent_cfg["description"],
        "",
        "## 担当タスク",
        f"タイトル: {task.get('title', '')}",
        f"説明: {task.get('description', '')}",
    ]
    if brand_ctx:
        lines += ["", "## ブランドコンテキスト", brand_ctx]

    lines += [
        "",
        "## 行動原則",
        "- 与えられたツールを使ってタスクを完遂してください。",
        "- 一度に複数のツールを使って効率よく処理してください。",
        "- 完了したら日本語で結果サマリーを返してください。",
        "- 不確かな場合は実行せず、理由を説明してください。",
    ]
    return "\n".join(lines)


def _brand_context(brand_id: str) -> str:
    CONTEXTS = {
        "dsc-marketing":   "DSc Marketing — SNS・LINE・Web集客の導線設計・運用支援。月額25,000円〜100,800円。",
        "cashflowsupport": "cashflowsupport — ファクタリング・資金繰り相談。丁寧・経営者目線・透明性を重視。",
        "upjapan":         "UPJ（株式会社ユニバースプラネットジャパン）— 事業設計・収益モデル再設計・国際展開。",
        "bangkok-peach":   "Bangkok Peach Group — バンコク拠点の事業・観光・ライフスタイル。日英タイ対応。",
        "satoshi-blog":    "Satoshi Life Blog — 起業家Satoshiの一人称ブログ。ビジネス・海外生活・AI活用。",
    }
    return CONTEXTS.get(brand_id, "")


# ════════════════════════════════════════════════════════════
# メイン実行ループ
# ════════════════════════════════════════════════════════════

def run(task_id: str) -> dict:
    """
    タスクを1件実行する。
    Returns: {"task_id", "run_id", "status", "output", "tokens_used", "cost_usd"}
    """
    task = db.get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} が見つかりません")

    agent_id = task.get("assigned_to_agent_id")
    if not agent_id:
        agent_id = orchestrator.auto_assign(task_id)  # type: ignore[attr-defined]
    if not agent_id:
        raise RuntimeError(f"Task {task_id} に割り当てエージェントがありません")

    run_id = orchestrator.start_task(task_id)
    if not run_id:
        raise RuntimeError(f"Task {task_id} の run 開始に失敗しました")

    agent_cfg = _get_agent_config(agent_id)
    available_tools = [
        TOOL_SCHEMAS[t] for t in agent_cfg["tools"] if t in TOOL_SCHEMAS
    ]

    input_data: dict = {}
    try:
        raw = task.get("input_data") or "{}"
        input_data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except json.JSONDecodeError:
        pass

    system_prompt = _build_system_prompt(agent_cfg, task)
    initial_message = _build_initial_message(task, input_data)

    messages: list[dict] = [{"role": "user", "content": initial_message}]
    log_entries: list[dict] = []
    tokens_used = 0
    output_text = ""

    client = _client()
    if not client:
        orchestrator.fail_task(task_id, run_id, "ANTHROPIC_API_KEY 未設定")
        return {
            "task_id": task_id,
            "run_id":  run_id,
            "status":  "failed",
            "error":   "API key not configured",
        }
    max_iterations = 10

    try:
        for iteration in range(max_iterations):
            kwargs: dict = {
                "model":      agent_cfg["model"],
                "max_tokens": 4096,
                "system":     system_prompt,
                "tools":      available_tools,
                "messages":   messages,
            }

            response = client.messages.create(**kwargs)
            tokens_used += (response.usage.input_tokens + response.usage.output_tokens)

            log_entries.append({
                "iteration":   iteration + 1,
                "stop_reason": response.stop_reason,
                "tokens":      response.usage.input_tokens + response.usage.output_tokens,
            })

            # 終了
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        output_text += block.text
                break

            # ツール呼び出し
            if response.stop_reason == "tool_use":
                messages.append({
                    "role":    "assistant",
                    "content": response.content,
                })
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    handler = TOOL_HANDLERS.get(block.name)
                    ctx = {"brand_id": task.get("brand_id", "")}
                    if handler:
                        try:
                            result = handler(block.input, ctx)
                        except Exception as e:
                            result = {"ok": False, "error": str(e)}
                            log.exception(f"Tool {block.name} raised an error")
                    else:
                        result = {"ok": False, "error": f"未実装ツール: {block.name}"}

                    log_entries.append({
                        "tool":   block.name,
                        "input":  block.input,
                        "result": result,
                    })
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps(result, ensure_ascii=False),
                    })

                messages.append({"role": "user", "content": tool_results})
                continue

            # 想定外の stop_reason
            log.warning(f"Unexpected stop_reason: {response.stop_reason}")
            break

        else:
            log.warning(f"Task {task_id}: max_iterations({max_iterations}) に達しました")

        cost_usd = tokens_used * 0.000001  # 概算

        orchestrator.complete_task(
            task_id, run_id,
            output_data={"result": output_text, "log": log_entries},
            log_entries=log_entries,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
        )
        log.info(f"Task {task_id} 完了 tokens={tokens_used}")
        return {
            "task_id":     task_id,
            "run_id":      run_id,
            "status":      "completed",
            "output":      output_text,
            "tokens_used": tokens_used,
            "cost_usd":    cost_usd,
        }

    except Exception as e:
        log.exception(f"Task {task_id} 実行エラー")
        orchestrator.fail_task(task_id, run_id, str(e))
        return {
            "task_id": task_id,
            "run_id":  run_id,
            "status":  "failed",
            "error":   str(e),
        }


def run_next(limit: int = 5) -> list[dict]:
    """
    キュー内の実行可能タスクを最大 limit 件取り出して順に実行する。
    Returns: list of run results
    """
    from agents.task_service import get_runnable_tasks
    tasks = get_runnable_tasks(limit=limit)
    results = []
    for t in tasks:
        log.info(f"run_next: starting task {t['id']} — {t.get('title')}")
        result = run(t["id"])
        results.append(result)
    return results


# ════════════════════════════════════════════════════════════
# ユーティリティ
# ════════════════════════════════════════════════════════════

def _build_initial_message(task: dict, input_data: dict) -> str:
    parts = [f"タスク: {task.get('title', '')}"]
    if task.get("description"):
        parts.append(f"詳細: {task['description']}")
    if input_data:
        parts.append(f"入力データ: {json.dumps(input_data, ensure_ascii=False, indent=2)}")
    return "\n".join(parts)


def _load_lead_from_file(lead_id: str) -> dict | None:
    import yaml
    for f in LEADS_DIR.rglob("*.yaml"):
        try:
            with open(f, encoding="utf-8") as fh:
                d = yaml.safe_load(fh) or {}
            if d.get("id") == lead_id or f.stem == lead_id:
                return d
        except Exception:
            pass
    return None


def _find_lead_file(lead_id: str) -> Path | None:
    for f in LEADS_DIR.rglob("*.yaml"):
        try:
            import yaml
            with open(f, encoding="utf-8") as fh:
                d = yaml.safe_load(fh) or {}
            if d.get("id") == lead_id or f.stem == lead_id:
                return f
        except Exception:
            pass
    return None
