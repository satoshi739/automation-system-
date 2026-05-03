"""
Google Business Profile (GBP) Connector
========================================
Abstract base + Mock implementation.

本番接続ポイント:
  - GBPRealConnector を実装し、Google My Business API v4.9 または
    Business Profile API を呼び出す。
  - 認証: OAuth2 (accounts.google.com) or Service Account
  - 環境変数: GBP_CLIENT_ID, GBP_CLIENT_SECRET, GBP_REFRESH_TOKEN
    もしくは GBP_SERVICE_ACCOUNT_JSON

現在は MockGBPConnector がリアルなサンプルデータを返す。
"""

from __future__ import annotations

import os
import random
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Optional


# ═══════════════════════════════════════════════════════
# Abstract Interface
# ═══════════════════════════════════════════════════════

class GBPConnector(ABC):

    @abstractmethod
    def sync_locations(self) -> list[dict]:
        """アカウント配下の全拠点を返す。"""

    @abstractmethod
    def sync_reviews(self, location_id: str) -> list[dict]:
        """指定拠点のレビューを返す。"""

    @abstractmethod
    def reply_to_review(self, location_id: str, review_id: str, reply_text: str) -> bool:
        """レビューに返信する。成功 True / 失敗 False。"""

    @abstractmethod
    def delete_review_reply(self, location_id: str, review_id: str) -> bool:
        """返信を削除する。"""

    @abstractmethod
    def sync_posts(self, location_id: str) -> list[dict]:
        """指定拠点の投稿一覧を返す。"""

    @abstractmethod
    def create_post(self, location_id: str, post_data: dict) -> dict:
        """投稿を作成し、作成された投稿データを返す。"""

    @abstractmethod
    def sync_media(self, location_id: str) -> list[dict]:
        """メディア（写真・動画）一覧を返す。"""

    @abstractmethod
    def upload_media(self, location_id: str, media_path: str, category: str = "EXTERIOR") -> dict:
        """写真・動画をアップロードする。"""

    @abstractmethod
    def sync_insights(self, location_id: str, period_days: int = 28) -> dict:
        """インサイト（表示回数・アクション数など）を返す。"""


# ═══════════════════════════════════════════════════════
# Mock Implementation
# ═══════════════════════════════════════════════════════

_MOCK_REVIEWS_UPJ = [
    {"gbp_review_id": "rev_upj_001", "reviewer_name": "田中 健太", "rating": 5,
     "comment": "とても丁寧なサービスでした。スタッフの対応が素晴らしく、また利用したいと思います。",
     "status": "unanswered", "created_at": "2026-04-18 10:23:00"},
    {"gbp_review_id": "rev_upj_002", "reviewer_name": "佐藤 美咲", "rating": 4,
     "comment": "全体的に満足です。ただ待ち時間が少し長かったです。",
     "status": "unanswered", "created_at": "2026-04-16 15:47:00"},
    {"gbp_review_id": "rev_upj_003", "reviewer_name": "鈴木 一郎", "rating": 2,
     "comment": "期待していたほどではありませんでした。説明が不足していると感じました。",
     "status": "unanswered", "created_at": "2026-04-14 09:12:00"},
    {"gbp_review_id": "rev_upj_004", "reviewer_name": "山田 花子", "rating": 5,
     "comment": "最高のサービスです！毎回対応が丁寧で信頼できます。",
     "reply": "山田様、嬉しいお言葉ありがとうございます。またのご来店をお待ちしております。",
     "status": "answered", "created_at": "2026-04-10 14:30:00"},
    {"gbp_review_id": "rev_upj_005", "reviewer_name": "中村 太郎", "rating": 1,
     "comment": "スタッフの態度が悪く、二度と行きたくありません。完全に時間の無駄でした。",
     "status": "unanswered", "created_at": "2026-04-08 11:55:00"},
    {"gbp_review_id": "rev_upj_006", "reviewer_name": "伊藤 明", "rating": 3,
     "comment": "普通です。特別良くも悪くもありません。",
     "status": "answered", "reply": "伊藤様、ご利用いただきありがとうございます。",
     "created_at": "2026-04-05 16:20:00"},
]

_MOCK_REVIEWS_DSC = [
    {"gbp_review_id": "rev_dsc_001", "reviewer_name": "高橋 奈々", "rating": 5,
     "comment": "マーケティングのサポートが非常に充実していました。費用対効果が高いです。",
     "status": "answered", "reply": "高橋様、ありがとうございます！引き続き全力でサポートいたします。",
     "created_at": "2026-04-17 13:00:00"},
    {"gbp_review_id": "rev_dsc_002", "reviewer_name": "渡辺 隆", "rating": 4,
     "comment": "良いサービスだと思います。もう少し説明資料があるとなお良いかと。",
     "status": "unanswered", "created_at": "2026-04-15 10:00:00"},
    {"gbp_review_id": "rev_dsc_003", "reviewer_name": "小林 誠", "rating": 2,
     "comment": "返信が遅い。問い合わせから3日経っても連絡がありませんでした。",
     "status": "unanswered", "created_at": "2026-04-12 08:30:00"},
    {"gbp_review_id": "rev_dsc_004", "reviewer_name": "加藤 由美", "rating": 5,
     "comment": "SNS運用を任せて売上が2倍になりました。本当に感謝しています。",
     "status": "unanswered", "created_at": "2026-04-11 09:45:00"},
]

_MOCK_REVIEWS_BPG = [
    {"gbp_review_id": "rev_bpg_001", "reviewer_name": "松本 浩二", "rating": 5,
     "comment": "バンコクでこれほど充実したサービスは初めてです。スタッフも日本語対応で安心。",
     "status": "unanswered", "created_at": "2026-04-19 07:30:00"},
    {"gbp_review_id": "rev_bpg_002", "reviewer_name": "井上 裕子", "rating": 3,
     "comment": "場所がわかりにくかったです。案内表示を増やしてほしいです。",
     "status": "unanswered", "created_at": "2026-04-18 14:00:00"},
    {"gbp_review_id": "rev_bpg_003", "reviewer_name": "木村 亮", "rating": 1,
     "comment": "予約したのに待たされました。時間管理をしっかりしてください。",
     "status": "unanswered", "created_at": "2026-04-17 18:00:00"},
    {"gbp_review_id": "rev_bpg_004", "reviewer_name": "林 さくら", "rating": 5,
     "comment": "最高でした！友達にも紹介したいと思います。",
     "reply": "林様、嬉しいお言葉をありがとうございます！ぜひまたお越しください。",
     "status": "answered", "created_at": "2026-04-14 12:00:00"},
]

_MOCK_LOCATIONS = [
    {
        "gbp_location_id": "accounts/123456/locations/upj-shibuya",
        "location_name": "UPJ 渋谷オフィス",
        "brand": "upjapan",
        "address": "東京都渋谷区道玄坂1-12-1",
        "city": "東京",
        "phone": "03-1234-5678",
        "website": "https://upjapan.co.jp/",
        "avg_rating": 3.8,
        "total_reviews": 6,
        "photos_count": 4,
    },
    {
        "gbp_location_id": "accounts/123456/locations/dsc-shinjuku",
        "location_name": "DSc Marketing 新宿",
        "brand": "dsc-marketing",
        "address": "東京都新宿区西新宿2-1-1",
        "city": "東京",
        "phone": "03-9876-5432",
        "website": "https://dsc-marketing.com/",
        "avg_rating": 4.0,
        "total_reviews": 4,
        "photos_count": 12,
    },
    {
        "gbp_location_id": "accounts/123456/locations/bpg-bangkok",
        "location_name": "Bangkok Peach Group",
        "brand": "bangkok-peach",
        "address": "123 Sukhumvit Rd, Bangkok 10110",
        "city": "Bangkok",
        "phone": "+66-2-123-4567",
        "website": "https://bangkok-peach-group.com/",
        "avg_rating": 3.5,
        "total_reviews": 4,
        "photos_count": 2,
    },
]

_MOCK_REVIEWS_BY_LOCATION = {
    "accounts/123456/locations/upj-shibuya":  _MOCK_REVIEWS_UPJ,
    "accounts/123456/locations/dsc-shinjuku": _MOCK_REVIEWS_DSC,
    "accounts/123456/locations/bpg-bangkok":  _MOCK_REVIEWS_BPG,
}

_MOCK_INSIGHTS = {
    "accounts/123456/locations/upj-shibuya": {
        "views_search": 312, "views_maps": 189,
        "actions_website": 47, "actions_directions": 28, "actions_phone": 15,
        "photos_views": 203,
    },
    "accounts/123456/locations/dsc-shinjuku": {
        "views_search": 528, "views_maps": 341,
        "actions_website": 93, "actions_directions": 41, "actions_phone": 22,
        "photos_views": 451,
    },
    "accounts/123456/locations/bpg-bangkok": {
        "views_search": 744, "views_maps": 512,
        "actions_website": 128, "actions_directions": 87, "actions_phone": 56,
        "photos_views": 89,
    },
}


class MockGBPConnector(GBPConnector):
    """開発・デモ用モック。本番 API は呼び出さない。"""

    def sync_locations(self) -> list[dict]:
        return [dict(loc) for loc in _MOCK_LOCATIONS]

    def sync_reviews(self, location_id: str) -> list[dict]:
        reviews = _MOCK_REVIEWS_BY_LOCATION.get(location_id, [])
        return [dict(r) for r in reviews]

    def reply_to_review(self, location_id: str, review_id: str, reply_text: str) -> bool:
        # モック: 常に成功
        return True

    def delete_review_reply(self, location_id: str, review_id: str) -> bool:
        return True

    def sync_posts(self, location_id: str) -> list[dict]:
        now = datetime.now()
        return [
            {"gbp_post_id": f"post_{location_id[-6:]}_001",
             "post_type": "STANDARD",
             "summary": "春の新メニューが登場しました！ぜひご来店ください。",
             "state": "LIVE", "published_at": (now - timedelta(days=3)).strftime("%Y-%m-%d")},
            {"gbp_post_id": f"post_{location_id[-6:]}_002",
             "post_type": "OFFER",
             "summary": "GW限定10%オフキャンペーン実施中。5月6日まで。",
             "state": "LIVE", "published_at": (now - timedelta(days=7)).strftime("%Y-%m-%d")},
        ]

    def create_post(self, location_id: str, post_data: dict) -> dict:
        return {"gbp_post_id": f"post_new_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                **post_data, "state": "LIVE"}

    def sync_media(self, location_id: str) -> list[dict]:
        loc = next((l for l in _MOCK_LOCATIONS if l["gbp_location_id"] == location_id), None)
        count = loc["photos_count"] if loc else 0
        return [{"media_id": f"photo_{i}", "category": "EXTERIOR",
                 "url": f"https://placeholder.example.com/photo_{i}.jpg"}
                for i in range(count)]

    def upload_media(self, location_id: str, media_path: str, category: str = "EXTERIOR") -> dict:
        return {"media_id": f"photo_new_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "category": category, "url": media_path}

    def sync_insights(self, location_id: str, period_days: int = 28) -> dict:
        base = _MOCK_INSIGHTS.get(location_id, {})
        now = datetime.now()
        return {
            "period_start": (now - timedelta(days=period_days)).strftime("%Y-%m-%d"),
            "period_end": now.strftime("%Y-%m-%d"),
            **base,
        }


# ═══════════════════════════════════════════════════════
# Real Implementation — Google Business Profile API
# ═══════════════════════════════════════════════════════

class GBPRealConnector(GBPConnector):
    """
    本番用 Google Business Profile コネクタ。

    認証方法（いずれか一方を .env に設定）:
      A) OAuth2 リフレッシュトークン方式（推奨）
         GBP_CLIENT_ID, GBP_CLIENT_SECRET, GBP_REFRESH_TOKEN
      B) サービスアカウント方式
         GBP_SERVICE_ACCOUNT_JSON (JSON文字列 or ファイルパス)

    GBP_ACCOUNT_NAME: "accounts/XXXXXXXXXX" 形式のアカウント名（省略時は自動取得）
    """

    # API ベース URL
    _ACCT_URL   = "https://mybusinessaccountmanagement.googleapis.com/v1"
    _INFO_URL   = "https://mybusinessbusinessinformation.googleapis.com/v1"
    _REV_URL    = "https://mybusinessreviews.googleapis.com/v1"
    _LEGACY_URL = "https://mybusiness.googleapis.com/v4"
    _PERF_URL   = "https://businessprofileperformance.googleapis.com/v1"

    _SCOPES = ["https://www.googleapis.com/auth/business.manage"]

    def __init__(self):
        import requests as _req
        self._requests = _req
        self._creds = self._build_credentials()
        self._account_name: str = os.environ.get("GBP_ACCOUNT_NAME", "")

    # ── 認証ヘルパー ──────────────────────────────────────

    def _build_credentials(self):
        sa_json = os.environ.get("GBP_SERVICE_ACCOUNT_JSON", "")
        if sa_json:
            return self._creds_from_service_account(sa_json)
        return self._creds_from_oauth2()

    def _creds_from_service_account(self, sa_json: str):
        import json as _json
        from google.oauth2 import service_account
        from pathlib import Path
        if sa_json.strip().startswith("{"):
            info = _json.loads(sa_json)
        else:
            info = _json.loads(Path(sa_json).read_text())
        return service_account.Credentials.from_service_account_info(
            info, scopes=self._SCOPES
        )

    def _creds_from_oauth2(self):
        from google.oauth2.credentials import Credentials
        client_id     = os.environ.get("GBP_CLIENT_ID", "")
        client_secret = os.environ.get("GBP_CLIENT_SECRET", "")
        refresh_token = os.environ.get("GBP_REFRESH_TOKEN", "")
        if not (client_id and client_secret and refresh_token):
            raise ValueError(
                "GBP認証情報が不足しています。"
                "GBP_CLIENT_ID / GBP_CLIENT_SECRET / GBP_REFRESH_TOKEN を設定してください。"
            )
        return Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=client_id,
            client_secret=client_secret,
            scopes=self._SCOPES,
        )

    def _auth_header(self) -> dict:
        from google.auth.transport.requests import Request
        if not self._creds.valid:
            self._creds.refresh(Request())
        return {"Authorization": f"Bearer {self._creds.token}"}

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = self._requests.get(url, headers=self._auth_header(),
                               params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, url: str, body: dict) -> dict:
        r = self._requests.post(url, headers={**self._auth_header(),
                                              "Content-Type": "application/json"},
                                json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _put(self, url: str, body: dict) -> dict:
        r = self._requests.put(url, headers={**self._auth_header(),
                                             "Content-Type": "application/json"},
                               json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    def _delete(self, url: str) -> bool:
        r = self._requests.delete(url, headers=self._auth_header(), timeout=30)
        return r.status_code in (200, 204)

    # ── アカウント取得 ─────────────────────────────────────

    def _get_account_name(self) -> str:
        if self._account_name:
            return self._account_name
        data = self._get(f"{self._ACCT_URL}/accounts")
        accounts = data.get("accounts", [])
        if not accounts:
            raise RuntimeError("GBP アカウントが見つかりません")
        self._account_name = accounts[0]["name"]
        return self._account_name

    # ── 拠点 ──────────────────────────────────────────────

    def sync_locations(self) -> list[dict]:
        account = self._get_account_name()
        read_mask = (
            "name,title,storefrontAddress,phoneNumbers,"
            "websiteUri,regularHours,metadata"
        )
        data = self._get(
            f"{self._INFO_URL}/{account}/locations",
            params={"readMask": read_mask, "pageSize": 100},
        )
        locations = data.get("locations", [])
        return [self._normalize_location(loc) for loc in locations]

    def _normalize_location(self, raw: dict) -> dict:
        addr = raw.get("storefrontAddress", {})
        phones = raw.get("phoneNumbers", {})
        meta  = raw.get("metadata", {})
        return {
            "gbp_location_id": raw.get("name", ""),
            "location_name":   raw.get("title", ""),
            "address":         ", ".join(addr.get("addressLines", [])),
            "city":            addr.get("locality", ""),
            "phone":           phones.get("primaryPhone", ""),
            "website":         raw.get("websiteUri", ""),
            "avg_rating":      None,
        }

    # ── レビュー ──────────────────────────────────────────

    def sync_reviews(self, location_id: str) -> list[dict]:
        data = self._get(
            f"{self._REV_URL}/{location_id}/reviews",
            params={"pageSize": 50},
        )
        reviews = data.get("reviews", [])
        result = []
        for r in reviews:
            reviewer = r.get("reviewer", {})
            reply    = r.get("reviewReply", {})
            rating_map = {
                "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5
            }
            result.append({
                "gbp_review_id":   r.get("reviewId", ""),
                "reviewer_name":   reviewer.get("displayName", ""),
                "reviewer_photo_url": reviewer.get("profilePhotoUrl", ""),
                "rating":          rating_map.get(r.get("starRating", "THREE"), 3),
                "comment":         r.get("comment", ""),
                "reply":           reply.get("comment", "") if reply else "",
                "reply_updated_at": reply.get("updateTime", "") if reply else "",
                "status":          "answered" if reply else "unanswered",
                "created_at":      r.get("createTime", ""),
                "raw_name":        r.get("name", ""),
            })
        return result

    def reply_to_review(self, location_id: str, review_id: str, reply_text: str) -> bool:
        url = f"{self._REV_URL}/{location_id}/reviews/{review_id}/reply"
        try:
            self._put(url, {"comment": reply_text})
            return True
        except Exception:
            return False

    def delete_review_reply(self, location_id: str, review_id: str) -> bool:
        url = f"{self._REV_URL}/{location_id}/reviews/{review_id}/reply"
        return self._delete(url)

    # ── 投稿 ──────────────────────────────────────────────

    def sync_posts(self, location_id: str) -> list[dict]:
        data = self._get(f"{self._LEGACY_URL}/{location_id}/localPosts")
        posts = data.get("localPosts", [])
        result = []
        for p in posts:
            result.append({
                "gbp_post_id":   p.get("name", "").split("/")[-1],
                "post_type":     p.get("topicType", "STANDARD"),
                "summary":       p.get("summary", ""),
                "state":         p.get("state", ""),
                "published_at":  p.get("createTime", "")[:10],
            })
        return result

    def create_post(self, location_id: str, post_data: dict) -> dict:
        """
        post_data 例:
          {"topicType": "STANDARD", "summary": "本文", "callToAction": {"actionType": "LEARN_MORE", "url": "..."}}
        """
        raw = self._post(f"{self._LEGACY_URL}/{location_id}/localPosts", post_data)
        return {
            "gbp_post_id": raw.get("name", "").split("/")[-1],
            **post_data,
            "state": raw.get("state", "LIVE"),
        }

    # ── メディア ──────────────────────────────────────────

    def sync_media(self, location_id: str) -> list[dict]:
        data = self._get(f"{self._LEGACY_URL}/{location_id}/media")
        items = data.get("mediaItems", [])
        return [
            {
                "media_id": m.get("name", "").split("/")[-1],
                "category": m.get("mediaFormat", "PHOTO"),
                "url":      m.get("googleUrl", m.get("sourceUrl", "")),
            }
            for m in items
        ]

    def upload_media(self, location_id: str, media_path: str, category: str = "EXTERIOR") -> dict:
        body = {
            "mediaFormat": "PHOTO",
            "locationAssociation": {"category": category},
            "sourceUrl": media_path,
        }
        raw = self._post(f"{self._LEGACY_URL}/{location_id}/media", body)
        return {
            "media_id": raw.get("name", "").split("/")[-1],
            "category": category,
            "url":      media_path,
        }

    # ── インサイト ────────────────────────────────────────

    def sync_insights(self, location_id: str, period_days: int = 28) -> dict:
        from datetime import date, timedelta
        end   = date.today()
        start = end - timedelta(days=period_days)

        metrics = [
            "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
            "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
            "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
            "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
            "CALL_CLICKS",
            "WEBSITE_CLICKS",
            "BUSINESS_DIRECTION_REQUESTS",
        ]
        body = {
            "dailyMetrics": metrics,
            "dailyRange": {
                "startDate": {"year": start.year, "month": start.month, "day": start.day},
                "endDate":   {"year": end.year,   "month": end.month,   "day": end.day},
            },
        }
        try:
            raw = self._post(
                f"{self._PERF_URL}/{location_id}:fetchMultiDailyMetricsTimeSeries",
                body,
            )
            series = raw.get("multiDailyMetricTimeSeries", [])
            totals: dict[str, int] = {}
            for s in series:
                metric_name = s.get("dailyMetric", "")
                for ts in s.get("timeSeries", {}).get("datedValues", []):
                    totals[metric_name] = totals.get(metric_name, 0) + int(ts.get("value", 0))
        except Exception:
            totals = {}

        return {
            "period_start": str(start),
            "period_end":   str(end),
            "views_maps":   (totals.get("BUSINESS_IMPRESSIONS_DESKTOP_MAPS", 0)
                             + totals.get("BUSINESS_IMPRESSIONS_MOBILE_MAPS", 0)),
            "views_search": (totals.get("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", 0)
                             + totals.get("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", 0)),
            "actions_phone":      totals.get("CALL_CLICKS", 0),
            "actions_website":    totals.get("WEBSITE_CLICKS", 0),
            "actions_directions": totals.get("BUSINESS_DIRECTION_REQUESTS", 0),
        }


# ═══════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════

def get_connector() -> GBPConnector:
    """
    GBP_CLIENT_ID または GBP_SERVICE_ACCOUNT_JSON が設定されていれば本番コネクタを返す。
    未設定の場合はモックを返す。

    本番設定に必要な環境変数（いずれか）:
      A) GBP_CLIENT_ID + GBP_CLIENT_SECRET + GBP_REFRESH_TOKEN
      B) GBP_SERVICE_ACCOUNT_JSON
    オプション: GBP_ACCOUNT_NAME (例: accounts/123456789)
    """
    if os.environ.get("GBP_CLIENT_ID") or os.environ.get("GBP_SERVICE_ACCOUNT_JSON"):
        return GBPRealConnector()
    return MockGBPConnector()
