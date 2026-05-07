# N8N セットアップ手順

## Railway への追加

1. [Railway ダッシュボード](https://railway.com) → プロジェクト `gallant-quietude` を開く
2. **+ New Service** → **Docker Image** → `n8nio/n8n` を入力
3. 以下の環境変数を設定:

### 必須環境変数

| 変数 | 値 |
|------|-----|
| `N8N_BASIC_AUTH_ACTIVE` | `true` |
| `N8N_BASIC_AUTH_USER` | `admin` |
| `N8N_BASIC_AUTH_PASSWORD` | （強いパスワードを設定） |
| `N8N_ENCRYPTION_KEY` | （ランダム32文字） |
| `WEBHOOK_URL` | `https://<n8n-service>.railway.app/` |
| `PYTHON_API_URL` | `https://<upjapan-automation>.railway.app` |
| `N8N_API_KEY` | （Python側と同じ値） |

### Instagram ブランド別トークン（取得後に設定）

| 変数 | 説明 |
|------|------|
| `BANGKOK_PEACH_META_ACCESS_TOKEN` | Bangkok Peach の長期トークン |
| `BANGKOK_PEACH_INSTAGRAM_ACCOUNT_ID` | Bangkok Peach の IG ビジネスアカウントID |
| `DSC_MARKETING_META_ACCESS_TOKEN` | DSC Marketing |
| `DSC_MARKETING_INSTAGRAM_ACCOUNT_ID` | |
| `CASHFLOWSUPPORT_META_ACCESS_TOKEN` | Cashflow Support |
| `CASHFLOWSUPPORT_INSTAGRAM_ACCOUNT_ID` | |
| `UPJAPAN_META_ACCESS_TOKEN` | UP JAPAN |
| `UPJAPAN_INSTAGRAM_ACCOUNT_ID` | |
| `SATOSHI_META_ACCESS_TOKEN` | Satoshi |
| `SATOSHI_INSTAGRAM_ACCOUNT_ID` | |

## ワークフローのインポート

1. N8N にログイン → **Workflows** → **Import from file**
2. `workflows/instagram_auto_post.json` をインポート
3. **Credentials** で `Python API Key` を作成（ヘッダー名: `X-Api-Key`、値: `N8N_API_KEY` の値）
4. ワークフローを **Active** にする

## Python 側の設定

`.env` と Railway に追加:
```
N8N_API_KEY=<ランダム32文字>
```
