"""
Google Drive 連携モジュール
- Googleドライブの指定フォルダから画像/動画を取得
- content_queue/instagram/ に自動追加する
- ナノバナナプロ等の外部ツールでGoogleドライブに書き出した素材を自動で投稿キューに流す

前提:
  - Google Cloud Console でDrive APIを有効化
  - サービスアカウント(credentials.json)を作成して automation-system/ に配置
  - 対象フォルダをサービスアカウントのメールアドレスと共有する

pip install google-auth google-auth-oauthlib google-api-python-client
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

QUEUE_DIR = Path(__file__).parent.parent / "content_queue" / "instagram"
CREDENTIALS_PATH = Path(__file__).parent.parent / "credentials.json"

# DRIVE_FOLDER_ID は load_dotenv() 前にモジュールロードされる可能性があるため
# sync_from_drive() 内で os.environ から毎回読む（後述）


def _get_drive_service():
    """Google Drive APIサービスを取得"""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_file(
            str(CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        return build("drive", "v3", credentials=creds)
    except ImportError:
        raise ImportError(
            "google-api-python-client が未インストールです。\n"
            "pip install google-auth google-auth-oauthlib google-api-python-client"
        )


def _get_public_url(file_id: str) -> str:
    """ファイルIDから公開URLを生成（事前に共有設定が必要）"""
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def sync_from_drive(folder_id: str = "", caption_default: str = "") -> int:
    """
    Googleドライブの指定フォルダから画像/動画を取得して
    content_queue/instagram/ に追加する

    Args:
        folder_id: Googleドライブのフォルダ ID（未指定時は .env の GOOGLE_DRIVE_FOLDER_ID）
        caption_default: キャプションが未設定の場合のデフォルト

    Returns:
        追加したファイル数
    """
    target_folder = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    if not target_folder:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID が設定されていません")
        return 0

    if not CREDENTIALS_PATH.exists():
        logger.warning(
            f"credentials.json が見つかりません: {CREDENTIALS_PATH}\n"
            "Google Cloud Console からサービスアカウントのJSONをダウンロードして配置してください。"
        )
        return 0

    try:
        service = _get_drive_service()
    except Exception as svc_err:
        err_str = str(svc_err)
        if "Service Accounts do not have storage quota" in err_str:
            logger.error("Google Drive: サービスアカウントのストレージ制限エラー — スキップ: %s", svc_err)
        else:
            logger.error("Google Drive サービス初期化エラー: %s", svc_err)
        return 0
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)

    # 既にキューにあるファイルIDを取得（重複防止）
    existing_ids = set()
    for f in QUEUE_DIR.glob("*.yaml"):
        with open(f, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if data and data.get("drive_file_id"):
            existing_ids.add(data["drive_file_id"])

    # Google Driveからファイル一覧を取得
    query = (
        f"'{target_folder}' in parents "
        "and trashed=false "
        "and (mimeType contains 'image/' or mimeType contains 'video/')"
    )
    result = service.files().list(
        q=query,
        fields="files(id, name, mimeType, createdTime, description)",
        orderBy="createdTime desc",
    ).execute()

    files = result.get("files", [])
    added = 0

    for item in files:
        file_id = item["id"]
        if file_id in existing_ids:
            continue  # 既にキューにある

        name = item["name"]
        mime = item["mimeType"]
        # ファイルのdescriptionをキャプションとして使用
        caption = item.get("description", "") or caption_default or f"{name}\n\n#DSCMarketing"

        # ナノバナナプロなどでファイル名にキャプションを埋め込む規則にする場合
        # 例: "2026-04-05_caption=〇〇のテキスト.jpg" → キャプション自動抽出
        if "caption=" in name:
            try:
                caption = name.split("caption=", 1)[1].rsplit(".", 1)[0].replace("_", "\n")
            except Exception as exc:
                logger.warning("キャプション抽出失敗: %s", exc)

        is_video = "video" in mime
        media_type = "reel" if is_video else "image"
        url = _get_public_url(file_id)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in name[:30])
        out_file = QUEUE_DIR / f"{timestamp}_{safe_name}.yaml"

        entry = {
            "media_type": media_type,
            "drive_file_id": file_id,
            "image_url" if not is_video else "video_url": url,
            "caption": caption,
            "posted": False,
            "source": "google_drive",
            "original_filename": name,
        }

        try:
            with open(out_file, "w", encoding="utf-8") as f:
                yaml.dump(entry, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as write_err:
            logger.error("キューファイル書き込み失敗 (%s): %s", name, write_err)
            continue

        logger.info(f"Driveから投稿キューに追加: {name}")
        added += 1

    logger.info(f"Google Drive同期完了: {added}件追加")
    return added
