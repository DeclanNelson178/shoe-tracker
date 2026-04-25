"""Send a workflow-failure email using stdlib SMTP.

Invoked from `.github/workflows/scrape.yml` as the `if: failure()` step. Stays
stdlib-only on purpose: even if `pip install -e .` failed earlier in the
workflow, this script still runs and we still hear about it. Silent failure is
the enemy.
"""
from __future__ import annotations

import os
import smtplib
import sys
from email.message import EmailMessage
from typing import Callable, ContextManager, Mapping

SMTPFactory = Callable[[str, int], ContextManager]


def build_message(
    *,
    from_addr: str,
    to_addr: str,
    workflow: str,
    repo: str,
    run_url: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = f"[shoe-tracker] {workflow} failed in {repo}"
    msg.set_content(
        f"GitHub Actions workflow '{workflow}' failed in {repo}.\n\n"
        f"Run: {run_url}\n"
    )
    return msg


def _send(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    msg: EmailMessage,
    smtp_factory: SMTPFactory,
) -> bool:
    try:
        with smtp_factory(host, port) as smtp:
            smtp.login(username, password)
            smtp.send_message(msg)
    except Exception:
        return False
    return True


def main(
    env: Mapping[str, str] | None = None,
    smtp_factory: SMTPFactory | None = None,
) -> int:
    env = env if env is not None else os.environ
    from_addr = env.get("GMAIL_FROM")
    password = env.get("GMAIL_APP_PASSWORD")
    if not (from_addr and password):
        print(
            "notify_workflow_failure: GMAIL_FROM/GMAIL_APP_PASSWORD missing; "
            "skipping failure email.",
            file=sys.stderr,
        )
        return 0

    to_addr = env.get("NOTIFY_EMAIL") or from_addr
    workflow = env.get("GITHUB_WORKFLOW", "unknown-workflow")
    repo = env.get("GITHUB_REPOSITORY", "unknown-repo")
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    run_id = env.get("GITHUB_RUN_ID", "")
    run_url = (
        f"{server}/{repo}/actions/runs/{run_id}"
        if run_id else f"{server}/{repo}/actions"
    )

    msg = build_message(
        from_addr=from_addr, to_addr=to_addr,
        workflow=workflow, repo=repo, run_url=run_url,
    )
    ok = _send(
        host=env.get("SMTP_HOST", "smtp.gmail.com"),
        port=int(env.get("SMTP_PORT", "465")),
        username=from_addr,
        password=password,
        msg=msg,
        smtp_factory=smtp_factory or smtplib.SMTP_SSL,
    )
    if not ok:
        print("notify_workflow_failure: SMTP send failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
