from __future__ import annotations

"""
リールコンテンツ自動生成（1ボタン実行）

Step1: 調査 → Step2: 台本 → Step3: 文案 → Step4: 品質 → reel-brief.md + queue YAML

使い方:
  python generate_content.py --topic "自動化で副業収入を増やす方法" \\
                             --target "副業初心者・会社員" --tone カジュアル

  python generate_content.py --input brief_input.yaml

  python generate_content.py --interactive
"""

import argparse
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path

import anthropic
import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "automation-system"))
from utils import claude_resp_text, atomic_yaml_write

load_dotenv(Path(__file__).parent.parent / "automation-system" / ".env")

_ROOT = Path(__file__).parent
_OUTPUT_DIR = _ROOT / "output"
_QUEUE_DIR = _ROOT.parent / "automation-system" / "content_queue" / "marketing"

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

logger = logging.getLogger(__name__)


# ─── Claude API ──────────────────────────────────────────────────────────────

def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY が未設定です。automation-system/.env を確認してください。"
        )
    return anthropic.Anthropic(api_key=key)


def _call(client: anthropic.Anthropic, system: str, messages: list[dict]) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
    )
    return claude_resp_text(resp)


def _read_prompt(filename: str) -> str:
    return (_ROOT / "prompts" / filename).read_text(encoding="utf-8")


# ─── ID 管理 ─────────────────────────────────────────────────────────────────

def _next_output_id(today: date) -> str:
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = sorted(_OUTPUT_DIR.glob(f"{today.isoformat()}_reel-*.md"))
    num = len(existing) + 1
    return f"{today.isoformat()}_reel-{num:03d}"


# ─── 4ステップ パイプライン ───────────────────────────────────────────────────

def run_pipeline(inputs: dict) -> dict:
    """調査→台本→文案→品質 の4ステップを順番に実行して全成果物を返す"""
    client = _client()
    history: list[dict] = []

    input_block = (
        f"トピック: {inputs['topic']}\n"
        f"ターゲット: {inputs['target']}\n"
        f"トーン: {inputs['tone']}\n"
        f"禁止事項: {inputs.get('forbidden', 'なし')}\n"
        f"出典方針: {inputs.get('citation_policy', 'オリジナル主張のみ')}"
    )

    # Step 1: 調査・素材
    logger.info("Step 1/4: 調査メモ生成中...")
    history.append({"role": "user", "content": input_block})
    research = _call(client, _read_prompt("01_topic_to_research.md"), history)
    history.append({"role": "assistant", "content": research})
    logger.info("Step 1/4 完了")

    # Step 2: 台本・構成
    logger.info("Step 2/4: 台本生成中...")
    history.append({"role": "user", "content": "このメモをもとに秒付き台本を作成してください。"})
    script = _call(client, _read_prompt("02_script_and_structure.md"), history)
    history.append({"role": "assistant", "content": script})
    logger.info("Step 2/4 完了")

    # Step 3: キャプション・メタ
    logger.info("Step 3/4: キャプション・ハッシュタグ・WP骨子生成中...")
    history.append({
        "role": "user",
        "content": "台本をもとにキャプション・ハッシュタグ・WordPress骨子を作成してください。",
    })
    captions = _call(client, _read_prompt("03_captions_and_meta.md"), history)
    history.append({"role": "assistant", "content": captions})
    logger.info("Step 3/4 完了")

    # Step 4: 品質レビュー
    logger.info("Step 4/4: 品質レビュー中...")
    quality_bar = (_ROOT / "docs" / "QUALITY_BAR.md").read_text(encoding="utf-8")
    system4 = _read_prompt("04_quality_review.md") + f"\n\n---\n\n# QUALITY_BAR\n\n{quality_bar}"
    history.append({
        "role": "user",
        "content": (
            "これまでの全出力をQUALITY_BARに照らしてレビューしてください。\n"
            f"禁止事項: {inputs.get('forbidden', 'なし')}\n"
            f"トーン指定: {inputs['tone']}"
        ),
    })
    quality = _call(client, system4, history)
    logger.info("Step 4/4 完了")

    return {
        "research": research,
        "script": script,
        "captions": captions,
        "quality": quality,
    }


# ─── 出力アセンブル ──────────────────────────────────────────────────────────

def _build_brief(inputs: dict, steps: dict, content_id: str) -> str:
    today = date.today().isoformat()
    return f"""# リール設計書 — {today} — {content_id}

## 入力（そのまま記録）

| 項目 | 内容 |
|------|------|
| トピック | {inputs['topic']} |
| ターゲット | {inputs['target']} |
| トーン | {inputs['tone']} |
| 禁止事項 | {inputs.get('forbidden', 'なし')} |
| 出典方針 | {inputs.get('citation_policy', 'オリジナル主張のみ')} |
| ブランド | {inputs.get('brand', 'satoshi')} |
| プラットフォーム | {inputs.get('platform', 'instagram')} |

---

## Step 1: 調査メモ（事実・境界線・引用候補）

{steps['research']}

---

## Step 2: 秒付き台本・構成

{steps['script']}

---

## Step 3: キャプション・ハッシュタグ・WordPress骨子

{steps['captions']}

---

## Step 4: 品質レビュー（QUALITY_BAR 照合）

{steps['quality']}
"""


def _extract_short_caption(captions_text: str) -> str:
    """Step 3 の出力から短キャプションを抽出する（フォールバック付き）"""
    for pat in [
        r'###\s*短[^\n]*\n+(.*?)(?=\n###|\Z)',
        r'\*\*キャプション短[^\n]*\*\*[^\n]*\n+(.*?)(?=\n\*\*|\Z)',
        r'短[（(][^）)]*[）)][^\n]*\n+(.*?)(?=\n\n|\Z)',
    ]:
        m = re.search(pat, captions_text, re.DOTALL)
        if m:
            text = m.group(1).strip()
            if text:
                return text[:300]
    # フォールバック: # や | 以外の最初の非空行
    for line in captions_text.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("|"):
            return stripped[:300]
    return captions_text[:300]


def _extract_hashtags(captions_text: str) -> list[str]:
    tags = re.findall(r"#[\w぀-ヿ一-鿿]+", captions_text)
    seen: dict[str, None] = {}
    for t in tags:
        seen[t] = None
    return list(seen.keys())[:10]


# ─── ファイル保存 ─────────────────────────────────────────────────────────────

def save_outputs(inputs: dict, steps: dict, content_id: str) -> tuple[Path, Path]:
    """reel-brief.md と content_queue YAML をアトミックに保存する"""
    today = date.today()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    brief_path = _OUTPUT_DIR / f"{content_id}.md"
    brief_path.write_text(_build_brief(inputs, steps, content_id), encoding="utf-8")
    logger.info(f"設計書: {brief_path}")

    _QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    queue_data = {
        "date": today.isoformat(),
        "brand": inputs.get("brand", "satoshi"),
        "platform": inputs.get("platform", "instagram"),
        "status": "draft",
        "topic": inputs["topic"],
        "caption": _extract_short_caption(steps["captions"]),
        "hashtags": _extract_hashtags(steps["captions"]),
        "reel_brief_path": str(brief_path),
        "notes": f"AI生成: {content_id}",
    }
    queue_path = _QUEUE_DIR / f"{content_id}.yaml"
    atomic_yaml_write(queue_path, queue_data)
    logger.info(f"キュー:  {queue_path}")

    return brief_path, queue_path


# ─── メインエントリ ──────────────────────────────────────────────────────────

def run(inputs: dict) -> tuple[Path, Path]:
    today = date.today()
    content_id = _next_output_id(today)
    logger.info(f"=== コンテンツ生成開始: {content_id} ===")
    logger.info(f"トピック: {inputs['topic']}  ターゲット: {inputs['target']}  トーン: {inputs['tone']}")

    steps = run_pipeline(inputs)
    brief_path, queue_path = save_outputs(inputs, steps, content_id)

    logger.info(f"=== 完了: {content_id} ===")
    return brief_path, queue_path


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _interactive_input() -> dict:
    print("\n=== リールコンテンツ生成（対話モード）===\n")
    topic = input("トピック: ").strip()
    if not topic:
        raise ValueError("トピックは必須です")
    target = input("ターゲット [一般]: ").strip() or "一般"
    tone = input("トーン（カジュアル/真面目/権威）[カジュアル]: ").strip() or "カジュアル"
    forbidden = input("禁止事項（任意）[なし]: ").strip() or "なし"

    cp_choices = ["オリジナル主張のみ", "要約のみ", "引用可"]
    print("出典方針: 1=オリジナル主張のみ  2=要約のみ  3=引用可  [Enter=1]")
    cp_idx = input("番号: ").strip()
    citation_policy = cp_choices[int(cp_idx) - 1] if cp_idx in ("1", "2", "3") else cp_choices[0]

    brand_choices = ["satoshi", "dsc-marketing", "bangkok-peach"]
    print("ブランド: 1=satoshi  2=dsc-marketing  3=bangkok-peach  [Enter=1]")
    br_idx = input("番号: ").strip()
    brand = brand_choices[int(br_idx) - 1] if br_idx in ("1", "2", "3") else brand_choices[0]

    platform_choices = ["instagram", "threads", "twitter", "tiktok"]
    print("プラットフォーム: 1=instagram  2=threads  3=twitter  4=tiktok  [Enter=1]")
    pl_idx = input("番号: ").strip()
    platform = platform_choices[int(pl_idx) - 1] if pl_idx in ("1", "2", "3", "4") else platform_choices[0]

    return {
        "topic": topic,
        "target": target,
        "tone": tone,
        "forbidden": forbidden,
        "citation_policy": citation_policy,
        "brand": brand,
        "platform": platform,
    }


def _parse_args() -> dict:
    parser = argparse.ArgumentParser(
        description="リールコンテンツ自動生成（1ボタン実行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
例:
  python generate_content.py --topic "自動化で副業収入を増やす方法" --target "副業初心者" --tone カジュアル
  python generate_content.py --input brief_input.yaml
  python generate_content.py --interactive
        """,
    )
    parser.add_argument("--topic", help="コンテンツのトピック（1行）")
    parser.add_argument("--target", default="一般", help="ターゲット読者")
    parser.add_argument("--tone", default="カジュアル",
                        choices=["カジュアル", "真面目", "権威"], help="トーン")
    parser.add_argument("--forbidden", default="なし", help="禁止表現・禁止領域")
    parser.add_argument("--citation-policy", dest="citation_policy",
                        default="オリジナル主張のみ",
                        choices=["オリジナル主張のみ", "要約のみ", "引用可"])
    parser.add_argument("--brand", default="satoshi",
                        choices=["satoshi", "dsc-marketing", "bangkok-peach"])
    parser.add_argument("--platform", default="instagram",
                        choices=["instagram", "threads", "twitter", "tiktok"])
    parser.add_argument("--input", metavar="YAML_FILE",
                        help="入力をYAMLファイルから読み込む")
    parser.add_argument("--interactive", action="store_true", help="対話モードで入力")
    args = parser.parse_args()

    if args.interactive:
        return _interactive_input()
    if args.input:
        return yaml.safe_load(Path(args.input).read_text(encoding="utf-8"))
    if not args.topic:
        parser.error("--topic、--input、--interactive のいずれかが必要です")
    return {
        "topic": args.topic,
        "target": args.target,
        "tone": args.tone,
        "forbidden": args.forbidden,
        "citation_policy": args.citation_policy,
        "brand": args.brand,
        "platform": args.platform,
    }


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    try:
        inputs_data = _parse_args()
        out_brief, out_queue = run(inputs_data)
        print(f"\n完了")
        print(f"  設計書: {out_brief}")
        print(f"  キュー:  {out_queue}")
    except KeyboardInterrupt:
        print("\nキャンセルしました")
        sys.exit(0)
    except Exception as err:
        logger.error(f"エラー: {err}")
        sys.exit(1)
