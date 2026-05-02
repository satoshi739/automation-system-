"""
TTS（Text-to-Speech）音声生成モジュール。

優先順位:
  1. OpenAI TTS（tts-1 / nova voice）   ← NEW: 自然な日本語
  2. gTTS（Google翻訳TTS・無料）        ← 従来のフォールバック
  3. pyttsx3（完全オフライン・低品質）
"""

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

TEMP_DIR = Path("/tmp/reel_pipeline/audio")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# OpenAI TTS の声設定（日本語に自然なもの優先）
# nova / shimmer → 女性っぽい自然なトーン
# onyx / echo    → 落ち着いた男性トーン
OPENAI_VOICE = os.environ.get("TTS_VOICE", "nova")
OPENAI_MODEL = "tts-1"


class TTSGenerator:
    """
    音声生成。優先順位:
      1. OpenAI TTS（OPENAI_API_KEY があれば自動選択）
      2. gTTS（完全無料、インターネット必要）
      3. pyttsx3（完全オフライン、品質は低め）
    """

    def __init__(self, speed: float = 1.0):
        """speed: atempo値。1.0=通常 / 1.1=10%速い / 0.9=10%遅い。有効範囲: 0.5〜4.0"""
        self.speed = max(0.5, min(4.0, float(speed)))
        self._openai_key = os.environ.get("OPENAI_API_KEY", "")

    def generate(self, text: str, lang: str = "ja"):
        """テキストから音声MP3を生成してパスを返す"""
        if not text.strip():
            return None

        out_path = TEMP_DIR / f"tts_{uuid.uuid4().hex[:8]}.mp3"

        # 1. OpenAI TTS（高品質）
        if self._openai_key:
            try:
                return self._openai_tts(text, out_path)
            except Exception as e:
                log.warning("OpenAI TTS失敗 (%s) → gTTSフォールバック", e)

        # 2. gTTS（無料）
        try:
            return self._gtts(text, lang, out_path)
        except Exception as e:
            log.warning("gTTS失敗 (%s) → pyttsx3フォールバック", e)

        # 3. pyttsx3（オフライン）
        try:
            return self._pyttsx3(text, out_path)
        except Exception as e:
            log.error("TTS完全失敗: %s", e)
            return None

    # ─────────────────────────────────────────────────
    # OpenAI TTS
    # ─────────────────────────────────────────────────

    def _openai_tts(self, text: str, out_path: Path) -> Path:
        from openai import OpenAI
        client = OpenAI(api_key=self._openai_key)

        response = client.audio.speech.create(
            model=OPENAI_MODEL,
            voice=OPENAI_VOICE,
            input=text,
            response_format="mp3",
        )

        if abs(self.speed - 1.0) < 0.01:
            # そのまま保存
            response.stream_to_file(str(out_path))
        else:
            # 速度調整が必要な場合は一旦 raw に保存してから atempo
            raw = out_path.with_suffix(".raw.mp3")
            response.stream_to_file(str(raw))
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw),
                 "-filter:a", f"atempo={self.speed:.2f}", str(out_path)],
                capture_output=True, text=True,
            )
            raw.unlink(missing_ok=True)
            if result.returncode != 0:
                raise RuntimeError(result.stderr[-300:])

        log.info("✓ OpenAI TTS生成: %s (voice=%s speed=%.1f)", out_path.name, OPENAI_VOICE, self.speed)
        return out_path

    # ─────────────────────────────────────────────────
    # gTTS（フォールバック1）
    # ─────────────────────────────────────────────────

    def _gtts(self, text: str, lang: str, out_path: Path) -> Path:
        from gtts import gTTS
        tts = gTTS(text=text, lang=lang, slow=False)
        if abs(self.speed - 1.0) < 0.01:
            tts.save(str(out_path))
        else:
            raw = out_path.with_suffix(".raw.mp3")
            tts.save(str(raw))
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(raw),
                 "-filter:a", f"atempo={self.speed:.2f}", str(out_path)],
                capture_output=True, text=True,
            )
            raw.unlink(missing_ok=True)
            if result.returncode != 0:
                raw.rename(out_path)
        log.info("✓ gTTS音声生成: %s (speed=%.1f)", out_path.name, self.speed)
        return out_path

    # ─────────────────────────────────────────────────
    # pyttsx3（フォールバック2・オフライン）
    # ─────────────────────────────────────────────────

    def _pyttsx3(self, text: str, out_path: Path) -> Path:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 180)
        engine.save_to_file(text, str(out_path))
        engine.runAndWait()
        log.info("✓ pyttsx3音声生成: %s", out_path.name)
        return out_path

    # ─────────────────────────────────────────────────
    # ユーティリティ
    # ─────────────────────────────────────────────────

    def get_duration(self, audio_path: Path) -> float:
        """音声ファイルの実際の長さ（秒）を返す"""
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", str(audio_path)],
            capture_output=True, text=True,
        )
        try:
            streams = json.loads(result.stdout).get("streams", [])
            return float(streams[0]["duration"]) if streams else 5.0
        except Exception:
            return 5.0
