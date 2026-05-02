import sys
from pathlib import Path
from datetime import datetime
import subprocess

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))
from sns.line_api import LINEMessenger
import os

BASE_DIR = Path(__file__).parent
SHOPS_DIR = BASE_DIR / "shops"
GENERATOR = BASE_DIR / "generate_shop_text.py"
AUDIT_LOG = BASE_DIR / "audit_log.md"


def run():
    shops = [d for d in sorted(SHOPS_DIR.iterdir()) if d.is_dir()]
    succeeded = []
    failed = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    for shop_dir in shops:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), str(shop_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            succeeded.append(shop_dir.name)
        else:
            failed.append({"name": shop_dir.name, "error": result.stderr.strip()})

    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## {timestamp}\n")
        f.write(f"同期完了: {', '.join(succeeded) if succeeded else 'なし'}\n")
        if failed:
            f.write("エラー:\n")
            for item in failed:
                f.write(f"  - {item['name']}: {item['error']}\n")
        f.write("\n")

    shop_names = " / ".join(succeeded) if succeeded else "なし"
    today = datetime.now().strftime("%Y-%m-%d")
    message = (
        f"【店舗チャネル同期】{today}\n"
        f"同期完了: {len(succeeded)}店舗 ({shop_names})\n"
        f"エラー: {len(failed)}件\n"
        f"出力: shop-update-system/output/"
    )
    print(message)

    messenger = LINEMessenger()
    owner_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if owner_id:
        messenger.push(owner_id, message)


if __name__ == "__main__":
    run()
