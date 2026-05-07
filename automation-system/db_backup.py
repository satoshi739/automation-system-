"""
DB バックアップ → Google Drive アップロード

認証方式: OAuth2 ユーザートークン（個人DriveはSAクォータ不可のため）
  - 初回: python3 db_backup.py --auth でブラウザ認証 → drive_token.json を保存
  - Railway: DRIVE_TOKEN_B64 env var に base64(drive_token.json) をセット

ENV:
  DB_BACKUP_DRIVE_FOLDER_ID  — バックアップ先フォルダID（必須）
  DRIVE_TOKEN_B64            — Railway用 base64エンコードのトークンJSON
  ALERT_LINE_CHANNEL_ACCESS_TOKEN / OWNER_LINE_USER_ID — 失敗時LINE通知
"""

from __future__ import annotations

import base64
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH             = Path(__file__).parent / "data" / "upj.db"
BACKUP_DIR          = Path(__file__).parent / "data" / "backups"
CLIENT_SECRETS_PATH = Path(__file__).parent / "oauth_client_secret.json"
TOKEN_PATH          = Path(__file__).parent / "drive_token.json"
KEEP_LOCAL_DAYS     = 7
KEEP_DRIVE_COUNT    = 30
SCOPES              = ["https://www.googleapis.com/auth/drive.file"]


def _line_alert(message: str) -> None:
    try:
        import requests
        token   = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
        user_id = os.environ.get("OWNER_LINE_USER_ID", "")
        if token and user_id:
            requests.post(
                "https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"to": user_id, "messages": [{"type": "text", "text": f"[DBバックアップ]\n{message}"}]},
                timeout=5,
            )
    except Exception as exc:
        logger.error("LINE通知失敗: %s", exc)


def _load_token_from_env() -> dict | None:
    """DRIVE_TOKEN_B64 env var からトークンJSONを読み込む（Railway用）"""
    b64 = os.environ.get("DRIVE_TOKEN_B64", "")
    if not b64:
        return None
    try:
        return __import__("json").loads(base64.b64decode(b64).decode())
    except Exception as exc:
        logger.warning("DRIVE_TOKEN_B64 のデコード失敗: %s", exc)
        return None


def _get_drive_service():
    """OAuth2ユーザー認証でDriveサービスを取得。ローカルファイルまたはenv varからトークンを読む。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None

    # ① ローカルトークンファイル
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

    # ② Railway env var（DRIVE_TOKEN_B64）
    if not creds or not creds.valid:
        token_data = _load_token_from_env()
        if token_data:
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)

    if not creds:
        raise RuntimeError(
            "Drive認証情報がありません。\n"
            "ローカルで python3 db_backup.py --auth を実行してください。"
        )

    # リフレッシュ
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if TOKEN_PATH.exists():
            TOKEN_PATH.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def auth_drive() -> None:
    """初回認証フロー（ローカルで一度だけ実行）。drive_token.json を生成する。"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    print(f"認証完了: {TOKEN_PATH}")
    print("\n--- Railway用 DRIVE_TOKEN_B64 ---")
    print(base64.b64encode(TOKEN_PATH.read_bytes()).decode())
    print("---------------------------------")
    print("上記の値を Railway の環境変数 DRIVE_TOKEN_B64 にセットしてください。")


def _make_local_backup() -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"upj_{ts}.db"
    shutil.copy2(DB_PATH, dest)
    cutoff = datetime.now().timestamp() - KEEP_LOCAL_DAYS * 86400
    for f in BACKUP_DIR.glob("upj_*.db"):
        if f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    return dest


def _upload_to_drive(local_path: Path, folder_id: str) -> str:
    from googleapiclient.http import MediaFileUpload
    service = _get_drive_service()
    file_metadata = {"name": local_path.name, "parents": [folder_id]}
    media = MediaFileUpload(str(local_path), mimetype="application/octet-stream")
    result = service.files().create(
        body=file_metadata, media_body=media, fields="id,name"
    ).execute()
    _prune_drive_backups(service, folder_id)
    return result["id"]


def _prune_drive_backups(service, folder_id: str) -> None:
    try:
        results = service.files().list(
            q=f"'{folder_id}' in parents and name contains 'upj_' and trashed=false",
            orderBy="createdTime",
            fields="files(id,name,createdTime)",
        ).execute()
        files = results.get("files", [])
        if len(files) > KEEP_DRIVE_COUNT:
            for f in files[: len(files) - KEEP_DRIVE_COUNT]:
                service.files().delete(fileId=f["id"]).execute()
                logger.info("古いDriveバックアップを削除: %s", f["name"])
    except Exception as exc:
        logger.warning("Driveバックアップ整理失敗（無視）: %s", exc)


def backup_and_upload() -> dict:
    folder_id = os.environ.get("DB_BACKUP_DRIVE_FOLDER_ID", "")
    if not folder_id:
        logger.warning("DB_BACKUP_DRIVE_FOLDER_ID 未設定 — ローカルバックアップのみ実行")

    if not DB_PATH.exists():
        msg = f"DBファイルが見つかりません: {DB_PATH}"
        logger.error(msg)
        _line_alert(f"失敗: {msg}")
        return {"ok": False, "error": msg}

    try:
        local_path = _make_local_backup()
        logger.info("ローカルバックアップ完了: %s (%.1f KB)", local_path.name, local_path.stat().st_size / 1024)
    except Exception as exc:
        msg = f"ローカルバックアップ失敗: {exc}"
        logger.error(msg, exc_info=True)
        _line_alert(f"失敗: {msg}")
        return {"ok": False, "error": msg}

    if not folder_id:
        return {"ok": True, "local": str(local_path), "drive_id": None}

    try:
        drive_id = _upload_to_drive(local_path, folder_id)
        logger.info("Drive アップロード完了: %s → %s", local_path.name, drive_id)
        return {"ok": True, "local": str(local_path), "drive_id": drive_id}
    except Exception as exc:
        msg = f"Drive アップロード失敗: {exc}"
        logger.error(msg, exc_info=True)
        _line_alert(f"失敗: {msg}")
        return {"ok": False, "local": str(local_path), "error": msg}


if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("--auth", action="store_true", help="初回OAuth認証（ブラウザが開きます）")
    args = parser.parse_args()

    if args.auth:
        auth_drive()
    else:
        result = backup_and_upload()
        print("結果:", result)
