# APIコストレポート（過去30日）
生成日時: 2026-05-02T16:54:11

## サマリー
| 項目 | 値 |
|------|-----|
| 総トークン数 | 428,161 |
| 総コスト | $0.4282 (¥64) |
| 1日平均 | $0.0143 |
| 月次推計 | $0.43 (¥64) |

## エージェント別コスト
| エージェント | モデル | 実行数 | トークン | コスト |
|------------|--------|--------|---------|--------|
| agent-content-bpg | haiku-4-5 | 1回 | 113,243 | $0.1132 |
| agent-content-dsc | haiku-4-5 | 5回 | 102,380 | $0.1024 |
| agent-content-upj | haiku-4-5 | 5回 | 69,521 | $0.0695 |
| agent-content-cfj | sonnet-4-6 | 1回 | 49,187 | $0.0492 |
| agent-analytics | sonnet-4-6 | 5回 | 49,016 | $0.0490 |
| agent-blog | haiku-4-5 | 1回 | 27,513 | $0.0275 |
| agent-ops | haiku-4-5 | 1回 | 13,603 | $0.0136 |
| 12cb2ed3-8ab7-455d-a543-4b62b5734fab | sonnet-4-6 | 2回 | 2,311 | $0.0023 |
| 89d0691f-e86e-4875-9fc3-aa02307c09ba | sonnet-4-6 | 4回 | 1,387 | $0.0014 |

## Batch API 化候補（50%コスト削減）
| ジョブ | 現在 | 改善後 |
|--------|------|--------|
| agent-content-* コンテンツ生成 | リアルタイム | Batch API 化可能 |
| agent-blog ブログ記事生成 | リアルタイム | Batch API 化可能 |
| video/script_generator.py 台本生成 | リアルタイム | Batch API 化可能 |
| agent-analytics 週次分析 | リアルタイム | Batch API 化可能 |

## プロンプトキャッシュ導入済み
- `ceo_executor.py` system prompt → cache_control 適用済み
- `agent_executor.py` system prompt → cache_control 適用済み
- `video/script_generator.py` → cache_control 適用済み

## 残高監視
- 警告閾値: $20 以下で LINE 通知
- 緊急閾値: $5 以下で LINE 緊急通知
- チェック頻度: 毎朝 05:30（morning_operator.py）

残高チェック: {'balance_usd': None, 'status': 'ok', 'alerted': False}
