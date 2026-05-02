"""
ブログ本文 → リール台本 生成モジュール。
Claude API（既存ANTHROPIC_API_KEY）を使用。

台本フォーマット（YAML/dict）:
    title: str
    format: str                # 使用したフォーマット名
    target: str                # ターゲット層
    scenes:
      - id: int
        duration: int          # 秒
        narration: str         # ナレーション（TTS用）
        telop: str             # 画面テキスト（1〜2行）
        visual_prompt: str     # Veo用英語プロンプト
        se: str                # 効果音タイプ（whoosh/impact/none）
    caption: str               # Instagram/TikTokキャプション
    hashtags: list[str]

NoimosAI連携:
    NoimosAIで台本を生成した場合、上記フォーマットのYAMLファイルを
    generated_media/noimos_scripts/ に保存しておけば pipeline.py が自動で読み込む。
"""

import logging
import os
import random
import re
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# CTAのローテーション管理（メモリ上、プロセス再起動でリセット）
_CTA_CYCLE = ["save", "follow", "engagement"]
_cta_index = 0


def _load_yaml(filename: str) -> dict:
    path = _TEMPLATES_DIR / filename
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _next_cta() -> str:
    global _cta_index
    key = _CTA_CYCLE[_cta_index % len(_CTA_CYCLE)]
    _cta_index += 1
    return key


def _build_system_prompt(format_key: str, target_key: str, cta_key: str) -> str:
    formats = _load_yaml("formats.yaml")
    targets = _load_yaml("targets.yaml")
    ctas = _load_yaml("cta_library.yaml")

    fmt = formats.get(format_key, formats.get("howto"))
    tgt = targets.get(target_key, targets.get("beginner"))
    cta = ctas.get(cta_key, ctas.get("save"))

    structure_lines = "\n".join(f"  {s}" for s in fmt["structure"])
    hook_lines = "\n".join(f"  - {h}" for h in fmt.get("hook_examples", []))
    pain_lines = "\n".join(f"  - {p}" for p in tgt["pain_points"])
    vocab_lines = "、".join(tgt["vocabulary"])
    tone_lines = "\n".join(f"  - {t}" for t in tgt["tone"])
    cta_text = random.choice(cta) if isinstance(cta, list) and cta else "保存しといて"

    return f"""あなたはTikTok/Instagram Reelsでバズらせる台本の専門家です。
ブログ記事を元に、15〜25秒の縦型ショート動画の台本を生成してください。

## 今回のフォーマット: {format_key}
{fmt['description']}

### 構成（このシーン順を厳守）:
{structure_lines}

### バズるフックの参考例:
{hook_lines}

## ターゲット: {tgt['label']}
### この人が抱えている痛み:
{pain_lines}
### 使う語彙（難しい言葉を使わない）: {vocab_lines}
### トーン:
{tone_lines}

## CTA（シーン5の最後に必ず入れる）
{cta_text}
↑トピックと自然につながる前置きを添えてシーン5に配置。押し売り厳禁。

## 映像プロンプト（visual_prompt）の書き方
- 必ず英語で書く
- カメラアングルを指定: close-up / wide shot / overhead / POV / dolly in
- 光の質を指定: golden hour light / soft diffused light / neon glow / dramatic rim light
- 動きを指定: slow motion / time-lapse / handheld / static / tracking shot
- 例: "close-up of hands counting cash money on a wooden desk, warm golden light, shallow depth of field, cinematic 9:16"

## 出力フォーマット（YAML）
title: バズりやすい動画タイトル（20字以内）
scenes:
  - id: 1
    duration: 4
    narration: ナレーション（話し言葉、1文20字以内。読んで3〜4秒で終わる長さ）
    telop: 画面テキスト（14文字以内・1行・インパクト重視）
    visual_prompt: Cinematic scene description in English, specific camera angle and lighting
    se: impact
  - id: 2
    duration: 4
    narration: ...
    telop: ...
    visual_prompt: ...
    se: whoosh
caption: Instagram/TikTokキャプション（冒頭2行で止まらせる・200字）
hashtags:
  - "#副業"
  - "#お金の話"

## 絶対守るルール
- シーン数: 5シーン（合計15〜25秒）
- narrationは1シーン1文、20字以内（TTSで3〜4秒に収める）
- telopは14文字以内、改行コード(\n)を絶対に含めない
- visual_promptは必ず英語、カメラアングル+光の質+動きを含める
- seは whoosh / impact / none のどれか
- YAMLのみ出力（```yaml ブロック不要、コメント行不要）"""


class ScriptGenerator:
    def __init__(self):
        self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    def generate(
        self,
        title: str,
        body: str,
        format_key: str = "howto",
        target_key: str = "beginner",
        cta_key: str = None,
    ) -> dict:
        """ブログ本文から台本を生成"""
        if not self.api_key:
            log.warning("ANTHROPIC_API_KEY未設定。ダミー台本を使用します。")
            return self._dummy_script(title)

        resolved_cta = cta_key or _next_cta()
        log.info(f"台本生成: format={format_key} target={target_key} cta={resolved_cta}")

        system_prompt = _build_system_prompt(format_key, target_key, resolved_cta)
        prompt = f"# タイトル\n{title}\n\n# 本文\n{body[:3000]}"

        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            message = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=2000,
                system=[{
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            result = self._parse_yaml(raw)
            result["format"] = format_key
            result["target"] = target_key
            result["cta"] = resolved_cta
            return result
        except Exception as e:
            log.error(f"台本生成エラー: {e}")
            return self._dummy_script(title)

    def load_from_file(self, path: str) -> dict:
        """NoimosAI等の外部ツールが生成したYAMLファイルを読み込む"""
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _parse_yaml(self, raw: str) -> dict:
        # コードブロック・先頭末尾の空白行を除去
        raw = re.sub(r"```ya?ml\s*", "", raw)
        raw = re.sub(r"```", "", raw)
        # インラインコメント（#以降）を除去してYAMLエラーを防ぐ
        lines = []
        for line in raw.splitlines():
            # キー行・値行のインラインコメントを除去（URLの#は除外）
            if re.match(r"^\s*#", line):
                continue  # コメント行はスキップ
            lines.append(line)
        raw = "\n".join(lines).strip()
        yaml_keys_pattern = r"^(\s*-?\s*)(id|duration|narration|telop|visual_prompt|se|caption|hashtags|scenes|title|format|target|cta)\s*:"
        fixed_lines = []
        prev_was_telop = False
        for line in raw.split("\n"):
            stripped = line.strip()
            if prev_was_telop and stripped and not re.match(yaml_keys_pattern, line) and not stripped.startswith("-"):
                fixed_lines[-1] = fixed_lines[-1].rstrip() + " " + stripped
                prev_was_telop = bool(re.match(r"^\s*telop\s*:", line))
            else:
                fixed_lines.append(line)
                prev_was_telop = bool(re.match(r"^\s*telop\s*:", line))
        raw = "\n".join(fixed_lines)
        try:
            result = yaml.safe_load(raw)
            if not isinstance(result, dict) or "scenes" not in result:
                raise ValueError("scenesキーが見つかりません")
            for scene in result.get("scenes", []):
                if "telop" in scene and isinstance(scene["telop"], str):
                    scene["telop"] = scene["telop"].replace("\n", " ").replace("\r", " ").strip()
                    scene["telop"] = " ".join(scene["telop"].split())
            return result
        except Exception as e:
            log.error(f"YAML解析エラー: {e}\n---\n{raw[:500]}")
            return self._dummy_script("パース失敗")

    def _dummy_script(self, title: str) -> dict:
        return {
            "title": title or "テスト動画",
            "scenes": [
                {"id": 1, "duration": 3, "narration": "今日はすごいことを話します", "telop": "知らないと損！", "visual_prompt": "cinematic close-up of a person looking surprised, modern office background", "se": "impact"},
                {"id": 2, "duration": 5, "narration": "副業で月10万円稼ぐ方法があります", "telop": "月10万円の稼ぎ方", "visual_prompt": "aerial view of a city at night, money flowing, dynamic motion", "se": "whoosh"},
                {"id": 3, "duration": 5, "narration": "まず自分のスキルを棚卸しすることが大切です", "telop": "スキルを棚卸し", "visual_prompt": "person writing notes at a minimalist desk, soft lighting, focused atmosphere", "se": "none"},
                {"id": 4, "duration": 4, "narration": "プログラミング、デザイン、ライティングが人気です", "telop": "人気スキルTOP3", "visual_prompt": "split screen showing coding, design work, and writing, vibrant colors", "se": "none"},
                {"id": 5, "duration": 3, "narration": "気になる人は保存しといて", "telop": "保存推奨", "visual_prompt": "smartphone screen with arrow pointing up, bright neon colors", "se": "whoosh"},
            ],
            "caption": f"{title}\n\n副業で稼ぐ方法を解説しています。",
            "hashtags": ["#副業", "#在宅ワーク", "#お金の話", "#月10万"],
        }
