# Instagram Graph API セットアップ手順

Instagram自動投稿を有効にするための完全手順。  
完了後は `META_ACCESS_TOKEN` と `INSTAGRAM_BUSINESS_ACCOUNT_ID` を `.env` に設定するだけで即投稿開始できます。

---

## 前提条件

- [ ] Facebookアカウント（個人）
- [ ] Instagramビジネスアカウント（またはクリエイターアカウント）
- [ ] Facebookページ（InstagramBizアカウントと連携済み）

---

## STEP 1: Instagramをビジネスアカウントに変換

1. Instagramアプリ → プロフィール → 右上メニュー
2. 「設定」→「アカウント」→「プロアカウントに切り替える」
3. 「ビジネス」を選択
4. Facebookページと連携する

---

## STEP 2: Meta Developers アプリを作成

1. https://developers.facebook.com/apps/ にアクセス
2. 「アプリを作成」→「ビジネス」タイプを選択
3. アプリ名: `UPJ Automation`（任意）
4. 「Instagram Graph API」プロダクトを追加

---

## STEP 3: 必要な権限（パーミッション）を申請

Appの「アプリレビュー」から以下を申請：

| 権限 | 用途 |
|------|------|
| `instagram_basic` | アカウント情報取得 |
| `instagram_content_publish` | 投稿（フィード・リール） |
| `pages_read_engagement` | ページ読み取り |
| `pages_show_list` | ページ一覧取得 |

> **開発モード中**は自分のアカウントのみテスト可能。本番公開には審査が必要。

---

## STEP 4: アクセストークンを取得

### 4-1. ユーザーアクセストークン（短期）

1. Meta Developers → Graph API Explorer
2. アプリを選択 → 上記権限を選択 → 「トークンを生成」

### 4-2. 長期トークンに交換（60日有効）

```bash
curl -X GET "https://graph.facebook.com/v21.0/oauth/access_token
  ?grant_type=fb_exchange_token
  &client_id={APP_ID}
  &client_secret={APP_SECRET}
  &fb_exchange_token={SHORT_TOKEN}"
```

### 4-3. システムユーザートークン（推奨・無期限）

1. Meta Business Suite → 設定 → ユーザー → システムユーザー
2. 「システムユーザーを追加」→ 権限を付与
3. 「新しいトークンを生成」→ アプリと権限を選択

---

## STEP 5: Instagram Business Account ID を取得

```bash
curl -X GET "https://graph.facebook.com/v21.0/me/accounts
  ?access_token={ACCESS_TOKEN}"
# → page_id を取得

curl -X GET "https://graph.facebook.com/v21.0/{PAGE_ID}
  ?fields=instagram_business_account
  &access_token={ACCESS_TOKEN}"
# → instagram_business_account.id が INSTAGRAM_BUSINESS_ACCOUNT_ID
```

---

## STEP 6: .env に設定

```env
META_ACCESS_TOKEN=EAAxxxxxx...（長期またはシステムユーザートークン）
INSTAGRAM_BUSINESS_ACCOUNT_ID=17841xxxxxxxxxx
META_APP_ID=（任意。ログ用）
META_APP_SECRET=（任意。トークン更新時に使用）
```

---

## STEP 7: 動作確認

```bash
cd automation-system
python3 -c "
from connectors.meta_connector import MetaRealConnector
c = MetaRealConnector()
result = c.validate_account('${INSTAGRAM_BUSINESS_ACCOUNT_ID}')
print(result)
"
```

`{'ok': True, 'name': '...', 'followers': ...}` が返れば成功。

---

## ブランド別トークン設定

複数ブランドで別々のInstagramを運用する場合：

```env
# cashflowsupport
CSF_META_ACCESS_TOKEN=EAAxxxxx
CSF_INSTAGRAM_BUSINESS_ACCOUNT_ID=178xxx

# DSc Marketing
DSC_META_ACCESS_TOKEN=EAAyyyyy
DSC_INSTAGRAM_BUSINESS_ACCOUNT_ID=178yyy

# Bangkok Peach
BPG_META_ACCESS_TOKEN=EAAzzzzz
BPG_INSTAGRAM_BUSINESS_ACCOUNT_ID=178zzz
```

---

## トラブルシューティング

| エラー | 原因 | 対処 |
|--------|------|------|
| `OAuthException: #200` | 権限不足 | アプリレビューで権限を追加 |
| `Invalid OAuth access token` | トークン期限切れ | 長期トークンに交換 |
| `Application does not have permission` | 開発モードの制限 | テスターとして自分を追加 |
| `Media container status: ERROR` | 画像URLが無効 | 公開アクセス可能なURLを使用 |

---

## 参考リンク

- [Meta Developers](https://developers.facebook.com/)
- [Instagram Graph API ドキュメント](https://developers.facebook.com/docs/instagram-api)
- [Graph API Explorer](https://developers.facebook.com/tools/explorer/)
- [アクセストークンデバッガー](https://developers.facebook.com/tools/debug/accesstoken/)
