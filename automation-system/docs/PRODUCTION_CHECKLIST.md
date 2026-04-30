# 本番運用チェックリスト

本番起動・運用再開前に全項目を確認すること。完了した項目は ✅ に変更。

---

## 1. 環境変数（.env）

- [ ] `ANTHROPIC_API_KEY` — 設定済み・有効なキーか
- [ ] `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_CHANNEL_SECRET` — DSCメインBot用
- [ ] `OWNER_LINE_USER_ID` — オーナー本人の LINE User ID（U から始まる文字列）
- [ ] `BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN` / `_SECRET` — BPG Bot用（値あり）
- [ ] `DASHBOARD_PASSWORD` — 推測されにくい強固なパスワードか
- [ ] `FLASK_SECRET_KEY` — ランダムな長い文字列か
- [ ] `DRY_RUN=false` — 本番では必ず `false`
- [ ] `MOCK_MODE=false` — 本番では必ず `false`（未設定でも `false` 扱い）
- [ ] `.env` が `.gitignore` に含まれている（git commit されていない）

## 2. launchd サービス（Mac ローカル）

- [ ] `jp.upjapan.scheduler` — 起動中（`launchctl list | grep scheduler`）
- [ ] `jp.upjapan.webhook` — 起動中（`launchctl list | grep webhook`）
- [ ] `jp.upjapan.dashboard` — 起動中（`launchctl list | grep dashboard`）
- [ ] `jp.upjapan.logrotate` — 登録済み（`launchctl list | grep logrotate`）
- [ ] 全サービスの `status` が `0` または `-`（PID ありなら正常）

## 3. 死活監視

- [ ] `logs/scheduler.heartbeat` が1分以内に更新されているか
- [ ] `server.py` の heartbeat 監視スレッドが起動ログに出ているか
  （`logs/server_err.log` に「heartbeat監視を開始しました」）
- [ ] `logs/alerts.log` に未解決のアラートがないか

## 4. LINE Webhook

- [ ] LINE Developers Console の Webhook URL が正しいドメインを向いているか
- [ ] Webhook の「検証」ボタンで 200 OK が返るか
- [ ] テストメッセージを送って自動返信が来るか

## 5. Instagram 投稿

- [ ] `DRY_RUN=true` で `morning_operator.py` を手動実行してエラーがないか
- [ ] `logs/morning.log` にエラーがないか

## 6. ログローテーション

- [ ] `logs/archive/` ディレクトリが存在するか
- [ ] `python3 rotate_logs.py --dry-run` がエラーなく完了するか

## 7. ダッシュボード

- [ ] `http://localhost:8080`（または本番URL）でログイン画面が表示されるか
- [ ] ログイン後にエラーページ（500）が出ていないか
- [ ] scheduler の稼働状態が「稼働中」と表示されるか

## 8. アラート通知

- [ ] `alerts.log` に `[server_check]` または `[log_rotate]` のエントリが正常に記録されるか
- [ ] Mac 通知（osascript）が表示されるか（Mac の通知設定を確認）

---

## 緊急時の操作コマンド

```bash
# サービス再起動
launchctl kickstart -k gui/$(id -u)/jp.upjapan.scheduler
launchctl kickstart -k gui/$(id -u)/jp.upjapan.webhook
launchctl kickstart -k gui/$(id -u)/jp.upjapan.dashboard

# ログ確認
tail -f logs/server_err.log
tail -f logs/scheduler_err.log
tail -f logs/alerts.log

# scheduler 手動停止・再開
launchctl unload ~/Library/LaunchAgents/jp.upjapan.scheduler.plist
launchctl load  ~/Library/LaunchAgents/jp.upjapan.scheduler.plist

# ログローテーション手動実行
python3 rotate_logs.py --dry-run  # 確認
python3 rotate_logs.py            # 本番実行
```
