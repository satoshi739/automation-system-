"""
Google Veo API による映像生成モジュール。
Veoが使えない場合（APIキー未設定・クォータ超過）は Ken Burns フォールバックに自動切替。

Google AI Studio APIキー: GOOGLE_AI_STUDIO_API_KEY
無料枠: 約5本/日（Veo 2）
"""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

TEMP_DIR = Path("/tmp/reel_pipeline/scenes")
TEMP_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"
FONT_MEDIUM = "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc"

# 縦型動画（Instagram Reels / TikTok）
REEL_W = 1080
REEL_H = 1920


class VeoGenerator:
    """Veo優先、失敗時はKen Burnsフォールバック"""

    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_AI_STUDIO_API_KEY", "")
        self.use_veo = bool(self.api_key)
        self.seconds_generated = 0
        if not self.use_veo:
            log.info("GOOGLE_AI_STUDIO_API_KEY未設定 → Ken Burnsフォールバックを使用")

    def generate(
        self,
        prompt: str,
        telop: str = "",
        duration: int = 5,
        scene_index: int = 0,
    ) -> Path:
        """
        シーン動画を生成して一時ファイルのパスを返す。

        Args:
            prompt: 英語の映像説明（Veo用）
            telop: テロップテキスト（スライド生成用）
            duration: シーンの秒数
            scene_index: シーン番号（ファイル名用）

        Returns:
            生成された動画ファイルのパス（MP4）
        """
        out_path = TEMP_DIR / f"scene_{scene_index:02d}.mp4"

        if self.use_veo:
            try:
                return self._veo_generate(prompt, duration, out_path)
            except Exception as e:
                log.warning(f"Veo生成失敗 ({e}) → Ken Burnsフォールバック")

        return self._ken_burns(prompt, telop, duration, out_path)

    # ──────────────────────────────────────────────
    # Google Veo API
    # ──────────────────────────────────────────────

    def _veo_generate(self, prompt: str, duration: int, out_path: Path) -> Path:
        """Google Veo API で動画を生成"""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        log.info(f"Veo生成開始: {prompt[:60]}...")
        operation = client.models.generate_video(
            model="veo-2.0-generate-001",
            prompt=prompt,
            config=types.GenerateVideoConfig(
                aspect_ratio="9:16",
                duration_seconds=min(duration, 8),  # Veoの最大は8秒
                number_of_videos=1,
            ),
        )

        # ポーリング（最大5分）
        timeout = 300
        elapsed = 0
        while not operation.done:
            if elapsed >= timeout:
                raise TimeoutError("Veoのタイムアウト（5分）")
            time.sleep(15)
            elapsed += 15
            operation = client.operations.get(operation.name)
            log.info(f"  Veo生成中... {elapsed}秒経過")

        if not operation.response or not operation.response.generated_videos:
            raise RuntimeError("Veoから動画を取得できませんでした")

        video = operation.response.generated_videos[0]
        video_bytes = client.files.download(file=video.video)
        out_path.write_bytes(video_bytes)

        self.seconds_generated += min(duration, 8)
        log.info(f"✓ Veo動画生成完了: {out_path}")
        return out_path

    @property
    def total_cost(self) -> float:
        return self.seconds_generated * 0.15

    # ──────────────────────────────────────────────
    # Ken Burns フォールバック（完全無料）
    # ──────────────────────────────────────────────

    def _ken_burns(
        self,
        prompt: str,
        telop: str,
        duration: int,
        out_path: Path,
    ) -> Path:
        """
        スライド画像 + ffmpeg zoompan で擬似動画を生成。
        Google APIなし・完全無料。
        """
        # 1. スライド画像生成
        slide_path = TEMP_DIR / f"{out_path.stem}_slide.png"
        self._make_slide(telop or prompt[:20], slide_path)

        # 2. ffmpegでKen Burns（ズームイン）
        fps = 25
        total_frames = duration * fps

        # zoompan: 徐々にズームイン
        vf = (
            f"scale={REEL_W}:{REEL_H},"
            f"zoompan=z='min(zoom+0.0008,1.3)':"
            f"d={total_frames}:"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)',"
            f"scale={REEL_W}:{REEL_H},"
            f"setsar=1"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-t", str(duration),
            "-i", str(slide_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-threads", "4",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg Ken Burns失敗: {result.stderr[-500:]}")

        log.info(f"✓ Ken Burns動画生成完了: {out_path}")
        return out_path

    def _make_slide(self, text: str, out_path: Path):
        """Pillowでブランドスライド画像を生成（1080x1920）"""
        img = Image.new("RGB", (REEL_W, REEL_H), color=(15, 15, 25))
        draw = ImageDraw.Draw(img)

        # グラデーション風の背景（ランダムに色を変える）
        import hashlib
        hue = int(hashlib.md5(text.encode()).hexdigest()[:2], 16)
        r = max(10, min(80, hue))
        g = max(10, min(60, hue // 2))
        b = max(80, min(180, hue + 100))

        for y in range(REEL_H):
            ratio = y / REEL_H
            cr = int(r * (1 - ratio) + 5 * ratio)
            cg = int(g * (1 - ratio) + 5 * ratio)
            cb = int(b * (1 - ratio) + 20 * ratio)
            draw.line([(0, y), (REEL_W, y)], fill=(cr, cg, cb))

        # テキスト描画
        try:
            font_large = ImageFont.truetype(FONT_BOLD, 80)
            font_small = ImageFont.truetype(FONT_MEDIUM, 40)
        except Exception:
            font_large = ImageFont.load_default()
            font_small = font_large

        # テロップ（中央）
        lines = self._wrap_text(text, 12)
        y_start = REEL_H // 2 - (len(lines) * 100) // 2
        for i, line in enumerate(lines):
            y = y_start + i * 100
            # 影
            draw.text((REEL_W // 2 + 3, y + 3), line, font=font_large, fill=(0, 0, 0, 180), anchor="mm")
            draw.text((REEL_W // 2, y), line, font=font_large, fill=(255, 255, 255), anchor="mm")

        img.save(str(out_path), "PNG")

    @staticmethod
    def _wrap_text(text: str, chars_per_line: int) -> list:
        lines = []
        while text:
            lines.append(text[:chars_per_line])
            text = text[chars_per_line:]
        return lines
