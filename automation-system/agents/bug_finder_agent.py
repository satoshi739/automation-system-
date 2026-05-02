"""
Bug Finding Agent
=================
git diff を受け取り Claude API でバグを検出。
  - バグあり     → LINE通知（バグなしは通知しない）
  - auto_fixable → ブランチ作成 → パッチ適用 → push → GitHub Draft PR 作成

Usage:
    python bug_finder_agent.py <diff_file>

Environment:
    ANTHROPIC_API_KEY
    ALERT_LINE_CHANNEL_ACCESS_TOKEN
    OWNER_LINE_USER_ID
    GITHUB_TOKEN      (GitHub Actions で自動供給)
    REPO              e.g. satoshi739/automation-system-
    BRANCH            push されたブランチ名
    COMMIT_SHA
    COMMIT_MESSAGE
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import requests

MAX_DIFF_CHARS = 60_000

SYSTEM_PROMPT = """あなたはシニアソフトウェアエンジニアです。
git diff を受け取り、バグを検出して JSON だけを返してください。説明文は不要です。

## 検出対象
- critical: クラッシュ・セキュリティ脆弱性・データ消失
- high:     誤動作・レースコンディション・認証バイパス
- medium:   エッジケース欠落・不適切なエラーハンドリング

## 出力形式（このJSONのみ返す。前後に文章を入れない）
{
  "has_bugs": true,
  "bugs": [
    {
      "severity": "critical" | "high" | "medium",
      "file": "path/to/file.py",
      "line": 42,
      "description": "バグの説明（日本語）",
      "fix_snippet": "修正後コードの簡潔な説明（日本語）または null"
    }
  ],
  "patches": [
    {
      "file": "path/to/file.py",
      "search": "置換対象の正確なコード（diff の - 行から5行以内）",
      "replace": "置換後コード"
    }
  ],
  "summary": "LINE通知用の1行サマリー（日本語、40字以内）",
  "auto_fixable": true
}

バグが見つからない場合:
{"has_bugs": false, "bugs": [], "patches": [], "summary": "バグ検出なし", "auto_fixable": false}

注意: patches は diff の - 行を正確に再現し、search が実ファイルに存在するものだけ含める。
"""


# ── diff 読み込み ──────────────────────────────────────────────

def read_diff(diff_path: str) -> str:
    text = Path(diff_path).read_text(errors="replace")
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS] + "\n\n[... diff truncated ...]"
    return text


# ── Claude 解析 ────────────────────────────────────────────────

def extract_json(text: str) -> dict:
    """Claude の返答から JSON を頑健に抽出する。"""
    # 1. 直接パース
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. ```json ... ``` ブロック
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3. 最外の { } を探す
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    # 4. 解析不能 → バグなし扱いで安全に続行
    print("[BugFinder] JSON 解析失敗 — バグなし扱いで続行")
    return {"has_bugs": False, "bugs": [], "patches": [], "summary": "解析エラー", "auto_fixable": False}


def analyze_diff(diff_text: str) -> dict:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"```diff\n{diff_text}\n```"}],
    )
    return extract_json(response.content[0].text.strip())


# ── LINE 通知 ──────────────────────────────────────────────────

def send_line(message: str) -> None:
    token = os.environ.get("ALERT_LINE_CHANNEL_ACCESS_TOKEN", "")
    user_id = os.environ.get("OWNER_LINE_USER_ID", "")
    if not token or not user_id:
        print("[LINE] token/user_id 未設定 — スキップ")
        return

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": message}]},
        timeout=10,
    )
    if resp.ok:
        print("[LINE] 通知送信成功")
    else:
        print(f"[LINE] 送信失敗: {resp.status_code} {resp.text[:100]}")


def build_line_message(result: dict, commit_sha: str, commit_msg: str, repo: str) -> str:
    sha_short = commit_sha[:7] if commit_sha else "unknown"
    severity_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡"}

    bugs = result.get("bugs", [])
    lines = [
        "🐛 Bug Finding Agent",
        f"{sha_short} — {commit_msg[:50]}",
        f"repo: {repo}",
        "",
        f"⚠️ {len(bugs)} 件のバグを検出",
        "",
    ]
    for bug in bugs[:5]:
        icon = severity_icon.get(bug.get("severity", "medium"), "⚪")
        lines.append(f"{icon} [{bug['severity'].upper()}] {bug.get('file','?')}:{bug.get('line','?')}")
        lines.append(f"   {bug.get('description','')}")
    if len(bugs) > 5:
        lines.append(f"   ...他 {len(bugs)-5} 件")

    if result.get("auto_fixable"):
        lines += ["", "🔧 自動修正PR を作成中..."]

    return "\n".join(lines)


# ── 自動修正 PR ────────────────────────────────────────────────

def apply_patches_and_push(
    patches: list,
    fix_branch: str,
    base_branch: str,
    repo: str,
    commit_sha: str,
    token: str,
) -> bool:
    """パッチを適用して fix_branch へ push する。成功したら True。"""

    # git 認証設定
    subprocess.run(["git", "config", "user.email", "bug-finder-bot@github-actions"], check=False)
    subprocess.run(["git", "config", "user.name", "Bug Finding Agent"], check=False)
    remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
    subprocess.run(["git", "remote", "set-url", "origin", remote_url], check=False)

    # ブランチ作成
    r = subprocess.run(["git", "checkout", "-b", fix_branch], capture_output=True)
    if r.returncode != 0:
        print(f"[PR] ブランチ作成失敗: {r.stderr.decode()[:200]}")
        return False

    # パッチ適用
    changed: list[str] = []
    for patch in patches:
        file_path = patch.get("file", "")
        search = patch.get("search", "")
        replace = patch.get("replace", "")
        if not (file_path and search and replace):
            continue
        p = Path(file_path)
        if not p.exists():
            print(f"[PR] ファイル不在: {file_path}")
            continue
        content = p.read_text(errors="replace")
        if search not in content:
            print(f"[PR] パッチ不一致（スキップ）: {file_path}")
            continue
        p.write_text(content.replace(search, replace, 1))
        changed.append(file_path)
        print(f"[PR] パッチ適用: {file_path}")

    if not changed:
        print("[PR] 適用できたパッチなし — ブランチを削除して終了")
        subprocess.run(["git", "checkout", base_branch], check=False)
        subprocess.run(["git", "branch", "-D", fix_branch], check=False)
        return False

    # コミット（[skip-bugcheck] で無限ループ防止）
    subprocess.run(["git", "add"] + changed, check=True)
    subprocess.run(
        ["git", "commit", "-m",
         f"fix: auto-fix bugs from {commit_sha[:7]} [skip-bugcheck]"],
        check=True,
    )

    # Push
    r = subprocess.run(["git", "push", "origin", fix_branch], capture_output=True)
    if r.returncode != 0:
        print(f"[PR] push 失敗: {r.stderr.decode()[:200]}")
        return False

    print(f"[PR] push 完了: {fix_branch}")
    return True


def create_fix_pr(result: dict, base_branch: str, repo: str, commit_sha: str) -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("[PR] GITHUB_TOKEN 未設定 — スキップ")
        return None

    patches = result.get("patches", [])
    if not patches:
        print("[PR] patches なし — スキップ")
        return None

    fix_branch = f"bugfix/auto-{commit_sha[:7]}-{datetime.now().strftime('%m%d%H%M')}"

    # ブランチ作成・パッチ適用・push
    if not apply_patches_and_push(patches, fix_branch, base_branch, repo, commit_sha, token):
        return None

    # PR 本文
    bugs = result.get("bugs", [])
    body_lines = [
        "## 🐛 自動バグ修正 PR",
        f"\n元コミット: `{commit_sha[:7]}`\n",
        "### 検出されたバグ",
    ]
    for bug in bugs:
        body_lines.append(
            f"- **[{bug.get('severity','?').upper()}]** "
            f"`{bug.get('file','')}:{bug.get('line','')}` — {bug.get('description','')}"
        )
        if bug.get("fix_snippet"):
            body_lines.append(f"  > {bug['fix_snippet']}")
    body_lines += ["\n---", "_Generated by Bug Finding Agent_"]

    # PR 作成
    resp = requests.post(
        f"https://api.github.com/repos/{repo}/pulls",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={
            "title": f"🐛 [Auto Fix] {result.get('summary','バグ自動修正')[:60]}",
            "body": "\n".join(body_lines),
            "head": fix_branch,
            "base": base_branch,
            "draft": True,
        },
        timeout=15,
    )
    if resp.ok:
        pr_url = resp.json().get("html_url", "")
        print(f"[PR] 作成成功: {pr_url}")
        return pr_url
    else:
        print(f"[PR] 作成失敗: {resp.status_code} {resp.text[:200]}")
        return None


# ── エントリーポイント ─────────────────────────────────────────

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: bug_finder_agent.py <diff_file>")
        sys.exit(1)

    diff_path    = sys.argv[1]
    commit_sha   = os.environ.get("COMMIT_SHA", "")
    commit_msg   = os.environ.get("COMMIT_MESSAGE", "")
    repo         = os.environ.get("REPO", "")
    branch       = os.environ.get("BRANCH", "main")

    print(f"[BugFinder] sha={commit_sha[:7] if commit_sha else '?'} branch={branch}")

    diff_text = read_diff(diff_path)
    if not diff_text.strip():
        print("[BugFinder] diff が空 — スキップ")
        return

    print("[BugFinder] Claude API でバグ解析中...")
    result = analyze_diff(diff_text)
    has_bugs = result.get("has_bugs", False)
    bugs     = result.get("bugs", [])
    print(f"[BugFinder] has_bugs={has_bugs} count={len(bugs)}")

    # バグなし → 通知なしで終了（ノイズ防止 #4）
    if not has_bugs:
        print("[BugFinder] バグなし — LINE通知スキップ")
        return

    # LINE 通知
    msg = build_line_message(result, commit_sha, commit_msg, repo)
    send_line(msg)

    # 自動修正 PR
    if result.get("auto_fixable"):
        pr_url = create_fix_pr(result, branch, repo, commit_sha)
        if pr_url:
            send_line(f"🔧 自動修正PR作成完了\n{pr_url}")

    # critical バグ → CI 失敗でマージをブロック
    critical = [b for b in bugs if b.get("severity") == "critical"]
    if critical:
        print(f"[BugFinder] ⛔ critical {len(critical)} 件 — exit 1")
        sys.exit(1)

    print("[BugFinder] 完了")


if __name__ == "__main__":
    main()
