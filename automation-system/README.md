# 自動化システム（Automation System）

**朝起きたら全部終わっている状態**を実現するシステム。

## 全体の流れ

```
前日夜または早朝
ナノバナナプロ（画像作成）
      ↓ Googleドライブに書き出し
      ↓
【毎朝5:00 自動実行】morning_operator.py
  ├── Google Drive から素材を取得（1時間ごとに同期）
  ├── Instagram に自動投稿
  ├── LINE に自動配信（曜日設定に従う）
  ├── フォローアップメッセージ送信
  └── 朝のサマリーをオーナーのLINEへ送信
         ↓
      LINEに通知が届く
      「✅ 全部完了。判断が必要な案件: 0件」

【24時間 常時稼働】server.py（Webhookサーバー）
  ├── LINE に問い合わせが来る
  ├── キーワード自動返信
  ├── リード自動起票（sales-system/leads/ に保存）
  └── 重要な案件 → decision_queue/ に記録（翌朝のサマリーで通知）

【2時間ごと】followup チェック
  └── リード獲得から24h・72h・168h後に自動フォローアップ
```

## セットアップ手順

### 1. 依存ライブラリのインストール

```bash
cd automation-system
pip install -r requirements.txt
```

### 2. 環境変数の設定

```bash
cp .env.example .env
```

`.env` を開いて以下を設定:

| 変数 | 取得場所 |
|------|---------|
| `META_ACCESS_TOKEN` | Meta for Developers > アプリ > アクセストークン |
| `INSTAGRAM_BUSINESS_ACCOUNT_ID` | Instagram Business アカウントのID（数値） |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE Developers > チャネル > チャネルアクセストークン |
| `LINE_CHANNEL_SECRET` | LINE Developers > チャネル > チャネルシークレット |
| `GOOGLE_DRIVE_FOLDER_ID` | ナノバナナプロの書き出し先フォルダのID |
| `OWNER_LINE_USER_ID` | 朝のサマリー受け取り用（自分のLINE user_id） |
| `FROM_EMAIL` | Gmail アドレス |
| `FROM_EMAIL_PASSWORD` | Gmail アプリパスワード |

### 3. Google Drive 連携（ナノバナナプロ用）

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクト作成
2. Drive API を有効化
3. サービスアカウントを作成 → JSONキーをダウンロード
4. `credentials.json` という名前で `automation-system/` に配置
5. ナノバナナプロの書き出しフォルダをサービスアカウントのメールアドレスと共有

### 4. LINE Webhook の設定

1. `server.py` を起動
2. 外部公開用に [ngrok](https://ngrok.com/)（開発用）または Render/Railway（本番）を使う
3. LINE Developers > Webhook URL に `https://[あなたのドメイン]/webhook` を設定

### 5. テスト実行（DRY RUN）

```bash
# .env の DRY_RUN=true にして実行（実際には投稿しない）
python morning_operator.py
```

### 6. 本番起動

#### スケジューラーを常時起動（Mac）

```bash
# launchd で Mac 起動時に自動起動（推奨）
launchctl load ~/Library/LaunchAgents/jp.upjapan.scheduler.plist
launchctl load ~/Library/LaunchAgents/jp.upjapan.webhook.plist
launchctl load ~/Library/LaunchAgents/jp.upjapan.dashboard.plist

# 状態確認
launchctl list | grep jp.upjapan
```

## ナノバナナプロとの連携方法

1. ナノバナナプロでデザインを作成
2. **Googleドライブの指定フォルダ** に書き出し（エクスポート）
3. ファイル名またはGoogleドライブの「説明」欄にキャプションを記入
   - 方法①：Googleドライブでファイルを右クリック → 詳細 → 説明欄に投稿文を貼る
   - 方法②：ファイル名に `caption=投稿テキスト.jpg` の形式で命名
4. あとは自動で Instagram に投稿される

## 判断待ちキューの確認方法

`automation-system/decision_queue/` フォルダを確認する。
朝のLINEサマリーに件数が表示される。

内容を見て対応したら `resolved: true` に変更する。

## ファイル構成

```
automation-system/
├── .env.example          ← APIキーのテンプレ（.envにコピーして使う）
├── requirements.txt      ← 必要ライブラリ
├── scheduler.py          ← メインスケジューラー（常時起動）
├── server.py             ← LINE Webhookサーバー（常時起動）
├── morning_operator.py   ← 朝5:00の全自動処理
├── config/
│   ├── schedule.yaml     ← 投稿時刻・曜日設定
│   └── line_scenarios.yaml  ← LINE自動返信キーワード・メッセージ
├── sns/
│   ├── instagram.py      ← Instagram投稿
│   ├── line_api.py       ← LINE Messaging API
│   └── google_drive.py   ← Googleドライブ同期（ナノバナナプロ連携）
├── sales/
│   ├── lead_intake.py    ← リード自動起票
│   ├── followup.py       ← フォローアップ自動送信
│   └── email_responder.py ← メール自動返信
├── content_queue/
│   ├── instagram/        ← 投稿待ちファイルを置く場所
│   └── line/             ← LINE配信待ちファイルを置く場所
├── decision_queue/       ← 判断が必要な案件（朝のLINEで通知）
└── logs/                 ← 実行ログ
```
