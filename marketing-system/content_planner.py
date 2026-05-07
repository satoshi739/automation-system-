from __future__ import annotations

"""
週次コンテンツプランナー
毎週日曜 20:00 JST に翌週7日分のトピック+キャプション+ハッシュタグを
Claude API で一括生成して content_queue/weekly/ に保存する。
"""

import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))
from utils import claude_resp_text, atomic_yaml_write
from sns.line_api import LINEMessenger

load_dotenv(Path(__file__).parent.parent / "automation-system" / ".env")

_ROOT = Path(__file__).parent
WEEKLY_DIR = Path(__file__).parent.parent / "automation-system" / "content_queue" / "weekly"

PLATFORM_BY_WEEKDAY = {
    0: "instagram",
    1: "threads",
    2: "twitter",
    3: "instagram",
    4: "threads",
    5: "twitter",
    6: "instagram",
}

MODEL = "claude-haiku-4-5-20251001"  # 週次バッチは高速・低コストのHaikuで十分
MAX_TOKENS = 2048

logger = logging.getLogger(__name__)


def _client() -> anthropic.Anthropic | None:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        logger.warning("ANTHROPIC_API_KEY 未設定 — スケルトンのみ生成します")
        return None
    return anthropic.Anthropic(api_key=key)


def _next_monday() -> date:
    today = date.today()
    days_ahead = 7 - today.weekday()
    return today + timedelta(days=days_ahead)


def _generate_week_topics(monday: date, brand: str = "satoshi") -> list[dict]:
    """Claude に7日分のトピック+キャプション+ハッシュタグを一括生成させる"""
    client = _client()
    if client is None:
        return []

    days = [
        {
            "date": (monday + timedelta(days=i)).isoformat(),
            "platform": PLATFORM_BY_WEEKDAY[(monday + timedelta(days=i)).weekday()],
        }
        for i in range(7)
    ]

    prompt = f"""あなたは日本語SNSコンテンツの専門家です。
ブランド「{brand}」の来週（{monday.isoformat()}〜）SNS投稿7本分を生成してください。

各投稿は以下の条件を守ること:
- 副業・自動化・ビジネス効率化 に関連するトピック
- プラットフォームごとに適したトーン（Instagram: ビジュアル訴求、Threads: 会話調、Twitter: 短く刺さる）
- 禁止: 誇大表現・数値保証・「必ず稼げる」系の表現
- ハッシュタグは5〜8個、乱発しない

対象日とプラットフォーム:
{json.dumps(days, ensure_ascii=False, indent=2)}

以下のJSON形式で出力してください（コードフェンスなし、純粋なJSONのみ）:
[
  {{
    "date": "YYYY-MM-DD",
    "platform": "instagram",
    "topic": "投稿トピック（1行）",
    "caption": "投稿キャプション（200字以内）",
    "hashtags": ["#タグ1", "#タグ2"]
  }}
]"""

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = claude_resp_text(resp)
        return _parse_topics_json(raw)
    except Exception as e:
        logger.error(f"Claude API エラー: {e}")
        return []


def _parse_topics_json(raw: str) -> list[dict]:
    """Claude のレスポンスから JSON 配列を抽出する（フォールバック付き）"""
    # コードフェンス除去
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:]
            if candidate.startswith("["):
                raw = candidate
                break

    # 直接パース
    try:
        return json.loads(raw.strip())
    except Exception:
        pass

    # [...] ブロックを抽出して再試行
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass

    logger.warning("JSON パース失敗 — スケルトン生成にフォールバック")
    return []


def run() -> None:
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    monday = _next_monday()
    logger.info(f"翌週分を生成: {monday.isoformat()}〜")

    # AI でトピック+キャプションを生成
    ai_topics: dict[str, dict] = {}
    generated_topics = _generate_week_topics(monday)
    for item in generated_topics:
        if isinstance(item, dict) and "date" in item:
            ai_topics[item["date"]] = item

    generated: list[str] = []

    for i in range(7):
        target_date = monday + timedelta(days=i)
        date_str = target_date.isoformat()
        platform = PLATFORM_BY_WEEKDAY[target_date.weekday()]
        output_path = WEEKLY_DIR / f"{date_str}.yaml"

        ai = ai_topics.get(date_str, {})
        content = {
            "date": date_str,
            "platform": platform,
            "brand": "satoshi",
            "status": "draft",
            "topic": ai.get("topic", ""),
            "caption": ai.get("caption", ""),
            "hashtags": ai.get("hashtags", []),
            "notes": "週次コンテンツプランナーが自動生成"
            + ("（AI）" if ai else "（スケルトン）"),
        }

        atomic_yaml_write(output_path, content)
        status = "AI" if ai.get("caption") else "スケルトン"
        generated.append(f"{date_str} ({platform}) [{status}]")

    today_str = date.today().isoformat()
    ai_count = sum(1 for d in generated if "[AI]" in d)
    message = (
        f"【コンテンツプラン生成】{today_str}\n"
        f"翌週分({monday.isoformat()}〜)を生成しました\n"
        f"AI生成: {ai_count}/7 件\n"
        + "\n".join(f"  {g}" for g in generated)
    )
    print(message)
    logger.info(message)

    messenger = LINEMessenger()
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if owner_id:
        try:
            messenger.push(owner_id, message)
        except Exception as e:
            logger.error(f"LINE通知エラー: {e}")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run()
