"""
朝のオペレーター（Morning Operator）
毎朝5:00に実行し、以下を全自動で処理する:

1. content_queue/ のInstagram・LINE投稿を実行
2. フォローアップメッセージを送信
3. 処理できなかった「重要な判断待ち」をLINEで通知する
4. 当日の作業サマリーをLINEに送る

「重要な決定だけ自分がする」ための仕組み:
- 自動処理できたもの → そのまま完了
- 判断が必要なもの  → decision_queue/ に溜めて朝に通知
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv


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

import database as db
from sns.instagram import InstagramPoster
from sns.line_api import LINEMessenger
from sales.followup import run_followup_check

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            Path(__file__).parent / "logs" / "morning.log",
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger(__name__)

QUEUE_DIR = Path(__file__).parent / "content_queue"
DECISION_QUEUE_DIR = Path(__file__).parent / "decision_queue"
LEADS_DIR = Path(__file__).parent.parent / "sales-system" / "leads"

# 朝のサマリー送信先（オーナー本人のLINE user_id）
OWNER_LINE_USER_ID = os.environ.get("OWNER_LINE_USER_ID", "")


def _count_pending_leads() -> int:
    """未対応のリード数を数える（DB版）"""
    try:
        leads = db.list_leads(stage="", outcome="active", limit=500)
        return sum(1 for l in leads if l.get("stage") in ("L1", "L2"))
    except Exception:
        # DBが使えない場合はYAMLにフォールバック
        if not LEADS_DIR.exists():
            return 0
        count = 0
        for f in LEADS_DIR.glob("*.yaml"):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and data.get("stage") in ("L1", "L2") and not data.get("outcome"):
                    count += 1
            except Exception:
                pass
        return count


def _load_decision_queue() -> list[dict]:
    """判断待ちキューを読み込む（DB版）"""
    try:
        return db.list_decisions(resolved=False)
    except Exception:
        # DBが使えない場合はYAMLにフォールバック
        DECISION_QUEUE_DIR.mkdir(exist_ok=True)
        items = []
        for f in sorted(DECISION_QUEUE_DIR.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and not data.get("resolved"):
                    items.append({**data, "_file": f})
            except Exception:
                pass
        return items


def _post_from_db(brand: str, channel: str, poster_fn) -> int:
    """DBから次の投稿候補を取得して投稿し、mark_posted を呼ぶ汎用ヘルパー"""
    item = db.next_pending(brand, channel)
    if not item:
        return 0
    try:
        poster_fn(item)
        db.mark_posted(item["id"])
        db.log_activity("post", brand=brand, platform=channel,
                        detail=f"投稿完了: {(item.get('caption') or item.get('message',''))[:40]}")
        logger.info(f"{channel}投稿完了 (DB id={item['id']}): {brand}")
        return 1
    except Exception as e:
        logger.error(f"{channel}投稿エラー (DB id={item['id']}): {e}", exc_info=True)
        _add_to_decision_queue(f"{channel}_post_error", str(e),
                               {"db_id": item["id"], "brand": brand})
        return 0


def post_instagram_queue() -> int:
    """Instagram投稿キューを処理（DB優先、YAML後方互換）"""
    posted_total = 0

    # DBから各ブランドの投稿を処理
    try:
        brands_cfg = yaml.safe_load(
            (Path(__file__).parent / "config" / "brands.yaml").read_text(encoding="utf-8") or ""
        ).get("brands", {}) or {}
    except Exception:
        brands_cfg = {}

    poster = InstagramPoster()

    for brand_id, bcfg in brands_cfg.items():
        if not bcfg.get("channels", {}).get("instagram"):
            continue
        item = db.next_pending(brand_id, "instagram")
        if item:
            try:
                media_type = item.get("media_type", "image")
                if media_type == "reel":
                    poster.post_reel(
                        video_url=item["video_url"],
                        caption=item.get("caption",""),
                        cover_url=item.get("cover_url",""),
                        brand=brand_id,
                    )
                else:
                    poster.post_image(
                        image_url=item.get("image_url",""),
                        caption=item.get("caption",""),
                        brand=brand_id,
                    )
                db.mark_posted(item["id"])
                db.log_activity("post", brand=brand_id, platform="instagram",
                                detail=f"投稿完了: {item.get('caption','')[:40]}")
                posted_total += 1
                logger.info(f"Instagram投稿完了: {brand_id} (DB id={item['id']})")
                break  # 1バッチ1投稿
            except Exception as e:
                logger.error(f"Instagram投稿エラー {brand_id}: {e}", exc_info=True)
                _add_to_decision_queue("instagram_post_error", str(e),
                                       {"db_id": item["id"], "brand": brand_id})

    # DBに何もなければ旧YAMLキューを処理（後方互換）
    if posted_total == 0:
        q_dir = QUEUE_DIR / "instagram"
        if q_dir.exists():
            now = datetime.now()
            for f in sorted(q_dir.glob("*.yaml")):
                try:
                    data = yaml.safe_load(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if data.get("posted"):
                    continue
                sched = data.get("scheduled_at")
                if sched:
                    try:
                        if datetime.strptime(str(sched), "%Y-%m-%d %H:%M") > now:
                            continue
                    except ValueError:
                        pass
                try:
                    media_type = data.get("media_type", "image")
                    if media_type == "reel":
                        poster.post_reel(video_url=data.get("video_url",""),
                                         caption=data.get("caption",""),
                                         cover_url=data.get("cover_url",""))
                    else:
                        poster.post_image(image_url=data.get("image_url",""),
                                          caption=data.get("caption",""))
                    data["posted"] = True
                    _atomic_yaml_write(f, data)
                    posted_total += 1
                    logger.info(f"Instagram投稿完了(YAML): {f.name}")
                    break
                except Exception as e:
                    logger.error(f"Instagram投稿エラー ({f.name}): {e}", exc_info=True)
                    _add_to_decision_queue("instagram_post_error", str(e), {"file": f.name})

    return posted_total


def post_line_queue() -> int:
    """LINE配信キューを処理（DB優先、YAML後方互換）"""
    messenger = LINEMessenger()
    sent_total = 0

    # LINE配信曜日チェック
    schedule_path = Path(__file__).parent / "config" / "schedule.yaml"
    if schedule_path.exists():
        try:
            sched_cfg = yaml.safe_load(schedule_path.read_text(encoding="utf-8"))
            weekdays = sched_cfg.get("line_broadcast", {}).get("weekdays", [])
            today = datetime.now().strftime("%A").lower()
            if weekdays and today not in weekdays:
                logger.info(f"LINE配信: 今日（{today}）は配信曜日ではないためスキップ")
                return 0
        except Exception:
            pass

    # DBから取得
    try:
        brands_cfg = yaml.safe_load(
            (Path(__file__).parent / "config" / "brands.yaml").read_text(encoding="utf-8")
        ).get("brands", {})
    except Exception:
        brands_cfg = {}

    for brand_id, bcfg in brands_cfg.items():
        if not bcfg.get("channels", {}).get("line"):
            continue
        item = db.next_pending(brand_id, "line")
        if item:
            try:
                if item.get("image_url"):
                    messenger.broadcast_with_image(item.get("message",""), item["image_url"],
                                                   brand=brand_id)
                else:
                    messenger.broadcast(item.get("message",""), brand=brand_id)
                db.mark_posted(item["id"])
                db.log_activity("post", brand=brand_id, platform="line",
                                detail=f"LINE配信完了: {item.get('message','')[:40]}")
                sent_total += 1
                logger.info(f"LINE配信完了: {brand_id} (DB id={item['id']})")
                break
            except Exception as e:
                logger.error(f"LINE配信エラー {brand_id}: {e}", exc_info=True)
                _add_to_decision_queue("line_broadcast_error", str(e),
                                       {"db_id": item["id"], "brand": brand_id})

    # DBに何もなければ旧YAMLキューを処理（後方互換）
    if sent_total == 0:
        q_dir = QUEUE_DIR / "line"
        if q_dir.exists():
            now = datetime.now()
            for f in sorted(q_dir.glob("*.yaml")):
                try:
                    data = yaml.safe_load(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if data.get("posted"):
                    continue
                sched_line = data.get("scheduled_at")
                if sched_line:
                    try:
                        if datetime.strptime(str(sched_line), "%Y-%m-%d %H:%M") > now:
                            continue
                    except ValueError:
                        pass
                try:
                    if data.get("image_url"):
                        messenger.broadcast_with_image(data.get("message",""), data["image_url"])
                    else:
                        messenger.broadcast(data.get("message",""))
                    data["posted"] = True
                    _atomic_yaml_write(f, data)
                    sent_total += 1
                    logger.info(f"LINE配信完了(YAML): {f.name}")
                    break
                except Exception as e:
                    logger.error(f"LINE配信エラー ({f.name}): {e}", exc_info=True)
                    _add_to_decision_queue("line_broadcast_error", str(e), {"file": f.name})

    return sent_total


def _add_to_decision_queue(type_: str, reason: str, detail: dict = {}):
    """判断待ちキューにアイテムを追加する（DB + YAML後方互換）"""
    # DBに記録
    try:
        now = datetime.now()
        filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{type_}.yaml"
        db.add_decision(reason=reason, type_=type_, context=detail, filename=filename)
    except Exception:
        pass
    # YAMLにも記録（後方互換）
    DECISION_QUEUE_DIR.mkdir(exist_ok=True)
    now = datetime.now()
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{type_}.yaml"
    item = {
        "type": type_, "reason": reason, "detail": detail,
        "created_at": now.strftime("%Y-%m-%d %H:%M:%S"), "resolved": False,
    }
    try:
        (DECISION_QUEUE_DIR / filename).write_text(
            yaml.dump(item, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    logger.warning(f"判断待ちキューに追加: {type_} — {reason}")


def send_morning_summary(
    ig_posted: int,
    line_sent: int,
    followup_sent: int,
    pending_leads: int,
    decisions: list[dict],
):
    """朝のサマリーをオーナーのLINEに送る"""
    if not OWNER_LINE_USER_ID:
        logger.warning("OWNER_LINE_USER_ID が未設定のためサマリーをスキップ")
        return

    today = datetime.now().strftime("%Y年%m月%d日")
    lines = [f"【{today} 朝の自動処理完了】"]
    lines.append("")
    lines.append(f"📱 Instagram投稿: {ig_posted}件")
    lines.append(f"📣 LINE配信: {line_sent}件")
    lines.append(f"💬 フォローアップ送信: {followup_sent}件")
    lines.append(f"🔖 未対応リード: {pending_leads}件")

    if decisions:
        lines.append("")
        lines.append(f"⚠️ あなたの判断が必要: {len(decisions)}件")
        for d in decisions[:5]:  # 最大5件表示
            lines.append(f"  • {d['type']}: {d['reason'][:40]}")
        if len(decisions) > 5:
            lines.append(f"  （他 {len(decisions) - 5}件）")
        lines.append("")
        lines.append("decision_queue/ フォルダを確認してください。")
    else:
        lines.append("")
        lines.append("✅ 判断が必要な案件はありません")

    summary = "\n".join(lines)
    messenger = LINEMessenger()
    messenger.push(OWNER_LINE_USER_ID, summary)
    logger.info("朝のサマリー送信完了")


def _read_unread_alerts() -> str:
    """alerts.log の未読分を返す。マーカーを更新する。"""
    alerts_log = Path(__file__).parent / "logs" / "alerts.log"
    marker    = Path(__file__).parent / "logs" / ".morning_alerts_marker"
    if not alerts_log.exists():
        return ""
    all_lines = alerts_log.read_text(encoding="utf-8").splitlines()
    last_pos = 0
    if marker.exists():
        try:
            last_pos = int(marker.read_text().strip())
        except Exception:
            last_pos = 0
    new_alerts = all_lines[last_pos:]
    marker.write_text(str(len(all_lines)))
    if not new_alerts:
        return ""
    return f"📋 前日アラート({len(new_alerts)}件):\n" + "\n".join(new_alerts)


def run():
    """朝のオペレーター本体"""
    logger.info("===== 朝のオペレーター開始 =====")
    (Path(__file__).parent / "logs").mkdir(exist_ok=True)

    # 0-pre. ログローテーション
    try:
        from log_rotation import run as rotate_logs
        rotated = rotate_logs()
        if rotated:
            logger.info("ログローテーション: %d ファイル処理", rotated)
    except Exception as e:
        logger.warning("ログローテーション失敗（無視して継続）: %s", e)

    # 0. alerts.log 読み上げ
    alert_msg = _read_unread_alerts()
    if alert_msg:
        logger.warning(alert_msg)

    # 1. Instagram投稿
    ig_posted = post_instagram_queue()

    # 2. LINE配信
    line_sent = post_line_queue()

    # 3. フォローアップ
    followup_sent = run_followup_check() or 0

    # 4. 未対応リード数
    pending_leads = _count_pending_leads()

    # 5. 判断待ちキュー
    decisions = _load_decision_queue()

    # 6. サマリーをオーナーのLINEへ
    send_morning_summary(ig_posted, line_sent, followup_sent, pending_leads, decisions)

    logger.info("===== 朝のオペレーター完了 =====")


if __name__ == "__main__":
    run()
