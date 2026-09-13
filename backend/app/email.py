"""Transaction confirmation email — sent via Brevo's HTTP API.

Previously this used smtplib over a Gmail app-password connection. Moved to
Brevo's transactional email HTTP API because:
  - No blocking SMTP socket to manage/time out — it's one plain HTTPS POST.
  - Gmail app-passwords can get silently revoked with zero warning.
  - Brevo's free tier (300 emails/day) needs no custom domain — you can
    verify a single sender address (e.g. your Gmail) and send to anyone.
"""
from __future__ import annotations

from html import escape
from typing import Any

import httpx

from .config import settings


def _build_html(transaction: dict[str, Any]) -> str:
    transaction_type = str(transaction.get("purchase_type", "transaction")).replace("_", " ").title()
    provider = str(transaction.get("provider") or "Not separately verified")
    amount = f"{transaction['currency']} {transaction['price']}"
    details = transaction.get("transaction_details") or []
    detail_rows = "".join(
        f"<tr><td>{escape(str(item.get('label', 'Detail')))}</td><td>{escape(str(item.get('value', '')))}</td></tr>"
        for item in details
        if isinstance(item, dict) and item.get("label") and item.get("value")
    )
    if not detail_rows:
        detail_rows = "<tr><td>Relevant details</td><td>None separately verified</td></tr>"

    return f"""<!doctype html><html><body style="margin:0;background:#f4f6fb;font-family:Arial,sans-serif;color:#172033">
<div style="max-width:640px;margin:24px auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e7eaf1">
<div style="padding:28px 32px;background:#17133c;color:#fff">
<div style="font-size:11px;letter-spacing:3px;color:#bfc7ff">SCOUT</div>
<h1 style="margin:8px 0 0;font-size:24px">AI Research &amp; Action Agent</h1>
</div>
<div style="padding:30px 32px">
<h2 style="margin:0 0 8px;color:#2f7d5c">Transaction Successful</h2>
<p style="color:#5d6575">Verified Razorpay Test Mode transaction summary</p>
<table style="width:100%;border-collapse:collapse;font-size:14px">
<tr><td style="padding:9px 0;color:#687082">Product / Booking</td><td style="padding:9px 0;font-weight:bold">{escape(str(transaction['option_name']))}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Transaction type</td><td>{escape(transaction_type)}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Amount</td><td style="font-weight:bold">{escape(amount)}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Payment status</td><td style="color:#2f7d5c;font-weight:bold">PAID — Razorpay Test Mode</td></tr>
<tr><td style="padding:9px 0;color:#687082">Provider / Seller</td><td>{escape(provider)}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Order ID</td><td>{escape(str(transaction['order_id']))}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Payment ID</td><td>{escape(str(transaction['payment_id']))}</td></tr>
<tr><td style="padding:9px 0;color:#687082">Date/time</td><td>{escape(str(transaction['verified_at']))}</td></tr>
{detail_rows}
</table>
<p style="margin-top:22px"><a href="{escape(str(transaction['purchase_url']), quote=True)}" style="color:#5947e8">Open original provider page</a></p>
<div style="margin-top:24px;padding:16px;background:#fff7e8;border-left:4px solid #c58b22;font-size:13px;line-height:1.5">
<strong>Important</strong><br>This was completed using Razorpay Test Mode as a prototype simulation. The external merchant/provider was not charged and Scout has not represented it as a real external purchase or booking.
</div>
</div></div></body></html>"""


def _build_text(transaction: dict[str, Any]) -> str:
    transaction_type = str(transaction.get("purchase_type", "transaction")).replace("_", " ").title()
    provider = str(transaction.get("provider") or "Not separately verified")
    amount = f"{transaction['currency']} {transaction['price']}"
    return (
        f"SCOUT — AI Research & Action Agent\n\n"
        f"Transaction Successful\n\n"
        f"Item: {transaction['option_name']}\n"
        f"Type: {transaction_type}\n"
        f"Amount: {amount}\n"
        f"Payment status: PAID — Razorpay Test Mode\n"
        f"Order ID: {transaction['order_id']}\n"
        f"Payment ID: {transaction['payment_id']}\n"
        f"Date/time: {transaction['verified_at']}\n"
        f"Provider: {provider}\n"
        f"URL: {transaction['purchase_url']}\n\n"
        f"Important: This Razorpay Test Mode payment is a prototype simulation. "
        f"The external merchant/provider was not charged and Scout has not "
        f"completed an external purchase or booking."
    )

async def send_demo_confirmation(
    recipient: str,
    transaction: dict[str, Any],
) -> None:
    if not settings.BREVO_API_KEY:
        raise RuntimeError("BREVO_API_KEY is missing")

    if not settings.EMAIL_FROM:
        raise RuntimeError("EMAIL_FROM is missing")

    payload = {
        "sender": {
            "email": settings.EMAIL_FROM,
            "name": "Scout",
        },
        "to": [
            {
                "email": recipient,
            }
        ],
        "subject": "Scout — Demo Transaction Confirmation",
        "htmlContent": _build_html(transaction),
        "textContent": _build_text(transaction),
    }

    print("BREVO DEBUG: attempting email")
    print(f"BREVO DEBUG: sender={settings.EMAIL_FROM}")
    print(f"BREVO DEBUG: recipient={recipient}")
    print("BREVO DEBUG: API key present=", bool(settings.BREVO_API_KEY))

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            response = await client.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={
                    "api-key": settings.BREVO_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )

            print("BREVO DEBUG: status=", response.status_code)
            print("BREVO DEBUG: response=", response.text[:1000])

        except Exception as exc:
            print("BREVO DEBUG: HTTP REQUEST FAILED")
            print("BREVO DEBUG:", repr(exc))
            raise

        if response.status_code >= 400:
            raise RuntimeError(
                f"Brevo HTTP {response.status_code}: {response.text[:1000]}"
            )

    print("BREVO DEBUG: email accepted by Brevo")
