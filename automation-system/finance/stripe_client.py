"""
Stripe アナリティクスモジュール
MRR・売上・チャーン率・顧客数を取得する
"""

import os
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

log = logging.getLogger(__name__)


def _stripe():
    import stripe as _stripe_lib
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise ValueError("STRIPE_SECRET_KEY 未設定")
    _stripe_lib.api_key = key
    return _stripe_lib


def get_mrr() -> dict:
    """アクティブなサブスクリプションからMRRを計算"""
    try:
        stripe = _stripe()
        subs = stripe.Subscription.list(status="active", limit=100, expand=["data.plan"])
        mrr = 0
        count = 0
        for sub in subs.auto_paging_iter():
            for item in sub["items"]["data"]:
                price = item.get("price", {})
                amount = price.get("unit_amount", 0) or 0
                interval = price.get("recurring", {}).get("interval", "month")
                if interval == "year":
                    amount = amount // 12
                mrr += amount
                count += 1
        return {
            "status":      "ok",
            "mrr_jpy":     mrr,
            "mrr_display": f"¥{mrr:,}",
            "sub_count":   count,
        }
    except ValueError as e:
        return {"status": "unset"}
    except Exception as e:
        log.error(f"Stripe MRR エラー: {e}")
        return {"status": "error", "error_msg": str(e)[:80]}


def get_revenue_series(days: int = 90) -> dict:
    """日別売上（過去N日間）"""
    try:
        stripe = _stripe()
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
        charges = stripe.Charge.list(
            created={"gte": since},
            limit=100,
        )
        daily: dict[str, int] = defaultdict(int)
        for ch in charges.auto_paging_iter():
            if ch.get("paid") and not ch.get("refunded"):
                d = datetime.fromtimestamp(ch["created"], tz=timezone.utc).strftime("%Y-%m-%d")
                daily[d] += ch.get("amount", 0)

        dates = sorted(daily.keys())
        values = [daily[d] for d in dates]
        return {"status": "ok", "dates": dates, "values": values}
    except ValueError:
        return {"status": "unset", "dates": [], "values": []}
    except Exception as e:
        log.error(f"Stripe 売上グラフ エラー: {e}")
        return {"status": "error", "dates": [], "values": []}


def get_churn_stats(days: int = 30) -> dict:
    """チャーン率・解約数（過去N日間）"""
    try:
        stripe = _stripe()
        since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

        canceled = list(stripe.Subscription.list(
            status="canceled",
            created={"gte": since},
            limit=100,
        ).auto_paging_iter())

        active_subs = list(stripe.Subscription.list(status="active", limit=100).auto_paging_iter())
        active_count = len(active_subs)

        churn_count = len(canceled)
        base = active_count + churn_count
        churn_rate = round(churn_count / base * 100, 1) if base > 0 else 0.0

        return {
            "status":       "ok",
            "churn_count":  churn_count,
            "churn_rate":   churn_rate,
            "active_count": active_count,
            "period_days":  days,
        }
    except ValueError:
        return {"status": "unset"}
    except Exception as e:
        log.error(f"Stripe チャーン エラー: {e}")
        return {"status": "error", "error_msg": str(e)[:80]}


def get_summary() -> dict:
    """MRR + チャーン + 直近30日売上を一括取得"""
    mrr    = get_mrr()
    churn  = get_churn_stats(30)
    series = get_revenue_series(90)

    total_revenue_30d = 0
    if series["status"] == "ok" and series["dates"]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        total_revenue_30d = sum(
            v for d, v in zip(series["dates"], series["values"]) if d >= cutoff
        )

    return {
        "mrr":              mrr,
        "churn":            churn,
        "series":           series,
        "total_revenue_30d": total_revenue_30d,
    }
