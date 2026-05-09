#!/usr/bin/env python3
"""
Search Console OAuth2 トークン取得スクリプト

【手順】
1. python3 setup_search_console_token.py を実行
2. ブラウザが開くので Google アカウントでログイン・権限許可
3. search_console_token.json が生成される
"""
import json
from pathlib import Path
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
CLIENT_SECRET = Path(__file__).parent / "oauth_client_secret.json"
TOKEN_OUT = Path(__file__).parent / "search_console_token.json"

def main():
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(port=0)

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }
    TOKEN_OUT.write_text(json.dumps(token_data, indent=2))
    print(f"✅ 保存完了: {TOKEN_OUT}")

if __name__ == "__main__":
    main()
