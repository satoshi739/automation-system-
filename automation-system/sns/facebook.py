"""
Facebook 投稿モジュール（Meta Graph API）
Instagramと同じアクセストークンで Facebookページにも投稿できる
"""
import os, logging, requests
log = logging.getLogger(__name__)

class FacebookPoster:
    BASE = "https://graph.facebook.com/v19.0"

    def __init__(self, brand: str = "dsc-marketing"):
        prefix = brand.upper().replace("-","_")
        self.page_id    = os.environ.get(f"{prefix}_FB_PAGE_ID","")
        self.page_token = os.environ.get(f"{prefix}_FB_PAGE_TOKEN","")
        self.dry_run    = os.environ.get("DRY_RUN","false").lower() == "true"
        if not self.page_token or not self.page_id:
            log.warning("[%s] FB_PAGE_TOKEN または FB_PAGE_ID 未設定 — Facebook投稿を無効化", brand)
            self.enabled = False
        else:
            self.enabled = True

    def _api(self, method, path, **kw):
        kw.setdefault("params",{})["access_token"] = self.page_token
        r = requests.request(method, f"{self.BASE}/{path}", **kw)
        r.raise_for_status()
        return r.json()

    def post_text(self, message: str) -> dict:
        if not self.enabled:
            log.warning("Facebook投稿をスキップ（未設定）")
            return {"status":"skipped"}
        if self.dry_run:
            log.info(f"[DRY RUN] Facebook投稿: {message[:40]}...")
            return {"status":"dry_run"}
        r = self._api("POST", f"{self.page_id}/feed", data={"message": message})
        post_id = r.get("id")
        log.info(f"Facebook投稿完了: {post_id}")
        return {"status":"posted","id":post_id}

    def post_image(self, image_url: str, message: str) -> dict:
        if not self.enabled:
            log.warning("Facebook画像投稿をスキップ（未設定）")
            return {"status":"skipped"}
        if self.dry_run:
            log.info(f"[DRY RUN] Facebook画像投稿: {message[:40]}...")
            return {"status":"dry_run"}
        r = self._api("POST", f"{self.page_id}/photos",
                      data={"url": image_url, "caption": message})
        return {"status":"posted","id":r.get("id")}

    def get_page_insights(self, days: int = 28) -> dict:
        """ページのインサイト（リーチ・エンゲージメント）"""
        if not self.page_id:
            return {}
        try:
            metrics = "page_impressions,page_reach,page_engaged_users,page_fans"
            r = self._api("GET", f"{self.page_id}/insights",
                          params={"metric": metrics, "period": "month"})
            result = {}
            for item in r.get("data", []):
                name = item["name"]
                values = item.get("values", [{}])
                result[name] = values[-1].get("value", 0) if values else 0
            return result
        except Exception as e:
            log.error(f"Facebook insights エラー: {e}")
            return {"error": str(e)}
