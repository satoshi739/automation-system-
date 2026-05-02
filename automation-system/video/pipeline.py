"""
Blog → Reel 自動生成パイプライン

使い方:
    python -m video.pipeline --url https://satoshi-life.site/?p=123
    python -m video.pipeline --script path/to/noimos_script.yaml
    python -m video.pipeline --latest  # WP最新記事を自動取得
    python -m video.pipeline --test    # テスト用ダミーデータで動作確認

フロー:
    1. ブログ取得 または NoimosAI台本ファイル読み込み
    2. Claude APIで台本構造化（NoimosAIファイルがない場合）
    3. シーンごとに Google Veo で映像生成（失敗時はKen Burnsフォールバック）
    4. gTTSで音声生成（完全無料）
    5. ffmpegでテロップ・音声・効果音を合成
    6. TikTok/Instagram Reelsの投稿キューに追加
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import yaml

# パスを通す
_ROOT = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system"))
sys.path.insert(0, str(_ROOT))

load_dotenv(_ROOT / ".env")

from video.blog_fetcher import BlogFetcher
from video.script_generator import ScriptGenerator
from video.nano_banana_generator import NanaBananaGenerator
from video.veo_generator import VeoGenerator
from video.tts_generator import TTSGenerator
from video.composer import VideoComposer

_PROJECT_ROOT = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system"))
OUTPUT_DIR = _PROJECT_ROOT / "generated_media" / "reels"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_CHANNELS_CONFIG = _ROOT / "config" / "channel_settings.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def _load_channels() -> dict:
    with open(_CHANNELS_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)["channels"]


def _resolve_channels(channel_key: str) -> list:
    """channel_key を受け取り、実行対象チャネルのキーリストを返す。

    - "both": status:active のみ返す。pending はスキップログを出力。
    - 個別指定かつ status:pending: 警告を出して sys.exit(1)。
    """
    channels = _load_channels()

    if channel_key == "both":
        active = []
        for key, cfg in channels.items():
            if cfg.get("status") == "pending":
                log.info(f"Skipping {key} (status: pending)")
            else:
                active.append(key)
        return active

    cfg = channels.get(channel_key)
    if cfg is None:
        log.error(f"チャネル '{channel_key}' が channel_settings.yaml に見つかりません")
        sys.exit(1)
    if cfg.get("status") == "pending":
        log.warning(
            f"チャネル '{channel_key}' は未開設 (status: pending) です。"
            " 開設後に instagram_account_id 等を設定してから実行してください。"
        )
        sys.exit(1)
    return [channel_key]


def run_pipeline(
    blog_url: str = None,
    blog_text: str = None,
    blog_title: str = None,
    script_file: Path = None,
    brand: str = "satoshi-blog",
    channel: str = "satoshi",
    format_key: str = "howto",
    target_key: str = "beginner",
    dry_run: bool = False,
    post: bool = False,
    use_veo: bool = False,
) -> Path:
    """
    パイプライン実行。完成した動画ファイルのパスを返す。

    Args:
        blog_url: WordPress記事URL（blog_textが未指定の場合に使用）
        blog_text: ブログ本文テキスト
        blog_title: ブログタイトル
        script_file: NoimosAI出力YAMLファイルのパス（指定時はStep1-2をスキップ）
        brand: 投稿ブランド名（satoshi-blog, dsc-marketing, etc.）
        channel: channel_settings.yaml のチャネルキー
        dry_run: 動画生成のみ、投稿しない
        post: True なら完成後に投稿キューへ追加

    Returns:
        完成動画ファイルのパス
    """
    channel_cfg = _load_channels().get(channel, {})
    log.info("=== Blog → Reel パイプライン 開始 ===")

    # Step 1: 台本取得
    if script_file and Path(script_file).exists():
        log.info(f"[Step 1] NoimosAI台本ファイルを読み込み: {script_file}")
        import yaml
        with open(script_file, encoding="utf-8") as f:
            script = yaml.safe_load(f)
    else:
        # ブログ本文取得
        if blog_url and not blog_text:
            log.info(f"[Step 1] ブログ記事を取得: {blog_url}")
            fetcher = BlogFetcher(brand=brand)
            post_data = fetcher.fetch_by_url(blog_url)
            blog_text = post_data["content"]
            blog_title = blog_title or post_data["title"]

        if not blog_text:
            raise ValueError("blog_url, blog_text, または script_file のいずれかが必要です")

        # Step 2: 台本生成
        log.info(f"[Step 2] 台本を生成中 (format={format_key} target={target_key})...")
        generator = ScriptGenerator()
        script = generator.generate(
            title=blog_title or "",
            body=blog_text,
            format_key=format_key,
            target_key=target_key,
        )

    log.info(f"台本: {len(script.get('scenes', []))} シーン / {script.get('title', '無題')}")

    # Step 3-5: シーンごとに動画生成
    log.info("[Step 3-5] シーン動画を生成中...")
    if use_veo:
        log.info("映像生成: Veo 3 Fast")
        video_gen = VeoGenerator()
    else:
        log.info("映像生成: Nano Banana 2 (gemini-3.1-flash-image-preview)")
        video_gen = NanaBananaGenerator()
    tts = TTSGenerator()
    composer = VideoComposer()

    scene_clips = []
    for i, scene in enumerate(script["scenes"]):
        log.info(f"  シーン {i+1}/{len(script['scenes'])}: {scene.get('telop', '')[:20]}")

        # 動画クリップ生成
        clip_path = video_gen.generate(
            prompt=scene.get("visual_prompt", ""),
            telop=scene.get("telop", ""),
            duration=scene.get("duration", 5),
            scene_index=i,
        )

        # TTS音声生成
        narration = scene.get("narration", "")
        audio_path = tts.generate(narration) if narration else None

        scene_clips.append({
            "clip": clip_path,
            "audio": audio_path,
            "telop": scene.get("telop", ""),
            "duration": scene.get("duration", 5),
            "se": scene.get("se", None),
        })

    # Step 6: 最終合成
    log.info("[Step 6] 最終動画を合成中...")
    title_safe = (script.get("title") or "reel").replace("/", "_").replace(" ", "_")[:40]
    from datetime import datetime
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"{timestamp}_{brand}_{title_safe}.mp4"

    final_video = composer.compose(
        scenes=scene_clips,
        output_path=output_path,
        title=script.get("title", ""),
    )

    log.info(f"✓ 完成: {final_video}")

    # コストサマリー
    if use_veo:
        veo_cost = video_gen.total_cost
        log.info(f"💰 映像コスト: Veo {video_gen.seconds_generated}秒 × $0.15 = ${veo_cost:.4f}")
    else:
        nb_cost = video_gen.total_cost
        log.info(f"💰 映像コスト: Nano Banana {video_gen.images_generated}枚 × $0.045 = ${nb_cost:.4f}")

    # Step 7: 投稿キューへ追加
    if post and not dry_run:
        _add_to_queue(final_video, script, brand, channel_cfg)

    log.info("=== パイプライン 完了 ===")
    return final_video


def _add_to_queue(video_path: Path, script: dict, brand: str, channel_cfg: dict = None):
    """完成動画を投稿キューに追加"""
    from datetime import datetime

    queue_dir = Path(__file__).parent.parent / "content_queue" / "instagram"
    queue_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    caption = script.get("caption", script.get("title", ""))

    # チャネル設定のハッシュタグを優先。なければ台本のハッシュタグを使用
    raw_hashtags = (channel_cfg or {}).get("hashtags") or script.get("hashtags", [])
    hashtags = " ".join(raw_hashtags)

    # CTA: TBD ガード
    cta = ""
    if channel_cfg:
        cta = channel_cfg.get("cta_template", "")
        if channel_cfg.get("profile_link_url") == "TBD":
            # プロフィールリンク未設定のためリンク誘導文言を除去
            cta = cta.replace("プロフィールのリンクから →", "").replace("プロフィールリンク", "").strip()

    # instagram_account_id が TBD の場合は自動投稿を無効化
    auto_post_enabled = (channel_cfg or {}).get("instagram_account_id") not in (None, "TBD")
    if not auto_post_enabled:
        log.info("instagram_account_id が未設定のため auto_post_enabled=false で保存します")

    body_parts = [p for p in [caption, cta, hashtags] if p]
    entry = {
        "status": "pending",
        "media_type": "REELS",
        "media_path": str(video_path),
        "caption": "\n\n".join(body_parts),
        "brand": brand,
        "channel": (channel_cfg or {}).get("name", brand),
        "auto_post_enabled": auto_post_enabled,
        "created_at": timestamp,
    }

    out_file = queue_dir / f"{timestamp}_{brand}_reel.yaml"
    with open(out_file, "w", encoding="utf-8") as f:
        yaml.dump(entry, f, allow_unicode=True)
    log.info(f"投稿キューに追加: {out_file}")


def _test_data():
    return {
        "title": "テスト: 副業で月10万稼ぐ方法",
        "body": "副業で月10万円を稼ぐには、まず自分のスキルを棚卸しすることが重要です。プログラミング、デザイン、ライティングなど、現代では様々なスキルが収益化できます。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Blog → Reel パイプライン")
    parser.add_argument("--url", help="ブログ記事URL")
    parser.add_argument("--text", help="ブログ本文テキスト")
    parser.add_argument("--title", help="ブログタイトル")
    parser.add_argument("--script", help="NoimosAI台本YAMLファイルのパス")
    parser.add_argument("--latest", action="store_true", help="最新記事を取得")
    parser.add_argument("--brand", default="satoshi-blog", help="ブランド名")
    parser.add_argument("--channel", default="satoshi",
                        help="投稿チャネル: satoshi / upj / both (channel_settings.yaml のキー)")
    parser.add_argument("--format", default="howto", help="動画フォーマット: profit_reveal/howto/failure_story/before_after/ranking")
    parser.add_argument("--target", default="beginner", help="ターゲット層: beginner/intermediate/advanced")
    parser.add_argument("--post", action="store_true", help="完成後に投稿キューへ追加")
    parser.add_argument("--dry-run", action="store_true", help="投稿せずに動画だけ生成")
    parser.add_argument("--test", action="store_true", help="テストデータで動作確認")
    parser.add_argument("--use-veo", action="store_true", help="Veo 3 Fastで映像生成（デフォルト: Nano Banana 2）")
    args = parser.parse_args()

    fmt = getattr(args, "format", "howto")
    tgt = getattr(args, "target", "beginner")
    use_veo = getattr(args, "use_veo", False)

    # チャネル解決（pending チェック含む）
    target_channels = _resolve_channels(args.channel)

    def _run(channel_key: str):
        base = dict(brand=args.brand, channel=channel_key, format_key=fmt,
                    target_key=tgt, use_veo=use_veo)
        if args.test:
            d = _test_data()
            run_pipeline(blog_text=d["body"], blog_title=d["title"], dry_run=True, **base)
        elif args.latest:
            fetcher = BlogFetcher(brand=args.brand)
            latest = fetcher.fetch_latest()
            run_pipeline(blog_text=latest["content"], blog_title=latest["title"],
                         post=args.post, dry_run=args.dry_run, **base)
        elif args.script:
            run_pipeline(script_file=args.script, post=args.post, dry_run=args.dry_run, **base)
        else:
            run_pipeline(blog_url=args.url, blog_text=args.text, blog_title=args.title,
                         post=args.post, dry_run=args.dry_run, **base)

    for ch in target_channels:
        log.info(f"--- チャネル: {ch} ---")
        _run(ch)
