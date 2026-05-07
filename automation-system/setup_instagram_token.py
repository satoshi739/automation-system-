#!/usr/bin/env python3
"""
Instagram トークンセットアップヘルパー

【手順】
1. https://developers.facebook.com/tools/explorer/ を開く
2. 右上のアプリを対象ブランドのアプリに切り替える
3. 「アクセストークンを生成」→ 以下の権限にチェック:
   - instagram_basic
   - instagram_content_publish
   - pages_read_engagement
   - pages_show_list
4. 生成されたトークンをこのスクリプトに貼る
5. python3 setup_instagram_token.py

ブランドとApp情報:
  bangkok-peach: App ID 1464447688120060 / Secret c7ea53d83d20cd881a7113fc0e21b622
  その他のブランド: Meta Developer Portal で確認
"""
from __future__ import annotations

import os
import sys
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

BRANDS = {
    "1": {"name": "bangkok-peach",    "prefix": "BANGKOK_PEACH",
          "app_id": os.environ.get("BANGKOK_PEACH_INSTAGRAM_APP_ID", ""),
          "app_secret": os.environ.get("BANGKOK_PEACH_INSTAGRAM_APP_SECRET", "")},
    "2": {"name": "dsc-marketing",    "prefix": "DSC_MARKETING",
          "app_id": os.environ.get("DSC_MARKETING_META_APP_ID", ""),
          "app_secret": os.environ.get("DSC_MARKETING_META_APP_SECRET", "")},
    "3": {"name": "cashflowsupport",  "prefix": "CASHFLOWSUPPORT",
          "app_id": os.environ.get("CASHFLOWSUPPORT_META_APP_ID", ""),
          "app_secret": os.environ.get("CASHFLOWSUPPORT_META_APP_SECRET", "")},
    "4": {"name": "upjapan",          "prefix": "UPJAPAN",
          "app_id": os.environ.get("UPJAPAN_META_APP_ID", ""),
          "app_secret": os.environ.get("UPJAPAN_META_APP_SECRET", "")},
    "5": {"name": "satoshi",          "prefix": "SATOSHI",
          "app_id": os.environ.get("SATOSHI_META_APP_ID", ""),
          "app_secret": os.environ.get("SATOSHI_META_APP_SECRET", "")},
}


def exchange_for_long_lived(short_token: str, app_id: str, app_secret: str) -> str:
    """短期トークン → 長期トークン（60日有効）に変換"""
    r = requests.get(
        "https://graph.facebook.com/v21.0/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": short_token,
        },
        timeout=15,
    )
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"長期変換失敗: {data['error']['message']}")
    return data["access_token"]


def get_instagram_account_id(long_token: str) -> tuple[str, str]:
    """長期トークンから Instagram Business Account ID を取得。(ig_user_id, page_name) を返す"""
    # Facebookページ一覧を取得
    r = requests.get(
        "https://graph.facebook.com/v21.0/me/accounts",
        params={"access_token": long_token, "fields": "id,name,instagram_business_account"},
        timeout=15,
    )
    pages = r.json().get("data", [])
    if not pages:
        raise RuntimeError("Facebookページが見つかりません。ビジネスアカウントに紐付いているか確認してください。")

    # Instagram Business Accountが紐付いているページを探す
    for page in pages:
        ig = page.get("instagram_business_account")
        if ig:
            return ig["id"], page["name"]

    # どれにも紐付いていない場合はページ一覧を表示
    print("\n接続されたFacebookページ:")
    for i, p in enumerate(pages, 1):
        print(f"  {i}. {p['name']} (ID: {p['id']})")
    raise RuntimeError(
        "Instagram Businessアカウントが見つかりません。\n"
        "Instagram設定 → アカウントの種類 → プロアカウント に切り替え、\n"
        "Facebookページと連携してください。"
    )


def update_env(prefix: str, token: str, ig_id: str) -> None:
    """`.env` の該当ブランドのトークンとIDを更新する"""
    env_path = Path(__file__).parent / ".env"
    content = env_path.read_text(encoding="utf-8")

    def _replace(text: str, key: str, value: str) -> str:
        pattern = rf"^({key}=).*$"
        replacement = rf"\g<1>{value}"
        new = re.sub(pattern, replacement, text, flags=re.MULTILINE)
        if new == text:
            return text.rstrip() + f"\n{key}={value}\n"
        return new

    content = _replace(content, f"{prefix}_META_ACCESS_TOKEN", token)
    content = _replace(content, f"{prefix}_INSTAGRAM_ACCOUNT_ID", ig_id)
    env_path.write_text(content, encoding="utf-8")


def main() -> None:
    print("=" * 55)
    print(" Instagram トークンセットアップ")
    print("=" * 55)
    print()
    print("ブランドを選択してください:")
    for k, v in BRANDS.items():
        has_app = "✅" if v["app_id"] else "⚠️ App ID未設定"
        print(f"  {k}. {v['name']} {has_app}")
    print()

    choice = input("番号を入力 > ").strip()
    brand = BRANDS.get(choice)
    if not brand:
        print("無効な番号です。")
        sys.exit(1)

    if not brand["app_id"] or not brand["app_secret"]:
        print(f"\n⚠️  {brand['name']} の App ID / App Secret が未設定です。")
        print(".env に以下を追加してから再実行してください:")
        print(f"  {brand['prefix']}_META_APP_ID=<App ID>")
        print(f"  {brand['prefix']}_META_APP_SECRET=<App Secret>")
        print("\nApp IDとApp Secretは Meta Developer Portal (developers.facebook.com) で確認できます。")
        sys.exit(1)

    print(f"\n▶ {brand['name']} のセットアップを開始します")
    print()
    print("【ステップ1】以下のURLをブラウザで開いてください:")
    print()
    scopes = "instagram_basic,instagram_content_publish,pages_read_engagement,pages_show_list,business_management"
    explorer_url = (
        f"https://developers.facebook.com/tools/explorer/"
        f"?app_id={brand['app_id']}&token_type=USER&scope={scopes}"
    )
    print(f"  {explorer_url}")
    print()
    print("【ステップ2】「アクセストークンを生成」ボタンを押し、ログインして許可してください。")
    print("【ステップ3】生成されたトークン（EAA...）をコピーしてください。")
    print()

    short_token = input("短期アクセストークンを貼り付けてください > ").strip()
    if not short_token.startswith("EAA"):
        print("❌ トークンの形式が正しくありません（EAA で始まる文字列を貼ってください）")
        sys.exit(1)

    print("\n🔄 長期トークンに変換中...")
    try:
        long_token = exchange_for_long_lived(short_token, brand["app_id"], brand["app_secret"])
        print(f"✅ 長期トークン取得成功（60日有効）")
    except Exception as e:
        print(f"❌ 変換失敗: {e}")
        sys.exit(1)

    print("🔄 Instagram Account ID を取得中...")
    try:
        ig_id, page_name = get_instagram_account_id(long_token)
        print(f"✅ Instagram Account ID: {ig_id}（Facebookページ: {page_name}）")
    except Exception as e:
        print(f"❌ 取得失敗: {e}")
        sys.exit(1)

    print("\n🔄 .env を更新中...")
    update_env(brand["prefix"], long_token, ig_id)
    print(f"✅ .env 更新完了")
    print()
    print("【次のステップ】Railway の環境変数にも反映してください:")
    print(f"  railway variables set {brand['prefix']}_META_ACCESS_TOKEN=\"{long_token[:20]}...\"")
    print(f"  railway variables set {brand['prefix']}_INSTAGRAM_ACCOUNT_ID=\"{ig_id}\"")
    print()

    # Railway への反映確認
    apply = input("Railway に今すぐ反映しますか？ [y/N] > ").strip().lower()
    if apply == "y":
        import subprocess
        subprocess.run([
            "railway", "variables", "set",
            f"{brand['prefix']}_META_ACCESS_TOKEN={long_token}",
            f"{brand['prefix']}_INSTAGRAM_ACCOUNT_ID={ig_id}",
        ])
        print("✅ Railway 反映完了")

    print()
    print(f"🎉 {brand['name']} の Instagram セットアップ完了！")
    print("  他のブランドも同様に実行してください。")


if __name__ == "__main__":
    main()
