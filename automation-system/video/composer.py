"""
ffmpeg による動画合成モジュール。
シーンクリップ + 音声 + テロップ + 効果音 → 最終動画
"""

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

TEMP_DIR = Path("/tmp/reel_pipeline/compose")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

SE_DIR = Path(os.environ.get("AUTOMATION_ROOT", "/Users/satoshi/会社全体設定/automation-system")) / "generated_media" / "se"
SE_DIR.mkdir(parents=True, exist_ok=True)

FONT_BOLD = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"
REEL_W = 1080
REEL_H = 1920
FPS = 25


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
            log.info(f"  シーン{i+1}を合成中...")
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

        return merged

    def _compose_scene(self, scene: dict, idx: int) -> Path:
        """1シーンを合成: 映像 + テロップ + 音声 + SE"""
        clip: Path = scene["clip"]
        audio: Optional[Path] = scene.get("audio")
        telop: str = scene.get("telop", "")
        duration: int = scene.get("duration", 5)
        se_type: str = scene.get("se", "none")

        out = TEMP_DIR / f"scene_{idx:02d}_final_{uuid.uuid4().hex[:6]}.mp4"

        # Step 1: テロップをPNGオーバーレイ画像として生成
        telop_png = None
        if telop:
            telop_png = self._make_telop_png(telop, idx)

        # Step 2: テロップを動画に合成
        if telop_png:
            clip_with_telop = TEMP_DIR / f"scene_{idx:02d}_telop.mp4"
            _ffmpeg(
                "-i", str(clip),
                "-i", str(telop_png),
                "-filter_complex", f"[0:v][1:v]overlay=(W-w)/2:H-h-80[v]",
                "-map", "[v]",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-t", str(duration),
                str(clip_with_telop),
            )
            clip = clip_with_telop
        else:
            # 秒数だけ揃える
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
            # 無音で出力
            _ffmpeg(
                "-i", str(clip),
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
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

    def _make_telop_png(self, text: str, idx: int) -> Path:
        """テロップ用透過PNGを生成（Pillow）"""
        out = TEMP_DIR / f"telop_{idx:02d}.png"

        img = Image.new("RGBA", (REEL_W, 200), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype(FONT_BOLD, 72)
        except Exception:
            font = ImageFont.load_default()

        # 背景帯（半透明黒）
        draw.rectangle([(0, 0), (REEL_W, 200)], fill=(0, 0, 0, 160))

        # テキスト（白・中央揃え）
        lines = self._split_telop(text)
        y = 20
        for line in lines:
            draw.text((REEL_W // 2, y), line, font=font, fill=(255, 255, 255, 255), anchor="mt")
            y += 90

        img.save(str(out), "PNG")
        return out

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

    @staticmethod
    def _split_telop(text: str, chars: int = 15) -> list:
        lines = []
        while text:
            lines.append(text[:chars])
            text = text[chars:]
            if len(lines) >= 2:
                break
        return lines
