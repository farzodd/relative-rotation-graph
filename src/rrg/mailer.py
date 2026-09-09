"""Send the report through the SendGrid v3 API.

Every delivery setting — key, sender, recipients, unsubscribe group — comes from
the environment. None of it is a config field, because config.toml is committed
to a public repository and an address belongs to a person, not to a repo. There
is deliberately no file-based fallback: a fallback is how an address ends up in
version control.

Sending is never implicit: `send_report` runs only when the caller has explicitly
asked for it, and `preflight` reports what would happen without touching the
network.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path

import requests

ENV_KEY = "SENDGRID_API_KEY"
ENV_FROM = "RRG_MAIL_FROM"
ENV_FROM_NAME = "RRG_MAIL_FROM_NAME"
ENV_TO = "RRG_MAIL_TO"
ENV_GROUP = "RRG_MAIL_UNSUBSCRIBE_GROUP"

API_URL = "https://api.sendgrid.com/v3/mail/send"
TIMEOUT = 30


class MailError(RuntimeError):
    """Raised when a report cannot be sent."""


@dataclass(frozen=True)
class EmailSettings:
    """Delivery settings, read from the environment only."""

    api_key: str
    from_address: str
    from_name: str
    to_addresses: tuple[str, ...]
    unsubscribe_group_id: int

    @classmethod
    def from_env(cls) -> "EmailSettings":
        raw_to = os.environ.get(ENV_TO, "")
        # Accept comma or semicolon separated, tolerate stray whitespace.
        recipients = tuple(
            a.strip() for a in raw_to.replace(";", ",").split(",") if a.strip()
        )
        group = os.environ.get(ENV_GROUP, "").strip()
        return cls(
            api_key=os.environ.get(ENV_KEY, ""),
            from_address=os.environ.get(ENV_FROM, "").strip(),
            from_name=os.environ.get(ENV_FROM_NAME, "RRG Report").strip(),
            to_addresses=recipients,
            unsubscribe_group_id=int(group) if group.isdigit() else 0,
        )


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


def preflight(mail: EmailSettings, subject: str, charts: list[Path]) -> Preflight:
    """Validate a send without performing one."""
    problems: list[str] = []

    if not mail.api_key:
        problems.append(
            f"{ENV_KEY} is not set. Create a key with Mail Send permission in the "
            f"SendGrid dashboard, then: export {ENV_KEY}=..."
        )
    if not mail.from_address:
        problems.append(
            f"{ENV_FROM} is not set. It must be an address you have verified as a "
            "Single Sender (or on a domain you authenticated) in SendGrid, or the "
            "API will reject the send."
        )
    if not mail.to_addresses:
        problems.append(f"{ENV_TO} is not set — nobody to send to.")
    for chart in charts:
        if not chart.exists():
            problems.append(f"chart missing: {chart}")

    # Mail to anyone but yourself is a different obligation than mail to
    # yourself; surface it here rather than after it has gone out.
    others = [a for a in mail.to_addresses if a != mail.from_address]
    if others and not mail.unsubscribe_group_id:
        problems.append(
            f"sending to {len(others)} address(es) other than your own with no "
            f"unsubscribe group configured. Set {ENV_GROUP} to a SendGrid "
            "suppression group id first."
        )

    return Preflight(
        ok=not problems,
        problems=problems,
        recipients=list(mail.to_addresses),
        sender=mail.from_address,
        subject=subject,
        attachments=[c.name for c in charts],
        unsubscribe_group=mail.unsubscribe_group_id,
    )


def _payload(
    mail: EmailSettings, subject: str, html: str, text: str, charts: list[Path]
) -> dict:
    # One personalization per recipient: a single personalization with several
    # `to` entries puts every address in the visible header, showing the list to
    # everyone on it.
    personalizations = [{"to": [{"email": address}]} for address in mail.to_addresses]

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
        "from": {"email": mail.from_address, "name": mail.from_name},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text},
            {"type": "text/html", "value": html},
        ],
        "attachments": attachments,
    }
    if mail.unsubscribe_group_id:
        payload["asm"] = {"group_id": mail.unsubscribe_group_id}
    return payload


def send_report(
    mail: EmailSettings, subject: str, html: str, text: str, charts: list[Path]
) -> str:
    """Send. Raises MailError rather than returning a failure quietly."""
    check = preflight(mail, subject, charts)
    if not check.ok:
        raise MailError("cannot send:\n" + "\n".join(f"  - {p}" for p in check.problems))

    response = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {mail.api_key}",
            "Content-Type": "application/json",
        },
        json=_payload(mail, subject, html, text, charts),
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
            f"Check the key has Mail Send permission and that {mail.from_address} is "
            f"a verified sender. Response: {detail}"
        )
    raise MailError(f"SendGrid returned {response.status_code}: {detail}")
