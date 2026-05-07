"""
Google Business Profile OAuth2 リフレッシュトークン取得スクリプト
=================================================================
このスクリプトを一度だけ実行すると refresh_token が取得できます。
取得した値を Railway の環境変数に設定してください。

実行方法:
  cd automation-system
  python3 get_gbp_token.py

必要なもの（事前準備）:
  1. Google Cloud Console でプロジェクトを作成
  2. "Google Business Profile API" を有効化
  3. OAuth2 認証情報（デスクトップアプリ）を作成
  4. クライアントID・シークレットをこのスクリプトに入力
"""

import json
import os
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

try:
    import requests
except ImportError:
    print("requests をインストールしてください: pip install requests")
    sys.exit(1)

# ── 設定 ─────────────────────────────────────────────────────
SCOPE         = "https://www.googleapis.com/auth/business.manage"
REDIRECT_URI  = "http://localhost:8765/callback"
TOKEN_URL     = "https://oauth2.googleapis.com/token"
AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"

# ── 認証情報の入力 ────────────────────────────────────────────
print("=" * 60)
print("  GBP OAuth2 リフレッシュトークン取得")
print("=" * 60)
print()
print("Google Cloud Console の手順:")
print("  1. https://console.cloud.google.com/ にアクセス")
print("  2. 左メニュー → 「APIとサービス」→「有効なAPIとサービス」")
print("  3. 「+ APIとサービスを有効にする」→ 'Business Profile API' を検索して有効化")
print("  4. 「認証情報」→「認証情報を作成」→「OAuth クライアント ID」")
print("  5. アプリの種類: 「デスクトップアプリ」を選択 → 作成")
print("  6. ダウンロードした JSON からクライアントIDとシークレットをコピー")
print()

client_id = os.environ.get("GBP_CLIENT_ID", "").strip()
if not client_id:
    client_id = input("クライアントID (GBP_CLIENT_ID): ").strip()

client_secret = os.environ.get("GBP_CLIENT_SECRET", "").strip()
if not client_secret:
    client_secret = input("クライアントシークレット (GBP_CLIENT_SECRET): ").strip()

if not client_id or not client_secret:
    print("エラー: クライアントIDとシークレットを入力してください。")
    sys.exit(1)

# ── 認可URL の生成 ────────────────────────────────────────────
auth_params = {
    "client_id":     client_id,
    "redirect_uri":  REDIRECT_URI,
    "response_type": "code",
    "scope":         SCOPE,
    "access_type":   "offline",
    "prompt":        "consent",   # 毎回 refresh_token を返させる
}
auth_url = f"{AUTH_BASE_URL}?{urlencode(auth_params)}"

print()
print("ブラウザを開いて Google アカウントで認証します...")
print(f"URL: {auth_url}")
print()
webbrowser.open(auth_url)

# ── ローカルサーバーでコールバックを受け取る ──────────────────
auth_code: list[str] = []

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if "code" in params:
            auth_code.append(params["code"][0])
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h2>認証完了！このタブを閉じてターミナルに戻ってください。</h2></body></html>"
                .encode("utf-8")
            )
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write("<html><body><h2>Error</h2></body></html>".encode("utf-8"))

    def log_message(self, format, *args):
        pass  # ログを抑制

print("Google アカウントでログインして「許可」をクリックしてください...")
server = HTTPServer(("localhost", 8765), _Handler)
server.handle_request()  # 1リクエストだけ受け取って終了

if not auth_code:
    print("エラー: 認可コードを取得できませんでした。")
    sys.exit(1)

code = auth_code[0]
print("認可コード取得成功 ✓")

# ── refresh_token の取得 ──────────────────────────────────────
print("リフレッシュトークンを取得中...")
resp = requests.post(TOKEN_URL, data={
    "code":          code,
    "client_id":     client_id,
    "client_secret": client_secret,
    "redirect_uri":  REDIRECT_URI,
    "grant_type":    "authorization_code",
}, timeout=30)

if not resp.ok:
    print(f"エラー: {resp.status_code} {resp.text}")
    sys.exit(1)

token_data = resp.json()
refresh_token = token_data.get("refresh_token", "")
if not refresh_token:
    print("エラー: refresh_token が返ってきませんでした。")
    print("       Google Cloud Console で OAuth 同意画面が「テスト」モードの場合、")
    print("       自分のアカウントをテストユーザーとして追加してください。")
    sys.exit(1)

# ── 結果表示 ──────────────────────────────────────────────────
print()
print("=" * 60)
print("  取得成功！以下を Railway の環境変数に設定してください")
print("=" * 60)
print()
print(f"  GBP_CLIENT_ID     = {client_id}")
print(f"  GBP_CLIENT_SECRET = {client_secret}")
print(f"  GBP_REFRESH_TOKEN = {refresh_token}")
print()

# ローカルの .env にも書き込む（任意）
write_env = input(".env ファイルにも書き込みますか？ (y/N): ").strip().lower()
if write_env == "y":
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    lines: list[str] = []
    existing_keys: set[str] = set()

    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            for key, val in [
                ("GBP_CLIENT_ID",     client_id),
                ("GBP_CLIENT_SECRET", client_secret),
                ("GBP_REFRESH_TOKEN", refresh_token),
            ]:
                if line.startswith(f"{key}="):
                    lines[i] = f"{key}={val}\n"
                    existing_keys.add(key)

    if lines:
        tmp_path = env_path.with_suffix(".tmp")
        try:
            tmp_path.write_text("".join(lines), encoding="utf-8")
            tmp_path.replace(env_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    with open(env_path, "a", encoding="utf-8") as f:
        for key, val in [
            ("GBP_CLIENT_ID",     client_id),
            ("GBP_CLIENT_SECRET", client_secret),
            ("GBP_REFRESH_TOKEN", refresh_token),
        ]:
            if key not in existing_keys:
                f.write(f"{key}={val}\n")

    print(f".env に書き込みました: {env_path}")

print()
print("Railway 設定手順:")
print("  1. https://railway.app → プロジェクト → Variables タブを開く")
print("  2. 上記 3つの変数を追加して保存 → 自動で再デプロイされます")
print()
print("完了！")
