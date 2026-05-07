"""
Threads 投稿モジュール
Meta Threads API（Instagram Graph API ベース）
https://developers.facebook.com/docs/threads

必要なもの:
- Meta for Developers でアプリを作成（Threads 権限）
- threads_content_publish / threads_read_replies 権限
- 各ブランドのアクセストークン・UserID
"""

import os, time, logging, requests
log = logging.getLogger(__name__)


class ThreadsPoster:
    BASE = "https://graph.threads.net/v1.0"

    def __init__(self, account_id: str, access_token: str):
        self.account_id = account_id
        self.token = access_token
        self.dry_run = os.environ.get("DRY_RUN","false").lower() == "true"

    def _api(self, method, path, **kw):
        kw.setdefault("params", {})["access_token"] = self.token
        r = requests.request(method, f"{self.BASE}/{path}", **kw)
        r.raise_for_status()
        return r.json()

    def post_text(self, text: str) -> dict:
        if self.dry_run:
            log.info(f"[DRY RUN] Threads テキスト投稿: {text[:40]}...")
            return {"status": "dry_run"}
        # Step1: コンテナ作成
        c = self._api("POST", f"{self.account_id}/threads",
                      data={"media_type":"TEXT","text":text})
        cid = c.get("id")
        if not cid:
            log.error(f"Threadsコンテナ作成失敗: idなし (resp={c})")
            return {"status":"error","error":"no container id"}
        time.sleep(3)
        # Step2: 公開
        r = self._api("POST", f"{self.account_id}/threads_publish",
                      data={"creation_id": cid})
        post_id = r.get("id")
        log.info(f"Threads投稿完了: {post_id}")
        return {"status":"posted","id":post_id}

    def post_image(self, image_url: str, text: str) -> dict:
        if self.dry_run:
            log.info(f"[DRY RUN] Threads 画像投稿: {text[:40]}...")
            return {"status":"dry_run"}
        c = self._api("POST", f"{self.account_id}/threads",
                      data={"media_type":"IMAGE","image_url":image_url,"text":text})
        cid = c.get("id")
        if not cid:
            log.error(f"Threads画像コンテナ作成失敗: idなし (resp={c})")
            return {"status":"error","error":"no container id"}
        time.sleep(5)
        r = self._api("POST", f"{self.account_id}/threads_publish",
                      data={"creation_id": cid})
        post_id = r.get("id")
        log.info(f"Threads画像投稿完了: {post_id}")
        return {"status":"posted","id":post_id}
