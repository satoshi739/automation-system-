"""
ffmpeg による動画合成モジュール。
シーンクリップ + 音声 + テロップ + 効果音 + BGM → 最終動画
"""

import logging
import os
import random
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

_ROOT    = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system"))
TEMP_DIR = Path("/tmp/reel_pipeline/compose")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

SE_DIR  = _ROOT / "generated_media" / "se"
BGM_DIR = _ROOT / "generated_media" / "bgm"
SE_DIR.mkdir(parents=True, exist_ok=True)
BGM_DIR.mkdir(parents=True, exist_ok=True)

# フォント（ヒラギノ W7 → W6 → デフォルトの順で試す）
_FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴ ProN W6.ttc",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
FONT_BOLD = next((p for p in _FONT_CANDIDATES if Path(p).exists()), None)

REEL_W = 1080
REEL_H = 1920
FPS    = 25

# テロップ帯の高さ
_STRIP_H = 320

# シーンごとのアクセントカラー
_ACCENTS = [
    (255, 215,   0),   # ゴールド
    (  0, 200, 255),   # シアン
    (255,  70, 120),   # ホットピンク
    ( 80, 255, 160),   # ネオングリーン
    (180, 100, 255),   # パープル
]

# BGMの音量（0.0〜1.0 / TTS音声に対する相対ボリューム）
BGM_VOLUME = float(os.environ.get("BGM_VOLUME", "0.12"))


def _ffmpeg(*args):
    """ffmpegコマンドを実行。エラー時は詳細なメッセージを出力。"""
    cmd = ["ffmpeg", "-y"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpegエラー:\nCMD: {' '.join(cmd)}\nSTDERR: {result.stderr[-800:]}")
    return result


class VideoComposer:
    def compose(
        self,
        scenes: List[dict],
        output_path: Path,
        title: str = "",
    ) -> Path:
        """
        シーンリストから最終動画を生成。

        scenes: [
            {
                "clip": Path,      # 動画クリップ
                "audio": Path,     # TTS音声（None可）
                "telop": str,      # テロップテキスト
                "duration": int,   # 秒
                "se": str,         # 効果音タイプ
            },
            ...
        ]
        """
        scene_finals = []

        for i, scene in enumerate(scenes):
            log.info("  シーン%d を合成中...", i + 1)
            final_clip = self._compose_scene(scene, i)
            scene_finals.append(final_clip)

        log.info("全シーンを結合中...")
        merged = self._concat_scenes(scene_finals, output_path)

        # 一時ファイルを削除
        for p in scene_finals:
            try:
                p.unlink()
            except Exception:
                pass

        # BGMミックス
        total_dur = sum(s.get("duration", 5) for s in scenes)
        merged = self._mix_bgm(merged, total_dur)

        return merged

    # ─────────────────────────────────────────────────
    # シーン合成
    # ─────────────────────────────────────────────────

    def _compose_scene(self, scene: dict, idx: int) -> Path:
        """1シーンを合成: 映像 + テロップ + 音声 + SE"""
        clip: Path     = scene["clip"]
        audio: Optional[Path] = scene.get("audio")
        telop: str     = scene.get("telop", "")
        duration: int  = scene.get("duration", 5)
        se_type: str   = scene.get("se", "none")

        out = TEMP_DIR / f"scene_{idx:02d}_final_{uuid.uuid4().hex[:6]}.mp4"

        # Step 1: テロップPNGを生成
        telop_png = self._make_telop_png(telop, idx) if telop else None

        # Step 2: テロップを動画に合成（下から 120px の位置に配置）
        if telop_png:
            clip_with_telop = TEMP_DIR / f"scene_{idx:02d}_telop.mp4"
            overlay_y = f"H-h-120"
            _ffmpeg(
                "-i", str(clip),
                "-i", str(telop_png),
                "-filter_complex", f"[0:v][1:v]overlay=(W-w)/2:{overlay_y}[v]",
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-t", str(duration),
                str(clip_with_telop),
            )
            clip = clip_with_telop
        else:
            trimmed = TEMP_DIR / f"scene_{idx:02d}_trim.mp4"
            _ffmpeg(
                "-i", str(clip),
                "-t", str(duration),
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                str(trimmed),
            )
            clip = trimmed

        # Step 3: 音声を合成
        if audio and audio.exists():
            _ffmpeg(
                "-i", str(clip),
                "-i", str(audio),
                "-filter_complex",
                    f"[1:a]apad,atrim=duration={duration}[a]",
                "-map", "0:v",
                "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-t", str(duration),
                str(out),
            )
        else:
            _ffmpeg(
                "-i", str(clip),
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac",
                "-t", str(duration),
                str(out),
            )

        # 一時ファイル削除
        if telop_png:
            try:
                telop_png.unlink()
            except Exception:
                pass

        return out

    # ─────────────────────────────────────────────────
    # テロップ PNG 生成（刷新版）
    # ─────────────────────────────────────────────────

    def _make_telop_png(self, text: str, idx: int) -> Path:
        """
        映画風テロップPNGを生成。

        デザイン:
          - グラデーション背景（上→透明 / 下→半透明ダーク）
          - アクセントカラーの上辺ライン（シーンごとに変化）
          - 大きめフォント（1行=100px / 2行=80px）
          - ドロップシャドウ + カラーグロー
        """
        out = TEMP_DIR / f"telop_{idx:02d}.png"
        accent = _ACCENTS[idx % len(_ACCENTS)]

        img  = Image.new("RGBA", (REEL_W, _STRIP_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # ── グラデーション背景（上→透明 / 下→濃い半透明）──
        for y in range(_STRIP_H):
            alpha = int(210 * (y / _STRIP_H) ** 0.65)
            draw.line([(0, y), (REEL_W, y)], fill=(6, 6, 14, alpha))

        # ── アクセントライン（上辺、グロー風）──
        for i in range(6):
            a = max(0, 255 - i * 38)
            draw.line([(0, i), (REEL_W, i)], fill=(*accent, a))

        # ── テキスト ──
        lines = self._split_telop(text)
        font_size = 100 if len(lines) == 1 else 80
        try:
            font = ImageFont.truetype(FONT_BOLD, font_size) if FONT_BOLD else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()

        line_gap  = 14
        total_h   = len(lines) * font_size + (len(lines) - 1) * line_gap
        start_y   = (_STRIP_H - total_h) // 2 + 16   # 帯内で縦中央寄り

        for line in lines:
            cx = REEL_W // 2
            cy = start_y + font_size // 2

            # ドロップシャドウ（濃い黒、4方向）
            for dx, dy in [(4, 4), (-4, 4), (4, -4), (-4, -4), (0, 5)]:
                draw.text((cx + dx, cy + dy), line, font=font,
                          fill=(0, 0, 0, 220), anchor="mm")

            # アクセントカラー グロー（淡く）
            r, g, b = accent
            for dx, dy in [(2, 2), (-2, 2), (2, -2), (-2, -2)]:
                draw.text((cx + dx, cy + dy), line, font=font,
                          fill=(r, g, b, 70), anchor="mm")

            # 本体テキスト（白）
            draw.text((cx, cy), line, font=font,
                      fill=(255, 255, 255, 255), anchor="mm")

            start_y += font_size + line_gap

        img.save(str(out), "PNG")
        return out

    # ─────────────────────────────────────────────────
    # シーン結合
    # ─────────────────────────────────────────────────

    def _concat_scenes(self, clips: List[Path], output: Path) -> Path:
        """全シーンを結合"""
        list_file = TEMP_DIR / "concat_list.txt"
        with open(list_file, "w") as f:
            for clip in clips:
                f.write(f"file '{clip.resolve()}'\n")

        _ffmpeg(
            "-f", "concat",
            "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            str(output),
        )

        try:
            list_file.unlink()
        except Exception:
            pass

        return output

    # ─────────────────────────────────────────────────
    # BGM ミックス（NEW）
    # ─────────────────────────────────────────────────

    def _mix_bgm(self, video_path: Path, duration: float) -> Path:
        """
        BGMをバックグラウンドに低音量でミックスする。

        BGMファイルは generated_media/bgm/ に MP3/WAV を入れておく。
        ファイルがなければスキップ（ログメッセージのみ）。

        ファイル名アルファベット順で選択（複数ある場合はランダム）。
        """
        bgm_files = sorted(BGM_DIR.glob("*.mp3")) + sorted(BGM_DIR.glob("*.wav"))
        if not bgm_files:
            log.info(
                "BGMスキップ — %s に MP3/WAV を入れると自動ミックスされます", BGM_DIR
            )
            return video_path

        bgm = random.choice(bgm_files)
        out = video_path.with_stem(video_path.stem + "_bgm")

        try:
            _ffmpeg(
                "-i", str(video_path),
                "-stream_loop", "-1",
                "-i", str(bgm),
                "-filter_complex",
                (
                    f"[1:a]atrim=duration={duration},volume={BGM_VOLUME:.2f}[bgm];"
                    "[0:a][bgm]amix=inputs=2:duration=first:weights=1 1[aout]"
                ),
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                str(out),
            )
            video_path.unlink()
            out.rename(video_path)
            log.info("✓ BGMミックス完了: %s (vol=%.2f)", bgm.name, BGM_VOLUME)
        except Exception as e:
            log.warning("BGMミックスエラー (%s) — スキップ", e)
            if out.exists():
                out.unlink()

        return video_path

    # ─────────────────────────────────────────────────
    # ユーティリティ
    # ─────────────────────────────────────────────────

    @staticmethod
    def _split_telop(text: str, chars: int = 14) -> list:
        """テロップを最大2行に分割（14文字/行）"""
        lines = []
        while text:
            lines.append(text[:chars])
            text = text[chars:]
            if len(lines) >= 2:
                break
        return lines
