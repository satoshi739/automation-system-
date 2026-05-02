# プロジェクト管理 — 使い方ガイド

## ディレクトリ構成

```
projects/
├── README.md               ← このファイル
├── {project_id}/
│   └── project-sheet.yaml  ← 案件シート（1案件につき1ファイル）
└── ...
```

- フォルダ名は `project_id`（例: `DSC-2026-001`）
- `project_id` の命名規則: `{ブランド略称}-{年}-{連番3桁}`
  - DSC → dsc-marketing
  - CSF → cashflowsupport
  - UPJ → upjapan
  - STH → satoshi

---

## フェーズ定義

| Phase | 意味 | 典型的な状態 |
|-------|------|-------------|
| P1 | リード | 商談前・見込み段階 |
| P2 | 提案中 | 提案書送付〜返答待ち |
| P3 | 契約交渉 | 条件交渉・契約書確認中 |
| P4 | 進行中 | 契約済み・稼働中 |
| P5 | 完了/継続 | 納品完了 または 月額継続中 |

---

## project-sheet.yaml 雛形

```yaml
project_id: ""                  # 例: DSC-2026-001
brand: ""                       # dsc-marketing / cashflowsupport / upjapan / satoshi
type: ""                        # monthly / one-time / consulting

client_name: ""
client_contact:
  name: ""
  email: ""
  phone: ""
  slack_or_line: ""

project_name: ""                # 例: 〇〇株式会社 SNS運用支援
description: ""

start_date: ""                  # YYYY-MM-DD
end_date: ""                    # 単発のみ。月額は空欄
monthly_renewal: true           # 月額は true / 単発は false

monthly_fee_jpy: 0              # 月額（税込）。単発は 0
total_fee_jpy: 0                # 単発合計（税込）。月額は 0

billing:
  billing_cycle: "月末締め翌月末払い"
  last_invoice_date: ""         # YYYY-MM-DD
  last_payment_date: ""         # YYYY-MM-DD
  payment_status: "current"     # current / overdue / paused / closed

phase: "P1"                     # P1〜P5
last_update: ""                 # YYYY-MM-DD (更新のたびに記録 → stale検出に使用)
next_milestone: ""
next_milestone_date: ""         # YYYY-MM-DD (7日以内で ⏰ アラート)

project_manager: "Satoshi"

deliverables:
  - ""

notes: ""
issues: ""                      # 懸念・問題点（dashboard に表示される）
```

---

## 記入例（月額 / 進行中）

```yaml
project_id: "DSC-2026-001"
brand: "dsc-marketing"
type: "monthly"

client_name: "株式会社◯◯"
client_contact:
  name: "田中 一郎"
  email: "tanaka@example.com"
  phone: "03-0000-0001"
  slack_or_line: "Slack: @tanaka"

project_name: "株式会社◯◯ SNS運用支援"
description: "Instagram/Threads/X の月次運用代行"

start_date: "2026-01-01"
end_date: ""
monthly_renewal: true

monthly_fee_jpy: 200000
total_fee_jpy: 0

billing:
  billing_cycle: "月末締め翌月末払い"
  last_invoice_date: "2026-04-30"
  last_payment_date: "2026-04-30"
  payment_status: "current"

phase: "P4"
last_update: "2026-05-01"
next_milestone: "5月コンテンツ最終確認"
next_milestone_date: "2026-05-20"

project_manager: "Satoshi"

deliverables:
  - "Instagram投稿 月12本"
  - "月次レポート"

notes: ""
issues: ""
```

## 記入例（単発 / 契約交渉中）

```yaml
project_id: "UPJ-2026-005"
brand: "upjapan"
type: "one-time"

client_name: "株式会社△△"
client_contact:
  name: "佐藤 次郎"
  email: "sato@example.com"
  phone: "06-0000-0002"
  slack_or_line: ""

project_name: "株式会社△△ コーポレートサイト制作"
description: "5ページ構成のブランドサイトリニューアル"

start_date: "2026-06-01"
end_date: "2026-08-31"
monthly_renewal: false

monthly_fee_jpy: 0
total_fee_jpy: 550000

billing:
  billing_cycle: "納品後30日払い"
  last_invoice_date: ""
  last_payment_date: ""
  payment_status: "current"

phase: "P3"
last_update: "2026-05-01"
next_milestone: "契約書締結"
next_milestone_date: "2026-05-15"

project_manager: "Satoshi"

deliverables:
  - "コーポレートサイト (5ページ)"
  - "CMS操作マニュアル"

notes: "見積もり提出済み。先方の稟議待ち。"
issues: ""
```

---

## よく使う操作

```bash
# ダッシュボード確認（コンソール表示）
python3 project-system/project_dashboard.py --dry-run

# 本番実行（LINE通知あり）
python3 project-system/project_dashboard.py
```

毎週月曜 09:30 JST に scheduler.py が自動実行する。

---

## 運用ルール

- **案件開始時**: フォルダ作成 → `project-sheet.yaml` 記入 → phase を P1 に
- **進捗更新のたびに**: `last_update` を今日の日付に更新（stale 検出のリセット）
- **完了・解約時**: `phase: P5`、`billing.payment_status: closed` に変更
- **30日以上 `last_update` を更新しないと** ⚠️ 停滞アラートが出る
