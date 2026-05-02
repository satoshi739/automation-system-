# オーナー専用コマンドリファレンス

LINE で以下のコマンドを送ると AI CEO や システムを直接操作できます。

**前提条件**
- あなたの LINE ユーザー ID が `OWNER_LINE_USER_ID` に設定済みであること
- 自分の LINE アカウントで LINE Official Account チャネルの Bot を友達登録済みであること

---

## コマンド一覧

### `@ceo <指示>`
AI CEO（Claude Sonnet）に自然言語で指示を出します。
CEOはシステム状態を確認した上で、適切なエージェントにタスクを割り当てます。

- 即座に「受付しました」とリプライが届きます
- 処理完了後（通常 15〜60 秒）に結果がプッシュ通知で届きます

### `@status`
現在のシステム状態を即座に確認します（DB を直接参照、AI 処理なし）。

表示内容:
- タスク件数（完了 / 実行中 / キュー / アイドル / 失敗）
- エスカレーション数・承認待ち数
- 本日の作成・完了件数と成功率

### プレフィックスなしのメッセージ
ヘルプメッセージが返ります。コマンド一覧を確認できます。

---

## @ceo コマンド例集

### 投稿管理

```
@ceo 今日のDSC Marketing Instagram投稿を3件生成してキューに入れて
```
```
@ceo UPJ の Threads・Twitter 投稿を各1件生成して
```
```
@ceo Bangkok Peach の日英タイ3言語投稿を今週分まとめて生成して
```
```
@ceo 未投稿キューが少ないブランドを確認して補充して
```
```
@ceo 今週のコンテンツカレンダーを全ブランド分生成して
```

### リード・営業対応

```
@ceo 今日の未対応リードを一覧にして優先度順に並べて
```
```
@ceo 3日以上フォローアップできていないリードに連絡を入れて
```
```
@ceo 新規リードを資格判定してホット・ウォーム・コールドに分類して
```

### 分析・レポート

```
@ceo 今週の全ブランドパフォーマンスレポートを生成して
```
```
@ceo DSC Marketing の過去30日 Instagram のエンゲージメント上位投稿を教えて
```

### システム・緊急対応

```
@ceo 失敗しているタスクを全部確認してリトライできるものを再実行して
```
```
@ceo システム全体のヘルスチェックをして問題があれば報告して
```
```
@ceo 投稿キューの在庫を全ブランド確認して
```

### ブログ・SEO

```
@ceo Satoshiブログ用に「AI活用で副業収入を増やす方法」の記事を書いてWordPressに下書き保存して
```
```
@ceo CFJ向けに「資金繰り改善の5つのポイント」をSEO最適化してブログ公開して（コンプライアンスチェック必須）
```

---

## 動作フロー

```
あなた（LINE）
    │
    │ @ceo <指示>
    ▼
Webhook (server.py:5001)
    │ _is_owner() で本人確認
    │ 即座に「受付しました」リプライ
    │
    ▼ バックグラウンドスレッド起動
AI CEO (ceo_executor.py)
    │ Claude Sonnet がシステム状態を分析
    │ tool_use で create_agent_task を呼び出し
    │
    ▼
エージェント (agent_executor.py)
    │ agent-content-upj / agent-analytics / etc.
    │ 各エージェントがツールを使ってタスクを実行
    │
    ▼ push通知
あなた（LINE）← ✅ 完了報告 + タスク作成数 + サマリー
```

---

## 既知の制限事項

| 制限 | 内容 |
|------|------|
| 処理時間 | Claude API 呼び出しのため @ceo は 15〜60 秒かかる |
| Push通知 | LINEチャネルに友達登録済みの場合のみ届く |
| エージェント実行 | タスク生成まで行う。エージェントの実際の実行は orchestrator の tick（5分ごと）で処理される |
| 並列 @ceo | 複数同時送信すると複数スレッドが走る。1件ずつ送ることを推奨 |

---

## コマンド追加の手順

新しいコマンドを追加したい場合は `automation-system/server.py` の
`_handle_owner_message()` 関数に elif ブロックを追加します。

```python
# 例: @tasks で当日タスク一覧を返す
elif stripped.lower().startswith("@tasks"):
    # タスク一覧を取得して整形
    messenger.reply(reply_token, _get_tasks_message())
```

追加後は `./deploy.sh` で反映。

---

## トラブルシューティング

### コマンドが反応しない
1. `OWNER_LINE_USER_ID` が自分の LINE ユーザー ID と一致しているか確認
2. `launchctl list jp.upjapan.webhook` でサーバーが起動しているか確認
3. `tail -f logs/server_err.log` でエラーを確認

### @ceo の結果が届かない
1. LINE公式アカウントを友達登録しているか確認
2. `ANTHROPIC_API_KEY` が設定されているか確認（`echo $ANTHROPIC_API_KEY`）
3. `tail -f logs/server_err.log | grep "CEO dispatch"` で処理状況を確認

### 「Invalid reply token」エラー
ローカルテストでは発生するが本番では問題なし（本物のWebhookは有効なトークンを持つ）。
