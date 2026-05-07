"""
Twitter / X 投稿モジュール
Twitter API v2（tweepy）

必要なもの:
- Twitter Developer Portal でアプリを作成
- Free プランでも投稿は可能（月1,500ツイートまで）
- API Key / API Secret / Access Token / Access Token Secret
"""

import os, logging
log = logging.getLogger(__name__)


class TwitterPoster:
    def __init__(self, brand: str = "dsc-marketing"):
        prefix = brand.upper().replace("-","_")
        self.api_key       = os.environ.get(f"{prefix}_TWITTER_API_KEY","")
        self.api_secret    = os.environ.get(f"{prefix}_TWITTER_API_SECRET","")
        self.access_token  = os.environ.get(f"{prefix}_TWITTER_ACCESS_TOKEN","")
        self.access_secret = os.environ.get(f"{prefix}_TWITTER_ACCESS_SECRET","")
        self.dry_run = os.environ.get("DRY_RUN","false").lower() == "true"

    def _client(self):
        try:
            import tweepy
        except ImportError:
            raise ImportError("pip install tweepy")
        return tweepy.Client(
            consumer_key=self.api_key,
            consumer_secret=self.api_secret,
            access_token=self.access_token,
            access_token_secret=self.access_secret,
        )

    def tweet(self, text: str) -> dict:
        if self.dry_run:
            log.info(f"[DRY RUN] ツイート: {text[:40]}...")
            return {"status":"dry_run"}
        if not self.api_key:
            raise ValueError("Twitter APIキーが設定されていません")
        r = self._client().create_tweet(text=text)
        tweet_id = r.data.get("id") if r.data else None
        if not tweet_id:
            log.error(f"ツイート失敗: APIレスポンスにIDなし (data={r.data})")
            return {"status":"error","error":"no tweet id returned"}
        log.info(f"ツイート完了: {tweet_id}")
        return {"status":"posted","id":tweet_id}

    def tweet_thread(self, texts: list[str]) -> list[dict]:
        """複数ツイートをスレッドとして投稿"""
        if self.dry_run:
            log.info(f"[DRY RUN] スレッドツイート: {len(texts)}件")
            return [{"status":"dry_run"}]*len(texts)
        client = self._client()
        results, prev_id = [], None
        for text in texts:
            kw = {"text": text}
            if prev_id:
                kw["in_reply_to_tweet_id"] = prev_id
            r = client.create_tweet(**kw)
            prev_id = r.data.get("id") if r.data else None
            if not prev_id:
                log.error(f"スレッドツイート失敗: APIレスポンスにIDなし")
                results.append({"status":"error","error":"no tweet id returned"})
                break
            results.append({"status":"posted","id":prev_id})
        return results
