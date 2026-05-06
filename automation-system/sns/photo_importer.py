from __future__ import annotations

"""
写真インポーター（現実の写真 → 投稿キュー自動化）

使い方:
  media/inbox/{brand}/ に写真・動画を入れると:
  1. AIがキャプション＋ハッシュタグを自動生成
  2. Google Drive にアップロードして公開URLを取得
  3. content_queue/instagram/{brand}/ に投稿YAMLを自動生成
  4. 処理済みファイルを media/processed/{brand}/ に移動

対応フォーマット:
  画像: .jpg .jpeg .png .webp .heic
  動画: .mp4 .mov .m4v  （reel_ プレフィックスで明示可）
"""

import base64
import logging
import mimetypes
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests
import yaml

logger = logging.getLogger(__name__)

ROOT      = Path(__file__).parent.parent
INBOX_DIR = ROOT / "media" / "inbox"
PROCESSED_DIR = ROOT / "media" / "processed"
QUEUE_DIR = ROOT / "content_queue" / "instagram"
CREDS_PATH = ROOT / "credentials.json"


def _ensure_credentials() -> None:
    """GOOGLE_CREDENTIALS_B64 環境変数から credentials.json を復元する（Railway用）"""
    if CREDS_PATH.exists():
        return
    b64 = os.environ.get("GOOGLE_CREDENTIALS_B64", "")
    if not b64:
        return
    try:
        CREDS_PATH.write_bytes(base64.b64decode(b64))
        logger.info("credentials.json を環境変数から復元しました")
    except Exception as e:
        logger.warning(f"credentials.json の復元に失敗: {e}")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


# ─── WordPress メディアアップロード ──────────────────────────

_WP_MAX_BYTES = 60 * 1024  # ホスティングWAFが大きいファイルをブロックするため60KB上限


def _compress_image(file_path: Path) -> tuple[bytes, str]:
    """画像をWAF制限内に収まるよう圧縮して返す (data, content_type)"""
    from PIL import Image
    import io
    img = Image.open(file_path).convert("RGB")
    for quality in (85, 70, 50, 30):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, optimize=True)
        data = buf.getvalue()
        if len(data) <= _WP_MAX_BYTES:
            logger.info(f"画像圧縮: {file_path.name} quality={quality} → {len(data)//1024}KB")
            return data, "image/jpeg"
    return data, "image/jpeg"


def _upload_to_wordpress_media(file_path: Path, brand: str) -> str:
    """ファイルをWordPressメディアライブラリにアップロードして公開URLを返す"""
    from sns.wordpress import WordPressPoster
    wp = WordPressPoster(brand=brand)
    if not wp.wp_url:
        raise ValueError(f"WordPress URL未設定: {brand}")

    if file_path.suffix.lower() in IMAGE_EXTS and file_path.stat().st_size > _WP_MAX_BYTES:
        data, ct = _compress_image(file_path)
    else:
        with open(file_path, "rb") as f:
            data = f.read()
        ct = mimetypes.guess_type(str(file_path))[0] or "image/jpeg"

    headers = {
        **wp._auth(),
        "Content-Disposition": f'attachment; filename="{file_path.stem}.jpg"',
        "Content-Type": ct,
    }
    r = requests.post(f"{wp.wp_url}/wp-json/wp/v2/media", headers=headers, data=data, timeout=60)
    r.raise_for_status()
    url = r.json().get("source_url", "")
    logger.info(f"WordPressメディアアップロード完了: {file_path.name} → {url}")
    return url


# ─── Google Drive アップロード（フォールバック）─────────────

def _upload_to_drive(file_path: Path, brand: str) -> str:
    """
    ファイルをGoogle Driveにアップロードして公開URLを返す

    Returns:
        公開URL（"https://drive.google.com/uc?export=download&id=..."）
    """
    _ensure_credentials()
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID", "")
    if not folder_id:
        raise ValueError("GOOGLE_DRIVE_FOLDER_ID が .env に設定されていません")
    if not CREDS_PATH.exists():
        raise FileNotFoundError(f"credentials.json が見つかりません: {CREDS_PATH}")

    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = service_account.Credentials.from_service_account_file(
        str(CREDS_PATH),
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    service = build("drive", "v3", credentials=creds)

    # アップロード
    file_metadata = {
        "name":    file_path.name,
        "parents": [folder_id],
    }
    mime_type = "video/mp4" if file_path.suffix.lower() in VIDEO_EXTS else "image/jpeg"
    media = MediaFileUpload(str(file_path), mimetype=mime_type, resumable=True)
    uploaded = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id",
    ).execute()
    file_id = uploaded["id"]

    # 公開設定（anyone が閲覧可能にする）
    service.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    url = f"https://drive.google.com/uc?export=download&id={file_id}"
    logger.info(f"Drive アップロード完了: {file_path.name} → {url}")
    return url


# ─── AI キャプション生成 ─────────────────────────────────────

def _generate_caption(file_path: Path, brand: str) -> dict:
    """
    ファイル名・ブランドからAIキャプションを生成する

    Returns:
        {"caption": str, "hashtags": str, "topic": str}
    """
    sys.path.insert(0, str(ROOT / "dashboard"))
    try:
        from ai import generate_instagram_post
    except ImportError:
        logger.warning("ai.py のインポートに失敗。デフォルトキャプションを使用")
        return {
            "caption": f"{brand.upper()}の最新情報\n\nプロフリンクから詳細をチェック↑",
            "hashtags": f"#{brand.replace('-', '')} #マーケティング #SNS運用",
            "topic": file_path.stem,
        }

    # ファイル名からトピックを推測
    stem = file_path.stem
    topic_hint = stem.replace("_", " ").replace("-", " ")
    # 先頭の "reel_" "story_" などのプレフィックスを除去
    for prefix in ("reel ", "reel_", "story ", "story_", "ig ", "ig_"):
        if topic_hint.lower().startswith(prefix):
            topic_hint = topic_hint[len(prefix):]
            break

    # 一般的すぎるファイル名は無視
    generic_names = {"img", "image", "photo", "pic", "dsc", "dscf", "p", "picture"}
    if topic_hint.lower().split()[0] in generic_names or topic_hint[:3].upper() in ("IMG", "DSC", "PIC"):
        topic_hint = f"{brand}の最新コンテンツ"

    result = generate_instagram_post(
        topic=topic_hint,
        target="SNS・集客に興味がある中小企業経営者・個人事業主",
        tone="実務的・親しみやすい",
        brand=brand,
    )
    return {
        "caption":  result.get("caption", ""),
        "hashtags": result.get("hashtags", ""),
        "topic":    topic_hint,
    }


# ─── メイン処理 ──────────────────────────────────────────────

def process_inbox(brand: str | None = None, dry_run: bool = False) -> int:
    """
    inbox フォルダをスキャンして写真を処理する

    Args:
        brand:   特定ブランドのみ処理（None で全ブランド）
        dry_run: True のときアップロード・移動を行わずログのみ

    Returns:
        処理したファイル数
    """
    brands = [brand] if brand else [d.name for d in INBOX_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")]
    total = 0

    for b in brands:
        inbox = INBOX_DIR / b
        if not inbox.exists():
            continue

        files = [
            f for f in inbox.iterdir()
            if f.is_file()
            and f.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS
            and not f.name.startswith(".")
        ]
        if not files:
            logger.debug(f"{b}: インボックスに新規ファイルなし")
            continue

        logger.info(f"{b}: {len(files)}件の写真を処理開始")

        for file_path in sorted(files):
            try:
                processed = _process_single_file(file_path, b, dry_run)
                if processed:
                    total += 1
            except Exception as e:
                logger.error(f"処理エラー ({file_path.name}): {e}", exc_info=True)

    logger.info(f"インボックス処理完了: 合計{total}件")
    return total


def _process_single_file(file_path: Path, brand: str, dry_run: bool = False) -> bool:
    """
    1ファイルを処理してキューに追加する

    Returns:
        True if successfully processed
    """
    suffix = file_path.suffix.lower()
    is_video = suffix in VIDEO_EXTS
    is_reel  = is_video or file_path.stem.lower().startswith("reel")

    logger.info(f"処理中: {file_path.name} ({'動画/リール' if is_reel else '画像'})")

    # 1. キャプション生成
    caption_data = _generate_caption(file_path, brand)
    caption  = caption_data["caption"]
    hashtags = caption_data["hashtags"]
    topic    = caption_data["topic"]
    full_caption = f"{caption}\n\n{hashtags}".strip()

    if dry_run:
        logger.info(f"[DRY RUN] キャプション生成: {caption[:60]}...")
        logger.info(f"[DRY RUN] ハッシュタグ: {hashtags[:60]}...")
        return True

    # 2. 画像URLを取得（WordPress優先 → Google Driveフォールバック）
    public_url = ""
    try:
        public_url = _upload_to_wordpress_media(file_path, brand)
    except Exception as wp_err:
        logger.warning(f"WordPress アップロード失敗: {wp_err} → Drive を試みます")
        try:
            public_url = _upload_to_drive(file_path, brand)
        except Exception as drive_err:
            logger.error(f"全アップロード失敗: Drive={drive_err}")
            _save_queue_entry(
                brand=brand,
                file_name=file_path.name,
                media_type="reel" if is_reel else "image",
                url="",
                caption=full_caption,
                topic=topic,
                error=str(drive_err),
            )
            return False

    # 3. キューに追加
    _save_queue_entry(
        brand=brand,
        file_name=file_path.name,
        media_type="reel" if is_reel else "image",
        url=public_url,
        caption=full_caption,
        topic=topic,
    )

    # 4. 処理済みフォルダに移動
    processed_dir = PROCESSED_DIR / brand
    processed_dir.mkdir(parents=True, exist_ok=True)
    dest = processed_dir / file_path.name
    # 同名ファイルが既にある場合はタイムスタンプを付加
    if dest.exists():
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = processed_dir / f"{file_path.stem}_{ts}{file_path.suffix}"
    shutil.move(str(file_path), str(dest))
    logger.info(f"処理済みに移動: {dest}")

    return True


def _save_queue_entry(
    brand: str,
    file_name: str,
    media_type: str,
    url: str,
    caption: str,
    topic: str,
    error: str = "",
) -> Path:
    """投稿キューにYAMLエントリを保存する"""
    queue_dir = QUEUE_DIR
    queue_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in file_name[:25])
    out_path  = queue_dir / f"{timestamp}_{brand}_{safe_name}.yaml"

    url_key = "video_url" if media_type == "reel" else "image_url"
    entry: dict = {
        "media_type":        media_type,
        url_key:             url,
        "caption":           caption,
        "topic":             topic,
        "brand":             brand,
        "source":            "photo_inbox",
        "original_filename": file_name,
        "posted":            False,
    }
    if error:
        entry["error"]        = error
        entry["needs_review"] = True

    out_path.write_text(
        yaml.dump(entry, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(f"キューに追加: {out_path.name}")
    return out_path


# ─── 単体実行 ────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="写真インボックスを処理してキューに追加する")
    parser.add_argument("--brand",   default=None,  help="ブランドを指定（省略時は全ブランド）")
    parser.add_argument("--dry-run", action="store_true", help="実際のアップロード・移動を行わない")
    args = parser.parse_args()

    count = process_inbox(brand=args.brand, dry_run=args.dry_run)
    print(f"\n✅ 処理完了: {count}件をキューに追加しました")
