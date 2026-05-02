# CS アカウント管理 — 使い方ガイド

## ディレクトリ構成

```
accounts/
├── README.md           ← このファイル
├── {project_id}/
│   ├── health-YYYY-MM.yaml   ← 月次ヘルスシート（毎月1件作成）
│   └── health-YYYY-MM.yaml   ← 翌月分…
└── ...
```

- フォルダ名は `project_id` と揃える（例: `DSC-2026-001`）
- ファイル名は `health-YYYY-MM.yaml`（例: `health-2026-05.yaml`）
- 月初（1〜3日以内）に当月分を作成する

---

## 月次ヘルスシート 雛形

```yaml
project_id: ""                  # project-system の project_id と一致させる
brand: ""                       # dsc-marketing / cashflowsupport / upjapan / satoshi
client_name: ""                 # 会社名または個人名
review_month: "YYYY-MM"

# --- 入金・SLA ---
payment_status: "current"       # current / overdue / paused
meetings_held: 0                # 当月ミーティング回数
sla_breaches: 0                 # 約束した返信・納品の遅延回数

# --- 満足度 ---
satisfaction: "neutral"         # high / neutral / low / unknown
relationship_owner_client: ""   # 顧客側のキーパーソン名
notes_on_relationship: ""       # 関係性の一言メモ

# --- リスク判定 ---
at_risk: false
risk_signals: []
# 例: ["返信遅延", "効果への不満", "予算削減の話題"]

# --- アクション記録 ---
actions_this_month: []
# 例:
#   - "2026-05-01: 月次定例MTG 実施"
#   - "2026-05-15: レポート送付"

next_touch_date: ""             # YYYY-MM-DD (これを過ぎると 🔴 アラート)

notes: ""
```

---

## 記入例（問題ありクライアント）

```yaml
project_id: "DSC-2026-001"
brand: "dsc-marketing"
client_name: "株式会社◯◯"
review_month: "2026-05"

payment_status: "current"
meetings_held: 0
sla_breaches: 2

satisfaction: "low"
relationship_owner_client: "田中 一郎"
notes_on_relationship: "2週間連絡が取れていない。Slack既読なし。"

at_risk: true
risk_signals:
  - "連絡途絶2週間"
  - "satisfaction低下"

actions_this_month:
  - "2026-05-01: メール送信 → 未返信"
  - "2026-05-05: 電話 → 繋がらず"
next_touch_date: "2026-05-08"

notes: "解約リスク高。次の週内に対応しなければエスカレーション。"
```

## 記入例（正常クライアント）

```yaml
project_id: "CSF-2026-003"
brand: "cashflowsupport"
client_name: "合同会社△△"
review_month: "2026-05"

payment_status: "current"
meetings_held: 2
sla_breaches: 0

satisfaction: "high"
relationship_owner_client: "鈴木 花子"
notes_on_relationship: "施策の成果を高く評価。アップセル検討中。"

at_risk: false
risk_signals: []

actions_this_month:
  - "2026-05-01: 月次定例MTG 実施"
  - "2026-05-01: 月次レポート送付"
next_touch_date: "2026-06-01"

notes: "契約更新の意向あり。"
```

---

## health_checker.py の使い方

```bash
# ローカル確認（LINE送信なし）
python3 customer-success-system/health_checker.py --dry-run

# 本番実行（LINE通知あり）
python3 customer-success-system/health_checker.py
```

毎週月曜 09:00 JST に scheduler.py が自動実行する。
