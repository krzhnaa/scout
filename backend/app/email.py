from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from html import escape
from typing import Any

from .config import settings


def _send_confirmation_sync(recipient: str, transaction: dict[str, Any]) -> None:
    if not all((settings.SMTP_HOST, settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.SMTP_FROM)):
        raise RuntimeError("SMTP is not configured.")
    transaction_type = str(transaction.get("purchase_type", "transaction")).replace("_", " ").title()
    provider = str(transaction.get("provider") or "Not separately verified")
    amount = f"{transaction['currency']} {transaction['price']}"
    details = transaction.get("transaction_details") or []
    detail_rows = "".join(f"<tr><td>{escape(str(item.get('label', 'Detail')))}</td><td>{escape(str(item.get('value', '')))}</td></tr>" for item in details if isinstance(item, dict) and item.get("label") and item.get("value"))
    if not detail_rows:
        detail_rows = "<tr><td>Relevant details</td><td>None separately verified</td></tr>"
    message = EmailMessage()
    message["Subject"] = "Scout — Demo Transaction Confirmation"
    message["From"] = settings.SMTP_FROM
    message["To"] = recipient
    message.set_content(f"SCOUT — AI Research & Action Agent\n\nTransaction Successful\n\nItem: {transaction['option_name']}\nType: {transaction_type}\nAmount: {amount}\nPayment status: PAID — Razorpay Test Mode\nOrder ID: {transaction['order_id']}\nPayment ID: {transaction['payment_id']}\nDate/time: {transaction['verified_at']}\nProvider: {provider}\nURL: {transaction['purchase_url']}\n\nImportant: This Razorpay Test Mode payment is a prototype simulation. The external merchant/provider was not charged and Scout has not completed an external purchase or booking.")
    message.add_alternative(f"""<!doctype html><html><body style="margin:0;background:#f4f6fb;font-family:Arial,sans-serif;color:#172033"><div style="max-width:640px;margin:24px auto;background:#fff;border-radius:14px;overflow:hidden;border:1px solid #e7eaf1"><div style="padding:28px 32px;background:#17133c;color:#fff"><div style="font-size:11px;letter-spacing:3px;color:#bfc7ff">SCOUT</div><h1 style="margin:8px 0 0;font-size:24px">AI Research &amp; Action Agent</h1></div><div style="padding:30px 32px"><h2 style="margin:0 0 8px;color:#2f7d5c">Transaction Successful</h2><p style="color:#5d6575">Verified Razorpay Test Mode transaction summary</p><table style="width:100%;border-collapse:collapse;font-size:14px"><tr><td style="padding:9px 0;color:#687082">Product / Booking</td><td style="padding:9px 0;font-weight:bold">{escape(str(transaction['option_name']))}</td></tr><tr><td style="padding:9px 0;color:#687082">Transaction type</td><td>{escape(transaction_type)}</td></tr><tr><td style="padding:9px 0;color:#687082">Amount</td><td style="font-weight:bold">{escape(amount)}</td></tr><tr><td style="padding:9px 0;color:#687082">Payment status</td><td style="color:#2f7d5c;font-weight:bold">PAID — Razorpay Test Mode</td></tr><tr><td style="padding:9px 0;color:#687082">Provider / Seller</td><td>{escape(provider)}</td></tr><tr><td style="padding:9px 0;color:#687082">Order ID</td><td>{escape(str(transaction['order_id']))}</td></tr><tr><td style="padding:9px 0;color:#687082">Payment ID</td><td>{escape(str(transaction['payment_id']))}</td></tr><tr><td style="padding:9px 0;color:#687082">Date/time</td><td>{escape(str(transaction['verified_at']))}</td></tr>{detail_rows}</table><p style="margin-top:22px"><a href="{escape(str(transaction['purchase_url']), quote=True)}" style="color:#5947e8">Open original provider page</a></p><div style="margin-top:24px;padding:16px;background:#fff7e8;border-left:4px solid #c58b22;font-size:13px;line-height:1.5"><strong>Important</strong><br>This was completed using Razorpay Test Mode as a prototype simulation. The external merchant/provider was not charged and Scout has not represented it as a real external purchase or booking.</div></div></div></body></html>""", subtype="html")
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as client:
        client.starttls()
        client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)


async def send_demo_confirmation(recipient: str, transaction: dict[str, Any]) -> None:
    await asyncio.to_thread(_send_confirmation_sync, recipient, transaction)
