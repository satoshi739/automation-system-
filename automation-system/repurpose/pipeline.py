"""
ブログ → 全SNSコンテンツ自動変換パイプライン

使い方（ブログ起点）:
    python -m repurpose.pipeline --url https://example.com/post/123
    python -m repurpose.pipeline --text "本文テキスト" --title "タイトル"
    python -m repurpose.pipeline --latest --brand satoshi-blog
    python -m repurpose.pipeline --test

使い方（ラウンジ/店舗 — ブログ不要）:
    python -m repurpose.pipeline --venue --brand bangkok-lounge
    python -m repurpose.pipeline --venue --brand bangkok-lounge --theme anshin
"""

import argparse
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

import yaml

_ROOT = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system"))
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from repurpose.analyzer import BlogAnalyzer
from repurpose.generators.x_generator import XGenerator
from repurpose.generators.carousel_generator import CarouselGenerator
from repurpose.generators.story_generator import StoryGenerator
from repurpose.generators.facebook_generator import FacebookGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

REVIEW_DIR = _ROOT / "content_queue" / "review"


def run_repurpose(
    blog_url: str = None,
    blog_text: str = None,
    blog_title: str = None,
    brand: str = "satoshi-blog",
    include_reel: bool = True,
    format_key: str = "howto",
    target_key: str = "beginner",
) -> Path:
    """
    ブログ → 全SNSコンテンツを生成してレビューキューに保存。
    保存先: content_queue/review/YYYYMMDD_HHMMSS_<brand>.yaml

    Returns:
        生成したレビューYAMLファイルのパス
    """
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: ブログ本文の取得
    source_url = blog_url
    if blog_url and not blog_text:
        log.info(f"[Step 1] ブログ記事を取得: {blog_url}")
        from video.blog_fetcher import BlogFetcher
        fetcher = BlogFetcher(brand=brand)
        try:
            post_data = fetcher.fetch_by_url(blog_url)
            blog_text = post_data["content"]
            blog_title = blog_title or post_data["title"]
        except Exception as e:
            log.error(f"ブログ取得エラー: {e}")
            raise

    if not blog_text:
        raise ValueError("blog_url, blog_text のいずれかが必要です")

    blog_title = blog_title or "無題"

    # Step 2: ブログ分析
    log.info("[Step 2] ブログを分析中...")
    analyzer = BlogAnalyzer()
    analysis = analyzer.analyze(title=blog_title, body=blog_text)
    log.info(f"  トピック: {analysis.get('topic', '')}")

    # Step 3: 各プラットフォーム向けコンテンツ生成（並列実行）
    log.info("[Step 3] SNSコンテンツを生成中...")
    results = {}
    errors = {}

    def _run(key, fn):
        try:
            results[key] = fn()
        except Exception as e:
            log.error(f"{key} 生成エラー: {e}")
            errors[key] = str(e)

    threads = [
        threading.Thread(target=_run, args=("x_thread", lambda: XGenerator().generate(analysis, brand=brand))),
        threading.Thread(target=_run, args=("instagram_carousel", lambda: CarouselGenerator().generate(analysis, brand=brand))),
        threading.Thread(target=_run, args=("instagram_story", lambda: StoryGenerator().generate(analysis, brand=brand))),
        threading.Thread(target=_run, args=("facebook_post", lambda: FacebookGenerator().generate(analysis, brand=brand))),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Step 4: リール生成（非同期キュー）
    reel_video_path = ""
    if include_reel:
        log.info("[Step 4] リール生成を非同期キューに追加...")
        reel_video_path = _enqueue_reel(
            blog_text=blog_text,
            blog_title=blog_title,
            blog_url=blog_url,
            brand=brand,
            format_key=format_key,
            target_key=target_key,
        )

    # Step 5: レビューYAMLに保存
    log.info("[Step 5] レビューYAMLを生成中...")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = REVIEW_DIR / f"{timestamp}_{brand}.yaml"

    doc = _build_review_yaml(
        analysis=analysis,
        results=results,
        brand=brand,
        blog_title=blog_title,
        source_url=source_url,
        reel_video_path=reel_video_path,
        include_reel=include_reel,
        timestamp=timestamp,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        yaml.dump(doc, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    log.info(f"レビューYAML保存完了: {out_path}")
    return out_path


def _enqueue_reel(
    blog_text: str,
    blog_title: str,
    blog_url: str,
    brand: str,
    format_key: str,
    target_key: str,
) -> str:
    """
    video/pipeline.py の run_pipeline を別スレッドで非同期実行。
    即座に空文字を返し、生成完了後に video_path が埋まる。
    """
    def _run():
        try:
            from video.pipeline import run_pipeline
            run_pipeline(
                blog_url=blog_url,
                blog_text=blog_text,
                blog_title=blog_title,
                brand=brand,
                format_key=format_key,
                target_key=target_key,
                post=False,
                dry_run=True,
            )
        except Exception as e:
            log.error(f"リール非同期生成エラー: {e}")

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return ""


def _build_review_yaml(
    analysis: dict,
    results: dict,
    brand: str,
    blog_title: str,
    source_url: str,
    reel_video_path: str,
    include_reel: bool,
    timestamp: str,
) -> dict:
    generated_at = datetime.strptime(timestamp, "%Y%m%d_%H%M%S").isoformat()

    contents = []

    # X スレッド
    x = results.get("x_thread", {})
    contents.append({
        "id": "x_thread",
        "type": "x_thread",
        "status": "pending",
        "main_tweet": x.get("main_tweet", ""),
        "thread": x.get("thread", []),
    })

    # Instagram カルーセル
    carousel = results.get("instagram_carousel", {})
    contents.append({
        "id": "instagram_carousel",
        "type": "instagram_carousel",
        "status": "pending",
        "slides": carousel.get("slides", []),
        "caption": carousel.get("caption", ""),
        "hashtags": carousel.get("hashtags", []),
    })

    # Instagram ストーリーズ
    story = results.get("instagram_story", {})
    contents.append({
        "id": "instagram_story",
        "type": "instagram_story",
        "status": "pending",
        "slides": story.get("slides", []),
    })

    # Facebook 投稿
    fb = results.get("facebook_post", {})
    contents.append({
        "id": "facebook_post",
        "type": "facebook_post",
        "status": "pending",
        "body": fb.get("body", ""),
    })

    # リール（include_reel=True のときのみ）
    if include_reel:
        contents.append({
            "id": "instagram_reel",
            "type": "instagram_reel",
            "status": "queued",
            "note": "video/pipeline.py で生成済み（非同期）",
            "video_path": reel_video_path,
        })

    doc = {
        "status": "pending_review",
        "source_title": blog_title,
        "brand": brand,
        "generated_at": generated_at,
        "analysis": {
            "topic": analysis.get("topic", ""),
            "summary": analysis.get("summary", ""),
            "key_points": analysis.get("key_points", []),
            "hooks": analysis.get("hooks", []),
            "best_quote": analysis.get("best_quote", ""),
            "tone": analysis.get("tone", "casual"),
            "target": analysis.get("target", "beginner"),
        },
        "contents": contents,
    }

    if source_url:
        # source_url は source_title の直後に挿入
        doc_ordered = {"status": doc["status"], "source_title": doc["source_title"], "source_url": source_url}
        doc_ordered.update({k: v for k, v in doc.items() if k not in doc_ordered})
        return doc_ordered

    return doc


def _test_data():
    return {
        "title": "テスト: 副業で月10万稼ぐ方法",
        "body": "副業で月10万円を稼ぐには、まず自分のスキルを棚卸しすることが重要です。プログラミング、デザイン、ライティングなど、現代では様々なスキルが収益化できます。最初は3,000円〜1万円の少額からスタートして、失敗しても痛くない金額でPDCAを回すことが成功への近道です。リサーチツールを使って需要のある市場を特定し、データで判断する習慣をつけることで利益率が大きく変わります。",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ブログ → 全SNSコンテンツ変換パイプライン")
    parser.add_argument("--url", help="ブログ記事URL")
    parser.add_argument("--text", help="ブログ本文テキスト")
    parser.add_argument("--title", help="ブログタイトル")
    parser.add_argument("--latest", action="store_true", help="最新記事を取得")
    parser.add_argument("--test", action="store_true", help="ダミーデータで動作確認")
    parser.add_argument("--brand", default="satoshi-blog", help="ブランド名")
    parser.add_argument("--format", default="howto", dest="format_key", help="動画フォーマット")
    parser.add_argument("--target", default="beginner", dest="target_key", help="ターゲット層")
    parser.add_argument("--no-reel", action="store_true", help="リール生成をスキップ")
    parser.add_argument("--venue", action="store_true", help="ラウンジ/店舗モード（ブログ不要）")
    parser.add_argument("--theme", default=None, help="venue モード時のテーマキー（例: anshin, clean, japanese）")
    args = parser.parse_args()

    include_reel = not args.no_reel

    if args.venue:
        from repurpose.generators.venue_generator import VenuePostGenerator, SAFE_THEMES
        gen = VenuePostGenerator()
        result = gen.generate(brand=args.brand, theme_key=getattr(args, "theme", None))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        REVIEW_DIR.mkdir(parents=True, exist_ok=True)
        out_path = REVIEW_DIR / f"{timestamp}_{args.brand}_venue.yaml"
        doc = {
            "status": "pending_review",
            "source_title": f"[venue] {SAFE_THEMES.get(result['theme'], result['theme'])}",
            "brand": args.brand,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "contents": [{
                "id": "instagram_post",
                "type": "instagram_post",
                "status": "pending",
                **{k: v for k, v in result.items() if k not in ("type",)},
            }],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(doc, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        print(f"生成完了: {out_path}")

    elif args.test:
        d = _test_data()
        out = run_repurpose(
            blog_text=d["body"],
            blog_title=d["title"],
            brand=args.brand,
            include_reel=include_reel,
            format_key=args.format_key,
            target_key=args.target_key,
        )
        print(f"生成完了: {out}")

    elif args.latest:
        from video.blog_fetcher import BlogFetcher
        fetcher = BlogFetcher(brand=args.brand)
        latest = fetcher.fetch_latest()
        out = run_repurpose(
            blog_text=latest["content"],
            blog_title=latest["title"],
            brand=args.brand,
            include_reel=include_reel,
            format_key=args.format_key,
            target_key=args.target_key,
        )
        print(f"生成完了: {out}")

    elif args.url or args.text:
        out = run_repurpose(
            blog_url=args.url,
            blog_text=args.text,
            blog_title=args.title,
            brand=args.brand,
            include_reel=include_reel,
            format_key=args.format_key,
            target_key=args.target_key,
        )
        print(f"生成完了: {out}")

    else:
        parser.print_help()
        sys.exit(1)
