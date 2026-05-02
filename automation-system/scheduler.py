from __future__ import annotations

"""
メインスケジューラー
- 毎朝 Instagram・LINE に自動投稿
- フォローアップチェックを定期実行
- 毎週月曜6:00 に週次コンテンツカレンダーを自動生成
- 投稿翌日にインサイトを取得してパフォーマンスログに蓄積
- 最適投稿時間を動的に調整（データ蓄積後に有効）

起動方法:
  python scheduler.py

常時起動推奨（Mac の場合 launchd、サーバーの場合 systemd or pm2）
"""

import logging
import os
os.environ.setdefault("TZ", "Asia/Tokyo")
import time as _time
try:
    _time.tzset()  # type: ignore[attr-defined]
except AttributeError:
    pass  # Windowsでは不要
from pathlib import Path
from dotenv import load_dotenv

# load_dotenv() をモジュールインポートより先に呼ぶ
# → sns/google_drive.py 等のモジュールレベル変数が正しい env 値を受け取れる
load_dotenv(Path(__file__).parent / ".env")

import schedule
import subprocess
import time
import yaml
from datetime import datetime

from sns.instagram import InstagramPoster
from sns.line_api import LINEMessenger
try:
    from sns.google_drive import sync_from_drive
except Exception as _gdrive_import_err:
    logging.warning("Google Drive モジュール読み込みスキップ: %s", _gdrive_import_err)
    def sync_from_drive(*a, **kw): pass
from sns.performance import log_post, update_metrics, get_optimal_post_time
from sns.photo_importer import process_inbox
from sales.followup import run_followup_check
from morning_operator import run as morning_run

_ROOT = Path(__file__).parent
_FINANCE_TRACKER   = _ROOT.parent / "finance-system"           / "finance_tracker.py"
_HEALTH_CHECKER    = _ROOT.parent / "customer-success-system"  / "health_checker.py"
_PROJECT_DASHBOARD = _ROOT.parent / "project-system"           / "project_dashboard.py"
_LEAD_PIPELINE     = _ROOT.parent / "sales-system"             / "lead_pipeline.py"
_SHOP_SYNC         = _ROOT.parent / "shop-update-system"       / "sync_all_channels.py"
_CONTENT_PLANNER   = _ROOT.parent / "marketing-system"         / "content_planner.py"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "logs" / "scheduler.log",
            encoding="utf-8",
        ),
    ],
    force=True,  # morning_operator の import が basicConfig を先取りするのを上書き
)
logger = logging.getLogger(__name__)

SCHEDULE_CFG   = Path(__file__).parent / "config" / "schedule.yaml"
QUEUE_DIR      = Path(__file__).parent / "content_queue"
SCENARIOS_PATH = Path(__file__).parent / "config" / "line_scenarios.yaml"
PERF_LOG_PATH  = Path(__file__).parent / "logs" / "performance_log.yaml"
HEARTBEAT_FILE = Path(__file__).parent / "logs" / "scheduler.heartbeat"
ALERTS_LOG     = Path(__file__).parent / "logs" / "alerts.log"


def _alert_owner(message: str, dedup_key: str = "") -> None:
    """Mac通知 + alerts.log + LINE push の三重記録。dedup_key を指定すると初回のみ送信。"""
    if dedup_key:
        flag = Path(__file__).parent / "logs" / f".alert_{dedup_key}.sent"
        if flag.exists():
            return
        try:
            flag.touch()
        except Exception:
            pass

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(ALERTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] {message}\n")
    except Exception as exc:
        logger.error("alerts.log 書き込み失敗: %s", exc)

    try:
        safe_msg = message.replace('"', "'")[:100]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "scheduler alert"'],
            timeout=5, capture_output=True,
        )
    except Exception as exc:
        logger.error("Mac通知失敗: %s", exc)

    try:
        owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
        alert_token = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
        alert_secret = os.environ.get("ALERT_LINE_CHANNEL_SECRET", "")
        if owner_id and alert_token and alert_secret:
            from sns.line_api import LINEMessenger
            messenger = LINEMessenger(token=alert_token, secret=alert_secret)
            if messenger.enabled:
                messenger.push(owner_id, f"[scheduler alert]\n{message}")
    except Exception as exc:
        logger.error("LINE push失敗: %s", exc)

    logger.warning("ALERT: %s", message)


def _touch_heartbeat() -> None:
    """メインループが生きていることを記録する（毎分更新）。"""
    try:
        HEARTBEAT_FILE.write_text(datetime.now().isoformat())
    except Exception as exc:
        logger.error("heartbeat 書き込み失敗: %s", exc)


def _load_schedule() -> dict:
    with open(SCHEDULE_CFG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _next_queued_post(subdir: str = "instagram") -> dict | None:
    """
    content_queue/instagram/ から次の投稿ファイルを取得
    ファイル名は YYYY-MM-DD_HHmm_[タイトル].yaml で管理
    """
    q_dir = QUEUE_DIR / subdir
    if not q_dir.exists():
        return None
    files = sorted(q_dir.glob("*.yaml"))
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not data.get("posted"):
            return {"path": f, **data}
    return None


def _atomic_yaml_write(file_path: Path, data: dict) -> None:
    """YAML をアトミックに書き込む（temp → rename で途中クラッシュによる破損防止）"""
    tmp = file_path.with_suffix(".tmp")
    try:
        tmp.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        tmp.replace(file_path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _mark_posted(file_path: Path):
    """投稿済みフラグを立てる"""
    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["posted"] = True
    _atomic_yaml_write(file_path, data)


def _mark_status(file_path: Path, status: str):
    """投稿ステータスを更新（posted / failed）"""
    with open(file_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["posted"] = (status == "posted")
    data["status"] = status
    _atomic_yaml_write(file_path, data)


def post_to_instagram():
    """Instagram投稿ジョブ（投稿後にパフォーマンスログへ記録）"""
    logger.info("=== Instagram投稿ジョブ開始 ===")
    post = _next_queued_post("instagram")
    if not post:
        logger.info("Instagram: キューに投稿がありません")
        return

    brand = post.get("brand", "")
    poster = InstagramPoster(brand=brand)
    try:
        media_type = post.get("media_type", "image")
        if media_type == "reel":
            result = poster.post_reel(
                video_url=post["video_url"],
                caption=post["caption"],
                cover_url=post.get("cover_url", ""),
            )
        elif media_type == "carousel":
            result = poster.post_carousel(
                slides=post.get("slides", []),
                caption=post["caption"],
            )
        else:
            result = poster.post_image(
                image_url=post["image_url"],
                caption=post["caption"],
            )

        _mark_posted(post["path"])
        logger.info(f"Instagram投稿完了: {result}")

        # 投稿成功時にパフォーマンスログへ記録（メトリクスは翌日更新）
        media_id = result.get("media_id", "")
        if media_id and result.get("status") == "posted":
            log_post(
                brand=post.get("brand", "dsc-marketing"),
                platform="instagram",
                topic=post.get("topic", post.get("caption", "")[:30]),
                post_id=media_id,
                caption=post.get("caption", ""),
                posted_hour=int(time.strftime("%H")),
            )
            # 次回の投稿時間を最適化（翌日のスケジュールに反映）
            _reschedule_instagram_next_day()

    except Exception as e:
        logger.error(f"Instagram投稿エラー: {e}", exc_info=True)


def fetch_instagram_insights():
    """
    前日投稿のインサイトを取得してパフォーマンスログを更新するジョブ
    毎朝6:00に実行（投稿から24時間後のデータが最も安定）
    """
    logger.info("=== Instagramインサイト取得ジョブ開始 ===")
    if not PERF_LOG_PATH.exists():
        return

    try:
        data = yaml.safe_load(PERF_LOG_PATH.read_text(encoding="utf-8")) or []
    except Exception as exc:
        logger.error("performance_log.yaml 読み込み失敗: %s", exc)
        return

    updated = 0
    for entry in data:
        if entry.get("platform") != "instagram":
            continue
        if entry.get("metrics", {}).get("reach", 0) > 0:
            continue  # 既にメトリクスあり
        post_id = entry.get("post_id", "")
        if not post_id:
            continue
        try:
            poster = InstagramPoster(brand=entry.get("brand", ""))
            metrics = poster.get_insights_parsed(post_id)
            if metrics.get("reach", 0) > 0:
                update_metrics(post_id, metrics)
                updated += 1
        except Exception as e:
            logger.debug(f"インサイト取得スキップ ({post_id}): {e}")

    logger.info(f"インサイト更新完了: {updated}件")


def _reschedule_instagram_next_day():
    """
    パフォーマンスデータをもとに翌日のInstagram投稿時間を最適化する
    """
    try:
        brand    = os.environ.get("DEFAULT_BRAND", "dsc-marketing")
        opt_time = get_optimal_post_time(brand, "instagram")
        logger.info(f"次回Instagram最適投稿時間: {opt_time}（{brand}）")
        # schedule ライブラリの動的変更は再起動が必要なため、
        # schedule.yaml に書き出してオペレーターに通知
        schedule_path = Path(__file__).parent / "config" / "schedule.yaml"
        if schedule_path.exists():
            cfg = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
            current_times = cfg.get("instagram", {}).get("post_times", ["12:00"])
            if opt_time not in current_times:
                cfg.setdefault("instagram", {})["suggested_optimal_time"] = opt_time
                schedule_path.write_text(
                    yaml.dump(cfg, allow_unicode=True, default_flow_style=False, sort_keys=False),
                    encoding="utf-8",
                )
                logger.info(f"schedule.yaml に最適時間を記録: {opt_time}")
    except Exception as e:
        logger.debug(f"最適時間の更新スキップ: {e}")


def broadcast_line():
    """LINE一斉配信ジョブ"""
    logger.info("=== LINE一斉配信ジョブ開始 ===")
    post = _next_queued_post("line")
    if not post:
        logger.info("LINE: キューに配信がありません")
        return

    messenger = LINEMessenger()
    try:
        image_url = post.get("image_url", "")
        if image_url:
            ok = messenger.broadcast_with_image(
                message=post["message"],
                image_url=image_url,
                preview_url=post.get("preview_url", image_url),
            )
        else:
            ok = messenger.broadcast(post["message"])

        if ok:
            _mark_posted(post["path"])
            logger.info("LINE一斉配信完了")
    except Exception as e:
        logger.error(f"LINE配信エラー: {e}", exc_info=True)


def check_scheduled_posts():
    """
    予約投稿チェックジョブ（毎分実行）
    scheduled_at が設定されていて現在時刻を過ぎた投稿を自動実行する
    """
    now = datetime.now()
    brands_cfg = Path(__file__).parent / "config" / "brands.yaml"
    try:
        brands = yaml.safe_load(brands_cfg.read_text(encoding="utf-8")).get("brands", {})
    except Exception as exc:
        logger.error("brands.yaml 読み込み失敗: %s", exc)
        return

    # 各プラットフォームの初期化は使用時に行う（未使用プラットフォームでのキー不在エラーを防ぐ）
    _posters: dict[str, InstagramPoster] = {}
    _messenger: LINEMessenger | None = None

    def get_poster(brand_key: str = ""):
        if brand_key not in _posters:
            try:
                _posters[brand_key] = InstagramPoster(brand=brand_key)
            except KeyError as exc:
                _alert_owner(
                    f"Instagram認証情報未設定 ({exc}) [{brand_key}] — 予約投稿をスキップ",
                    dedup_key=f"instagram_key_missing_{brand_key}",
                )
                return None
        return _posters[brand_key]

    def get_messenger():
        nonlocal _messenger
        if _messenger is None:
            try:
                _messenger = LINEMessenger()
            except KeyError as exc:
                _alert_owner(
                    f"LINE認証情報未設定 ({exc}) — LINE配信をスキップ",
                    dedup_key="line_key_missing",
                )
                return None
        return _messenger

    # 全ブランド × 全プラットフォームをスキャン
    for brand_key in brands:
        brand_queue = QUEUE_DIR / brand_key
        if not brand_queue.exists():
            continue
        for platform_dir in brand_queue.iterdir():
            if not platform_dir.is_dir():
                continue
            platform = platform_dir.name
            for f in sorted(platform_dir.glob("*.yaml")):
                try:
                    with open(f, encoding="utf-8") as fh:
                        data = yaml.safe_load(fh)
                    if not data or data.get("posted") or data.get("status") == "failed":
                        continue
                    sched_str = data.get("scheduled_at")
                    if not sched_str:
                        continue
                    sched_dt = datetime.strptime(str(sched_str), "%Y-%m-%d %H:%M")
                    if sched_dt > now:
                        continue  # まだ予約時刻前

                    logger.info(f"予約投稿実行: {brand_key}/{platform}/{f.name} (予約:{sched_str})")

                    if platform == "instagram":
                        poster = get_poster(brand_key)
                        if poster is None:
                            continue  # 認証情報未設定 — アラート送信済み
                        mt = data.get("media_type", "image")
                        if mt == "reel":
                            poster.post_reel(video_url=data.get("video_url",""), caption=data.get("caption",""), cover_url=data.get("cover_url",""))
                        elif mt == "carousel":
                            poster.post_carousel(slides=data.get("slides",[]), caption=data.get("caption",""))
                        else:
                            poster.post_image(image_url=data.get("image_url",""), caption=data.get("caption",""))
                        _mark_posted(f)
                        logger.info(f"予約Instagram投稿完了: {f.name}")

                    elif platform == "line":
                        messenger = get_messenger()
                        if messenger is None:
                            continue  # 認証情報未設定 — アラート送信済み
                        image_url = data.get("image_url","")
                        if image_url:
                            messenger.broadcast_with_image(message=data.get("message",""), image_url=image_url, preview_url=data.get("preview_url", image_url))
                        else:
                            messenger.broadcast(data.get("message",""))
                        _mark_posted(f)
                        logger.info(f"予約LINE配信完了: {f.name}")

                    elif platform == "twitter":
                        from sns.twitter import TwitterPoster
                        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
                        tw = TwitterPoster(brand=brand_key)
                        content = data.get("content", "").strip()
                        hashtags = " ".join(data.get("hashtags", []))
                        full_text = f"{content}\n\n{hashtags}".strip()
                        if dry_run:
                            logger.info(f"[DRY_RUN] Twitter投稿: {full_text[:80]}...")
                            logger.info(f"予約Twitter投稿完了（DRY_RUN）: {f.name}")
                        else:
                            try:
                                result = tw.tweet(full_text)
                                if result.get("status") == "posted":
                                    _mark_status(f, "posted")
                                    logger.info(f"予約Twitter投稿完了: {f.name}")
                                else:
                                    _mark_status(f, "failed")
                                    logger.error(f"Twitter投稿失敗（応答異常）: {f.name} / result={result}")
                            except Exception as e:
                                _mark_status(f, "failed")
                                logger.error(f"Twitter投稿失敗（例外）: {f.name} / error={e}")

                except Exception as e:
                    logger.error(f"予約投稿エラー ({f.name}): {e}", exc_info=True)


def followup_job():
    """フォローアップ送信ジョブ"""
    logger.info("=== フォローアップチェック開始 ===")
    try:
        run_followup_check()
    except Exception as e:
        logger.error(f"フォローアップエラー: {e}", exc_info=True)
        _alert_owner(f"ジョブ失敗: {e}", dedup_key="job_fail")


def agent_tick_job():
    """エージェントタスク実行ジョブ（5分ごと）"""
    logger.info("=== エージェントタスク実行開始 ===")
    try:
        from agents.orchestrator import tick
        summary = tick(execute=True)
        logger.info(f"エージェントtick完了: {summary}")
    except Exception as e:
        logger.error(f"エージェントtickエラー: {e}", exc_info=True)
        _alert_owner(f"ジョブ失敗: {e}", dedup_key="job_fail")


def balance_check_job():
    """
    Anthropic クレジット残高チェック（毎朝9:00 JST）。
    推計残高が ANTHROPIC_CREDIT_WARN_USD（デフォルト$10）を下回ったら LINE 通知。
    ANTHROPIC_CREDIT_TOTAL_USD が未設定の場合はスキップ（ログのみ）。
    """
    logger.info("=== Anthropic 残高チェック開始 ===")
    try:
        from api_cost_tracker import check_balance_and_alert, get_balance_info
        info = get_balance_info()
        if not info["tracking_enabled"]:
            logger.info(
                "残高チェックスキップ: .env に ANTHROPIC_CREDIT_TOTAL_USD=<金額> を設定してください"
            )
            return
        result = check_balance_and_alert()
        bi = result["balance_info"]
        logger.info(
            "残高チェック完了: $%.2f 残 (状態=%s, 通知=%s)",
            bi.get("estimated_balance", 0),
            bi.get("status", "unknown"),
            result.get("alerted", False),
        )
    except Exception as e:
        logger.error("残高チェックエラー: %s", e, exc_info=True)


def ceo_dispatch_job():
    """AI CEO ディスパッチジョブ（毎朝5:30）"""
    logger.info("=== AI CEO ディスパッチ開始 ===")
    try:
        from agents.ceo_executor import run_ceo_dispatch
        result = run_ceo_dispatch()
        logger.info(f"AI CEO ディスパッチ完了: {result.get('tasks_created', 0)}タスク作成")
        logger.info(f"サマリー: {result.get('summary', '')}")
    except Exception as e:
        logger.error(f"AI CEO ディスパッチエラー: {e}", exc_info=True)
        _alert_owner(f"ジョブ失敗: {e}", dedup_key="job_fail")


BLOG_BRANDS = ["satoshi-blog", "upjapan", "dsc-marketing", "cashflowsupport", "bangkok-peach"]


def blog_auto_post_job():
    """全ブランドのブログ記事をAI生成して即公開（1日3回実行）"""
    logger.info("=== ブログ自動投稿開始 ===")
    from dashboard.ai import generate_blog_post_auto
    from sns.wordpress import WordPressPoster
    import database as db

    for brand in BLOG_BRANDS:
        try:
            result = generate_blog_post_auto(brand=brand)
            wp = WordPressPoster(brand=brand)
            wp_result = wp.create_post(
                title=result["title"],
                content=result["content_html"],
                status="publish",
            )
            logger.info(f"ブログ公開: [{brand}] {result['title']} → {wp_result.get('url','')}")
            db.log_activity(
                "blog_post", brand=brand, platform="wordpress",
                detail=f"{result['title']} ({result.get('estimated_read_time',0)}分読)",
            )
        except Exception as e:
            logger.error(f"ブログ投稿エラー [{brand}]: {e}", exc_info=True)
            _alert_owner(f"ブログ投稿失敗 [{brand}]: {e}", dedup_key=f"blog_fail_{brand}")

    logger.info("=== ブログ自動投稿完了 ===")


def generate_weekly_calendar_job():
    """
    週次コンテンツカレンダー自動生成ジョブ（毎週月曜6:00）
    翌週1週間分のコンテンツ計画をAIが生成してYAMLに保存する
    """
    logger.info("=== 週次コンテンツカレンダー生成開始 ===")
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "dashboard"))
        from ai import generate_weekly_calendar, save_weekly_calendar

        brands_cfg = Path(__file__).parent / "config" / "brands.yaml"
        brands = yaml.safe_load(brands_cfg.read_text(encoding="utf-8")).get("brands", {})

        for brand_key in brands:
            try:
                logger.info(f"カレンダー生成中: {brand_key}")
                calendar = generate_weekly_calendar(brand=brand_key)
                saved_path = save_weekly_calendar(calendar, brand=brand_key)
                logger.info(f"カレンダー保存完了: {saved_path}")
            except Exception as e:
                logger.error(f"カレンダー生成エラー ({brand_key}): {e}", exc_info=True)

    except Exception as e:
        logger.error(f"週次カレンダー生成エラー: {e}", exc_info=True)

    logger.info("=== 週次コンテンツカレンダー生成完了 ===")


def _jst_to_utc(jst_time: str) -> str:
    """HH:MM JST を HH:MM UTC に変換（Railway サーバーが UTC のため）"""
    parts = jst_time.split(":")
    if len(parts) < 2:
        return "00:00"
    h, m = int(parts[0]), int(parts[1])
    h = (h - 9) % 24
    return f"{h:02d}:{m:02d}"

def _jst_weekday_to_utc(day: str, jst_time: str):
    """JST の曜日+時刻を UTC の曜日+時刻に変換（UTCは9時間前なので曜日がずれる場合あり）"""
    parts = jst_time.split(":")
    if len(parts) < 2:
        return "00:00"
    h, m = int(parts[0]), int(parts[1])
    utc_h = h - 9
    if utc_h < 0:
        utc_h += 24
        days_order = ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]
        idx = days_order.index(day)
        day = days_order[(idx - 1) % 7]
    return day, f"{utc_h:02d}:{m:02d}"


def setup_schedule():
    cfg = _load_schedule()

    # Instagram（最適投稿時間を反映）- YAML時刻はJSTなのでUTCに変換
    if cfg.get("instagram", {}).get("enabled"):
        post_times = cfg["instagram"].get("post_times", ["12:00"])
        suggested = cfg["instagram"].get("suggested_optimal_time")
        if suggested and suggested not in post_times:
            post_times = [suggested]
            logger.info(f"最適投稿時間を採用: {suggested}")
        for t in post_times:
            utc_t = _jst_to_utc(t)
            schedule.every().day.at(utc_t).do(post_to_instagram)
            logger.info(f"Instagram投稿スケジュール設定: 毎日 {t} JST ({utc_t} UTC)")

    # LINE一斉配信 - YAML時刻はJSTなのでUTCに変換
    line_cfg = cfg.get("line_broadcast", {})
    if line_cfg.get("enabled"):
        weekday_map = {
            "monday":    schedule.every().monday,
            "tuesday":   schedule.every().tuesday,
            "wednesday": schedule.every().wednesday,
            "thursday":  schedule.every().thursday,
            "friday":    schedule.every().friday,
            "saturday":  schedule.every().saturday,
            "sunday":    schedule.every().sunday,
        }
        t = line_cfg.get("time", "10:00")
        for day in line_cfg.get("weekdays", ["monday"]):
            utc_day, utc_t = _jst_weekday_to_utc(day, t)
            weekday_map[utc_day].at(utc_t).do(broadcast_line)
            logger.info(f"LINE配信スケジュール設定: 毎週{day} {t} JST ({utc_day} {utc_t} UTC)")

    # フォローアップ（2時間ごとにチェック）
    schedule.every(2).hours.do(followup_job)
    logger.info("フォローアップチェック: 2時間ごと")

    # Googleドライブ同期（1時間ごと: ナノバナナプロで書き出した素材を自動取得）
    def _safe_sync_from_drive():
        try:
            sync_from_drive()
        except Exception as exc:
            logger.error("Google Drive同期エラー（schedulerは継続）: %s", exc)
    schedule.every(1).hours.do(_safe_sync_from_drive)
    logger.info("Google Drive同期: 1時間ごと")

    # 朝のオペレーター（毎朝5:00 JST = 20:00 UTC前日）
    schedule.every().day.at("20:00").do(morning_run)
    logger.info("朝のオペレーター: 毎朝5:00 JST (20:00 UTC)")

    # インサイト取得（毎朝6:00 JST = 21:00 UTC前日）
    schedule.every().day.at("21:00").do(fetch_instagram_insights)
    logger.info("インサイト取得: 毎朝6:00 JST (21:00 UTC)")

    # 週次コンテンツカレンダー生成（毎週月曜6:30 JST = 日曜21:30 UTC）
    schedule.every().sunday.at("21:30").do(generate_weekly_calendar_job)
    logger.info("週次カレンダー生成: 毎週月曜6:30 JST (日曜21:30 UTC)")

    # 写真インボックスチェック（1時間ごと）
    # media/inbox/{brand}/ に写真を入れると自動でキューに追加される
    schedule.every(1).hours.do(lambda: process_inbox())
    logger.info("写真インボックスチェック: 1時間ごと")

    # 予約投稿チェック（1分ごと: scheduled_at が設定された投稿を時刻通りに実行）
    schedule.every(1).minutes.do(check_scheduled_posts)
    logger.info("予約投稿チェック: 1分ごと")

    # Story Autopilot（5分ごとにテンプレートの実行時刻をチェック）
    schedule.every(5).minutes.do(story_autopilot_job)
    logger.info("Story Autopilot: 5分ごと")

    # エージェントタスク実行（5分ごと: キュー内タスクを自動実行）
    schedule.every(5).minutes.do(agent_tick_job)
    logger.info("エージェントタスク実行: 5分ごと")

    # Anthropic 残高チェック（毎朝9:00 JST = 00:00 UTC）
    schedule.every().day.at("00:00").do(balance_check_job)
    logger.info("Anthropic残高チェック: 毎朝9:00 JST (00:00 UTC)")

    # AI CEO ディスパッチ（毎朝5:30 JST = 20:30 UTC前日）
    schedule.every().day.at("20:30").do(ceo_dispatch_job)
    logger.info("AI CEO ディスパッチ: 毎朝5:30 JST (20:30 UTC)")

    # ブログ自動投稿 JST→UTC変換: 08:30→23:30, 12:30→03:30, 18:00→09:00
    schedule.every().day.at("23:30").do(blog_auto_post_job)
    schedule.every().day.at("03:30").do(blog_auto_post_job)
    schedule.every().day.at("09:00").do(blog_auto_post_job)
    logger.info("ブログ自動投稿: 08:30/12:30/18:00 JST (23:30/03:30/09:00 UTC)")

    # 死活監視 heartbeat（毎分: logs/scheduler.heartbeat を更新）
    schedule.every(1).minutes.do(_touch_heartbeat)
    logger.info("heartbeat: 毎分更新")

    # 動画パイプライン（毎日 20:00 JST = 11:00 UTC）
    schedule.every().day.at("11:00").do(video_pipeline_job)
    logger.info("動画パイプライン: 毎日20:00 JST (11:00 UTC)")

    # 財務月次レポート（毎月1日 09:00 JST = 00:00 UTC）
    schedule.every().day.at("00:00").do(finance_monthly_job)
    logger.info("財務月次レポート: 毎日チェック（1日のみ実行）")

    # CSヘルスチェック（毎週月曜 09:00 JST = 日曜 00:00 UTC）
    schedule.every().sunday.at("00:00").do(cs_health_check_job)
    logger.info("CSヘルスチェック: 毎週月曜09:00 JST (日曜00:00 UTC)")

    # プロジェクトダッシュボード（毎週月曜 09:30 JST = 日曜 00:30 UTC）
    schedule.every().sunday.at("00:30").do(project_dashboard_job)
    logger.info("プロジェクトダッシュボード: 毎週月曜09:30 JST (日曜00:30 UTC)")

    # 営業パイプラインチェック（毎週月曜 08:00 JST = 日曜 23:00 UTC）
    schedule.every().sunday.at("23:00").do(lead_pipeline_job)
    logger.info("営業パイプラインチェック: 毎週月曜08:00 JST (日曜23:00 UTC)")

    # 店舗チャネル同期（毎週月曜 07:30 JST = 日曜 22:30 UTC）
    schedule.every().sunday.at("22:30").do(shop_sync_job)
    logger.info("店舗チャネル同期: 毎週月曜07:30 JST (日曜22:30 UTC)")

    # コンテンツプランナー（毎週日曜 20:00 JST = 11:00 UTC: 翌週分を生成）
    schedule.every().sunday.at("11:00").do(content_planner_job)
    logger.info("コンテンツプランナー: 毎週日曜20:00 JST (11:00 UTC)")


def video_pipeline_job():
    """毎日20:00 JST: satoshi-blog の最新記事を取得して動画生成 → 投稿キューへ追加"""
    logger.info("=== 動画パイプラインジョブ開始 ===")
    try:
        import sys
        if str(_ROOT) not in sys.path:
            sys.path.insert(0, str(_ROOT))
        from video.blog_fetcher import BlogFetcher
        from video.pipeline import run_pipeline

        fetcher = BlogFetcher(brand="satoshi-blog")
        latest = fetcher.fetch_latest()
        if not latest:
            logger.warning("最新記事が取得できませんでした — 動画パイプラインをスキップ")
            return

        run_pipeline(
            blog_text=latest["content"],
            blog_title=latest["title"],
            brand="satoshi-blog",
            channel="satoshi",
            post=True,
            dry_run=False,
        )
        logger.info("動画パイプラインジョブ完了: %s", latest.get("title", ""))
    except Exception as exc:
        _alert_owner(f"動画パイプラインジョブエラー: {exc}", dedup_key="video_pipeline_error")
        logger.error("動画パイプラインジョブエラー: %s", exc, exc_info=True)


def finance_monthly_job():
    """毎月1日09:00 JST: 月次財務レポートを生成してLINEに送信する"""
    if datetime.now().day != 1:
        return
    logger.info("=== 財務月次レポートジョブ開始 ===")
    try:
        if _FINANCE_TRACKER.exists():
            result = subprocess.run(
                ["python3", str(_FINANCE_TRACKER), "--report"],
                capture_output=True, text=True, timeout=120,
            )
            logger.info("財務レポート出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("財務レポートエラー: %s", result.stderr)
        else:
            logger.warning("finance_tracker.py が見つかりません: %s", _FINANCE_TRACKER)
    except Exception as exc:
        _alert_owner(f"財務月次レポートエラー: {exc}", dedup_key="finance_report_error")
        logger.error("財務月次レポートエラー: %s", exc, exc_info=True)


def cs_health_check_job():
    """毎週月曜09:00 JST: 顧客ヘルスチェックを実行してLINEにアラートを送信する"""
    logger.info("=== CSヘルスチェックジョブ開始 ===")
    try:
        if _HEALTH_CHECKER.exists():
            result = subprocess.run(
                ["python3", str(_HEALTH_CHECKER)],
                capture_output=True, text=True, timeout=60,
            )
            logger.info("CSヘルスチェック出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("CSヘルスチェックエラー: %s", result.stderr)
        else:
            logger.warning("health_checker.py が見つかりません: %s", _HEALTH_CHECKER)
    except Exception as exc:
        _alert_owner(f"CSヘルスチェックエラー: {exc}", dedup_key="cs_health_error")
        logger.error("CSヘルスチェックエラー: %s", exc, exc_info=True)


def project_dashboard_job():
    """毎週月曜09:30 JST: プロジェクトダッシュボードを生成してLINEに送信する"""
    logger.info("=== プロジェクトダッシュボードジョブ開始 ===")
    try:
        if _PROJECT_DASHBOARD.exists():
            result = subprocess.run(
                ["python3", str(_PROJECT_DASHBOARD)],
                capture_output=True, text=True, timeout=60,
            )
            logger.info("プロジェクトダッシュボード出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("プロジェクトダッシュボードエラー: %s", result.stderr)
        else:
            logger.warning("project_dashboard.py が見つかりません: %s", _PROJECT_DASHBOARD)
    except Exception as exc:
        _alert_owner(f"プロジェクトダッシュボードエラー: {exc}", dedup_key="project_dashboard_error")
        logger.error("プロジェクトダッシュボードエラー: %s", exc, exc_info=True)


def lead_pipeline_job():
    """毎週月曜08:00 JST: 営業パイプラインをチェックして期限超過リードをLINEで通知する"""
    logger.info("=== 営業パイプラインジョブ開始 ===")
    try:
        if _LEAD_PIPELINE.exists():
            result = subprocess.run(
                ["python3", str(_LEAD_PIPELINE)],
                capture_output=True, text=True, timeout=60,
            )
            logger.info("営業パイプライン出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("営業パイプラインエラー: %s", result.stderr)
        else:
            logger.warning("lead_pipeline.py が見つかりません: %s", _LEAD_PIPELINE)
    except Exception as exc:
        _alert_owner(f"営業パイプラインエラー: {exc}", dedup_key="lead_pipeline_error")
        logger.error("営業パイプラインエラー: %s", exc, exc_info=True)


def shop_sync_job():
    """毎週月曜07:30 JST: 全店舗のチャネル情報を同期する"""
    logger.info("=== 店舗チャネル同期ジョブ開始 ===")
    try:
        if _SHOP_SYNC.exists():
            result = subprocess.run(
                ["python3", str(_SHOP_SYNC)],
                capture_output=True, text=True, timeout=120,
            )
            logger.info("店舗チャネル同期出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("店舗チャネル同期エラー: %s", result.stderr)
        else:
            logger.warning("sync_all_channels.py が見つかりません: %s", _SHOP_SYNC)
    except Exception as exc:
        _alert_owner(f"店舗チャネル同期エラー: {exc}", dedup_key="shop_sync_error")
        logger.error("店舗チャネル同期エラー: %s", exc, exc_info=True)


def content_planner_job():
    """毎週日曜20:00 JST: 翌週分のコンテンツプランをキューに生成する"""
    logger.info("=== コンテンツプランナージョブ開始 ===")
    try:
        if _CONTENT_PLANNER.exists():
            result = subprocess.run(
                ["python3", str(_CONTENT_PLANNER)],
                capture_output=True, text=True, timeout=60,
            )
            logger.info("コンテンツプランナー出力:\n%s", result.stdout)
            if result.returncode != 0:
                logger.error("コンテンツプランナーエラー: %s", result.stderr)
        else:
            logger.warning("content_planner.py が見つかりません: %s", _CONTENT_PLANNER)
    except Exception as exc:
        _alert_owner(f"コンテンツプランナーエラー: {exc}", dedup_key="content_planner_error")
        logger.error("コンテンツプランナーエラー: %s", exc, exc_info=True)


def story_autopilot_job():
    """
    Story Autopilot: アクティブなテンプレートの run_time と active_days を確認し、
    今日・今の時刻に一致するテンプレートを自動実行する。
    """
    now = datetime.now()
    weekday_idx = now.weekday()  # 0=月, 6=日
    current_time = now.strftime("%H:%M")

    try:
        sys_path = str(Path(__file__).parent / "dashboard")
        if sys_path not in __import__("sys").path:
            __import__("sys").path.insert(0, sys_path)

        from repositories.story_repo import StoryTemplateRepo, StoryRunRepo, SocialAccountRepo
        from connectors.meta_connector import get_meta_connector
        import json

        tmpl_repo = StoryTemplateRepo()
        run_repo  = StoryRunRepo()
        acct_repo = SocialAccountRepo()

        templates = tmpl_repo.list()
        triggered = 0

        for tmpl in templates:
            if not tmpl.get("is_active"):
                continue

            # 実行時刻チェック（HH:MM が一致、±2分の許容）
            run_time = tmpl.get("run_time", "09:00")
            try:
                th, tm = map(int, run_time.split(":"))
                nh, nm = now.hour, now.minute
                diff_min = abs((nh * 60 + nm) - (th * 60 + tm))
                if diff_min > 2:
                    continue
            except Exception:
                continue

            # 曜日チェック
            active_days = tmpl.get("active_days")
            if active_days:
                try:
                    days = json.loads(active_days) if isinstance(active_days, str) else active_days
                    if weekday_idx not in days:
                        continue
                except Exception:
                    pass

            # 本日すでに実行済みかチェック
            today_str = now.strftime("%Y-%m-%d")
            existing = run_repo.list(template_id=tmpl["id"])
            if any(r.get("created_at", "")[:10] == today_str for r in existing):
                logger.info(f"Story Autopilot: tmpl={tmpl['id']} は本日実行済みのためスキップ")
                continue

            # 実行
            logger.info(f"Story Autopilot: テンプレート '{tmpl['name']}' を自動実行")
            try:
                acct = next(
                    (a for a in acct_repo.list() if a.get("brand") == tmpl["brand"] and a.get("platform") == "instagram"),
                    None
                )
                run_id = run_repo.create({
                    "template_id":     tmpl["id"],
                    "brand":           tmpl["brand"],
                    "run_mode":        tmpl.get("run_mode", "semi_auto"),
                    "status":          "generating",
                    "social_account_id": acct["id"] if acct else None,
                    "caption":         f"Story Autopilot — {tmpl['name']}",
                    "frames_json":     json.dumps([
                        {"type": "cover", "text": tmpl["name"], "bg": "#6366f1"},
                        {"type": "content", "text": tmpl.get("topic_prompt", ""), "bg": "#111118"},
                        {"type": "cta", "text": "詳しくはプロフィールへ", "bg": "#10b981"},
                    ]),
                })

                run_mode = tmpl.get("run_mode", "semi_auto")
                if run_mode == "full_auto":
                    # 即座に公開
                    run = run_repo.get(run_id)
                    connector = get_meta_connector("auto")
                    ig_uid = acct["ig_user_id"] if acct else tmpl["brand"]
                    result = connector.publish_story(ig_uid, media_url="https://placehold.co/1080x1920/png")
                    if result.get("error"):
                        run_repo.update_status(run_id, "failed", error_message=result["error"])
                    else:
                        run_repo.update_status(
                            run_id, "published",
                            ig_media_id=result.get("ig_media_id", ""),
                            ig_permalink=result.get("permalink", ""),
                        )
                        tmpl_repo.touch_last_run(tmpl["id"])
                    logger.info(f"Story full_auto 公開完了: run_id={run_id}")
                else:
                    # semi_auto / human_approval_required → 承認待ちにする
                    run_repo.update_status(run_id, "pending_approval")
                    tmpl_repo.touch_last_run(tmpl["id"])
                    logger.info(f"Story semi_auto 承認待ち: run_id={run_id}")

                triggered += 1

            except Exception as e:
                logger.error(f"Story Autopilot 実行エラー (tmpl={tmpl['id']}): {e}", exc_info=True)

        if triggered:
            logger.info(f"Story Autopilot: {triggered}件のテンプレートを実行しました")

    except Exception as e:
        logger.error(f"Story Autopilot ジョブエラー: {e}", exc_info=True)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="UPJ スケジューラー")
    parser.add_argument("--test-once", action="store_true",
                        help="予約投稿チェックを1回だけ実行して終了（DRY_RUN=true 推奨）")
    args = parser.parse_args()

    logger.info("スケジューラー起動")
    (Path(__file__).parent / "logs").mkdir(exist_ok=True)

    # DB初期化・エージェントシード（冪等）
    try:
        import setup_from_config
        setup_from_config.run()
        logger.info("DB初期化・エージェントシード完了")
    except Exception as _e:
        logger.warning(f"DB初期化スキップ: {_e}")

    if args.test_once:
        logger.info("=== --test-once モード: 予約投稿チェックを1回実行 ===")
        check_scheduled_posts()
        logger.info("=== --test-once 完了 ===")
    else:
        setup_schedule()
        _touch_heartbeat()  # 起動時に即書き込み
        # 起動直後に1回実行
        followup_job()
        while True:
            try:
                schedule.run_pending()
            except Exception as exc:
                _alert_owner(f"scheduler ループエラー: {exc}", dedup_key="loop_error")
                logger.error("メインループエラー: %s", exc, exc_info=True)
            time.sleep(60)
