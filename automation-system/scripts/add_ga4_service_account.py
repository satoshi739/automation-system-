"""
GA4プロパティにサービスアカウントを閲覧者として追加するスクリプト。

実行前に:
  pip install google-analytics-admin google-auth-oauthlib

実行方法:
  python3 scripts/add_ga4_service_account.py

初回実行時にブラウザが開いてGoogleログインを求められます。
GA4の管理者権限を持つアカウント（satoshi6667s@gmail.com）でログインしてください。
"""

import os, json
from pathlib import Path

# .env 読み込み
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

SERVICE_ACCOUNT_EMAIL = "upjapan-drive-bot@gen-lang-client-0671871313.iam.gserviceaccount.com"

# GA4プロパティID一覧（ブランド名: プロパティID）
GA4_PROPERTIES = {
    "dsc-marketing":    os.environ.get("DSC_MARKETING_GA4_PROPERTY_ID", ""),
    "cashflowsupport":  os.environ.get("CASHFLOWSUPPORT_GA4_PROPERTY_ID", ""),
    "bangkok-peach":    os.environ.get("BANGKOK_PEACH_GA4_PROPERTY_ID", ""),
    "satoshi-blog":     os.environ.get("SATOSHI_BLOG_GA4_PROPERTY_ID", ""),
}

SCOPES = ["https://www.googleapis.com/auth/analytics.manage.users"]
TOKEN_FILE = Path(__file__).parent / "ga4_admin_token.json"
CLIENT_SECRET_FILE = Path(__file__).parent.parent / "oauth_client_secret.json"


def get_credentials():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
            return creds

    if not CLIENT_SECRET_FILE.exists():
        print(f"\n❌ {CLIENT_SECRET_FILE} が見つかりません。")
        print("Google Cloud Console → APIとサービス → 認証情報 → OAuthクライアントID（デスクトップアプリ）を作成し、")
        print(f"JSONをダウンロードして {CLIENT_SECRET_FILE} に配置してください。")
        raise SystemExit(1)

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    return creds


def add_service_account_to_property(creds, property_id: str, brand: str):
    import requests
    from google.auth.transport.requests import Request as GRequest

    if not property_id:
        print(f"  ⏭  {brand}: プロパティID未設定、スキップ")
        return

    if creds.expired:
        creds.refresh(GRequest())

    url = f"https://analyticsadmin.googleapis.com/v1alpha/properties/{property_id}/accessBindings"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    body = {"user": SERVICE_ACCOUNT_EMAIL, "roles": ["predefinedRoles/viewer"]}

    resp = requests.post(url, json=body, headers=headers)
    if resp.status_code in (200, 201):
        print(f"  ✅ {brand} ({property_id}): 追加完了")
    elif resp.status_code == 409 or "already exists" in resp.text.lower():
        print(f"  ✓  {brand} ({property_id}): 既に追加済み")
    else:
        err = resp.json().get("error", {})
        print(f"  ❌ {brand} ({property_id}): {resp.status_code} — {err.get('message','')[:120]}")


def main():
    print(f"サービスアカウント: {SERVICE_ACCOUNT_EMAIL}")
    print(f"追加するGA4プロパティ数: {sum(1 for v in GA4_PROPERTIES.values() if v)}\n")

    creds = get_credentials()

    for brand, prop_id in GA4_PROPERTIES.items():
        add_service_account_to_property(creds, prop_id, brand)

    print("\n完了。server.py を再起動するとGA4データが表示されます。")


if __name__ == "__main__":
    main()
