# 財務ログ 月次更新フロー（所要時間: 約5分）

## 概要

毎月1日に当月のログファイルが自動生成される。
数字を記入するだけでLINEレポートが送られる。

```
毎月1日 09:00 JST
  └─ scheduler が finance_tracker.py --report を自動実行
       └─ logs/YYYY-MM.yaml が未作成なら自動生成
            └─ 月次レポートをLINEに送信
```

---

## 月次更新の手順（月初 1〜3日以内に実施）

### Step 1: 当月ログを開く（30秒）

```
finance-system/logs/YYYY-MM.yaml
```

ファイルが存在しない場合は手動で生成:

```bash
python3 finance-system/finance_tracker.py
```

---

### Step 2: MRR を更新する（1分）

```yaml
mrr_start: 350000     # ← 前月の mrr_end をコピー
new_mrr: 80000        # ← 今月 新規契約したMRRの合計
churned_mrr: 0        # ← 今月 解約されたMRRの合計
mrr_end: 430000       # ← mrr_start + new_mrr - churned_mrr
```

**計算メモ:** `mrr_end = mrr_start + new_mrr - churned_mrr`

---

### Step 3: 売上・原価を更新する（2分）

```yaml
one_time_revenue: 110000       # 単発案件の入金合計（税込）

total_revenue_excl_tax: 490909 # MRR + 単発を税抜で合計
total_revenue_incl_tax: 540000 # 税込合計
cogs: 95000                    # 外注費・ツール費など直接原価（税込）
gross_profit: 395909           # total_revenue_excl_tax - cogs
```

**計算メモ:**
- 税抜き換算: 税込金額 ÷ 1.1
- 粗利 = 税抜売上 − 原価

---

### Step 4: 請求書の状況を記録する（30秒）

```yaml
invoices_sent: 5      # 今月 発行した請求書の件数
invoices_paid: 4      # 入金が確認できた件数
invoices_overdue: 1   # 支払い期日を過ぎて未入金の件数
overdue_amount: 110000 # 未回収金額の合計（税込）
```

---

### Step 5: ブランド別内訳を更新する（1分）

```yaml
by_brand:
  dsc_marketing:
    mrr: 200000       # DSCの月額MRR
    one_time: 110000  # DSCの単発売上
  cashflowsupport:
    mrr: 100000
    one_time: 0
  upjapan:
    mrr: 130000
    one_time: 0
```

---

### Step 6: 新規・解約クライアントを記録する（30秒）

```yaml
new_clients:
  - "株式会社◯◯ (MRR ¥80,000 / UPJ枠)"
churned_clients:
  - "合同会社△△ (解約理由: 予算削減)"
```

---

### Step 7: レポートを手動送信して確認する（30秒）

```bash
python3 finance-system/finance_tracker.py --report
```

コンソールに出力された内容を確認して完了。

---

## 月次以外のタイミング

| タイミング | 対応 |
|-----------|------|
| 新規契約時 | `new_mrr` と `new_clients` を当月ログに追記 |
| 解約発生時 | `churned_mrr` と `churned_clients` を当月ログに追記 |
| 請求書発行時 | `invoices_sent` を +1 |
| 入金確認時 | `invoices_paid` を +1、`invoices_overdue` と `overdue_amount` を更新 |
| 単発案件入金時 | `one_time_revenue` と `by_brand` の該当箇所を更新 |

---

## 未回収アラートを手動で送る

```bash
python3 finance-system/finance_tracker.py --alert-overdue
```

`invoices_overdue > 0` の場合のみLINEに通知される。

---

## ファイル管理ルール

- ファイルは削除しない（前月比の計算に使う）
- 前月のファイルに後から修正が必要な場合は直接編集してよい
- `finance-system/logs/` 配下が財務の正（マスタ）

---

## よくある質問

**Q: 前月比がレポートに出ない**
→ `finance-system/logs/` に前月のYAMLがあるか確認する。

**Q: ブランド別の合計がMRRと合わない**
→ `by_brand` 各社の `mrr` の合計 = `mrr_end` になるように記入する。

**Q: scheduler が毎月1日に自動実行してくれない**
→ Railway の scheduler プロセスが動いているか確認する（`railway logs`）。
