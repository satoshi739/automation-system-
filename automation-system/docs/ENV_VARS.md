# 環境変数一覧

`.env` ファイルに設定する全変数の用途・必須/任意をブランド別に整理。

---

## 共通・システム

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | **必須** | Claude AI エージェント全般。未設定でエージェント機能が無効化される |
| `DRY_RUN` | 任意 | `true` にすると実際の投稿・送信をスキップ（テスト用） |
| `FLASK_SECRET_KEY` | **必須** | Dashboard セッション署名 |
| `FLASK_DEBUG` | 任意 | `true` でデバッグモード（本番では設定しない） |
| `DASHBOARD_PASSWORD` | **必須** | Dashboard ログインパスワード |
| `MOCK_MODE` | 任意 | `true` でダッシュボードをモックデータで表示（開発用） |

---

## LINE（メインBot / DSC Marketing）

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `LINE_CHANNEL_ACCESS_TOKEN` | **必須** | server.py の Webhook・自動返信 |
| `LINE_CHANNEL_SECRET` | **必須** | Webhook 署名検証 |
| `OWNER_LINE_USER_ID` | **必須** | 朝サマリーの送信先（オーナー本人の LINE User ID） |
| `LINE_CHANNEL_ACCESS_TOKEN_CSF` | 任意 | CashflowSupport Bot（コード未接続、将来用） |
| `LINE_CHANNEL_SECRET_CSF` | 任意 | 同上 |
| `LINE_CHANNEL_ACCESS_TOKEN_BANGKOK` | 任意 | 旧キー（`BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN` が正、削除推奨） |
| `LINE_CHANNEL_SECRET_BANGKOK` | 任意 | 同上 |

---

## メール送信

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `FROM_EMAIL` | **必須** | 送信元 Gmail アドレス |
| `FROM_EMAIL_PASSWORD` | **必須** | Gmail アプリパスワード（2段階認証設定後に発行） |
| `NOTIFY_EMAIL` | 任意 | 通知受け取り先メールアドレス |
| `SMTP_HOST` | **必須** | SMTP サーバー（通常 `smtp.gmail.com`） |
| `SMTP_PORT` | **必須** | SMTP ポート（通常 `587`） |

---

## Google

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `GOOGLE_DRIVE_FOLDER_ID` | **必須** | 素材の同期元 Google Drive フォルダ ID |

---

## ブランド別 SNS トークン

### upjapan

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `UPJAPAN_META_ACCESS_TOKEN` | **必須** | Instagram/Facebook 投稿 |
| `UPJAPAN_INSTAGRAM_ACCOUNT_ID` | **必須** | Instagram Business アカウント ID |
| `UPJAPAN_FB_PAGE_ID` | **必須** | Facebook ページ ID |
| `UPJAPAN_FB_PAGE_TOKEN` | **必須** | Facebook ページトークン |
| `UPJAPAN_TWITTER_API_KEY` | 任意 | Twitter/X 投稿 |
| `UPJAPAN_TWITTER_API_SECRET` | 任意 | 同上 |
| `UPJAPAN_TWITTER_ACCESS_TOKEN` | 任意 | 同上 |
| `UPJAPAN_TWITTER_ACCESS_SECRET` | 任意 | 同上 |
| `UPJAPAN_THREADS_USER_ID` | 任意 | Threads 投稿 |
| `UPJAPAN_WP_URL` | 任意 | WordPress サイト URL |
| `UPJAPAN_WP_USER` | 任意 | WordPress ユーザー名 |
| `UPJAPAN_WP_APP_PASSWORD` | 任意 | WordPress アプリパスワード |
| `UPJAPAN_GA4_PROPERTY_ID` | 任意 | Google Analytics 4 プロパティ ID |
| `UPJAPAN_GSC_SITE_URL` | 任意 | Google Search Console サイト URL |

### dsc-marketing

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `DSC_MARKETING_META_ACCESS_TOKEN` | **必須** | Instagram/Facebook 投稿 |
| `DSC_MARKETING_INSTAGRAM_ACCOUNT_ID` | **必須** | Instagram Business アカウント ID |
| `DSC_MARKETING_FB_PAGE_ID` | **必須** | Facebook ページ ID |
| `DSC_MARKETING_FB_PAGE_TOKEN` | **必須** | Facebook ページトークン |
| `DSC_MARKETING_TIKTOK_ACCESS_TOKEN` | 任意 | TikTok 投稿 |
| `DSC_MARKETING_TWITTER_API_KEY` | 任意 | Twitter/X 投稿 |
| `DSC_MARKETING_TWITTER_API_SECRET` | 任意 | 同上 |
| `DSC_MARKETING_TWITTER_ACCESS_TOKEN` | 任意 | 同上 |
| `DSC_MARKETING_TWITTER_ACCESS_SECRET` | 任意 | 同上 |
| `DSC_MARKETING_THREADS_USER_ID` | 任意 | Threads 投稿 |
| `DSC_MARKETING_WP_URL` | 任意 | WordPress サイト URL |
| `DSC_MARKETING_WP_USER` | 任意 | WordPress ユーザー名 |
| `DSC_MARKETING_WP_APP_PASSWORD` | 任意 | WordPress アプリパスワード |
| `DSC_MARKETING_YOUTUBE_CHANNEL_ID` | 任意 | YouTube チャンネル ID |
| `DSC_MARKETING_GA4_PROPERTY_ID` | 任意 | Google Analytics 4 プロパティ ID |
| `DSC_MARKETING_GSC_SITE_URL` | 任意 | Google Search Console サイト URL |

### cashflowsupport

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `CASHFLOWSUPPORT_META_ACCESS_TOKEN` | **必須** | Instagram/Facebook 投稿 |
| `CASHFLOWSUPPORT_INSTAGRAM_ACCOUNT_ID` | **必須** | Instagram Business アカウント ID |
| `CASHFLOWSUPPORT_WP_URL` | 任意 | WordPress サイト URL |
| `CASHFLOWSUPPORT_WP_USER` | 任意 | WordPress ユーザー名 |
| `CASHFLOWSUPPORT_WP_APP_PASSWORD` | 任意 | WordPress アプリパスワード |
| `CASHFLOWSUPPORT_GA4_PROPERTY_ID` | 任意 | Google Analytics 4 プロパティ ID |
| `CASHFLOWSUPPORT_GSC_SITE_URL` | 任意 | Google Search Console サイト URL |

### bangkok-peach

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `BANGKOK_PEACH_META_ACCESS_TOKEN` | **必須** | Instagram/Facebook 投稿 |
| `BANGKOK_PEACH_INSTAGRAM_ACCOUNT_ID` | **必須** | Instagram Business アカウント ID |
| `BANGKOK_PEACH_FB_PAGE_ID` | **必須** | Facebook ページ ID |
| `BANGKOK_PEACH_FB_PAGE_TOKEN` | **必須** | Facebook ページトークン |
| `BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN` | **必須** | BPG LINE Bot（値あり・コード接続済み） |
| `BANGKOK_PEACH_LINE_CHANNEL_SECRET` | **必須** | BPG LINE Bot 署名検証（値あり） |
| `BANGKOK_PEACH_TIKTOK_ACCESS_TOKEN` | 任意 | TikTok 投稿 |
| `BANGKOK_PEACH_TWITTER_API_KEY` | 任意 | Twitter/X 投稿 |
| `BANGKOK_PEACH_TWITTER_API_SECRET` | 任意 | 同上 |
| `BANGKOK_PEACH_TWITTER_ACCESS_TOKEN` | 任意 | 同上 |
| `BANGKOK_PEACH_TWITTER_ACCESS_SECRET` | 任意 | 同上 |
| `BANGKOK_PEACH_THREADS_USER_ID` | 任意 | Threads 投稿 |
| `BANGKOK_PEACH_WP_URL` | 任意 | WordPress サイト URL |
| `BANGKOK_PEACH_WP_USER` | 任意 | WordPress ユーザー名 |
| `BANGKOK_PEACH_WP_APP_PASSWORD` | 任意 | WordPress アプリパスワード |
| `BANGKOK_PEACH_GA4_PROPERTY_ID` | 任意 | Google Analytics 4 プロパティ ID |
| `BANGKOK_PEACH_GSC_SITE_URL` | 任意 | Google Search Console サイト URL |

### satoshi-blog

| 変数名 | 必須 | 用途 |
|--------|------|------|
| `SATOSHI_BLOG_WP_URL` | 任意 | WordPress サイト URL |
| `SATOSHI_BLOG_WP_USER` | 任意 | WordPress ユーザー名 |
| `SATOSHI_BLOG_WP_APP_PASSWORD` | 任意 | WordPress アプリパスワード |
| `SATOSHI_BLOG_GA4_PROPERTY_ID` | 任意 | Google Analytics 4 プロパティ ID |
| `SATOSHI_BLOG_GSC_SITE_URL` | 任意 | Google Search Console サイト URL |

---

## 削除推奨（重複・不要キー）

| 変数名 | 理由 |
|--------|------|
| `OWNER LINE USER` | スペース混入の誤記（`OWNER_LINE_USER_ID` が正） |
| `LINE_CHANNEL_ACCESS_TOKEN_BANGKOK` | `BANGKOK_PEACH_LINE_CHANNEL_ACCESS_TOKEN` と重複 |
| `LINE_CHANNEL_SECRET_BANGKOK` | `BANGKOK_PEACH_LINE_CHANNEL_SECRET` と重複 |
