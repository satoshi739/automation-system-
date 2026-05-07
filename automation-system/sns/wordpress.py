from __future__ import annotations

"""
WordPress 投稿モジュール
WordPress REST API（アプリパスワード認証）

必要なもの:
- WordPress管理画面 → ユーザー → プロフィール → アプリケーションパスワードを生成
- .env に WP_URL / WP_USER / WP_APP_PASSWORD を設定
"""

import os, logging, requests
from base64 import b64encode
log = logging.getLogger(__name__)


class WordPressPoster:
    def __init__(self, brand: str = "dsc-marketing"):
        prefix = brand.upper().replace("-","_")
        self.wp_url  = os.environ.get(f"{prefix}_WP_URL","").rstrip("/")
        self.wp_user = os.environ.get(f"{prefix}_WP_USER","")
        self.wp_pass = os.environ.get(f"{prefix}_WP_APP_PASSWORD","")
        self.dry_run = os.environ.get("DRY_RUN","false").lower() == "true"

    def _auth(self):
        token = b64encode(f"{self.wp_user}:{self.wp_pass}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def create_post(
        self,
        title: str,
        content: str,
        status: str = "draft",   # "draft" or "publish"
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        featured_image_url: str = "",
    ) -> dict:
        if self.dry_run:
            log.info(f"[DRY RUN] WordPress投稿: {title} ({status})")
            return {"status":"dry_run","title":title}
        if not self.wp_url:
            raise ValueError(f"WP_URL が未設定です（ブランド: {self.wp_user}）")

        body = {"title":title,"content":content,"status":status}
        if categories: body["categories"] = categories
        if tags:       body["tags"] = tags

        # アイキャッチ画像のアップロード（URLから）
        if featured_image_url:
            media_id = self._upload_image_from_url(featured_image_url, title)
            if media_id:
                body["featured_media"] = media_id

        r = requests.post(
            f"{self.wp_url}/wp-json/wp/v2/posts",
            headers=self._auth(), json=body
        )
        if r.status_code == 401:
            brand = self.wp_user or "(不明)"
            log.error(f"WordPress認証失敗: {brand} — アプリパスワードを確認してください")
            return {"status": "auth_error", "error": "401 Unauthorized"}
        r.raise_for_status()
        post = r.json()
        post_url = post.get("link", "")
        post_id  = post.get("id")
        log.info(f"WordPress投稿{'公開' if status=='publish' else '下書き'}完了: {post_url}")
        return {"status":status,"id":post_id,"url":post_url}

    def _upload_image_from_url(self, image_url: str, title: str) -> int | None:
        """URLから画像をダウンロードしてWordPressにアップロード"""
        try:
            img_resp = requests.get(image_url, timeout=10)
            img_resp.raise_for_status()
            ct = img_resp.headers.get("Content-Type","image/jpeg")
            ext = ct.split("/")[-1].split(";")[0]
            headers = {**self._auth(), "Content-Disposition": f'attachment; filename="{title}.{ext}"',
                       "Content-Type": ct}
            r = requests.post(f"{self.wp_url}/wp-json/wp/v2/media",
                              headers=headers, data=img_resp.content)
            r.raise_for_status()
            return r.json().get("id")
        except Exception as e:
            log.warning(f"アイキャッチ画像アップロード失敗: {e}")
            return None

    def get_posts(self, status: str = "draft", per_page: int = 10) -> list[dict]:
        """投稿一覧を取得"""
        r = requests.get(
            f"{self.wp_url}/wp-json/wp/v2/posts",
            headers=self._auth(),
            params={"status":status,"per_page":per_page,"_fields":"id,title,status,link,date"}
        )
        r.raise_for_status()
        return r.json()

    def publish_post(self, post_id: int) -> dict:
        """下書きを公開する"""
        r = requests.post(
            f"{self.wp_url}/wp-json/wp/v2/posts/{post_id}",
            headers=self._auth(), json={"status":"publish"}
        )
        if r.status_code == 401:
            log.error(f"WordPress公開失敗: 認証エラー (post_id={post_id})")
            return {"status":"auth_error","error":"401 Unauthorized"}
        if r.status_code == 403:
            log.error(f"WordPress公開失敗: 権限不足 (post_id={post_id})")
            return {"status":"forbidden","error":"403 Forbidden"}
        if r.status_code == 404:
            log.error(f"WordPress公開失敗: 投稿が見つからない (post_id={post_id})")
            return {"status":"not_found","error":"404 Not Found"}
        r.raise_for_status()
        post = r.json()
        return {"status":"published","url":post.get("link","")}
