#!/bin/bash
# automation-system/deploy.sh
# コード修正後にこれ1発でスケジューラーへ反映する
#
# 使い方:
#   ./deploy.sh              # 再起動のみ
#   ./deploy.sh --commit     # git commit してから再起動
#   ./deploy.sh --commit "fix: メッセージ"   # コミットメッセージ指定
#
# 再発防止: launchd 経由で古いコードが動き続ける事故を防ぐ

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHD_LABEL="jp.upjapan.scheduler"
LOG_FILE="$SCRIPT_DIR/logs/scheduler.log"

# ────────────────────────────────────────────
# 引数パース
# ────────────────────────────────────────────
DO_COMMIT=false
COMMIT_MSG=""

for arg in "$@"; do
    case "$arg" in
        --commit)
            DO_COMMIT=true
            ;;
        --commit=*)
            DO_COMMIT=true
            COMMIT_MSG="${arg#--commit=}"
            ;;
        *)
            # --commit の次の引数はコミットメッセージ
            if $DO_COMMIT && [ -z "$COMMIT_MSG" ]; then
                COMMIT_MSG="$arg"
            fi
            ;;
    esac
done

echo "=================================="
echo " UPJ Scheduler Deploy"
echo " $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================="

# ────────────────────────────────────────────
# Step 1: git commit (オプション)
# ────────────────────────────────────────────
if $DO_COMMIT; then
    cd "$SCRIPT_DIR/.."
    if git diff --quiet && git diff --cached --quiet; then
        echo "[1/4] git: 変更なし、commit スキップ"
    else
        MSG="${COMMIT_MSG:-"chore: scheduler deploy $(date '+%Y-%m-%d %H:%M')"}"
        git add -A
        git commit -m "$MSG"
        echo "[1/4] git commit 完了: $MSG"
    fi
    cd "$SCRIPT_DIR"
else
    echo "[1/4] git commit: スキップ（--commit で有効化）"
fi

# ────────────────────────────────────────────
# Step 2: launchd 経由でスケジューラーを再起動
# ────────────────────────────────────────────
echo ""
echo "[2/4] スケジューラーを再起動..."

LAUNCHD_STATUS=$(launchctl list "$LAUNCHD_LABEL" 2>/dev/null || echo "NOT_FOUND")
if echo "$LAUNCHD_STATUS" | grep -q "NOT_FOUND"; then
    echo "  ⚠️  $LAUNCHD_LABEL が launchd に登録されていません"
    echo "     先に setup_launchd.sh を実行してください"
    exit 1
fi

launchctl kickstart -k "gui/$(id -u)/$LAUNCHD_LABEL"
echo "  → kickstart 完了"

# ────────────────────────────────────────────
# Step 3: 起動確認（最大30秒待機）
# ────────────────────────────────────────────
echo ""
echo "[3/4] 起動確認中..."

HEARTBEAT_FILE="$SCRIPT_DIR/logs/scheduler.heartbeat"
STARTED=false

for i in $(seq 1 10); do
    sleep 3
    # heartbeat ファイルが更新されていれば起動済み
    if [ -f "$HEARTBEAT_FILE" ]; then
        LAST_HB=$(cat "$HEARTBEAT_FILE" 2>/dev/null || echo "")
        HB_AGE=$(( $(date +%s) - $(date -j -f "%Y-%m-%dT%H:%M:%S" "${LAST_HB%%.*}" "+%s" 2>/dev/null || echo 0) ))
        if [ "$HB_AGE" -lt 60 ] 2>/dev/null; then
            STARTED=true
            break
        fi
    fi
    # launchctl でも確認
    CURRENT_PID=$(launchctl list "$LAUNCHD_LABEL" 2>/dev/null | awk 'NR==2 {print $1}')
    if [ -n "$CURRENT_PID" ] && [ "$CURRENT_PID" != "-" ] && [ "$CURRENT_PID" != "0" ]; then
        STARTED=true
        break
    fi
    echo "  ... 待機中 (${i}/10)"
done

if $STARTED; then
    echo "  ✅ スケジューラー起動確認 OK"
else
    echo "  ⚠️  起動確認タイムアウト（ログを確認してください）"
fi

echo ""
echo "--- morning.log (最終5行) ---"
MORNING_LOG="$SCRIPT_DIR/logs/morning.log"
if [ -f "$MORNING_LOG" ]; then
    tail -5 "$MORNING_LOG" | grep -v "werkzeug\|googleapi" || true
else
    echo "(ログファイルなし)"
fi
echo "--------------------------------"

# ────────────────────────────────────────────
# Step 4: LINE 通知
# ────────────────────────────────────────────
echo ""
echo "[4/4] LINE 通知を送信..."

TIMESTAMP="$(date '+%Y-%m-%d %H:%M')"
STATUS_MARK="✅"
$STARTED || STATUS_MARK="⚠️"

python3 - <<PYEOF
import os, sys
from pathlib import Path
sys.path.insert(0, "$SCRIPT_DIR")

from dotenv import load_dotenv
load_dotenv("$SCRIPT_DIR/.env")

from sns.line_api import LINEMessenger

started = $( $STARTED && echo "True" || echo "False" )
msg_lines = [
    "【deploy.sh】スケジューラー再起動",
    "",
    "時刻: $TIMESTAMP",
    "状態: $STATUS_MARK $( $STARTED && echo '起動確認OK' || echo '起動確認タイムアウト')",
]

# git commit した場合はコミット情報を追加
do_commit = "$DO_COMMIT" == "true"
if do_commit:
    import subprocess
    result = subprocess.run(
        ["git", "-C", "$SCRIPT_DIR/..", "log", "--oneline", "-1"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        msg_lines.append(f"commit: {result.stdout.strip()}")

messenger = LINEMessenger(
    token=os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", ""),
    secret=os.environ.get("ALERT_LINE_CHANNEL_SECRET", ""),
)
ok = messenger.push_to_owner("\n".join(msg_lines))
if ok:
    print("  → LINE送信完了")
else:
    print("  ⚠️  LINE送信スキップ（トークン未設定 or 送信失敗）")
PYEOF

echo ""
echo "=================================="
echo " Deploy 完了: $TIMESTAMP"
echo "=================================="
