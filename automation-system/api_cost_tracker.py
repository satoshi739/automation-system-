"""
APIコスト追跡・残高監視モジュール。

残高の計算方法:
  Anthropic に公開残高取得 API は存在しないため、手動クレジット追跡方式を採用。
  .env に購入済みクレジット総額を設定 → agent_runs の実績消費を引き算 → 残高推計。

.env に以下を追加してください:
  ANTHROPIC_CREDIT_TOTAL_USD=50      # チャージした合計金額（ドル）
  ANTHROPIC_CREDIT_WARN_USD=10       # この残高を下回ったら LINE 警告（デフォルト $10）
  ANTHROPIC_CREDIT_CRIT_USD=3        # この残高を下回ったら LINE 緊急通知（デフォルト $3）

重複通知防止:
  同じ閾値違反を1日1回までに制限（logs/.balance_alert_<date>.sent フラグ）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

_LOGS_DIR = Path(__file__).parent / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

# ── モデル別料金（$ per 1M tokens）──────────────────────────────
MODEL_PRICING: dict[str, dict] = {
    "claude-sonnet-4-6":         {"input": 3.00,  "output": 15.00},
    "claude-sonnet-4-5":         {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.25,  "output": 1.25},
    "claude-haiku-4-5":          {"input": 0.25,  "output": 1.25},
    "claude-opus-4-7":           {"input": 15.00, "output": 75.00},
}
DEFAULT_PRICING = {"input": 3.00, "output": 15.00}

# ── エージェント→モデルマッピング ──────────────────────────────
AGENT_MODEL_MAP: dict[str, str] = {
    "agent-content-upj":  "claude-haiku-4-5-20251001",
    "agent-content-dsc":  "claude-haiku-4-5-20251001",
    "agent-content-cfj":  "claude-sonnet-4-6",
    "agent-content-bpg":  "claude-haiku-4-5-20251001",
    "agent-blog":         "claude-haiku-4-5-20251001",
    "agent-sales":        "claude-haiku-4-5-20251001",
    "agent-analytics":    "claude-sonnet-4-6",
    "agent-ops":          "claude-haiku-4-5-20251001",
    "ai-ceo":             "claude-sonnet-4-6",
}


def _get_pricing(agent_id: str) -> dict:
    model = AGENT_MODEL_MAP.get(agent_id, "claude-sonnet-4-6")
    return MODEL_PRICING.get(model, DEFAULT_PRICING)


# ── コストサマリー ────────────────────────────────────────────

def get_cost_summary(days: int = 30) -> dict:
    """
    過去N日間のAPIコストサマリーを返す。
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import org_database as db

    since = (datetime.now() - timedelta(days=days)).isoformat()

    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT agent_id,
                   COUNT(*) as runs,
                   SUM(COALESCE(tokens_used, 0)) as tokens,
                   SUM(COALESCE(cost_usd, 0)) as cost_usd
            FROM agent_runs
            WHERE started_at >= ?
            GROUP BY agent_id
            ORDER BY cost_usd DESC
            """,
            (since,),
        ).fetchall()

    by_agent = []
    total_tokens = 0
    total_cost = 0.0

    for r in rows:
        agent_id = r["agent_id"]
        tokens   = int(r["tokens"] or 0)
        cost     = float(r["cost_usd"] or 0.0)

        if cost == 0.0 and tokens > 0:
            pricing = _get_pricing(agent_id)
            cost = (tokens * 0.70 / 1_000_000 * pricing["input"] +
                    tokens * 0.30 / 1_000_000 * pricing["output"])

        model = AGENT_MODEL_MAP.get(agent_id, "claude-sonnet-4-6")
        by_agent.append({
            "agent_id": agent_id,
            "runs":     int(r["runs"]),
            "tokens":   tokens,
            "cost_usd": round(cost, 4),
            "model":    model,
        })
        total_tokens += tokens
        total_cost   += cost

    daily_avg   = total_cost / days if days > 0 else 0.0
    monthly_est = daily_avg * 30

    return {
        "period_days":     days,
        "total_tokens":    total_tokens,
        "total_cost_usd":  round(total_cost, 4),
        "by_agent":        by_agent,
        "daily_avg_usd":   round(daily_avg, 4),
        "monthly_est_usd": round(monthly_est, 2),
        "generated_at":    datetime.now().isoformat(),
    }


def get_cumulative_spend() -> float:
    """agent_runs の全履歴から累計消費額を返す（period制限なし）"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    import org_database as db

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT agent_id, SUM(COALESCE(tokens_used,0)) as tokens,"
            " SUM(COALESCE(cost_usd,0)) as cost_usd FROM agent_runs GROUP BY agent_id"
        ).fetchall()

    total = 0.0
    for r in rows:
        cost = float(r["cost_usd"] or 0.0)
        if cost == 0.0:
            tokens = int(r["tokens"] or 0)
            if tokens > 0:
                pricing = _get_pricing(r["agent_id"])
                cost = (tokens * 0.70 / 1_000_000 * pricing["input"] +
                        tokens * 0.30 / 1_000_000 * pricing["output"])
        total += cost
    return round(total, 4)


# ── 残高計算（手動クレジット追跡）────────────────────────────

def get_balance_info() -> dict:
    """
    残高情報を返す。

    Returns:
        {
          "credit_total":   float,   # .envに設定した購入総額（0=未設定）
          "cumulative_spend": float, # 全時累計消費
          "estimated_balance": float | None,  # 推計残高（未設定時はNone）
          "warn_threshold": float,
          "crit_threshold": float,
          "status": "ok" | "warn" | "critical" | "unknown",
          "tracking_enabled": bool,
        }
    """
    def _env_float(key: str, default: float) -> float:
        try:
            return float(os.environ.get(key, "") or default)
        except (ValueError, TypeError):
            return default
    credit_total = _env_float("ANTHROPIC_CREDIT_TOTAL_USD", 0.0)
    warn_thr     = _env_float("ANTHROPIC_CREDIT_WARN_USD", 10.0)
    crit_thr     = _env_float("ANTHROPIC_CREDIT_CRIT_USD", 3.0)

    try:
        spent = get_cumulative_spend()
    except Exception as e:
        log.warning("累計消費取得失敗: %s", e)
        spent = 0.0

    if credit_total <= 0:
        return {
            "credit_total":       0.0,
            "cumulative_spend":   spent,
            "estimated_balance":  None,
            "warn_threshold":     warn_thr,
            "crit_threshold":     crit_thr,
            "status":             "unknown",
            "tracking_enabled":   False,
        }

    balance = round(credit_total - spent, 4)
    if balance <= crit_thr:
        status = "critical"
    elif balance <= warn_thr:
        status = "warn"
    else:
        status = "ok"

    return {
        "credit_total":       credit_total,
        "cumulative_spend":   spent,
        "estimated_balance":  balance,
        "warn_threshold":     warn_thr,
        "crit_threshold":     crit_thr,
        "status":             status,
        "tracking_enabled":   True,
    }


# ── 重複通知防止 ──────────────────────────────────────────────

def _alert_sent_today(level: str) -> bool:
    flag = _LOGS_DIR / f".balance_alert_{level}_{datetime.now().strftime('%Y%m%d')}.sent"
    return flag.exists()


def _mark_alert_sent(level: str) -> None:
    flag = _LOGS_DIR / f".balance_alert_{level}_{datetime.now().strftime('%Y%m%d')}.sent"
    flag.touch()
    # 古いフラグを掃除（7日以上前）
    for f in _LOGS_DIR.glob(".balance_alert_*.sent"):
        try:
            date_str = f.stem.rsplit("_", 1)[-1]
            if (datetime.now() - datetime.strptime(date_str, "%Y%m%d")).days > 7:
                f.unlink(missing_ok=True)
        except Exception:
            pass


# ── メイン：チェック＆アラート ──────────────────────────────

def check_balance_and_alert() -> dict:
    """
    残高チェックを実行し、閾値を下回ったら LINE に通知する。
    毎朝 scheduler から呼ばれる想定。重複通知は1日1回に制限。

    Returns:
        {"balance_info": dict, "alerted": bool, "skip_reason": str}
    """
    info = get_balance_info()
    status = info["status"]

    if not info["tracking_enabled"]:
        log.info(
            "残高チェックスキップ: ANTHROPIC_CREDIT_TOTAL_USD が未設定。"
            " .env に設定するとチェックが有効になります。"
        )
        return {"balance_info": info, "alerted": False, "skip_reason": "tracking_disabled"}

    balance = info["estimated_balance"]
    log.info(
        "Anthropic残高チェック: $%.2f 残 (総額$%.2f - 消費$%.4f) 状態=%s",
        balance, info["credit_total"], info["cumulative_spend"], status
    )

    if status == "ok":
        return {"balance_info": info, "alerted": False, "skip_reason": "ok"}

    # 重複通知チェック
    if _alert_sent_today(status):
        log.info("残高アラート重複スキップ（本日送信済み: %s）", status)
        return {"balance_info": info, "alerted": False, "skip_reason": "already_sent_today"}

    alerted = _send_balance_alert(info)
    if alerted:
        _mark_alert_sent(status)

    return {"balance_info": info, "alerted": alerted, "skip_reason": ""}


def _send_balance_alert(info: dict) -> bool:
    """LINE でコスト警告を送信"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from sns.line_api import LINEMessenger
        owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if not owner_id:
            log.warning("OWNER_LINE_USER_ID未設定、LINE通知をスキップ")
            return False

        status  = info["status"]
        balance = info["estimated_balance"]
        spent   = info["cumulative_spend"]
        total   = info["credit_total"]
        warn    = info["warn_threshold"]
        crit    = info["crit_threshold"]

        emoji   = "🚨" if status == "critical" else "⚠️"
        label   = "【緊急】残高わずか" if status == "critical" else "【警告】残高少なめ"
        thr_msg = f"緊急閾値 ${crit:.0f}" if status == "critical" else f"警告閾値 ${warn:.0f}"

        msg = (
            f"{emoji} Anthropic 残高{label}\n\n"
            f"推計残高: ${balance:.2f}\n"
            f"購入総額: ${total:.2f}\n"
            f"累計消費: ${spent:.4f}\n"
            f"({thr_msg} を下回りました)\n\n"
            f"チャージ: https://console.anthropic.com/settings/billing"
        )
        messenger = LINEMessenger()
        ok = messenger.push(owner_id, msg)
        if ok:
            log.info("残高アラートLINE送信完了: status=%s balance=$%.2f", status, balance)
        return ok
    except Exception as e:
        log.warning("残高アラートLINE送信失敗: %s", e)
        return False


# ── レポートテキスト生成 ──────────────────────────────────────

def generate_report_text(days: int = 30) -> str:
    """人間が読めるコストレポートを生成"""
    s    = get_cost_summary(days)
    info = get_balance_info()

    balance_section = []
    if info["tracking_enabled"]:
        balance_section = [
            "",
            "## 残高（推計）",
            f"| 項目 | 値 |",
            f"|------|-----|",
            f"| 購入総額 | ${info['credit_total']:.2f} |",
            f"| 累計消費 | ${info['cumulative_spend']:.4f} |",
            f"| **推計残高** | **${info['estimated_balance']:.2f}** |",
            f"| 状態 | {info['status'].upper()} |",
            f"| 警告閾値 | ${info['warn_threshold']:.0f} 以下でLINE通知 |",
            f"| 緊急閾値 | ${info['crit_threshold']:.0f} 以下でLINE緊急通知 |",
        ]
    else:
        balance_section = [
            "",
            "## 残高",
            "ANTHROPIC_CREDIT_TOTAL_USD が未設定のため残高追跡は無効です。",
            ".env に `ANTHROPIC_CREDIT_TOTAL_USD=<購入金額>` を追加してください。",
        ]

    lines = [
        f"# APIコストレポート（過去{days}日）",
        f"生成日時: {s['generated_at'][:19]}",
        "",
        "## コストサマリー",
        "| 項目 | 値 |",
        "|------|-----|",
        f"| 総トークン数 | {s['total_tokens']:,} |",
        f"| 総コスト | ${s['total_cost_usd']:.4f} (¥{s['total_cost_usd']*150:.0f}) |",
        f"| 1日平均 | ${s['daily_avg_usd']:.4f} |",
        f"| 月次推計 | ${s['monthly_est_usd']:.2f} (¥{s['monthly_est_usd']*150:.0f}) |",
    ] + balance_section + [
        "",
        "## エージェント別コスト",
        "| エージェント | モデル | 実行数 | トークン | コスト |",
        "|------------|--------|--------|---------|--------|",
    ]

    for a in s["by_agent"]:
        model_short = a["model"].replace("claude-", "").replace("-20251001", "")
        lines.append(
            f"| {a['agent_id']} | {model_short} | {a['runs']}回 | {a['tokens']:,} | ${a['cost_usd']:.4f} |"
        )

    lines += [
        "",
        "## Batch API 化候補（50%コスト削減）",
        "| ジョブ | 現在 | 改善後 |",
        "|--------|------|--------|",
        "| agent-content-* | リアルタイム | Batch API 化可能 |",
        "| agent-blog | リアルタイム | Batch API 化可能 |",
        "| video/script_generator.py | リアルタイム | Batch API 化可能 |",
        "| agent-analytics 週次分析 | リアルタイム | Batch API 化可能 |",
        "",
        "## プロンプトキャッシュ導入済み",
        "- ceo_executor.py → cache_control 適用済み",
        "- video/script_generator.py → cache_control 適用済み",
    ]

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    logging.basicConfig(level=logging.INFO)

    info = get_balance_info()
    print(f"残高情報: {info}")

    result = check_balance_and_alert()
    print(f"チェック結果: {result}")

    print()
    print(generate_report_text())
