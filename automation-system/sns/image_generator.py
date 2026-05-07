"""
テロップ画像・リールスライド自動生成
PIL を使ってブランドカラーのスライド画像を生成する
"""

import textwrap
import subprocess
import os
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

# フォントパス（ヒラギノ角ゴシック）
FONT_BOLD   = "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc"
FONT_MEDIUM = "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc"
FONT_LIGHT  = "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc"

GENERATED_DIR = Path(__file__).parent.parent / "generated_media"
GENERATED_DIR.mkdir(exist_ok=True)


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _darken(rgb: tuple, factor=0.3) -> tuple:
    return tuple(max(0, int(c * factor)) for c in rgb)


def _lighten(rgb: tuple, factor=1.6) -> tuple:
    return tuple(min(255, int(c * factor)) for c in rgb)


def _load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


def _draw_text_wrapped(draw, text, x, y, width, font, fill, line_spacing=1.4):
    """テキストを折り返しながら描画"""
    chars_per_line = max(1, width // (font.size if hasattr(font, 'size') else 24))
    lines = []
    for paragraph in text.split("\n"):
        if paragraph.strip() == "":
            lines.append("")
        else:
            wrapped = textwrap.wrap(paragraph, width=chars_per_line)
            lines.extend(wrapped if wrapped else [""])

    line_height = int((font.size if hasattr(font, 'size') else 24) * line_spacing)
    total_height = line_height * len(lines)
    current_y = y - total_height // 2

    for line in lines:
        if line:
            draw.text((x, current_y), line, font=font, fill=fill, anchor="mm")
        current_y += line_height

    return current_y


def generate_title_slide(
    title: str,
    subtitle: str = "",
    brand_color: str = "#5b8af5",
    brand_name: str = "UPJ",
    size: tuple = (1080, 1080),
) -> Image.Image:
    """タイトルスライドを生成"""
    W, H = size
    brand_rgb = _hex_to_rgb(brand_color)
    dark_rgb  = _darken(brand_rgb, 0.12)

    img  = Image.new("RGB", (W, H), dark_rgb)
    draw = ImageDraw.Draw(img)

    # グラデーション風のオーバーレイ（上部に薄い帯）
    for i in range(H):
        alpha = int(30 * (1 - i / H))
        r = min(255, brand_rgb[0] + alpha)
        g = min(255, brand_rgb[1] + alpha)
        b = min(255, brand_rgb[2] + alpha)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    # ブランドカラーのアクセントバー（上部）
    draw.rectangle([(0, 0), (W, 8)], fill=brand_rgb)
    draw.rectangle([(0, H-8), (W, H)], fill=brand_rgb)

    # ブランド名（小さく上部）
    font_brand = _load_font(FONT_MEDIUM, 32)
    draw.text((W//2, 80), brand_name, font=font_brand, fill=(*brand_rgb, 200), anchor="mm")

    # メインタイトル
    font_title = _load_font(FONT_BOLD, 72 if len(title) < 20 else 56)
    _draw_text_wrapped(draw, title, W//2, H//2 - 40, W - 120, font_title, fill=(255, 255, 255), line_spacing=1.5)

    # サブタイトル
    if subtitle:
        font_sub = _load_font(FONT_LIGHT, 36)
        _draw_text_wrapped(draw, subtitle, W//2, H//2 + 160, W - 160, font_sub, fill=(*brand_rgb, 220), line_spacing=1.4)

    # デコレーション（左下の線）
    draw.rectangle([(60, H - 100), (300, H - 96)], fill=brand_rgb)

    return img


def generate_content_slide(
    point_number: int,
    point_text: str,
    detail: str = "",
    brand_color: str = "#5b8af5",
    total_points: int = 3,
    size: tuple = (1080, 1080),
) -> Image.Image:
    """ポイントスライドを生成"""
    W, H = size
    brand_rgb = _hex_to_rgb(brand_color)
    bg_rgb    = (14, 18, 32)

    img  = Image.new("RGB", (W, H), bg_rgb)
    draw = ImageDraw.Draw(img)

    # サイドアクセント
    draw.rectangle([(0, 0), (10, H)], fill=brand_rgb)

    # ポイント番号の大きな円
    cx, cy = 160, H // 3
    r = 80
    draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], fill=brand_rgb)
    font_num = _load_font(FONT_BOLD, 64)
    draw.text((cx, cy), str(point_number), font=font_num, fill=(255, 255, 255), anchor="mm")

    # 進捗バー
    bar_y = H - 60
    bar_total_w = W - 120
    bar_filled  = int(bar_total_w * point_number / total_points)
    draw.rectangle([(60, bar_y), (60 + bar_total_w, bar_y + 8)], fill=(40, 48, 70))
    draw.rectangle([(60, bar_y), (60 + bar_filled, bar_y + 8)], fill=brand_rgb)

    # ポイントテキスト
    font_point = _load_font(FONT_BOLD, 58 if len(point_text) < 20 else 46)
    _draw_text_wrapped(draw, point_text, W//2 + 30, H//2 - 20, W - 200, font_point, fill=(255, 255, 255), line_spacing=1.5)

    # 詳細テキスト
    if detail:
        font_detail = _load_font(FONT_LIGHT, 34)
        _draw_text_wrapped(draw, detail, W//2 + 30, H * 2//3 + 40, W - 200, font_detail, fill=(180, 190, 220), line_spacing=1.4)

    return img


def generate_cta_slide(
    cta_text: str,
    sub_text: str = "プロフィールのリンクからどうぞ",
    brand_color: str = "#5b8af5",
    brand_name: str = "UPJ",
    size: tuple = (1080, 1080),
) -> Image.Image:
    """CTA（行動促進）スライドを生成"""
    W, H = size
    brand_rgb = _hex_to_rgb(brand_color)
    dark_rgb  = _darken(brand_rgb, 0.10)

    img  = Image.new("RGB", (W, H), dark_rgb)
    draw = ImageDraw.Draw(img)

    # グラデーション効果
    for i in range(H):
        t = i / H
        r = int(dark_rgb[0] * (1-t) + brand_rgb[0] * t * 0.3)
        g = int(dark_rgb[1] * (1-t) + brand_rgb[1] * t * 0.3)
        b = int(dark_rgb[2] * (1-t) + brand_rgb[2] * t * 0.3)
        draw.line([(0, i), (W, i)], fill=(r, g, b))

    # 大きな矢印アイコン風
    draw.text((W//2, H//2 - 150), "↓", font=_load_font(FONT_BOLD, 120), fill=(*brand_rgb, 200), anchor="mm")

    # CTAメインテキスト
    font_cta = _load_font(FONT_BOLD, 64)
    _draw_text_wrapped(draw, cta_text, W//2, H//2 + 60, W - 120, font_cta, fill=(255, 255, 255), line_spacing=1.5)

    # サブテキスト
    font_sub = _load_font(FONT_LIGHT, 36)
    _draw_text_wrapped(draw, sub_text, W//2, H//2 + 200, W - 160, font_sub, fill=(*brand_rgb, 200), line_spacing=1.4)

    # ブランド名
    draw.text((W//2, H - 70), brand_name, font=_load_font(FONT_MEDIUM, 28), fill=(150, 160, 190), anchor="mm")

    return img


def generate_reel_slides(
    title: str,
    points: list[dict],
    cta: str,
    brand_color: str = "#5b8af5",
    brand_name: str = "UPJ",
) -> list[Image.Image]:
    """
    リール用スライド一式を生成

    Args:
        title: タイトル（例: 「集客を3倍にした3つの方法」）
        points: [{"text": "ポイント1", "detail": "詳細説明"}, ...]
        cta: CTA文（例: 「無料相談はプロフリンクから」）
        brand_color: ブランドカラー（hex）
        brand_name: ブランド名
    Returns:
        PIL Image のリスト（1枚目=タイトル、中間=ポイント、最後=CTA）
    """
    slides = []

    # タイトルスライド
    subtitle = f"{len(points)}つのポイントで解説"
    slides.append(generate_title_slide(title, subtitle, brand_color, brand_name))

    # ポイントスライド
    for i, p in enumerate(points, 1):
        slides.append(generate_content_slide(
            point_number=i,
            point_text=p.get("text", ""),
            detail=p.get("detail", ""),
            brand_color=brand_color,
            total_points=len(points),
        ))

    # CTAスライド
    slides.append(generate_cta_slide(cta, brand_color=brand_color, brand_name=brand_name))

    return slides


def save_slides(slides: list[Image.Image], prefix: str) -> list[Path]:
    """スライドをPNGとして保存し、パスのリストを返す"""
    paths = []
    for i, slide in enumerate(slides):
        path = GENERATED_DIR / f"{prefix}_slide{i+1:02d}.png"
        slide.save(path, "PNG")
        paths.append(path)
    return paths


def slides_to_video(image_paths: list, output_name: str, duration_per_slide: float = 3.0) -> Optional[Path]:
    """
    画像スライドをmp4動画に変換（FFmpegが必要）
    Returns: 動画ファイルのパス、またはFFmpeg未インストール時はNone
    """
    ffmpeg = "ffmpeg"
    try:
        result = subprocess.run([ffmpeg, "-version"], capture_output=True, timeout=5)
        if result.returncode != 0:
            return None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    output_path = GENERATED_DIR / f"{output_name}.mp4"

    # concat demuxerを使って各スライドの表示時間を設定
    concat_file = GENERATED_DIR / f"{output_name}_concat.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in image_paths:
            f.write(f"file '{p.resolve()}'\n")
            f.write(f"duration {duration_per_slide}\n")
        # 最後のファイルを再掲（concat demuxerの仕様）
        if image_paths:
            f.write(f"file '{image_paths[-1].resolve()}'\n")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vf", "scale=1080:1080:force_original_aspect_ratio=decrease,pad=1080:1080:(ow-iw)/2:(oh-ih)/2,fps=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-preset", "fast", "-crf", "22",
        str(output_path)
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
        concat_file.unlink(missing_ok=True)
        return output_path
    except Exception:
        return None


if __name__ == "__main__":
    # テスト実行
    slides = generate_reel_slides(
        title="集客を3倍にした\n3つの方法",
        points=[
            {"text": "導線を1本に絞る", "detail": "複数のSNSを中途半端に運用するより\n1媒体に集中することで効果が出やすい"},
            {"text": "毎日投稿より質を重視", "detail": "週3回の高品質投稿の方が\nフォロワーの信頼を得やすい"},
            {"text": "数字で語るコンテンツ", "detail": "「〇〇%改善」「〇日で達成」など\n具体的な数字が読者の興味を引く"},
        ],
        cta="無料相談はプロフリンクから",
        brand_color="#5b8af5",
        brand_name="UPJ",
    )
    paths = save_slides(slides, "test")
    print(f"生成完了: {paths}")
