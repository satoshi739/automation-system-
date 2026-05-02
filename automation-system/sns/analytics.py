"""
Google Analytics 4 + Search Console モジュール

必要なもの:
- Google Cloud Console でサービスアカウント作成
- analytics.readonly / webmasters.readonly 権限を付与
- credentials.json を automation-system/ に配置
- GA4 プロパティID / Search Console サイトURL を .env に設定

pip install google-analytics-data google-auth google-api-python-client
"""

import os, logging
from datetime import date, timedelta
log = logging.getLogger(__name__)


# ─── Google Analytics 4 ───────────────────────────────────

class GA4Client:
    def __init__(self, property_id_env: str):
        self.property_id = os.environ.get(property_id_env,"")

    def _client(self):
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account
        from pathlib import Path
        import json, base64
        creds_path = Path(__file__).parent.parent / "credentials.json"
        b64 = os.environ.get("GOOGLE_CREDENTIALS_BASE64", "")
        if b64 and not creds_path.exists():
            creds_info = json.loads(base64.b64decode(b64))
            creds = service_account.Credentials.from_service_account_info(
                creds_info, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=["https://www.googleapis.com/auth/analytics.readonly"]
            )
        return BetaAnalyticsDataClient(credentials=creds)

    def get_overview(self, days: int = 28) -> dict:
        """直近N日間のセッション・PV・ユーザー数を取得"""
        if not self.property_id:
            return {"error":"GA4_PROPERTY_ID未設定","sessions":0,"pageviews":0,"users":0}
        try:
            from google.analytics.data_v1beta.types import (
                RunReportRequest, DateRange, Metric
            )
            req = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
                metrics=[
                    Metric(name="sessions"),
                    Metric(name="screenPageViews"),
                    Metric(name="activeUsers"),
                    Metric(name="averageSessionDuration"),
                    Metric(name="bounceRate"),
                ]
            )
            r = self._client().run_report(req)
            row = r.rows[0].metric_values if r.rows else [None]*5
            return {
                "sessions":  int(row[0].value) if row[0] else 0,
                "pageviews":  int(row[1].value) if row[1] else 0,
                "users":      int(row[2].value) if row[2] else 0,
                "avg_duration": round(float(row[3].value)/60, 1) if row[3] else 0,
                "bounce_rate": round(float(row[4].value)*100, 1) if row[4] else 0,
                "period_days": days,
            }
        except Exception as e:
            log.error(f"GA4エラー: {e}")
            return {"error":str(e),"sessions":0,"pageviews":0,"users":0}

    def get_top_pages(self, days: int = 28, limit: int = 10) -> list[dict]:
        """アクセスの多いページTOP10"""
        if not self.property_id:
            return []
        try:
            from google.analytics.data_v1beta.types import (
                RunReportRequest, DateRange, Dimension, Metric, OrderBy
            )
            req = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
                dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
                metrics=[Metric(name="screenPageViews"), Metric(name="activeUsers")],
                order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="screenPageViews"), desc=True)],
                limit=limit,
            )
            r = self._client().run_report(req)
            return [
                {
                    "path":  row.dimension_values[0].value,
                    "title": row.dimension_values[1].value[:40],
                    "views": int(row.metric_values[0].value),
                    "users": int(row.metric_values[1].value),
                }
                for row in r.rows
            ]
        except Exception as e:
            log.error(f"GA4 top pages エラー: {e}")
            return []

    def get_daily_series(self, days: int = 28) -> dict:
        """日別セッション数（グラフ用）"""
        if not self.property_id:
            return {"dates":[],"sessions":[]}
        try:
            from google.analytics.data_v1beta.types import (
                RunReportRequest, DateRange, Dimension, Metric, OrderBy
            )
            req = RunReportRequest(
                property=f"properties/{self.property_id}",
                date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
                dimensions=[Dimension(name="date")],
                metrics=[Metric(name="sessions")],
                order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
            )
            r = self._client().run_report(req)
            dates, sessions = [], []
            for row in r.rows:
                d = row.dimension_values[0].value  # YYYYMMDD
                dates.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
                sessions.append(int(row.metric_values[0].value))
            return {"dates": dates, "sessions": sessions}
        except Exception as e:
            log.error(f"GA4 daily series エラー: {e}")
            return {"dates":[],"sessions":[]}


# ─── Google Search Console ───────────────────────────────

class SearchConsoleClient:
    def __init__(self, site_url_env: str):
        self.site_url = os.environ.get(site_url_env,"")

    def _service(self):
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        from pathlib import Path
        import json, base64
        creds_path = Path(__file__).parent.parent / "credentials.json"
        b64 = os.environ.get("GOOGLE_CREDENTIALS_BASE64", "")
        if b64 and not creds_path.exists():
            creds_info = json.loads(base64.b64decode(b64))
            creds = service_account.Credentials.from_service_account_info(
                creds_info, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
            )
        else:
            creds = service_account.Credentials.from_service_account_file(
                str(creds_path), scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
            )
        return build("searchconsole","v1",credentials=creds)

    def get_overview(self, days: int = 28) -> dict:
        """クリック数・表示回数・CTR・平均掲載順位"""
        if not self.site_url:
            return {"error":"サイトURL未設定","clicks":0,"impressions":0,"ctr":0,"position":0}
        try:
            end = date.today()
            start = end - timedelta(days=days)
            body = {
                "startDate": str(start), "endDate": str(end),
                "searchType": "web",
            }
            r = self._service().searchanalytics().query(
                siteUrl=self.site_url, body=body
            ).execute()
            row = r.get("rows",[{}])[0] if r.get("rows") else {}
            return {
                "clicks":      int(row.get("clicks",0)),
                "impressions": int(row.get("impressions",0)),
                "ctr":         round(row.get("ctr",0)*100, 2),
                "position":    round(row.get("position",0), 1),
                "period_days": days,
            }
        except Exception as e:
            log.error(f"Search Console エラー: {e}")
            return {"error":str(e),"clicks":0,"impressions":0,"ctr":0,"position":0}

    def get_top_queries(self, days: int = 28, limit: int = 10) -> list[dict]:
        """検索クエリTOP10"""
        if not self.site_url:
            return []
        try:
            end = date.today()
            start = end - timedelta(days=days)
            body = {
                "startDate": str(start), "endDate": str(end),
                "searchType": "web",
                "dimensions": ["query"],
                "rowLimit": limit,
                "orderBy": [{"fieldName":"clicks","sortOrder":"DESCENDING"}],
            }
            r = self._service().searchanalytics().query(
                siteUrl=self.site_url, body=body
            ).execute()
            return [
                {
                    "query":       row["keys"][0],
                    "clicks":      int(row.get("clicks",0)),
                    "impressions": int(row.get("impressions",0)),
                    "ctr":         round(row.get("ctr",0)*100,1),
                    "position":    round(row.get("position",0),1),
                }
                for row in r.get("rows",[])
            ]
        except Exception as e:
            log.error(f"Search Console top queries エラー: {e}")
            return []


# ─── Instagram インサイト ─────────────────────────────────────

BRAND_META_ENV: dict[str, str] = {
    "upjapan":       "UPJAPAN",
    "dsc-marketing": "DSC_MARKETING",
    "cashflowsupport": "CASHFLOWSUPPORT",
    "bangkok-peach": "BANGKOK_PEACH",
    "satoshi":       "SATOSHI",
    "satoshi-blog":  "SATOSHI_BLOG",
}


class InstagramInsights:
    BASE = "https://graph.facebook.com/v19.0"

    def __init__(self, brand: str):
        prefix = BRAND_META_ENV.get(brand, brand.upper().replace("-", "_"))
        self.token      = os.environ.get(f"{prefix}_META_ACCESS_TOKEN", "")
        self.account_id = os.environ.get(f"{prefix}_INSTAGRAM_ACCOUNT_ID", "")
        self.enabled    = bool(self.token and self.account_id)

    def _get(self, path: str, params: dict = None) -> dict:
        import requests
        p = {"access_token": self.token, **(params or {})}
        r = requests.get(f"{self.BASE}/{path}", params=p, timeout=15)
        r.raise_for_status()
        return r.json()

    def get_account_stats(self) -> dict:
        """フォロワー数・投稿数・エンゲージメントを返す"""
        if not self.enabled:
            return {"status": "unset"}
        try:
            d = self._get(self.account_id, {"fields": "followers_count,media_count,name,username"})
            return {
                "status":          "ok",
                "followers":       d.get("followers_count", 0),
                "media_count":     d.get("media_count", 0),
                "username":        d.get("username", ""),
            }
        except Exception as e:
            err = str(e)
            if "190" in err or "token" in err.lower():
                return {"status": "token_expired"}
            return {"status": "error", "error_msg": err[:80]}

    def get_recent_insights(self, limit: int = 10) -> dict:
        """直近N件の投稿のいいね・コメント・リーチ合計を返す"""
        if not self.enabled:
            return {"status": "unset", "total_likes": 0, "total_comments": 0, "avg_reach": 0, "posts": []}
        try:
            media = self._get(
                f"{self.account_id}/media",
                {"fields": "id,like_count,comments_count,timestamp,media_type", "limit": limit}
            )
            items = media.get("data", [])
            total_likes    = sum(i.get("like_count", 0) for i in items)
            total_comments = sum(i.get("comments_count", 0) for i in items)
            return {
                "status":         "ok",
                "total_likes":    total_likes,
                "total_comments": total_comments,
                "post_count":     len(items),
                "avg_likes":      round(total_likes / max(len(items), 1), 1),
            }
        except Exception as e:
            err = str(e)
            if "190" in err or "token" in err.lower():
                return {"status": "token_expired", "total_likes": 0, "total_comments": 0, "avg_reach": 0}
            return {"status": "error", "error_msg": err[:80], "total_likes": 0, "total_comments": 0}
