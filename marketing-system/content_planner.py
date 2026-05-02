import sys
from pathlib import Path
from datetime import date, timedelta
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))
from sns.line_api import LINEMessenger
import os

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


def next_monday():
    today = date.today()
    days_ahead = 7 - today.weekday()
    return today + timedelta(days=days_ahead)


def run():
    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    monday = next_monday()
    generated = []

    for i in range(7):
        target_date = monday + timedelta(days=i)
        platform = PLATFORM_BY_WEEKDAY[target_date.weekday()]
        output_path = WEEKLY_DIR / f"{target_date.isoformat()}.yaml"

        content = {
            "date": target_date.isoformat(),
            "platform": platform,
            "brand": "satoshi",
            "status": "draft",
            "caption": "",
            "hashtags": [],
            "notes": "週次コンテンツプランナーが自動生成",
        }

        with open(output_path, "w", encoding="utf-8") as f:
            yaml.dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        generated.append(f"{target_date.isoformat()} ({platform})")

    today = date.today().isoformat()
    message = (
        f"【コンテンツプラン生成】{today}\n"
        f"翌週分({monday.isoformat()}〜)を生成しました\n"
        + "\n".join(f"  {g}" for g in generated)
    )
    print(message)

    messenger = LINEMessenger()
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if owner_id:
        messenger.push(owner_id, message)


if __name__ == "__main__":
    run()
