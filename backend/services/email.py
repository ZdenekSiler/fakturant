"""
services/email.py — Transactional email via Resend REST API.

Uses httpx (already a project dependency) to call https://api.resend.com/emails.
No extra package required.

Dev mode: if RESEND_API_KEY is not set, the email is logged to the console
instead of sent. Verification/reset links are printed so local dev works
without any mail server.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("uvicorn.error")

_RESEND_URL = "https://api.resend.com/emails"


async def send_email(to: str, subject: str, html_body: str) -> None:
    """Send a transactional email via Resend. Falls back to console log if unconfigured."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_   = os.environ.get("RESEND_FROM", "onboarding@resend.dev")

    if not api_key:
        logger.info("[email-dev] To=%s | Subject=%s", to, subject)
        # Strip HTML tags for readable console output
        import re
        plain = re.sub(r"<[^>]+>", " ", html_body).strip()
        plain = re.sub(r"\s{2,}", "\n", plain)
        logger.info("[email-dev body]\n%s", plain)
        return

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"from": from_, "to": [to], "subject": subject, "html": html_body},
        )
        if not resp.is_success:
            logger.error("Resend error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        logger.info("Email sent via Resend to %s (id=%s)", to, resp.json().get("id"))


def _base_html(title: str, body: str) -> str:
    """Minimal HTML email wrapper matching the app's visual style."""
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f5f4f2;font-family:'DM Sans',Arial,sans-serif;font-size:14px;color:#1a1a18;">
<table width="100%" cellpadding="0" cellspacing="0" style="padding:40px 20px;">
  <tr><td align="center">
    <table width="480" cellpadding="0" cellspacing="0"
           style="background:#fff;border:1px solid #d8d3ca;border-radius:12px;padding:40px 36px;">
      <tr><td>
        <div style="font-size:22px;font-style:italic;margin-bottom:28px;color:#1a1a18;">
          <span style="font-size:26px;color:#2155cd;">F</span>akturant
        </div>
        {body}
        <div style="margin-top:32px;padding-top:20px;border-top:1px solid #eceae5;
                    font-size:11px;color:#b0ada6;text-align:center;">
          Fakturant · Česká fakturace
        </div>
      </td></tr>
    </table>
  </td></tr>
</table>
</body>
</html>"""


def make_verification_email(link: str) -> str:
    body = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:500;">Potvrďte svůj e-mail</p>
<p style="margin:0 0 24px;color:#4a4a58;line-height:1.6;">
  Pro dokončení registrace klikněte na tlačítko níže.
  Odkaz je platný <strong>7 dní</strong>.
</p>
<a href="{link}"
   style="display:inline-block;padding:10px 24px;background:#2155cd;color:#fff;
          text-decoration:none;border-radius:8px;font-weight:500;font-size:14px;">
  Potvrdit e-mail
</a>
<p style="margin:24px 0 0;font-size:12px;color:#7a7870;">
  Pokud tlačítko nefunguje, zkopírujte tento odkaz do prohlížeče:<br/>
  <a href="{link}" style="color:#2155cd;word-break:break-all;">{link}</a>
</p>
<p style="margin:16px 0 0;font-size:12px;color:#b0ada6;">
  Pokud jste si účet nezaregistrovali, tento e-mail ignorujte.
</p>"""
    return _base_html("Potvrďte svůj e-mail — Fakturant", body)


def make_reset_email(link: str) -> str:
    body = f"""
<p style="margin:0 0 16px;font-size:15px;font-weight:500;">Obnovení hesla</p>
<p style="margin:0 0 24px;color:#4a4a58;line-height:1.6;">
  Obdrželi jsme žádost o obnovení hesla k vašemu účtu.
  Klikněte na tlačítko níže — odkaz platí <strong>1 hodinu</strong>.
</p>
<a href="{link}"
   style="display:inline-block;padding:10px 24px;background:#2155cd;color:#fff;
          text-decoration:none;border-radius:8px;font-weight:500;font-size:14px;">
  Nastavit nové heslo
</a>
<p style="margin:24px 0 0;font-size:12px;color:#7a7870;">
  Pokud tlačítko nefunguje, zkopírujte tento odkaz do prohlížeče:<br/>
  <a href="{link}" style="color:#2155cd;word-break:break-all;">{link}</a>
</p>
<p style="margin:16px 0 0;font-size:12px;color:#b0ada6;">
  Pokud jste o obnovení hesla nežádali, tento e-mail ignorujte.
  Vaše heslo zůstane nezměněno.
</p>"""
    return _base_html("Obnovení hesla — Fakturant", body)
