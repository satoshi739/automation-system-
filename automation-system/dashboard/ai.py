from __future__ import annotations

"""
Claude AI エンジン
- Instagram投稿文の自動生成（3バリアント＋自動選択）
- LINE配信メッセージの自動生成
- リードへの返信ドラフトの生成
- 問い合わせ内容の要約・分類
- トレンドトピックのリサーチ
- 豪華リール台本（BGM候補・テロップ・シーン割）
- YouTube Shorts / TikTok 専用コンテンツ
- Instagram ストーリーズテキスト
- 週次コンテンツカレンダー自動生成
- 過去パフォーマンスフィードバック連携
"""

import os
import re
import sys
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import anthropic


def _extract_json(raw: str) -> dict | None:
    """Claude応答からJSONを抽出する（複数戦略）。失敗時はNoneを返す。"""
    # 戦略1: コードフェンス内のJSONを取得
    if "```" in raw:
        block = raw.split("```")[1]
        if block.startswith("json"):
            block = block[4:]
        try:
            return json.loads(block.strip())
        except Exception:
            pass

    # 戦略2: 直接パース
    try:
        return json.loads(raw.strip())
    except Exception:
        pass

    # 戦略3: {…} ブロックを抽出して再試行（改行エスケープ補正含む）
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            try:
                return json.loads(re.sub(r'(?<!\\)\n', r'\\n', candidate))
            except Exception:
                pass

    # 戦略4: 正規表現で caption / hashtags を直接抽出
    # Claudeが日本語の「」を"と書くとJSONが壊れるが値は取り出せる
    cap_m = re.search(r'"caption":\s*"(.*?)(?=",\s*\n?\s*"hashtags")', raw, re.DOTALL)
    tag_m = re.search(r'"hashtags":\s*"(.*?)(?=",?\s*\n?\s*(?:"hook"|"?\s*\}))', raw, re.DOTALL)
    if cap_m:
        def _unescape(s: str) -> str:
            return s.replace("\\n", "\n").replace("\\t", "\t")
        return {
            "caption": _unescape(cap_m.group(1)),
            "hashtags": _unescape(tag_m.group(1)) if tag_m else "",
            "hook": "",
        }

    return None

# パフォーマンストラッキングモジュール（automation-system/sns/performance.py）
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    from sns.performance import get_performance_summary
except ImportError:
    def get_performance_summary(*args, **kwargs) -> str:  # type: ignore[misc]
        return ""

logger = logging.getLogger(__name__)

BLOG_CONTEXTS = {
    "satoshi-blog": """
あなたは個人ブログ「Satoshi Life」の記事を書くAIです。
URL: https://satoshi-life.site/blog/
筆者: Satoshi（起業家・バンコク在住・AI自動化・マーケター）
テーマ: ビジネス・海外生活・AI活用・マーケティング・自動化・人生設計・投資
トーン: 一人称「僕」。体験談・具体例を交えた親しみやすい文体。専門的な内容もわかりやすく。
構成: 導入→本論（h2見出し3〜5個）→まとめ→CTA
SEO: 検索意図を意識したタイトル・見出し構成
NG: 誇大表現・根拠のない断言
""",
    "upjapan": """
あなたは株式会社ユニバースプラネットジャパン（UPJ）のコーポレートブログ担当AIです。
URL: https://upjapan.co.jp
サービス: 事業設計・収益モデル再設計・国際展開・マーケティング統合支援
テーマ: 経営戦略・マーケティング・海外展開・ブランディング・DX・AI活用・収益改善
ターゲット: 中小企業経営者・起業家・マーケター
トーン: 専門的かつ親しみやすい。実践例・数字を交える。ポジティブで前向き。
構成: 導入→課題提起→解決策（h2見出し3〜4個）→UPJの支援例→まとめ→CTA
SEO: ビジネス系キーワードを意識
NG: 確実に儲かる・誰でも成功
""",
    "dsc-marketing": """
あなたは DSc Marketing（デジタルマーケティング専門会社）のブログ担当AIです。
URL: https://dsc-marketing.com
サービス: SNS・LINE・Web集客の導線設計・運用支援（月額25,000円〜）
テーマ: SNSマーケティング・LINE集客・Instagram運用・TikTok活用・Web集客・コンテンツ戦略・AI活用
ターゲット: 中小企業・個人事業主・マーケター初心者〜中級者
トーン: 実務的・成果重視。初心者にも分かりやすく手順を示す。
構成: タイトル→課題→解決策（h2見出し3〜5個・具体的な手順）→まとめ→CTA
SEO: SNS・マーケティング系キーワードを意識
NG: 必ず上位・誰でも稼げる・誇大な効果保証
""",
    "cashflowsupport": """
あなたは cashflowsupport のブログ担当AIです。
URL: https://cashflowsupport.jp
サービス: ファクタリング・資金繰り相談・中小企業の資金調達支援
テーマ: ファクタリング・資金繰り・キャッシュフロー改善・売掛金・資金調達・経営財務・銀行融資との比較
ターゲット: 資金繰りに悩む中小企業経営者・個人事業主
トーン: 丁寧・経営者目線・透明性を強調。専門用語は解説を添える。
構成: 導入（共感）→問題解説→解決策（h2見出し3〜4個）→ファクタリングの活用例→まとめ→CTA（無料相談）
SEO: ファクタリング・資金繰り系キーワードを意識
NG: 絶対・必ず審査通過・違法・グレーな暗示
""",
    "bangkok-peach": """
あなたは Bangkok Peach Group のブログ担当AIです。
URL: https://bangkok-peach-group.com
サービス: バンコクの日本語対応エンターテインメント・ナイトライフ・観光案内
テーマ: バンコク旅行・タイ観光・海外移住・バンコクナイトライフ・タイ料理・バンコク生活・現地情報・節約術
ターゲット: バンコクに興味のある日本人旅行者・移住検討者・バンコク在住者
トーン: 明るく・わかりやすく・旅行者目線。体験談・リアルな現地情報を交える。
構成: 導入→現地情報（h2見出し3〜4個・具体的なスポット・値段・アクセス）→まとめ→CTA
SEO: バンコク・タイ観光系キーワードを意識
NG: 過度な誇大表現・違法暗示
""",
}

BRAND_CONTEXTS = {
    "dsc-marketing": """
あなたは DSc Marketing（株式会社ユニバースプラネットジャパン）のマーケティング担当AIです。
サービス：SNS・LINE・Web集客の導線設計・運用支援
トーン：成果・導線・仕組みを前面。無理に煽らない実務寄り。
料金：月額25,000円〜100,800円（税込）
NG表現：必ず上位・誰でも稼げる・違法・誇大な効果保証
""",
    "cashflowsupport": """
あなたは cashflowsupport（株式会社ユニバースプラネットジャパン）のコンサルタントAIです。
サービス：ファクタリング・資金繰り相談
トーン：丁寧・経営者目線・透明性を強調
NG表現：絶対・必ず儲かる・誰でも必ず審査通過・違法・グレーな暗示
""",
    "upjapan": """
あなたは株式会社ユニバースプラネットジャパン（UPJ）のコンサルタントAIです。
サービス：事業設計・収益モデル再設計・国際展開・マーケティング統合
トーン：設計・軌道・構造など比喩を用いた前向きで落ち着いたトーン
NG表現：確実に儲かる・誰でも成功
""",
    "bangkok-peach": """
あなたは Bangkok Peach Group のマーケティング担当AIです。
サービス：バンコク・タイを拠点とした事業・観光・ライフスタイル関連
Webサイト：https://bangkok-peach-group.com/
トーン：明るく洗練された国際感覚。日本語と英語を交えた親しみやすさ。
NG表現：過度な誇大表現・確実保証・違法暗示
""",
    "satoshi-blog": """
あなたは個人ブログ「Satoshi Life」の記事を書くAIです。
ブログURL：https://satoshi-life.site/blog/
筆者：Satoshi（起業家・マーケター・タイ・バンコク在住）
テーマ：ビジネス・海外生活・マーケティング・自動化・AI活用・人生設計
トーン：一人称「僕」で話す。読者に語りかけるような親しみやすい文体。
     専門的な内容もわかりやすく、体験談・具体例を交えて書く。
文字数：800〜2000字（テーマに応じて）
構成：導入→本論（見出し3〜5個）→まとめ→CTA
SEO：検索意図を意識したタイトル・見出し構成
NG：誇大表現・根拠のない断言・他者への誹謗中傷
""",
}


def _client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY が設定されていません")
    return anthropic.Anthropic(api_key=api_key)


def generate_instagram_post(
    topic: str,
    target: str,
    tone: str,
    brand: str = "dsc-marketing",
    extra: str = "",
) -> dict:
    """
    Instagram投稿文（キャプション＋ハッシュタグ）を生成する

    Returns:
        {"caption": str, "hashtags": str, "full": str}
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])

    prompt = f"""
{brand_ctx}

以下の条件でInstagram投稿のキャプションを日本語で作成してください。

【トピック】{topic}
【ターゲット】{target}
【トーン】{tone}
{"【補足】" + extra if extra else ""}

出力形式（JSONで返す）:
{{
  "caption": "キャプション本文（改行あり、ハッシュタグなし、300文字以内）",
  "hashtags": "#タグ1 #タグ2 ... （10〜15個、スペース区切り）",
  "hook": "冒頭の1行（最も目を引く文）"
}}

- フック（最初の1〜2行）で興味を引く
- 箇条書きを使って読みやすくする
- CTAで締める（例：「プロフリンクから無料相談↑」）
- 「確実」「必ず」などの断定語は使わない
"""

    client = _client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    # JSON抽出（複数戦略でフォールバック）
    data = _extract_json(raw)
    if data is None:
        data = {"caption": raw, "hashtags": "", "hook": ""}

    caption = data.get("caption", "")
    hashtags = data.get("hashtags", "")
    data["full"] = f"{caption}\n\n{hashtags}".strip()
    return data


def generate_line_message(
    topic: str,
    brand: str = "dsc-marketing",
    purpose: str = "集客・認知",
) -> str:
    """LINE配信メッセージを生成する"""
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])

    prompt = f"""
{brand_ctx}

以下の条件でLINE一斉配信のメッセージを日本語で作成してください。

【トピック】{topic}
【目的】{purpose}

条件:
- 200〜300文字程度
- 親しみやすく、押し付けがましくない
- 読んでよかったと思える情報か価値を含める
- 末尾にCTA（公式LINEに問い合わせ、プロフリンク等）
- 絵文字を2〜3個適度に使う
- 「確実」「必ず」などの断定語は使わない

メッセージ本文のみを返してください（説明文不要）。
"""

    client = _client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def generate_lead_reply(lead: dict) -> str:
    """
    リード情報から返信ドラフトを生成する

    Args:
        lead: lead-sheet.yaml の内容（dict）

    Returns:
        返信メッセージ（文字列）
    """
    brand = lead.get("brand", "dsc-marketing")
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    channel = lead.get("channel", "line")
    name = lead.get("name", "お客様")
    situation = lead.get("current_situation", "")
    stage = lead.get("stage", "L2")

    stage_guidance = {
        "L1": "まだコンタクトしていない。初回の挨拶と課題ヒアリングへの誘導をする。",
        "L2": "コンタクト済みだが商談未実施。商談日程の調整を促す。",
        "L3": "商談中。提案に向けた追加ヒアリングや次のステップを提案する。",
        "L4": "提案済み。フォローアップと質問への回答をする。",
    }.get(stage, "適切にフォローアップする。")

    prompt = f"""
{brand_ctx}

以下のリード情報をもとに、{channel}で送る返信メッセージを日本語で作成してください。

【相手の名前】{name}
【状況・相談内容】{situation or "（未記入）"}
【現在のステージ】{stage} — {stage_guidance}

条件:
- 200文字以内で簡潔に
- 相手の状況に共感・寄り添う一言を入れる
- 次のアクションを1つだけ明示する（日程調整 or 質問 等）
- 「確実」「必ず」などの断定語は使わない
- {channel}らしい自然な文体

返信メッセージ本文のみ返してください。
"""

    client = _client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


def generate_all_platforms(
    topic: str,
    brand: str = "dsc-marketing",
    extra_context: str = "",
) -> dict:
    """
    全プラットフォーム向けコンテンツを一括生成する
    ストーリーズ・Shorts・TikTok 用コンテンツも含む

    Returns:
        {
          "topic": str,
          "instagram": {"caption": str, "hashtags": str},
          "stories": {"slide1": str, "slide2": str, "slide3": str, "cta_slide": str},
          "threads": {"text": str},
          "facebook": {"text": str},
          "twitter": {"text": str},
          "line": {"message": str},
          "wordpress": {"title": str, "content": str},
          "reel": {"title": str, "points": [...], "cta": str, "script": str},
          "shorts": {"title": str, "description": str, "script": str, "tags": [str]},
          "tiktok": {"caption": str, "hashtags": str, "hook_text": str},
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "instagram")

    prompt = f"""
{brand_ctx}
{perf_ctx}

以下のトピックで、各SNSプラットフォーム向けの投稿コンテンツを一括生成してください。

【トピック】{topic}
{"【補足】" + extra_context if extra_context else ""}

以下のJSON形式で返してください。各フィールドは必須です:

{{
  "instagram": {{
    "caption": "Instagramキャプション本文（300文字以内、改行あり、ハッシュタグなし）",
    "hashtags": "#タグ1 #タグ2 ... （10〜15個）"
  }},
  "stories": {{
    "slide1": "1枚目テキスト（問いかけ・フック、20文字以内）",
    "slide2": "2枚目テキスト（ポイント要約、30文字以内）",
    "slide3": "3枚目テキスト（具体的なヒント・数字、30文字以内）",
    "cta_slide": "最終スライドCTA（20文字以内、例: プロフリンクから詳細チェック↑）"
  }},
  "threads": {{
    "text": "Threads投稿文（500文字以内、ハッシュタグ含む）"
  }},
  "facebook": {{
    "text": "Facebookページ投稿文（400文字以内、URLやCTAを含む）"
  }},
  "twitter": {{
    "text": "X(Twitter)ツイート文（140文字以内、ハッシュタグ2〜3個含む）"
  }},
  "line": {{
    "message": "LINE配信メッセージ（200〜250文字、絵文字2〜3個、CTA含む）"
  }},
  "wordpress": {{
    "title": "ブログ記事タイトル（SEOを意識した30文字以内）",
    "content": "記事本文（HTML形式、見出し<h2>3つ、各400文字程度、合計1200文字以上）"
  }},
  "reel": {{
    "title": "リールのタイトルスライドテキスト（15文字以内、インパクトある）",
    "points": [
      {{"text": "ポイント1（15文字以内）", "detail": "詳細説明（30文字以内）"}},
      {{"text": "ポイント2（15文字以内）", "detail": "詳細説明（30文字以内）"}},
      {{"text": "ポイント3（15文字以内）", "detail": "詳細説明（30文字以内）"}}
    ],
    "cta": "CTA文（20文字以内）",
    "script": "リール台本（セリフ・テロップの流れを箇条書きで、全体30秒想定）"
  }},
  "shorts": {{
    "title": "YouTube Shortsタイトル（50文字以内、検索キーワード含む）",
    "description": "概要欄テキスト（150文字以内、ハッシュタグ3〜5個含む）",
    "script": "Shorts台本（テロップ主体、60秒以内、セリフは短く・テンポ重視）",
    "tags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"]
  }},
  "tiktok": {{
    "caption": "TikTokキャプション（150文字以内）",
    "hashtags": "#fyp #foryou 関連タグ5〜8個",
    "hook_text": "冒頭3秒のテロップ（10文字以内、興味を引くフレーズ）"
  }}
}}

- 全て日本語で
- 「確実」「必ず儲かる」などの誇大表現は使わない
- 各プラットフォームの特性に合わせたトーンで
- JSONのみ返す（説明文不要）
"""

    client = _client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()

    try:
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                p = part.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    raw = p
                    break
        data = json.loads(raw.strip())
    except Exception as e:
        logger.error(f"JSON parse error: {e}\nraw: {raw[:300]}")
        data = {}

    data["topic"] = topic
    return data


def generate_reel_script(topic: str, brand: str = "dsc-marketing") -> dict:
    """
    リール動画の台本とスライド構成を生成する（軽量版）
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    prompt = f"""
{brand_ctx}

【トピック】{topic}

30秒のInstagramリール動画の台本をJSON形式で作成してください:
{{
  "title": "タイトルテキスト（15文字以内）",
  "points": [
    {{"text": "ポイント1（15文字以内）", "detail": "補足（30文字以内）"}},
    {{"text": "ポイント2（15文字以内）", "detail": "補足（30文字以内）"}},
    {{"text": "ポイント3（15文字以内）", "detail": "補足（30文字以内）"}}
  ],
  "cta": "CTA（20文字以内）",
  "caption": "投稿キャプション（ハッシュタグ含む、300文字以内）"
}}
JSONのみ返す。
"""
    client = _client()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception:
        return {"title": topic[:15], "points": [], "cta": "詳しくはプロフリンクから", "caption": ""}


def summarize_inquiry(subject: str, body: str) -> dict:
    """
    問い合わせメールを要約・分類する

    Returns:
        {"summary": str, "brand": str, "urgency": "high"/"normal", "suggested_reply": str}
    """
    prompt = f"""
以下の問い合わせを分析してJSON形式で返してください。

【件名】{subject}
【本文】{body[:500]}

出力:
{{
  "summary": "問い合わせ内容の要約（50文字以内）",
  "brand": "dsc-marketing / cashflowsupport / upjapan のどれか",
  "urgency": "high（急ぎ・クレーム・金融関連） / normal",
  "category": "新規問い合わせ / 既存顧客 / クレーム / その他",
  "suggested_reply": "返信の冒頭2〜3文（日本語）"
}}
"""

    client = _client()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception:
        return {"summary": raw[:50], "brand": "upjapan", "urgency": "normal", "suggested_reply": ""}


# ═══════════════════════════════════════════════════════════════
# ── 豪華機能追加セクション ──────────────────────────────────────
# ═══════════════════════════════════════════════════════════════

def _parse_json(raw: str, fallback: dict | list) -> dict | list:
    """JSON抽出の共通ヘルパー"""
    try:
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                p = part.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith(("{", "[")):
                    raw = p
                    break
        return json.loads(raw.strip())
    except Exception:
        return fallback


# ── A-1: トレンドトピックリサーチ ────────────────────────────────

def research_trending_topics(
    brand: str = "dsc-marketing",
    n: int = 5,
) -> list[dict]:
    """
    ブランドのターゲット層に刺さるトレンドトピックをAIがリサーチして提案する。
    現在の月・季節・社会トレンドを考慮した提案を返す。

    Returns:
        [
          {
            "topic": str,
            "why": str,          # なぜ今このトピックか
            "target": str,       # ターゲット読者像
            "hook": str,         # 冒頭フック案
            "platform_fit": str, # 最も向くプラットフォーム
          }, ...
        ]
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    today = datetime.now().strftime("%Y年%m月%d日")
    perf_ctx = get_performance_summary(brand, "instagram")

    prompt = f"""
{brand_ctx}
{perf_ctx}

今日は{today}です。

このブランドのInstagram・LINE・TikTok・YouTube向けに、
今旬の投稿トピックを{n}件提案してください。

考慮すること:
- 今月の季節・イベント・ビジネストレンド
- ターゲット層（中小企業経営者・個人事業主・マーケ担当者）の関心事
- 過去に伸びたトピックの傾向（上記データがあれば参考に）
- バズりやすい切り口（数字・比較・「知らないと損」系）

JSON配列で返してください:
[
  {{
    "topic": "投稿トピック（20文字以内）",
    "why": "今このトピックが刺さる理由（40文字以内）",
    "target": "ターゲット読者像（30文字以内）",
    "hook": "冒頭フック案（30文字以内）",
    "platform_fit": "instagram / tiktok / youtube / line のどれか1つ"
  }}
]
JSONのみ返す。
"""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    result = _parse_json(resp.content[0].text.strip(), [])
    logger.info(f"トレンドトピック提案: {len(result)}件")
    return result if isinstance(result, list) else []


# ── A-2: 3バリアント生成＋自動選択 ──────────────────────────────

def generate_instagram_post_variants(
    topic: str,
    target: str,
    tone: str,
    brand: str = "dsc-marketing",
    extra: str = "",
) -> dict:
    """
    Instagram投稿を3パターン生成し、AIが最もエンゲージメントが取れると
    判断したものを自動選択して返す。

    Returns:
        {
          "selected": {"caption": str, "hashtags": str, "hook": str, "full": str},
          "variants": [同形式 × 3],
          "selection_reason": str,
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "instagram")

    # Step 1: 3パターン生成
    prompt_gen = f"""
{brand_ctx}
{perf_ctx}

以下の条件でInstagram投稿キャプションを3パターン生成してください。
各パターンはアプローチ（教育系・共感系・エンタメ系）で変化をつけてください。

【トピック】{topic}
【ターゲット】{target}
【トーン】{tone}
{"【補足】" + extra if extra else ""}

JSON配列で返してください（3要素）:
[
  {{
    "pattern": "教育系",
    "caption": "キャプション本文（300文字以内、改行あり、ハッシュタグなし）",
    "hashtags": "#タグ1 #タグ2 ... （10〜15個）",
    "hook": "冒頭1行（最も目を引く文）"
  }},
  {{
    "pattern": "共感系",
    ...
  }},
  {{
    "pattern": "エンタメ系",
    ...
  }}
]
- CTAで締める
- 「確実」「必ず」などの断定語は使わない
- JSONのみ返す
"""

    resp1 = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt_gen}],
    )
    variants = _parse_json(resp1.content[0].text.strip(), [])
    if not isinstance(variants, list) or len(variants) == 0:
        return {"selected": {}, "variants": [], "selection_reason": "生成失敗"}

    # Step 2: AIが最良パターンを選択
    variants_text = json.dumps(variants, ensure_ascii=False, indent=2)
    prompt_select = f"""
以下の3パターンのInstagram投稿を評価し、最もエンゲージメントが取れると思われる
パターンを選んでください。

評価軸:
1. フックの強さ（スクロールが止まるか）
2. 読みやすさ（箇条書き・改行）
3. CTA の自然さ
4. ターゲット「{target}」への刺さり具合

{variants_text}

以下のJSON形式で返してください:
{{
  "best_index": 0または1または2,
  "reason": "選んだ理由（50文字以内）"
}}
JSONのみ返す。
"""
    resp2 = _client().messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt_select}],
    )
    selection = _parse_json(resp2.content[0].text.strip(), {"best_index": 0, "reason": ""})
    best_idx = int(selection.get("best_index", 0))
    if best_idx >= len(variants):
        best_idx = 0

    selected = variants[best_idx]
    caption  = selected.get("caption", "")
    hashtags = selected.get("hashtags", "")
    selected["full"] = f"{caption}\n\n{hashtags}".strip()

    logger.info(f"3バリアント生成完了: 選択={selected.get('pattern')} ({selection.get('reason','')})")
    return {
        "selected":         selected,
        "variants":         variants,
        "selection_reason": selection.get("reason", ""),
    }


# ── A-3: 豪華リール台本（BGM候補・テロップ・シーン割り） ─────────

def generate_reel_script_rich(
    topic: str,
    brand: str = "dsc-marketing",
    duration_sec: int = 30,
) -> dict:
    """
    リール動画の完全台本を生成する。
    BGM候補・シーン割り・テロップタイミング・カメラワーク案も含む。

    Returns:
        {
          "title_slide": str,
          "bgm_suggestions": [{"title": str, "mood": str, "bpm": str}],
          "scenes": [
            {
              "sec": "0-3",
              "telop": str,        # テロップテキスト
              "narration": str,    # セリフ（口パク・VO）
              "visual": str,       # 映像・画面の指示
              "camera": str,       # カメラワーク
            }
          ],
          "caption": str,
          "hashtags": str,
          "cta": str,
          "thumbnail_text": str,   # サムネイルに載せるテキスト
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "instagram")

    prompt = f"""
{brand_ctx}
{perf_ctx}

【トピック】{topic}
【想定尺】{duration_sec}秒

Instagramリール動画の完全台本をJSON形式で作成してください。

{{
  "title_slide": "タイトルスライドテキスト（15文字以内、インパクト重視）",
  "thumbnail_text": "サムネイル用テキスト（20文字以内、一番目を引くフレーズ）",
  "bgm_suggestions": [
    {{
      "title": "楽曲名または雰囲気（例: アップテンポなビート系）",
      "mood": "energetic / calm / dramatic / playful のいずれか",
      "bpm": "おおよそのBPM（例: 120BPM）"
    }},
    {{...}},
    {{...}}
  ],
  "scenes": [
    {{
      "sec": "0-3",
      "telop": "テロップテキスト（15文字以内）",
      "narration": "ナレーション・セリフ（話す内容）",
      "visual": "映像・画面の指示（何を映すか）",
      "camera": "カメラワーク指示（例: ズームイン、静止画、テキストオンリーなど）"
    }}
  ],
  "caption": "投稿キャプション本文（300文字以内、ハッシュタグなし）",
  "hashtags": "#タグ1 #タグ2 ... （10〜15個）",
  "cta": "CTA文（20文字以内）"
}}

シーンは{duration_sec}秒をカバーするよう3〜6シーン作成。
最後のシーンは必ずCTAスライドにする。
「確実」「必ず儲かる」などの誇大表現は使わない。
JSONのみ返す。
"""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    result = _parse_json(resp.content[0].text.strip(), {})
    result["topic"] = topic
    logger.info(f"豪華リール台本生成完了: {len(result.get('scenes', []))}シーン")
    return result


# ── B-1: YouTube Shorts 専用コンテンツ ──────────────────────────

def generate_shorts_content(
    topic: str,
    brand: str = "dsc-marketing",
) -> dict:
    """
    YouTube Shorts 専用のコンテンツ（台本・タイトル・概要欄・タグ）を生成する。
    Shortsは縦型・60秒以内・検索流入を意識した構成。

    Returns:
        {
          "title": str,
          "description": str,
          "tags": [str],
          "chapters": [{"sec": str, "text": str}],
          "script": str,
          "thumbnail_text": str,
          "end_card_text": str,
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "youtube")

    prompt = f"""
{brand_ctx}
{perf_ctx}

【トピック】{topic}

YouTube Shorts（縦型・最大60秒）のコンテンツをJSON形式で作成してください。

{{
  "title": "動画タイトル（40〜50文字、検索キーワード含む、数字や疑問形が効果的）",
  "description": "概要欄（200文字以内、キーワードを自然に含め、関連リンク誘導のCTA付き）",
  "tags": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7"],
  "chapters": [
    {{"sec": "0", "text": "冒頭フック（3秒で掴む）"}},
    {{"sec": "5", "text": "本題導入"}},
    {{"sec": "15", "text": "ポイント1"}},
    {{"sec": "30", "text": "ポイント2"}},
    {{"sec": "45", "text": "まとめ・CTA"}}
  ],
  "script": "完全台本（テロップ主体で書く。セリフは短く・テンポ重視。各シーンの区切りに===を入れる）",
  "thumbnail_text": "サムネイルテキスト（20文字以内、「知らないと損」「〇〇の真実」など興味喚起型）",
  "end_card_text": "エンドカード・締めの一言（15文字以内）"
}}

- テロップは1枚8文字以内が理想
- 最初の3秒で結論を言うか、強烈な問いかけをする
- 「確実」「必ず儲かる」などの誇大表現は使わない
- JSONのみ返す
"""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    result = _parse_json(resp.content[0].text.strip(), {})
    result["topic"] = topic
    logger.info(f"YouTube Shorts コンテンツ生成完了: {topic[:20]}")
    return result


# ── B-2: TikTok 専用コンテンツ ──────────────────────────────────

def generate_tiktok_content(
    topic: str,
    brand: str = "dsc-marketing",
) -> dict:
    """
    TikTok 専用コンテンツを生成する。
    TikTokはトレンド音源・エンタメ性・テンポが重要。

    Returns:
        {
          "caption": str,
          "hashtags": str,
          "hook_text": str,
          "script": str,
          "text_overlays": [{"sec": str, "text": str, "style": str}],
          "sound_suggestion": str,
          "trend_angle": str,
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "tiktok")

    prompt = f"""
{brand_ctx}
{perf_ctx}

【トピック】{topic}

TikTok動画のコンテンツをJSON形式で作成してください。
TikTokはInstagramより若い層・エンタメ性・テンポが重要です。

{{
  "caption": "キャプション（150文字以内、自然な口語体）",
  "hashtags": "#fyp #foryoupage 関連タグ5〜8個（スペース区切り）",
  "hook_text": "冒頭3秒のテロップ（10文字以内、スクロールを止める一言）",
  "script": "台本（口語体・テンポ重視・1文を短く。ビジネス内容もカジュアルに伝える）",
  "text_overlays": [
    {{"sec": "0-3",  "text": "テキストオーバーレイ（8文字以内）", "style": "大文字インパクト"}},
    {{"sec": "5-10", "text": "テキストオーバーレイ（8文字以内）", "style": "通常"}},
    {{"sec": "15-20","text": "テキストオーバーレイ（8文字以内）", "style": "CTA"}},
    {{"sec": "25-30","text": "テキストオーバーレイ（8文字以内）", "style": "エンド"}}
  ],
  "sound_suggestion": "BGM・音源の方向性（例: トレンドのポップビート、落ち着いたLoFi）",
  "trend_angle": "このトピックをTikTokらしく見せるための切り口・アングル"
}}

- 「確実」「必ず儲かる」などの誇大表現は使わない
- JSONのみ返す
"""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    result = _parse_json(resp.content[0].text.strip(), {})
    result["topic"] = topic
    logger.info(f"TikTok コンテンツ生成完了: {topic[:20]}")
    return result


# ── C: 個人ブログ記事生成 ───────────────────────────────────────

def generate_blog_post(
    topic: str,
    style: str = "体験談・実践寄り",
    word_count: int = 1200,
    auto_publish: bool = False,
) -> dict:
    """
    個人ブログ記事をAIで生成する。

    Returns:
        {
          "title": str,
          "meta_description": str,
          "content_html": str,   # WordPress用HTML
          "content_plain": str,  # プレーンテキスト
          "tags": [str],
          "estimated_read_time": int,  # 分
        }
    """
    brand_ctx = BRAND_CONTEXTS.get("satoshi-blog", "")

    prompt = f"""
{brand_ctx}

以下のトピックで個人ブログ記事を書いてください。

トピック: {topic}
スタイル: {style}
目標文字数: {word_count}字前後

【出力形式】必ずJSON形式で返してください:
{{
  "title": "SEOを意識した記事タイトル（30〜40文字）",
  "meta_description": "検索結果に表示されるメタ説明文（120文字以内）",
  "content_html": "WordPress用HTML本文（h2/h3/p/ul/strongタグ使用）",
  "content_plain": "プレーンテキストの本文",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "estimated_read_time": 読了時間（分）
}}

記事の構成:
1. 導入（読者の共感を引く）
2. 本論（h2見出し3〜5個、各セクションに体験談・具体例）
3. まとめ（読者へのメッセージ）
4. CTA（次のアクションへの誘導）
"""

    client = _client()
    msg = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    # JSON抽出
    import re, json
    m = re.search(r'\{[\s\S]*\}', raw)
    if m:
        result = json.loads(m.group())
    else:
        result = {
            "title": topic,
            "meta_description": "",
            "content_html": f"<p>{raw}</p>",
            "content_plain": raw,
            "tags": [],
            "estimated_read_time": word_count // 400,
        }

    logger.info(f"ブログ記事生成完了: {result.get('title','')}")
    return result


def generate_blog_post_auto(brand: str, word_count: int = 1200) -> dict:
    """
    ブランド指定でトピックをAIが自動選択し、ブログ記事を生成する。
    1回のAPI呼び出しでトピック選択→記事生成を完結させる。

    Returns:
        {"title": str, "meta_description": str, "content_html": str,
         "content_plain": str, "tags": [str], "estimated_read_time": int}
    """
    blog_ctx = BLOG_CONTEXTS.get(brand) or BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    now_str = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    prompt = f"""
{blog_ctx}

今日は {now_str} です。
重複しないよう、今日の日付と時刻を考慮して新鮮なトピックを1つ選び、
そのままそのトピックで記事を書いてください。

目標文字数: {word_count}字前後

【出力形式】必ずJSON形式で返してください:
{{
  "title": "SEOを意識した記事タイトル（30〜40文字）",
  "meta_description": "検索結果に表示されるメタ説明文（120文字以内）",
  "content_html": "WordPress用HTML本文（h2/h3/p/ul/strongタグ使用）",
  "content_plain": "プレーンテキストの本文",
  "tags": ["タグ1", "タグ2", "タグ3"],
  "estimated_read_time": 読了時間（分）
}}

記事の構成:
1. 導入（読者の共感を引く）
2. 本論（h2見出し3〜5個、各セクションに具体例）
3. まとめ（読者へのメッセージ）
4. CTA（次のアクションへの誘導）
"""

    import re
    client = _client()
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        messages=[
            {"role": "user", "content": prompt},
        ],
    )
    raw = msg.content[0].text.strip()

    result = None
    # まずそのままパース試行
    try:
        result = json.loads(raw)
    except Exception:
        pass
    # 失敗時: マークダウンコードブロックを除去して再試行
    if not result:
        m = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw)
        if m:
            try:
                result = json.loads(m.group(1))
            except Exception:
                pass
    # 失敗時: 最初の { から JSONDecoder でパース
    if not result:
        try:
            idx = raw.index("{")
            result, _ = json.JSONDecoder().raw_decode(raw[idx:])
        except Exception:
            pass

    if not result:
        logger.warning(f"ブログJSON解析失敗 [{brand}] raw={raw[:120]}")
        result = {
            "title": f"{brand} ブログ",
            "meta_description": "",
            "content_html": f"<p>{raw}</p>",
            "content_plain": raw,
            "tags": [],
            "estimated_read_time": word_count // 400,
        }

    logger.info(f"ブログ記事自動生成完了: [{brand}] {result.get('title','')}")
    return result


# ── C: 週次コンテンツカレンダー自動生成 ───────────────────────────

def generate_weekly_calendar(
    brand: str = "dsc-marketing",
    week_start: str | None = None,
    posts_per_day: int = 1,
) -> dict:
    """
    翌週1週間分のコンテンツカレンダーをAIが自動生成する。
    毎週月曜朝に実行することを想定。

    Args:
        brand:         ブランドキー
        week_start:    週の開始日 "YYYY-MM-DD"（省略時: 翌月曜）
        posts_per_day: 1日あたりの投稿数（デフォルト1）

    Returns:
        {
          "week_start": str,
          "week_end": str,
          "brand": str,
          "theme": str,          # 今週の全体テーマ
          "calendar": [
            {
              "date": "YYYY-MM-DD",
              "day_of_week": str,
              "posts": [
                {
                  "time": "HH:MM",
                  "platform": str,
                  "topic": str,
                  "format": "image / reel / carousel / stories",
                  "caption_draft": str,
                  "hashtags": str,
                  "notes": str,
                }
              ]
            }
          ],
          "line_schedule": [
            {"date": str, "topic": str, "message_draft": str}
          ],
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    perf_ctx  = get_performance_summary(brand, "instagram")

    if week_start is None:
        today = datetime.now()
        days_until_monday = (7 - today.weekday()) % 7 or 7
        next_monday = today + timedelta(days=days_until_monday)
        week_start = next_monday.strftime("%Y-%m-%d")

    week_start_dt = datetime.strptime(week_start, "%Y-%m-%d")
    week_end_dt   = week_start_dt + timedelta(days=6)
    week_end      = week_end_dt.strftime("%Y-%m-%d")

    # 7日分の日付リスト
    days_jp = ["月", "火", "水", "木", "金", "土", "日"]
    dates = [
        {
            "date": (week_start_dt + timedelta(days=i)).strftime("%Y-%m-%d"),
            "day":  days_jp[(week_start_dt + timedelta(days=i)).weekday()],
        }
        for i in range(7)
    ]
    dates_str = "\n".join(f"- {d['date']}（{d['day']}）" for d in dates)

    prompt = f"""
{brand_ctx}
{perf_ctx}

以下の1週間分のSNSコンテンツカレンダーを作成してください。

【期間】{week_start} 〜 {week_end}
【日程】
{dates_str}

条件:
- 1日{posts_per_day}投稿（Instagram中心）
- 月・木はLINE一斉配信も追加
- 週全体で統一感のあるテーマを設定する
- 月曜は認知拡大系、水曜は教育系、金曜は共感系など曜日で変化をつける
- 土日はエンゲージメント重視（参加型・質問系）
- フォーマット（image/reel/carousel/stories）をバランスよく混在させる

JSON形式で返してください:
{{
  "week_start": "{week_start}",
  "week_end": "{week_end}",
  "theme": "今週の全体テーマ（20文字以内）",
  "calendar": [
    {{
      "date": "YYYY-MM-DD",
      "day_of_week": "月〜日",
      "posts": [
        {{
          "time": "12:00",
          "platform": "instagram",
          "topic": "投稿トピック（20文字以内）",
          "format": "image / reel / carousel / stories",
          "caption_draft": "キャプション草稿（200文字以内）",
          "hashtags": "#タグ1 #タグ2 ... （5〜10個）",
          "notes": "制作メモ・素材の指示（30文字以内）"
        }}
      ]
    }}
  ],
  "line_schedule": [
    {{
      "date": "YYYY-MM-DD",
      "topic": "LINEメッセージのトピック",
      "message_draft": "メッセージ草稿（200文字以内、絵文字含む）"
    }}
  ]
}}

- 「確実」「必ず儲かる」などの誇大表現は使わない
- JSONのみ返す
"""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    result = _parse_json(resp.content[0].text.strip(), {})
    result.setdefault("week_start", week_start)
    result.setdefault("week_end", week_end)
    result.setdefault("brand", brand)
    logger.info(f"週次カレンダー生成完了: {week_start}〜{week_end} ({brand})")
    return result


def generate_story_content(
    topic: str,
    brand: str = "dsc-marketing",
    story_type: str = "promotion",
) -> dict:
    """
    Instagramストーリー用コンテンツを生成する（3フレーム構成）

    Returns:
        {
          "frames": [
            {"frame": 1, "type": "hook", "headline": str, "subtext": str, "emoji": str, "bg": str},
            {"frame": 2, "type": "content", "headline": str, "subtext": str, "emoji": str, "bg": str},
            {"frame": 3, "type": "cta", "headline": str, "subtext": str, "emoji": str, "bg": str, "button": str},
          ],
          "caption": str,
          "hashtags": str,
          "sticker_suggestion": str,
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    type_map = {
        "promotion": "プロモーション・サービス紹介",
        "info":      "情報共有・豆知識",
        "poll":      "アンケート・質問",
        "event":     "イベント・お知らせ",
        "behind":    "舞台裏・日常",
    }
    type_label = type_map.get(story_type, story_type)

    prompt = f"""{brand_ctx}

【トピック】{topic}
【ストーリータイプ】{type_label}

Instagramストーリー（縦型・15秒×3枚）のコンテンツをJSON形式で作成してください。
各フレームは読んで即理解できるシンプルさが重要。

{{
  "frames": [
    {{
      "frame": 1,
      "type": "hook",
      "headline": "掴みの一言（20文字以内）",
      "subtext": "補足テキスト（40文字以内）",
      "emoji": "雰囲気に合う絵文字1〜2個",
      "bg": "グラデーション色イメージ（例: purple-blue, orange-red, green-teal）"
    }},
    {{
      "frame": 2,
      "type": "content",
      "headline": "メインメッセージ（25文字以内）",
      "subtext": "詳細・数字・理由（60文字以内）",
      "emoji": "絵文字1〜2個",
      "bg": "グラデーション色イメージ"
    }},
    {{
      "frame": 3,
      "type": "cta",
      "headline": "行動を促す一言（20文字以内）",
      "subtext": "次のアクション（40文字以内）",
      "emoji": "絵文字1〜2個",
      "bg": "グラデーション色イメージ",
      "button": "ボタンテキスト（10文字以内）"
    }}
  ],
  "caption": "ストーリーに添えるキャプション（100文字以内）",
  "hashtags": "#タグ1 #タグ2 #タグ3（5個）",
  "sticker_suggestion": "おすすめのインタラクティブスタンプ（例: アンケート・質問・スライダー）"
}}
JSONのみ返す。"""

    client = _client()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.content[0].text.strip(), {"frames": [], "caption": "", "hashtags": ""})


def generate_reel_script_v2(
    topic: str,
    brand: str = "dsc-marketing",
    duration: int = 30,
    style: str = "教育系",
) -> dict:
    """
    リール台本を生成する（v2: スタイル・尺・ナレーション付き）

    Returns:
        {
          "title": str,
          "hook": str,
          "slides": [{"no": int, "headline": str, "detail": str, "duration_sec": int}],
          "narration": str,
          "cta": str,
          "caption": str,
          "hashtags": str,
          "bgm_mood": str,
          "thumbnail_concept": str,
        }
    """
    brand_ctx = BRAND_CONTEXTS.get(brand, BRAND_CONTEXTS["dsc-marketing"])
    style_map = {
        "教育系":   "有益な情報を教える形式。数字・事実・ノウハウ重視",
        "体験談":   "一人称の体験談・ストーリー形式。感情移入しやすい",
        "比較":     "Before/After または A vs B の対比形式",
        "リスト系": "〇選・〇つの方法など箇条書き形式。保存率が高い",
        "問題解決": "よくある悩みを提示→解決策を提示する形式",
    }
    style_desc = style_map.get(style, style)

    prompt = f"""{brand_ctx}

【トピック】{topic}
【尺】{duration}秒
【スタイル】{style}（{style_desc}）

Instagramリール動画の完全な台本をJSON形式で作成してください。

{{
  "title": "動画タイトル（20文字以内・サムネに使用）",
  "hook": "冒頭0〜3秒の掴みセリフ（20文字以内）",
  "slides": [
    {{"no": 1, "headline": "スライド見出し（15文字以内）", "detail": "補足テキスト（30文字以内）", "duration_sec": 5}},
    {{"no": 2, "headline": "スライド見出し", "detail": "補足テキスト", "duration_sec": 5}},
    {{"no": 3, "headline": "スライド見出し", "detail": "補足テキスト", "duration_sec": 5}},
    {{"no": 4, "headline": "スライド見出し", "detail": "補足テキスト", "duration_sec": 5}},
    {{"no": 5, "headline": "CTA（保存・フォロー・相談）", "detail": "誘導テキスト", "duration_sec": 5}}
  ],
  "narration": "ナレーション全文（読み上げ用・自然な口語）",
  "cta": "最後のCTA（20文字以内）",
  "caption": "投稿キャプション（300文字以内・ハッシュタグなし）",
  "hashtags": "#タグ（10〜15個）",
  "bgm_mood": "BGMの雰囲気（例: アップテンポ・落ち着いたピアノ・ポップ）",
  "thumbnail_concept": "サムネイル画像のイメージ（40文字以内）"
}}
JSONのみ返す。"""

    client = _client()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    return _parse_json(resp.content[0].text.strip(), {
        "title": topic[:20], "hook": "", "slides": [], "cta": "保存してあとで見返してね",
        "caption": "", "hashtags": "", "bgm_mood": "", "thumbnail_concept": "",
    })


def save_weekly_calendar(calendar: dict, brand: str = "dsc-marketing") -> Path:
    """
    週次カレンダーをYAMLファイルとして保存する。
    content_queue/calendar/ に保存。

    Returns:
        保存したファイルのPath
    """
    calendar_dir = Path(__file__).parent.parent / "content_queue" / "calendar"
    calendar_dir.mkdir(parents=True, exist_ok=True)

    week_start = calendar.get("week_start", datetime.now().strftime("%Y-%m-%d"))
    filename   = f"{week_start}_{brand}_calendar.yaml"
    path       = calendar_dir / filename

    import yaml
    path.write_text(
        yaml.dump(calendar, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(f"週次カレンダー保存: {path}")
    return path
