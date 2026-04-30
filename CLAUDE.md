# 会社全体設定 — Claude Code OS

## 目的

1. **自動化本体** — `automation-system/`：SNS自動投稿・LINE自動返信・リード起票・フォローアップ・朝のサマリー通知
2. **マーケ** — `marketing-system/`：リール設計・文案・品質ゲート
3. **店舗更新の自動化** — `shop-update-system/`：店舗情報の一元管理と各チャネルへの反映
4. **営業〜継続** — `sales-system/` → `project-system/` → `finance-system/` → `customer-success-system/`

## 前提（日本ビジネス）

- 店舗マスタの正は `shop-update-system/shops/`（日本法人・国内サイト表記）
- 越境・多言語は各 `profile.yaml` のメモで区別する
- 過剰約束をしない。`sales/WHAT_WE_SELL.md` に沿う

## 作業の優先順位

- 仕様とデータの型（YAML/テンプレ）を先に固める → スクリプト・API は後から足す
- 「全部自動」は最終形。v1 は **単一の正（マスタ）** と **更新チェックリスト** まで

## 触る場所

| 領域 | パス |
|------|------|
| 自動化（SNS・営業） | `automation-system/` |
| マーケ | `marketing-system/` |
| 店舗更新 | `shop-update-system/` |
| 営業 | `sales-system/` |
| 案件・納品 | `project-system/` |
| 財務・請求 | `finance-system/` |
| カスタマーサクセス | `customer-success-system/` |
| Cursor ルール | `.cursor/rules/*.mdc` |

---

## Week 3: サブエージェント ルーティングテーブル

タスクの性質に応じて下記のサブエージェントを使う。

| ドメイン | 使うエージェント | トリガー例 |
|----------|----------------|-----------|
| コード実装・バグ修正 | `coder` | Pythonエラー、新機能実装、DB変更 |
| インフラ・Railway・デプロイ | `ops` | デプロイ失敗、env管理、Railwayログ確認 |
| SNS台本・LP文章・DM文 | `marketer` | 投稿文作成、セールスコピー、キャッチコピー |
| コンテンツカレンダー・TikTok分析 | `content` | 投稿計画、バズ分析、リール設計 |
| 新規ユーザー獲得・DM営業 | `growth` | 集客施策、紹介プログラム、ファネル改善 |
| KPI分析・週次レポート | `analyst` | 売上分析、チャーン分析、競合比較 |
| 機能要件・ロードマップ | `planner` | 新機能の要件定義、優先順位付け |
| ユーザー問い合わせ・解約防止 | `support` | ユーザー対応、オンボーディング改善 |
| ファイル検索・コード探索 | `Explore` | シンボル検索、パス特定、広域調査 |
| 実装計画・アーキ設計 | `Plan` | 複数ファイル変更、設計判断 |

**判断基準**: タスクが「調べる系」なら Explore → 「作る系」なら coder → 「運用系」なら ops → 「書く系」なら marketer/content

---

## Week 4: 運用ループ（無人運転チェックリスト）

### 日次（自動）
- [ ] 05:30 CEO Dispatcher が当日タスクを生成（`morning_operator.py`）
- [ ] 07:00 SNS投稿キューをドレイン（`content_queue/`）
- [ ] 朝サマリーをLINEに送信

### 週次（確認）
- [ ] 月曜: 週次KPIレポート（/weekly-report）
- [ ] 木曜: コンテンツカレンダー確認（/instagram-carousel）
- [ ] 金曜: 翌週分のSNS素材ストック確認

### 月次（戦略）
- [ ] 競合スキャン（/competitor-scan）
- [ ] Stripe売上確認（/stripe-check）
- [ ] ロードマップ見直し

---

## キーコマンド

```bash
# Railway
railway status          # デプロイ状態確認
railway logs            # ログ確認
railway up              # デプロイ（確認してから）

# ローカル開発
cd automation-system
python3 morning_operator.py   # 朝のオペレーター手動実行
python3 server.py             # ダッシュボード起動 (port 5001)

# CEOディスパッチャー
python3 -c "from agents.ceo_executor import run_ceo_dispatch; run_ceo_dispatch()"
```

---

## やってはいけないこと

- `.env` を git commit しない
- `railway up` を確認なしに叩かない（本番影響）
- 店舗マスタ（`shop-update-system/shops/`）を直接編集してチャネル反映を忘れない
- force push しない
