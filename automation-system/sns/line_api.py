"""
LINE Messaging API モジュール
- 自動返信（Webhook）
- 一斉配信（Broadcast）
- フォローアップメッセージ送信
"""

import os
import hashlib
import hmac
import base64
import logging
import requests

logger = logging.getLogger(__name__)

# ブランドスラッグ → (トークン環境変数名, シークレット環境変数名)
_BRAND_LINE_ENV: dict[str, tuple[str, str]] = {
    "cashflowsupport": ("LINE_CHANNEL_ACCESS_TOKEN",            "LINE_CHANNEL_SECRET"),
    "dsc-marketing":   ("LINE_CHANNEL_ACCESS_TOKEN_DSC",         "LINE_CHANNEL_SECRET_DSC"),
    "bangkok-peach":   ("BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN", "BANGKOK_PEACH_LINE_CHANNEL_SECRET"),
}


def get_brand_messenger(brand_slug: str) -> "LINEMessenger":
    """
    ブランドに対応した LINEMessenger を返す。
    ブランド固有トークンが未設定の場合は enabled=False のメッセンジャーを返す
    （デフォルトチャンネルへのフォールバックはしない）。
    """
    token_key, secret_key = _BRAND_LINE_ENV.get(
        brand_slug, ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")
    )
    token  = os.environ.get(token_key, "")
    secret = os.environ.get(secret_key, "")

    # ブランド固有スロットがあるのにトークン未設定 → 誤チャンネルへの送信を防ぐ
    if brand_slug in _BRAND_LINE_ENV and (not token or not secret):
        m = object.__new__(LINEMessenger)
        m.token = ""
        m.secret = ""
        m.dry_run = False
        m.enabled = False
        m._headers = {}
        return m

    return LINEMessenger(token=token, secret=secret)


class LINEMessenger:
    BASE_URL = "https://api.line.me/v2/bot"

    def __init__(self, token: str = "", secret: str = ""):
        self.token = token or os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        self.secret = secret or os.environ.get("LINE_CHANNEL_SECRET", "")
        self.dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
        if not self.token or not self.secret:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN または LINE_CHANNEL_SECRET 未設定 — LINE機能を無効化")
            self.enabled = False
        else:
            self.enabled = True
        self._headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def verify_signature(self, body: bytes, signature: str) -> bool:
        """Webhookの署名検証（セキュリティ必須）"""
        if not self.enabled:
            return False
        digest = hmac.new(
            self.secret.encode("utf-8"), body, hashlib.sha256
        ).digest()
        expected = base64.b64encode(digest).decode("utf-8")
        return hmac.compare_digest(expected, signature)

    def reply(self, reply_token: str, message: str) -> bool:
        """Webhookイベントへの返信"""
        if not self.enabled:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN未設定、LINE返信をスキップ")
            return False
        if self.dry_run:
            logger.info(f"[DRY RUN] LINE返信: {message[:40]}...")
            return True

        payload = {
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": message}],
        }
        resp = requests.post(
            f"{self.BASE_URL}/message/reply",
            headers=self._headers,
            json=payload,
        )
        if resp.status_code != 200:
            logger.error(f"LINE返信エラー: {resp.text}")
            return False
        return True

    def push(self, user_id: str, message: str) -> bool:
        """特定ユーザーへのプッシュメッセージ"""
        if not self.enabled:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN未設定、LINEプッシュをスキップ")
            return False
        if self.dry_run:
            logger.info(f"[DRY RUN] LINEプッシュ to {user_id}: {message[:40]}...")
            return True

        payload = {
            "to": user_id,
            "messages": [{"type": "text", "text": message}],
        }
        resp = requests.post(
            f"{self.BASE_URL}/message/push",
            headers=self._headers,
            json=payload,
        )
        if resp.status_code != 200:
            logger.error(f"LINEプッシュエラー: {resp.text}")
            return False
        logger.info(f"LINEプッシュ完了: user_id={user_id}")
        return True

    def push_to_owner(self, message: str) -> bool:
        """OWNER_LINE_USER_ID に設定されたオーナーへのプッシュ"""
        owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if not owner_id:
            logger.warning("OWNER_LINE_USER_ID 未設定 — オーナーへのLINE送信をスキップ")
            return False
        return self.push(owner_id, message)

    def broadcast(self, message: str) -> bool:
        """全友だちへの一斉配信"""
        if not self.enabled:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN未設定、LINE一斉配信をスキップ")
            return False
        if self.dry_run:
            logger.info(f"[DRY RUN] LINE一斉配信: {message[:40]}...")
            return True

        payload = {"messages": [{"type": "text", "text": message}]}
        resp = requests.post(
            f"{self.BASE_URL}/message/broadcast",
            headers=self._headers,
            json=payload,
        )
        if resp.status_code != 200:
            logger.error(f"LINE一斉配信エラー: {resp.text}")
            return False
        logger.info("LINE一斉配信完了")
        return True

    def broadcast_with_image(self, message: str, image_url: str, preview_url: str = "") -> bool:
        """画像付き一斉配信"""
        if not self.enabled:
            logger.warning("LINE_CHANNEL_ACCESS_TOKEN未設定、LINE画像付き配信をスキップ")
            return False
        if not preview_url:
            preview_url = image_url

        if self.dry_run:
            logger.info(f"[DRY RUN] LINE画像付き配信: {message[:40]}...")
            return True

        payload = {
            "messages": [
                {
                    "type": "image",
                    "originalContentUrl": image_url,
                    "previewImageUrl": preview_url,
                },
                {"type": "text", "text": message},
            ]
        }
        resp = requests.post(
            f"{self.BASE_URL}/message/broadcast",
            headers=self._headers,
            json=payload,
        )
        if resp.status_code != 200:
            logger.error(f"LINE画像付き配信エラー: {resp.text}")
            return False
        logger.info("LINE画像付き一斉配信完了")
        return True

    def get_profile(self, user_id: str) -> dict:
        """ユーザープロフィール取得（名前・アイコン）"""
        if not self.enabled:
            return {}
        resp = requests.get(
            f"{self.BASE_URL}/profile/{user_id}",
            headers=self._headers,
        )
        if resp.status_code != 200:
            return {}
        return resp.json()
