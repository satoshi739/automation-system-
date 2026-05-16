#!/usr/bin/env python3
"""
Layer2 Instagram セットアップ進捗チェッカー

各ブランドのトークン・アカウントID設定状況をマトリクスで表示する。
secret 値は表示せず、有無のみ判定。

使い方:
    python3 check_instagram_setup.py
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ブランド定義（setup_instagram_token.py と整合）
BRANDS = [
    ("bangkok-peach",   "BANGKOK_PEACH",   "INSTAGRAM_APP_ID",  "INSTAGRAM_APP_SECRET"),
    ("dsc-marketing",   "DSC_MARKETING",   "META_APP_ID",       "META_APP_SECRET"),
    ("cashflowsupport", "CASHFLOWSUPPORT", "META_APP_ID",       "META_APP_SECRET"),
    ("upjapan",         "UPJAPAN",         "META_APP_ID",       "META_APP_SECRET"),
    ("satoshi",         "SATOSHI",         "META_APP_ID",       "META_APP_SECRET"),
]

REQUIRED_KEYS_SUFFIX = [
    "META_ACCESS_TOKEN",
    "INSTAGRAM_ACCOUNT_ID",
]


def _has(key: str) -> bool:
    return bool(os.environ.get(key, "").strip())


def main() -> None:
    print("=" * 70)
    print(" Layer2 Instagram セットアップ進捗")
    print("=" * 70)
    print()
    print(f"{'ブランド':<18} {'App ID':<8} {'Secret':<8} {'Token':<8} {'IG ID':<8} 状態")
    print("-" * 70)

    total_done = 0
    next_actions: list[str] = []

    for name, prefix, app_id_suffix, app_secret_suffix in BRANDS:
        app_id     = _has(f"{prefix}_{app_id_suffix}")
        app_secret = _has(f"{prefix}_{app_secret_suffix}")
        token      = _has(f"{prefix}_META_ACCESS_TOKEN")
        ig_id      = _has(f"{prefix}_INSTAGRAM_ACCOUNT_ID")

        mark = lambda b: "✅" if b else "❌"
        all_done = app_id and app_secret and token and ig_id

        if all_done:
            status = "完了"
            total_done += 1
        elif app_id and app_secret and token and not ig_id:
            status = "Account ID 取得待ち"
            next_actions.append(f"  • {name}: setup_instagram_token.py を実行して IG Account ID を取得")
        elif app_id and app_secret and not token:
            status = "Token 未取得"
            next_actions.append(f"  • {name}: setup_instagram_token.py を実行してトークン取得")
        elif not app_id or not app_secret:
            status = "App登録から必要"
            next_actions.append(f"  • {name}: Meta Developer Portal でアプリ作成 → {prefix}_{app_id_suffix} / {prefix}_{app_secret_suffix} を .env に追加")
        else:
            status = "不完全"

        print(f"{name:<18} {mark(app_id):<8} {mark(app_secret):<8} {mark(token):<8} {mark(ig_id):<8} {status}")

    print("-" * 70)
    print(f"進捗: {total_done}/{len(BRANDS)} ブランド完了")
    print()
    if next_actions:
        print("【次にやること】")
        for a in next_actions:
            print(a)
        print()
        print("詳細手順: automation-system/docs/META_API_SETUP.md")
    else:
        print("🎉 全ブランドのセットアップが完了しています！")


if __name__ == "__main__":
    main()
