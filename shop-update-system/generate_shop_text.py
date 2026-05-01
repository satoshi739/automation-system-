#!/usr/bin/env python3
"""
generate_shop_text.py — profile.yaml からチャネル別コピペ用テキストを出力する

Usage:
  python3 generate_shop_text.py <shop-id>
  python3 generate_shop_text.py --all
"""

import sys
import yaml
from pathlib import Path
from datetime import date, datetime

SHOPS_DIR = Path(__file__).parent / "shops"
OUTPUT_DIR = Path(__file__).parent / "output"
AUDIT_LOG = Path(__file__).parent / "audit_log.md"

DAY_LABELS = {
    "mon": "月", "tue": "火", "wed": "水",
    "thu": "木", "fri": "金", "sat": "土", "sun": "日",
}


def load_profile(shop_id: str) -> dict:
    path = SHOPS_DIR / shop_id / "profile.yaml"
    if not path.exists():
        sys.exit(f"Error: {path} が見つかりません")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def format_address(p: dict) -> str:
    addr = p.get("address", {})
    parts = [
        f"〒{addr.get('postal', '')}" if addr.get("postal") else "",
        addr.get("prefecture", ""),
        addr.get("lines", ""),
        addr.get("building", ""),
    ]
    return " ".join(x for x in parts if x)


def format_hours(p: dict) -> str:
    hours = p.get("hours", {})
    lines = []
    for key, label in DAY_LABELS.items():
        val = hours.get(key, "")
        if val:
            display = "定休日" if val.lower() == "closed" else val
            lines.append(f"  {label}: {display}")
    note = p.get("holiday_note", "")
    if note:
        lines.append(f"  ※ {note}")
    return "\n".join(lines)


def active_campaigns(p: dict) -> list:
    today = date.today()
    result = []
    for c in p.get("campaigns", []):
        try:
            start = date.fromisoformat(str(c.get("start", "2000-01-01")))
            end = date.fromisoformat(str(c.get("end", "2099-12-31")))
            if start <= today <= end:
                result.append(c)
        except ValueError:
            result.append(c)
    return result


def build_gbp(p: dict) -> str:
    name = p.get("name_ja", p.get("id", ""))
    category = p.get("category", "")
    address = format_address(p)
    phone = p.get("phone", "")
    hours_text = format_hours(p)
    usp = p.get("usp", "")
    services = p.get("services", [])
    cta = p.get("cta", "")
    website = p.get("links", {}).get("website", "")

    services_text = "\n".join(f"  ・{s}" for s in services)

    campaigns = active_campaigns(p)
    campaign_block = ""
    if campaigns:
        items = [
            f"【{c.get('name', 'キャンペーン')}】{c.get('summary', '')}（〜{c.get('end', '')}）"
            for c in campaigns
        ]
        campaign_block = "\n\n▼ 最新情報（GBP 投稿用）\n" + "\n".join(items)

    sections = [
        f"■ ビジネス名\n{name}",
        f"■ カテゴリ\n{category}",
        f"■ 住所\n{address}",
        f"■ 電話\n{phone}",
        f"■ 営業時間\n{hours_text}",
        f"■ 説明文（ビジネス説明欄）\n{usp}\n\n▼ サービス\n{services_text}\n\n{cta}",
        f"■ Webサイト\n{website}",
    ]
    if campaign_block:
        sections.append(campaign_block)

    return "\n\n".join(sections)


def build_instagram_bio(p: dict) -> str:
    name = p.get("name_ja", "")
    category = p.get("category", "")
    usp = p.get("usp", "")
    cta = p.get("cta", "")
    links = p.get("links", {})
    link = links.get("line") or links.get("website", "")

    usp_short = usp[:60] + ("…" if len(usp) > 60 else "")
    bio_lines = [name, category, usp_short, cta]
    if link:
        bio_lines.append(f"▼ {link}")

    bio = "\n".join(line for line in bio_lines if line)
    return f"{bio}\n\n（{len(bio)}文字 / 150文字上限）"


def build_wordpress(p: dict) -> str:
    name = p.get("name_ja", "")
    services = p.get("services", [])
    usp = p.get("usp", "")
    cta = p.get("cta", "")
    website = p.get("links", {}).get("website", "")

    services_text = "、".join(services)

    campaigns = active_campaigns(p)
    campaign_block = ""
    if campaigns:
        items = [
            f"◆ {c.get('name', '')}（{c.get('start', '')} 〜 {c.get('end', '')}）\n{c.get('summary', '')}"
            for c in campaigns
        ]
        campaign_block = "\n\n■ キャンペーン情報\n" + "\n\n".join(items)

    return (
        f"{name} では、{services_text}のサービスを提供しています。\n\n"
        f"{usp}\n\n"
        f"{cta}\n\n"
        f"詳しくは公式サイトをご覧ください。\n{website}"
        f"{campaign_block}"
    )


def append_audit_log(shop_id: str, channels_used: list[str]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ch_str = "、".join(channels_used) if channels_used else "（チャネルなし）"
    entry = f"| {now} | {shop_id} | generate_shop_text.py | テキスト生成: {ch_str} |\n"
    if not AUDIT_LOG.exists():
        AUDIT_LOG.write_text(
            "# 監査ログ\n\n"
            "| 日時 | shop-id | 担当 | 概要 |\n"
            "|------|---------|------|------|\n",
            encoding="utf-8",
        )
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(entry)


def generate(shop_id: str) -> None:
    p = load_profile(shop_id)
    channels = p.get("channels", {})
    today_str = date.today().isoformat()

    out_dir = OUTPUT_DIR / shop_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{today_str}.txt"

    blocks = [
        f"# {p.get('name_ja', shop_id)} — チャネル別テキスト出力",
        f"生成日: {today_str}\n",
    ]

    channels_used = []

    if channels.get("google_business_profile"):
        blocks += ["=" * 60, "【GBP — Google Business Profile】", "=" * 60, build_gbp(p)]
        channels_used.append("GBP")

    if channels.get("instagram"):
        blocks += ["\n" + "=" * 60, "【Instagram bio】", "=" * 60, build_instagram_bio(p)]
        channels_used.append("Instagram")

    if channels.get("wordpress_news"):
        blocks += ["\n" + "=" * 60, "【WordPress — お知らせ本文】", "=" * 60, build_wordpress(p)]
        channels_used.append("WordPress")

    if not channels_used:
        blocks.append("（有効なチャネルがありません。profile.yaml の channels を確認してください）")

    output = "\n".join(blocks)
    out_path.write_text(output, encoding="utf-8")
    append_audit_log(shop_id, channels_used)

    print(f"出力: {out_path}")
    print("-" * 60)
    print(output)


def main():
    if len(sys.argv) < 2:
        shops = sorted(
            d.name for d in SHOPS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )
        print("Usage: python3 generate_shop_text.py <shop-id>")
        print("       python3 generate_shop_text.py --all")
        print("\n利用可能な店舗ID:")
        for s in shops:
            print(f"  {s}")
        sys.exit(1)

    if sys.argv[1] == "--all":
        shops = sorted(
            d.name for d in SHOPS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".")
            and (SHOPS_DIR / d.name / "profile.yaml").exists()
        )
        for s in shops:
            print(f"\n{'#' * 60}\n# {s}\n{'#' * 60}")
            generate(s)
    else:
        generate(sys.argv[1])


if __name__ == "__main__":
    main()
