"""
Nano Banana 2 (gemini-3.1-flash-image-preview) による画像→動画生成モジュール。

フロー:
    1. Gemini API で映画品質の 1080x1920 PNG を生成
    2. ffmpeg zoompan (Ken Burns) で MP4 に変換
    失敗時は Pillow スライドにフォールバック。

コスト: $0.045 / 枚
"""

import io
import logging
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

TEMP_DIR = Path("/tmp/reel_pipeline/scenes")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

MODEL = "gemini-3.1-flash-image-preview"
COST_PER_IMAGE = 0.045
REEL_W = 1080
REEL_H = 1920
FPS = 25
FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"


class NanaBananaGenerator:
    """Gemini画像生成 → Ken Burns動画変換。pipeline のデフォルト映像生成器。"""

    def __init__(self):
        self.api_key = os.environ.get("GOOGLE_AI_STUDIO_API_KEY", "")
        self.images_generated = 0
        if self.api_key:
            log.info(f"NanaBananaGenerator初期化: {MODEL}")
        else:
            log.info("GOOGLE_AI_STUDIO_API_KEY未設定 → Pillowスライドフォールバックを使用")

    def generate(
        self,
        prompt: str,
        telop: str = "",
        duration: int = 5,
        scene_index: int = 0,
    ) -> Path:
        """シーン動画を生成して MP4 パスを返す。"""
        out_path = TEMP_DIR / f"scene_{scene_index:02d}.mp4"

        img_path = None
        if self.api_key:
            try:
                img_path = self._generate_image(prompt, scene_index)
                self.images_generated += 1
            except Exception as e:
                log.warning(f"Nano Banana画像生成失敗 ({e}) → Pillowスライドフォールバック")

        if img_path is None:
            img_path = self._make_slide(telop or prompt[:20], scene_index)

        return self._ken_burns(img_path, duration, out_path, scene_index)

    # ──────────────────────────────────────────────
    # Gemini 画像生成
    # ──────────────────────────────────────────────

    def _generate_image(self, prompt: str, scene_index: int) -> Path:
        """Gemini API で 1080x1920 PNG を生成"""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        enhanced = (
            "Cinematic vertical 9:16 portrait, dramatic lighting, "
            "professional photography, movie quality. "
            f"{prompt}"
        )

        response = client.models.generate_content(
            model=MODEL,
            contents=enhanced,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )

        part = response.candidates[0].content.parts[0]
        img_bytes = part.inline_data.data
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img = img.resize((REEL_W, REEL_H), Image.LANCZOS)

        out_path = TEMP_DIR / f"scene_{scene_index:02d}_nb.png"
        img.save(str(out_path), "PNG")
        log.info(f"✓ Nano Banana画像生成完了: {out_path}")
        return out_path

    # ──────────────────────────────────────────────
    # Pillow スライド（フォールバック）
    # ──────────────────────────────────────────────

    def _make_slide(self, text: str, scene_index: int) -> Path:
        """近未来UIスライド: サイバーグリッド + ネオングロー + テキスト"""
        import hashlib, math

        # シーンごとにアクセントカラーを変える
        hue = int(hashlib.md5(f"{text}{scene_index}".encode()).hexdigest()[:2], 16)
        ACCENTS = [
            (0, 240, 255),    # シアン
            (180, 0, 255),    # パープル
            (0, 255, 160),    # ネオングリーン
            (255, 60, 120),   # ホットピンク
            (255, 180, 0),    # ゴールド
        ]
        accent = ACCENTS[scene_index % len(ACCENTS)]
        accent_dim = tuple(max(0, c // 4) for c in accent)

        # ── 背景: 深宇宙グラデーション ──
        img = Image.new("RGB", (REEL_W, REEL_H), (4, 4, 12))
        draw = ImageDraw.Draw(img)
        for y in range(REEL_H):
            ratio = y / REEL_H
            r = int(4 + accent[0] * 0.06 * (1 - ratio))
            g = int(4 + accent[1] * 0.04 * (1 - ratio))
            b = int(12 + accent[2] * 0.10 * (1 - ratio))
            draw.line([(0, y), (REEL_W, y)], fill=(r, g, b))

        # ── グリッドライン ──
        grid_color = tuple(max(0, c // 8) for c in accent)
        for x in range(0, REEL_W, 80):
            draw.line([(x, 0), (x, REEL_H)], fill=grid_color, width=1)
        for y in range(0, REEL_H, 80):
            draw.line([(0, y), (REEL_W, y)], fill=grid_color, width=1)

        # ── スキャンライン（横縞の光）──
        for y in range(0, REEL_H, 4):
            alpha = int(8 + 4 * math.sin(y / 40))
            draw.line([(0, y), (REEL_W, y)], fill=(0, 0, 0), width=1)

        # ── アクセントライン（上下帯）──
        bar_h = 8
        for i in range(bar_h):
            alpha = int(255 * (1 - i / bar_h))
            c = tuple(int(v * alpha / 255) for v in accent)
            draw.line([(0, i), (REEL_W, i)], fill=c)
            draw.line([(0, REEL_H - 1 - i), (REEL_W, REEL_H - 1 - i)], fill=c)

        # ── コーナーブラケット ──
        blen, bw = 80, 4
        corners = [(40, 80), (REEL_W - 40, 80), (40, REEL_H - 80), (REEL_W - 40, REEL_H - 80)]
        for cx, cy in corners:
            sx = 1 if cx < REEL_W // 2 else -1
            sy = 1 if cy < REEL_H // 2 else -1
            draw.line([(cx, cy), (cx + sx * blen, cy)], fill=accent, width=bw)
            draw.line([(cx, cy), (cx, cy + sy * blen)], fill=accent, width=bw)

        # ── 中央グロー円 ──
        cx, cy, r = REEL_W // 2, REEL_H // 2, 320
        for ring in range(6, 0, -1):
            rr = r + ring * 20
            alpha = int(18 - ring * 2)
            c = tuple(min(255, int(v * alpha / 18)) for v in accent)
            draw.ellipse([(cx - rr, cy - rr), (cx + rr, cy + rr)], outline=c, width=2)

        # ── テキストグロー ──
        try:
            font_main = ImageFont.truetype(FONT_BOLD, 88)
            font_small = ImageFont.truetype(FONT_BOLD, 44)
        except Exception:
            font_main = font_small = ImageFont.load_default()

        lines = [text[i:i+10] for i in range(0, len(text), 10)][:3]
        text_y = REEL_H // 2 - len(lines) * 60

        for line in lines:
            # グロー（アクセントカラーでぼかし風に重ね描き）
            for dx, dy in [(-3, -3), (3, -3), (-3, 3), (3, 3), (0, -4), (0, 4), (-4, 0), (4, 0)]:
                glow_c = tuple(min(255, int(v * 0.5)) for v in accent)
                draw.text((REEL_W // 2 + dx, text_y + dy), line, font=font_main, fill=glow_c, anchor="mm")
            # 本体（白）
            draw.text((REEL_W // 2, text_y), line, font=font_main, fill=(255, 255, 255), anchor="mm")
            text_y += 120

        # ── シーン番号バッジ ──
        badge = f"SCENE {scene_index + 1:02d}"
        draw.text((REEL_W // 2, REEL_H - 140), badge, font=font_small, fill=accent, anchor="mm")
        # バッジ下線
        bw2 = 160
        draw.line([(REEL_W // 2 - bw2, REEL_H - 120), (REEL_W // 2 + bw2, REEL_H - 120)], fill=accent, width=2)

        out_path = TEMP_DIR / f"scene_{scene_index:02d}_slide.png"
        img.save(str(out_path), "PNG")
        return out_path

    # ──────────────────────────────────────────────
    # Ken Burns（ffmpeg zoompan）
    # ──────────────────────────────────────────────

    # 画像を1.15倍にスケールしてパン（4方向ローテーション）
    # crop の x/y 式で n（フレーム番号）を使って滑らかに移動
    _W15 = int(REEL_W * 1.15)
    _H15 = int(REEL_H * 1.15)

    _MOTIONS = [
        # 0: 左→右パン
        lambda f, w, h, W, H: f"scale={W}:{H},crop={w}:{h}:'(iw-{w})*n/max({f}-1,1)':'(ih-{h})/2',setsar=1",
        # 1: 上→下パン
        lambda f, w, h, W, H: f"scale={W}:{H},crop={w}:{h}:'(iw-{w})/2':'(ih-{h})*n/max({f}-1,1)',setsar=1",
        # 2: 右→左パン
        lambda f, w, h, W, H: f"scale={W}:{H},crop={w}:{h}:'(iw-{w})*(1-n/max({f}-1,1))':'(ih-{h})/2',setsar=1",
        # 3: 下→上パン
        lambda f, w, h, W, H: f"scale={W}:{H},crop={w}:{h}:'(iw-{w})/2':'(ih-{h})*(1-n/max({f}-1,1))',setsar=1",
    ]

    def _ken_burns(self, img_path: Path, duration: int, out_path: Path, scene_index: int = 0) -> Path:
        frames = duration * FPS
        vf = self._MOTIONS[scene_index % len(self._MOTIONS)](
            frames, REEL_W, REEL_H, self._W15, self._H15
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-t", str(duration),
            "-i", str(img_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast",
            "-threads", "4",
            "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg変換失敗: {result.stderr[-500:]}")
        log.info(f"✓ シーン動画生成完了: {out_path}")
        return out_path

    @property
    def total_cost(self) -> float:
        return self.images_generated * COST_PER_IMAGE
