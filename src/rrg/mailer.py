"""Send the report through the SendGrid v3 API.

The API key comes from SENDGRID_API_KEY and is never read from config.toml,
which is committed. Nothing here writes the key to disk or logs it.

Sending is never implicit: `send_report` is only called when the caller has
explicitly asked for it, and `preflight` reports what would happen without
touching the network.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import Config

API_URL = "https://api.sendgrid.com/v3/mail/send"
TIMEOUT = 30


class MailError(RuntimeError):
    """Raised when a report cannot be sent."""


@dataclass
class Preflight:
    """Everything checked before a send, so problems surface without network."""

    ok: bool
    problems: list[str]
    recipients: list[str]
    sender: str
    subject: str
    attachments: list[str]
    unsubscribe_group: int

    def render(self) -> str:
        lines = [
            f"  from        {self.sender or '(unset)'}",
            f"  to          {', '.join(self.recipients) or '(none configured)'}",
            f"  subject     {self.subject}",
            f"  charts      {', '.join(self.attachments) or '(none)'}",
            f"  unsubscribe {'group ' + str(self.unsubscribe_group)
                             if self.unsubscribe_group else 'not configured'}",
        ]
        if self.problems:
            lines.append("  blocked by:")
            lines += [f"    - {p}" for p in self.problems]
        return "\n".join(lines)


def preflight(cfg: Config, subject: str, charts: list[Path]) -> Preflight:
    """Validate a send without performing one."""
    problems: list[str] = []

    if not os.environ.get("SENDGRID_API_KEY"):
        problems.append(
            "SENDGRID_API_KEY is not set. Create a key with Mail Send permission "
            "in the SendGrid dashboard, then: export SENDGRID_API_KEY=..."
        )
    if not cfg.from_address:
        problems.append(
            "[email] from_address is empty. It must be an address you have "
            "verified as a Single Sender (or on a domain you authenticated) in "
            "SendGrid, or the API will reject the send."
        )
    if not cfg.to_addresses:
        problems.append("[email] to is empty — nobody to send to.")
    for chart in charts:
        if not chart.exists():
            problems.append(f"chart missing: {chart}")

    # Mail to anyone but yourself is a different obligation than mail to
    # yourself; surface it here rather than after it has gone out.
    others = [a for a in cfg.to_addresses if a != cfg.from_address]
    if others and not cfg.unsubscribe_group_id:
        problems.append(
            f"sending to {len(others)} address(es) other than your own with no "
            "unsubscribe group configured. Set [email] unsubscribe_group_id to a "
            "SendGrid suppression group id first."
        )

    return Preflight(
        ok=not problems,
        problems=problems,
        recipients=list(cfg.to_addresses),
        sender=cfg.from_address,
        subject=subject,
        attachments=[c.name for c in charts],
        unsubscribe_group=cfg.unsubscribe_group_id,
    )


def _payload(
    cfg: Config, subject: str, html: str, text: str, charts: list[Path]
) -> dict:
    # One personalization per recipient: a single personalization with several
    # `to` entries puts every address in the visible header, showing the list to
    # everyone on it.
    personalizations = [{"to": [{"email": address}]} for address in cfg.to_addresses]

    attachments = []
    for i, chart in enumerate(charts):
        attachments.append(
            {
                "content": base64.b64encode(chart.read_bytes()).decode("ascii"),
                "type": "image/png",
                "filename": chart.name,
                "disposition": "inline",
                "content_id": f"chart{i}",
            }
        )

    payload: dict = {
        "personalizations": personalizations,
        "from": {"email": cfg.from_address, "name": cfg.from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
        "attachments": attachments,
    }
    if cfg.unsubscribe_group_id:
        payload["asm"] = {"group_id": cfg.unsubscribe_group_id}
    return payload


def send_report(
    cfg: Config, subject: str, html: str, text: str, charts: list[Path]
) -> str:
    """Send. Raises MailError rather than returning a failure quietly."""
    check = preflight(cfg, subject, charts)
    if not check.ok:
        raise MailError("cannot send:\n" + "\n".join(f"  - {p}" for p in check.problems))

    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {os.environ['SENDGRID_API_KEY']}",
            "Content-Type": "application/json",
        },
        json=_payload(cfg, subject, html, text, charts),
        timeout=TIMEOUT,
    )

    if response.status_code == 202:
        return response.headers.get("X-Message-Id", "(no message id returned)")

    # SendGrid puts the actual reason in the body; the status alone is not
    # enough to act on.
    detail = response.text.strip()[:400] or "(empty response body)"
    if response.status_code in (401, 403):
        raise MailError(
            f"SendGrid rejected the credentials or sender ({response.status_code}). "
            f"Check the key has Mail Send permission and that {cfg.from_address} is "
            f"a verified sender. Response: {detail}"
        )
    raise MailError(f"SendGrid returned {response.status_code}: {detail}")
