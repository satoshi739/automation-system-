"""
メール自動返信モジュール
- 問い合わせフォームからのメールを受け取り、自動で返信する
- 予約確認・相談受付の自動応答
- 重要な判断が必要なものはフラグを立てて通知する

使い方:
  Webサイトのフォームから問い合わせが来たら、このモジュールが自動返信。
  担当者への転送・通知は別途設定（Gmailフィルタ or Webhook連携）。

Gmail API を使う場合の前提:
  - Google Cloud Console でGmail APIを有効化
  - credentials.json を automation-system/ に配置
  - pip install google-auth google-auth-oauthlib google-api-python-client
"""

import logging
import os
import re
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "config" / "email_templates"


# -------------------------------------------------------
# メール送信（SMTPを使う場合。Gmailアプリパスワードが必要）
# -------------------------------------------------------
class EmailSender:
    def __init__(self):
        self.smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        self.from_email = os.environ.get("FROM_EMAIL", "")
        self.from_password = os.environ.get("FROM_EMAIL_PASSWORD", "")
        self.dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
        if not self.from_email or not self.from_password:
            logger.warning("FROM_EMAIL または FROM_EMAIL_PASSWORD 未設定 — メール送信を無効化")
            self.enabled = False
        else:
            self.enabled = True

    def send(self, to: str, subject: str, body: str, reply_to: str = "") -> bool:
        if not self.enabled:
            logger.warning("FROM_EMAIL未設定、メール送信をスキップ")
            return False
        if self.dry_run:
            logger.info(f"[DRY RUN] メール送信: to={to}, subject={subject}")
            return True

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.from_email
        msg["To"] = to
        if reply_to:
            msg["Reply-To"] = reply_to

        msg.attach(MIMEText(body, "plain", "utf-8"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.from_email, self.from_password)
                server.sendmail(self.from_email, to, msg.as_string())
            logger.info(f"メール送信完了: {to}")
            return True
        except Exception as e:
            logger.error(f"メール送信エラー: {e}", exc_info=True)
            return False


# -------------------------------------------------------
# 問い合わせ分類と自動返信テンプレ
# -------------------------------------------------------
INQUIRY_PATTERNS = {
    "dsc_marketing": {
        "keywords": ["マーケ", "集客", "SNS", "インスタ", "LINE公式", "ホームページ", "HP", "Web制作", "SEO", "MEO"],
        "subject": "【DSc Marketing】お問い合わせありがとうございます",
        "template": """
{name} 様

この度は DSc Marketing へのお問い合わせをいただき、
誠にありがとうございます。

ご連絡いただきました内容を確認し、
担当者より改めてご連絡いたします。

＜受付内容＞
{inquiry}

---
なお、ご返信は平日 10:00〜17:00 となります。
お急ぎの場合は、公式LINEよりご連絡ください。

DSc Marketing | 株式会社ユニバースプラネットジャパン
https://dsc-marketing.com/
""",
    },
    "cashflowsupport": {
        "keywords": ["ファクタリング", "資金繰り", "売掛", "キャッシュフロー", "急ぎ", "資金調達"],
        "subject": "【cashflowsupport】お問い合わせを受け付けました",
        "template": """
{name} 様

この度は cashflowsupport へのご相談をいただき、
誠にありがとうございます。

内容を確認し、担当者より改めてご連絡いたします。

＜受付内容＞
{inquiry}

---
資金繰りに関するご相談は、お急ぎのケースも多いかと思います。
至急のご要件の場合は、お電話またはLINEにてご連絡ください。

cashflowsupport | 株式会社ユニバースプラネットジャパン
https://cashflowsupport.jp/
""",
    },
    "upjapan": {
        "keywords": ["コンサル", "事業設計", "経営", "戦略", "UPJ", "ユニバース"],
        "subject": "【UPJ】お問い合わせありがとうございます",
        "template": """
{name} 様

この度は株式会社ユニバースプラネットジャパン（UPJ）への
お問い合わせをいただき、誠にありがとうございます。

内容を確認し、担当者より3営業日以内にご連絡いたします。

＜受付内容＞
{inquiry}

---
株式会社ユニバースプラネットジャパン
https://upjapan.co.jp/
""",
    },
}

# 自動返信せず担当者に通知する（重要な判断が必要な）パターン
ESCALATION_KEYWORDS = [
    "クレーム", "返金", "解約", "契約違反", "弁護士", "訴訟",
    "詐欺", "急ぎ", "至急", "緊急", "今すぐ", "キャンセル",
    "未入金", "入金できない", "遅延",
]


def classify_inquiry(subject: str, body: str) -> tuple[str, bool]:
    """
    問い合わせを分類する

    Returns:
        (brand_key, needs_escalation)
        brand_key: "dsc_marketing" / "cashflowsupport" / "upjapan" / "general"
        needs_escalation: True なら担当者への即時通知が必要
    """
    text = f"{subject} {body}".lower()

    # エスカレーション判定
    needs_escalation = any(kw in text for kw in ESCALATION_KEYWORDS)

    # ブランド分類
    for brand_key, cfg in INQUIRY_PATTERNS.items():
        if any(kw in text for kw in cfg["keywords"]):
            return brand_key, needs_escalation

    return "general", needs_escalation


def auto_reply(
    to_email: str,
    sender_name: str,
    inquiry_body: str,
    inquiry_subject: str = "",
) -> dict:
    """
    問い合わせに自動返信する

    Returns:
        {"status": "sent"/"escalated"/"dry_run", "brand": ..., "escalation": bool}
    """
    brand_key, needs_escalation = classify_inquiry(inquiry_subject, inquiry_body)

    sender = EmailSender()

    if needs_escalation:
        # エスカレーションが必要 → 担当者に通知して自動返信しない
        logger.warning(f"エスカレーション検知: from={to_email}, subject={inquiry_subject}")
        _notify_escalation(sender, to_email, sender_name, inquiry_subject, inquiry_body)
        return {"status": "escalated", "brand": brand_key, "escalation": True}

    # 自動返信
    cfg = INQUIRY_PATTERNS.get(brand_key, INQUIRY_PATTERNS["upjapan"])
    body = cfg["template"].format(
        name=sender_name or "お客様",
        inquiry=inquiry_body[:200],
    )
    ok = sender.send(
        to=to_email,
        subject=cfg["subject"],
        body=body.strip(),
    )

    status = "sent" if ok else "error"
    logger.info(f"自動返信: {status}, brand={brand_key}, to={to_email}")
    return {"status": status, "brand": brand_key, "escalation": False}


def _notify_escalation(
    sender: EmailSender,
    from_email: str,
    name: str,
    subject: str,
    body: str,
):
    """担当者への緊急通知メール"""
    notify_email = os.environ.get("NOTIFY_EMAIL", "")
    if not notify_email:
        logger.warning("NOTIFY_EMAIL が設定されていないためエスカレーション通知をスキップ")
        return

    msg = f"""
【要対応】問い合わせが届いています

差出人: {name} <{from_email}>
件名: {subject}

---
{body[:500]}
---

※ このメールはキーワードによる自動検知です。
  内容を確認して直接ご対応ください。
"""
    sender.send(
        to=notify_email,
        subject=f"【要対応】{subject}",
        body=msg.strip(),
        reply_to=from_email,
    )
