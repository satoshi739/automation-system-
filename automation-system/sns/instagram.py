"""
Instagram 自動投稿モジュール
Meta Graph API (Content Publishing API) を使用

必要なもの:
- Instagramビジネスアカウント（Facebookページと連携済み）
- Meta for Developers でアプリを作成
- instagram_content_publish 権限を取得
"""

import os
import time
import logging
import requests
from pathlib import Path

logger = logging.getLogger(__name__)


BRAND_ENV_PREFIX: dict[str, str] = {
    "bangkok-peach":  "BANGKOK_PEACH",
    "cashflowsupport": "CASHFLOWSUPPORT",
    "dsc-marketing":  "DSC_MARKETING",
    "satoshi":        "SATOSHI",
    "satoshi-blog":   "SATOSHI_BLOG",
    "upjapan":        "UPJAPAN",
}


class InstagramPoster:
    BASE_URL = "https://graph.facebook.com/v19.0"

    def __init__(self, brand: str = ""):
        prefix = BRAND_ENV_PREFIX.get(brand, "")
        token_key   = f"{prefix}_META_ACCESS_TOKEN"        if prefix else "META_ACCESS_TOKEN"
        account_key = f"{prefix}_INSTAGRAM_ACCOUNT_ID"    if prefix else "INSTAGRAM_BUSINESS_ACCOUNT_ID"

        self.access_token = os.environ.get(token_key, "") or os.environ.get("META_ACCESS_TOKEN", "")
        self.account_id   = os.environ.get(account_key, "") or os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID", "")
        self.dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
        if not self.access_token or not self.account_id:
            label = f"[{brand}] " if brand else ""
            logger.warning("%sMETA_ACCESS_TOKEN または INSTAGRAM_BUSINESS_ACCOUNT_ID 未設定 — Instagram投稿を無効化", label)
            self.enabled = False
        else:
            self.enabled = True

    def _api(self, method: str, endpoint: str, **kwargs) -> dict:
        url = f"{self.BASE_URL}/{endpoint}"
        kwargs.setdefault("params", {})["access_token"] = self.access_token
        kwargs.setdefault("timeout", 30)
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def post_image(self, image_url: str, caption: str) -> dict:
        """
        画像投稿（image_url は公開アクセス可能なURL）

        Returns:
            {"media_id": "...", "status": "posted"} or {"status": "dry_run"}
        """
        if not self.enabled:
            logger.warning("META_ACCESS_TOKEN未設定、Instagram投稿をスキップ")
            return None
        if self.dry_run:
            logger.info(f"[DRY RUN] Instagram画像投稿: {caption[:40]}...")
            return {"status": "dry_run", "caption": caption}

        # Step 1: メディアコンテナ作成
        logger.info("Instagram: メディアコンテナ作成中...")
        container = self._api(
            "POST",
            f"{self.account_id}/media",
            data={"image_url": image_url, "caption": caption},
        )
        container_id = container.get("id")
        if not container_id:
            raise RuntimeError(f"メディアコンテナIDが取得できませんでした: {container}")

        # Step 2: 準備完了まで待機（最大60秒）
        for _ in range(12):
            status = self._api("GET", container_id, params={"fields": "status_code"})
            if status.get("status_code") == "FINISHED":
                break
            time.sleep(5)
        else:
            raise TimeoutError("メディアコンテナの準備がタイムアウトしました")

        # Step 3: 公開
        logger.info("Instagram: 投稿公開中...")
        result = self._api(
            "POST",
            f"{self.account_id}/media_publish",
            data={"creation_id": container_id},
        )
        media_id = result.get("id")
        if not media_id:
            raise RuntimeError(f"投稿IDが取得できませんでした: {result}")
        logger.info(f"Instagram投稿完了: media_id={media_id}")
        return {"status": "posted", "media_id": media_id}

    def post_reel(self, video_url: str, caption: str, cover_url: str = "") -> dict:
        """
        リール投稿（video_url は公開アクセス可能なURL）
        """
        if not self.enabled:
            logger.warning("META_ACCESS_TOKEN未設定、Instagram投稿をスキップ")
            return None
        if self.dry_run:
            logger.info(f"[DRY RUN] Instagramリール投稿: {caption[:40]}...")
            return {"status": "dry_run", "caption": caption}

        data = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
        }
        if cover_url:
            data["cover_url"] = cover_url

        # Step 1: コンテナ作成
        container = self._api("POST", f"{self.account_id}/media", data=data)
        container_id = container.get("id")
        if not container_id:
            raise RuntimeError(f"リールコンテナIDが取得できませんでした: {container}")

        # Step 2: 動画処理を待機（動画は画像より時間がかかる）
        logger.info("Instagram: リール動画処理中（最大5分）...")
        for _ in range(60):
            status = self._api("GET", container_id, params={"fields": "status_code"})
            code = status.get("status_code")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise RuntimeError(f"動画処理エラー: {status}")
            time.sleep(5)
        else:
            raise TimeoutError("リール動画の処理がタイムアウトしました")

        # Step 3: 公開
        result = self._api(
            "POST",
            f"{self.account_id}/media_publish",
            data={"creation_id": container_id},
        )
        media_id = result.get("id")
        if not media_id:
            raise RuntimeError(f"リール投稿IDが取得できませんでした: {result}")
        logger.info(f"Instagramリール投稿完了: media_id={media_id}")
        return {"status": "posted", "media_id": media_id}

    def post_carousel(self, slides: list[dict], caption: str) -> dict:
        """
        カルーセル投稿（複数枚スライド）

        Args:
            slides: [{"image_url": str}, ...] または [{"video_url": str}, ...]
                    最大10枚
            caption: 投稿キャプション

        Returns:
            {"media_id": str, "status": "posted"} or {"status": "dry_run"}
        """
        if not self.enabled:
            logger.warning("META_ACCESS_TOKEN未設定、Instagram投稿をスキップ")
            return None
        if self.dry_run:
            logger.info(f"[DRY RUN] Instagramカルーセル投稿: {len(slides)}枚 {caption[:40]}...")
            return {"status": "dry_run", "slide_count": len(slides)}

        if not slides:
            raise ValueError("スライドが空です")
        if len(slides) > 10:
            slides = slides[:10]
            logger.warning("カルーセルは最大10枚のため先頭10枚に切り詰めました")

        # Step 1: 各スライドのメディアコンテナを作成
        child_ids = []
        for i, slide in enumerate(slides):
            logger.info(f"Instagram: カルーセルスライド {i+1}/{len(slides)} コンテナ作成...")
            data: dict = {"is_carousel_item": "true"}
            if "video_url" in slide:
                data["media_type"] = "VIDEO"
                data["video_url"]  = slide["video_url"]
            else:
                data["image_url"] = slide["image_url"]

            container = self._api("POST", f"{self.account_id}/media", data=data)
            cid = container.get("id")
            if not cid:
                raise RuntimeError(f"スライド{i+1}のコンテナIDが取得できませんでした: {container}")
            child_ids.append(cid)

            # 動画スライドは処理完了を待つ
            if "video_url" in slide:
                for _ in range(60):
                    status = self._api("GET", cid, params={"fields": "status_code"})
                    if status.get("status_code") == "FINISHED":
                        break
                    if status.get("status_code") == "ERROR":
                        raise RuntimeError(f"スライド{i+1}の動画処理エラー: {status}")
                    time.sleep(5)

        # Step 2: カルーセルコンテナ作成
        logger.info("Instagram: カルーセルコンテナ作成中...")
        carousel = self._api(
            "POST",
            f"{self.account_id}/media",
            data={
                "media_type": "CAROUSEL",
                "children":   ",".join(child_ids),
                "caption":    caption,
            },
        )
        carousel_id = carousel.get("id")
        if not carousel_id:
            raise RuntimeError(f"カルーセルコンテナIDが取得できませんでした: {carousel}")

        # Step 3: 準備完了待機
        for _ in range(12):
            status = self._api("GET", carousel_id, params={"fields": "status_code"})
            if status.get("status_code") == "FINISHED":
                break
            time.sleep(5)
        else:
            raise TimeoutError("カルーセルコンテナの準備がタイムアウトしました")

        # Step 4: 公開
        result = self._api(
            "POST",
            f"{self.account_id}/media_publish",
            data={"creation_id": carousel_id},
        )
        media_id = result.get("id")
        if not media_id:
            raise RuntimeError(f"カルーセル投稿IDが取得できませんでした: {result}")
        logger.info(f"Instagramカルーセル投稿完了: media_id={media_id} ({len(slides)}枚)")
        return {"status": "posted", "media_id": media_id, "slide_count": len(slides)}

    def post_stories_text(self, sticker_texts: list[str]) -> dict:
        """
        ストーリーズ用テキストスタンプ（テキストのみのストーリーズ）
        ※ Meta Graph API の Stories 投稿は背景色付きテキスト形式

        Args:
            sticker_texts: テキスト一覧（1投稿につき1枚のストーリーズ）

        Returns:
            {"posted": int, "status": "posted"} or {"status": "dry_run"}
        """
        if not self.enabled:
            logger.warning("META_ACCESS_TOKEN未設定、Instagram投稿をスキップ")
            return None
        if self.dry_run:
            logger.info(f"[DRY RUN] Instagramストーリーズ: {len(sticker_texts)}枚")
            return {"status": "dry_run", "count": len(sticker_texts)}

        posted = 0
        for text in sticker_texts:
            try:
                container = self._api(
                    "POST",
                    f"{self.account_id}/media",
                    data={
                        "media_type":   "STORIES",
                        "caption":      text,
                        "story_format": "STICKER",
                    },
                )
                story_id = container.get("id")
                if not story_id:
                    raise RuntimeError(f"ストーリーズコンテナIDが取得できませんでした: {container}")
                self._api(
                    "POST",
                    f"{self.account_id}/media_publish",
                    data={"creation_id": story_id},
                )
                posted += 1
                time.sleep(2)
            except Exception as e:
                logger.error(f"ストーリーズ投稿エラー: {e}")

        logger.info(f"Instagramストーリーズ投稿完了: {posted}枚")
        return {"status": "posted", "posted": posted}

    def get_insights(self, media_id: str) -> dict:
        """投稿のインサイト（いいね・リーチ・再生数・保存数）を取得"""
        metrics = "impressions,reach,likes,comments,shares,saved"
        if "REEL" in media_id.upper():
            metrics += ",plays"
        try:
            return self._api(
                "GET",
                f"{media_id}/insights",
                params={"metric": metrics},
            )
        except Exception as e:
            logger.warning(f"インサイト取得エラー (media_id={media_id}): {e}")
            return {}

    def get_insights_parsed(self, media_id: str) -> dict:
        """
        インサイトを取得してパフォーマンス記録用に整形する

        Returns:
            {"likes": int, "reach": int, "comments": int, "saves": int, "plays": int}
        """
        raw = self.get_insights(media_id)
        result: dict = {"likes": 0, "reach": 0, "comments": 0, "saves": 0, "plays": 0}
        for item in raw.get("data", []):
            name  = item.get("name", "")
            value = item.get("values", [{}])[0].get("value", 0) if item.get("values") else item.get("value", 0)
            if name == "likes":
                result["likes"] = int(value)
            elif name == "reach":
                result["reach"] = int(value)
            elif name == "comments":
                result["comments"] = int(value)
            elif name == "saved":
                result["saves"] = int(value)
            elif name == "plays":
                result["plays"] = int(value)
        return result
