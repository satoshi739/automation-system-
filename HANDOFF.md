# 自動化システム 引き継ぎサマリー
生成日時: 2026-04-30

---

## 1. 稼働中サービス一覧

| PID | ポート | Label | 役割 | スクリプト |
|-----|--------|-------|------|-----------|
| 5015 | 5001 | jp.upjapan.webhook | LINE Webhook受信 + Flask API | run_server.sh → server.py |
| 5005 | 8080 | jp.upjapan.dashboard | 管理ダッシュボード (GUI) | run_dashboard.sh → dashboard/app.py |
| 74333 | — | jp.upjapan.scheduler | 定時タスク実行（status=1: 要確認） | run_scheduler.sh → scheduler.py |
| 77354 | 3000 | — | bussan-system Next.js dev（別プロジェクト） | desktop/bussan-system/frontend |

> ⚠️ `jp.upjapan.scheduler` が status=1（終了済み）。scheduler.py のログを確認すること。

---

## 2. launchd 登録ジョブ

| Label | plist | 起動条件 | エントリポイント |
|-------|-------|---------|----------------|
| jp.upjapan.webhook | ~/Library/LaunchAgents/ | OS起動時 (KeepAlive) | run_server.sh |
| jp.upjapan.scheduler | ~/Library/LaunchAgents/ | OS起動時 (KeepAlive) | run_scheduler.sh |
| jp.upjapan.dashboard | ~/Library/LaunchAgents/ | OS起動時 (KeepAlive) | run_dashboard.sh |

crontab: 未登録。Claude Code CronCreate でセッション内Cron稼働中（後述）。

---

## 3. .claude/settings.json Hooks一覧

**場所**: `/Users/satoshi/会社全体設定/.claude/settings.json`（プロジェクトレベル）
グローバル設定: `~/.claude/settings.json`

| タイミング | matcher | 役割 |
|-----------|---------|------|
| PostToolUse | Edit\|Write | YAML構文検証（statusMessage付き） |
| PostToolUse | Edit\|Write | automation-system/*.py 変更時 → Railwayデプロイリマインド |
| PostToolUse | Edit\|Write | .env 変更時 → git commit 禁止警告 |
| PostToolUse | Edit\|Write | shop-update-system/shops/*.yaml → チャネル反映リマインド |
| PreToolUse | Bash | `git push --force` → ブロック |
| PreToolUse | Bash | `rm -rf` → 警告表示 |
| Stop | * | Mac通知「会社OS: 作業完了」 |

---

## 4. エージェントルーティングテーブル

| ドメイン | エージェント |
|----------|------------|
| コード実装・バグ修正 | `coder` |
| Railway/Vercel/インフラ | `ops` |
| SNS台本・LP・DM文 | `marketer` / `content` |
| KPI・分析・週次レポート | `analyst` |
| 新機能要件・ロードマップ | `planner` |
| ユーザー問い合わせ・解約防止 | `support` |
| 新規獲得・紹介施策 | `growth` |
| 広域コード探索（3クエリ以上） | `Explore` |
| 実装計画・アーキ設計 | `Plan` |

**判断基準**: 調べる→Explore / 作る→coder / 運用→ops / 書く→marketer or content

---

## 5. 直近1週間の変更ファイル

```
95aca57  automation-system/dashboard/app.py
         automation-system/dashboard/templates/settings.html
fa6b0ac  automation-system/dashboard/templates/base.html
74c811f  automation-system/agents/ceo_executor.py
         automation-system/dashboard/real_service.py
         automation-system/dashboard/templates/ceo.html
         automation-system/scheduler.py
dcc28f4  automation-system/agents/agent_executor.py
20b96e1  automation-system/agents/agent_executor.py
         automation-system/config/os_config.yaml
6bd751f  automation-system/org_database.py
         automation-system/setup_from_config.py
fb69b15  automation-system/get_gbp_token.py
948ef34  automation-system/connectors/gbp_connector.py
         automation-system/.env.example
```

---

## 6. 未解決タスク

| 優先度 | タスク | 詳細 |
|--------|--------|------|
| ⚠️ 高 | scheduler クラッシュ | jp.upjapan.scheduler が status=1。`cat automation-system/logs/` で原因確認 |
| 中 | GBP本番接続 | get_gbp_token.py / gbp_connector.py 実装済みだが本番OAuth未完 |
| 中 | Cron永続化 | 朝サマリー・週次レポートCronが Claude Codeセッション内限定。`/schedule` で永続化が必要 |
| 低 | Railway env同期 | settings.html に同期UIを追加済み。実際の同期動作テスト未完 |

---

## スタック早見表

```
Language:   Python 3.9 (automation) / Next.js 16 (bussan-system)
Hosting:    Railway (本番) / launchd (ローカル常駐)
DB:         SQLite (database.py, org_database.py)
LLM:        claude-sonnet-4-6 (CEOディスパッチャー)
LINE:       line-bot-sdk (Webhook on port 5001)
Project:    /Users/satoshi/会社全体設定/automation-system/
```
