#!/bin/bash
# Railway / 本番サーバー用ダッシュボード起動スクリプト
cd "$(dirname "$0")"
export PYTHONPATH="$(pwd)/dashboard:$(pwd)"

mkdir -p logs

# scheduler をバックグラウンドで起動（クラッシュ時は自動再起動）
(
  while true; do
    python3 scheduler.py >> logs/scheduler.log 2>&1
    echo "$(date '+%Y-%m-%d %H:%M:%S') scheduler 停止 → 10秒後に再起動" >> logs/scheduler.log
    sleep 10
  done
) &

exec gunicorn "app:app" \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers 1 \
    --timeout 120 \
    --chdir "$(pwd)/dashboard" \
    --access-logfile - \
    --error-logfile -
