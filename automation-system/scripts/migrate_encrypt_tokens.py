"""
social_accounts.access_token の平文データを Fernet 暗号化に一括移行するスクリプト。

使い方:
    TOKEN_ENCRYPTION_KEY=<key> python3 scripts/migrate_encrypt_tokens.py

キーの生成（初回のみ）:
    python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    → 生成されたキーを .env に TOKEN_ENCRYPTION_KEY=<key> で追記する

動作:
    - 既に暗号化済みのトークンはスキップ（冪等）
    - NULL / 空文字のトークンはスキップ
    - 移行前に data/upj.db のバックアップを自動作成
"""

import os
import sys
from pathlib import Path

# プロジェクトルートを sys.path に追加
_HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HERE))

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv(_HERE / ".env")

import database as db


def _get_fernet() -> Fernet:
    key = os.environ.get("TOKEN_ENCRYPTION_KEY", "")
    if not key:
        print("❌ 環境変数 TOKEN_ENCRYPTION_KEY が未設定です。")
        print("   python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")
        print("   で生成して .env に追記してください。")
        sys.exit(1)
    return Fernet(key.encode())


def is_encrypted(fernet: Fernet, token: str) -> bool:
    """既に Fernet 暗号化済みかチェック（冪等性の確保）"""
    if not token:
        return False
    try:
        fernet.decrypt(token.encode())
        return True
    except Exception:
        return False


def migrate():
    fernet = _get_fernet()

    # バックアップ（移行前に必ず取得）
    backup_path = db.backup_db()
    print(f"📦 バックアップ作成: {backup_path}")

    migrated = 0
    skipped = 0

    # SELECT と UPDATE を同一トランザクションで処理する
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, access_token FROM social_accounts"
            " WHERE access_token IS NOT NULL AND access_token != ''"
        ).fetchall()

        print(f"🔍 対象レコード: {len(rows)} 件")

        for row in rows:
            account_id = row["id"]
            token = row["access_token"]

            if is_encrypted(fernet, token):
                skipped += 1
                continue

            encrypted = fernet.encrypt(token.encode()).decode()
            conn.execute(
                "UPDATE social_accounts SET access_token=? WHERE id=?",
                (encrypted, account_id),
            )
            migrated += 1

    # with ブロックを抜けた時点で全 UPDATE が一括 commit（例外時は全 rollback）
    print(f"✅ 暗号化完了: {migrated} 件 / スキップ（既暗号化）: {skipped} 件")


if __name__ == "__main__":
    migrate()
