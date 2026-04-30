
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
from flask import Flask, abort, request

from sales.lead_intake import create_lead_from_line, load_lead_by_line_id
from sns.line_api import LINEMessenger

load_dotenv()

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
    """alerts.log への書き込み + Mac通知（server_check 発信）。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(ALERTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"[{timestamp}] [server_check] {message}\n")
    except Exception as exc:
        logger.error("alerts.log 書き込み失敗: %s", exc)
    try:
        safe_msg = message.replace('"', "'")[:100]
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{safe_msg}" with title "server_check alert"'],
            timeout=5, capture_output=True,
        )
    except Exception as exc:
        logger.error("Mac通知失敗: %s", exc)
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


# TODO (gunicorn化時): app の module-level で
# _start_heartbeat_monitor() を呼ぶように変更する。
# multi-worker の場合は worker_id=0 のみが起動するよう
# fcntl ロックや env var (GUNICORN_WORKER_ID) で制御する。
# 現状の __main__ 内呼び出しでは、gunicorn 起動時に
# 監視スレッドが一本も立ち上がらない(無音の監視停止)。
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
    scenarios = _load_scenarios()

    for event in data.get("events", []):
        event_type = event.get("type")
        source = event.get("source", {})
        user_id = source.get("userId", "")

        # --- 友だち追加 ---
        if event_type == "follow":
            _handle_follow(user_id, scenarios)

        # --- メッセージ受信 ---
        elif event_type == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"]
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


def _handle_message(user_id: str, text: str, reply_token: str, scenarios: dict, messenger: LINEMessenger | None = None):
    """メッセージ受信時の処理"""
    # 既存リードか確認
    existing = load_lead_by_line_id(user_id)

    messenger = messenger or _get_messenger()
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


@app.route("/", methods=["GET"])
def index():
    return {"status": "ok", "service": "upjapan-automation"}


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    _start_heartbeat_monitor()
    logger.info(f"LINE Webhookサーバー起動: port={port}")
    app.run(host="0.0.0.0", port=port, debug=False)
