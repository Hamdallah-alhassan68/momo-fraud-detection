"""Email notification service for MoMo Sentinel step-up verification and fraud alerts."""

from datetime import datetime, timezone
import email.utils
import json
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import httpx

from services import events
from services.otp_delivery import OtpDeliveryError

logger = logging.getLogger(__name__)

OUTBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "outbox"
OUTBOX_DIR.mkdir(parents=True, exist_ok=True)

# In-memory recent outbox for rapid access and debugging
_RECENT_OUTBOX = []
MAX_RECENT_OUTBOX = 50


def get_smtp_config():
    """Retrieve SMTP settings from environment variables."""
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587").strip() or 587)
    user = os.getenv("SMTP_USER", "").strip() or os.getenv("GMAIL_USER", "").strip()
    password = (
        os.getenv("SMTP_PASSWORD", "").strip()
        or os.getenv("SMTP_PASS", "").strip()
        or os.getenv("GMAIL_APP_PASSWORD", "").strip()
    )
    explicit_from_address = os.getenv("SMTP_FROM", "").strip()
    from_address = explicit_from_address or user or "security@momosentinel.com"
    use_tls = os.getenv("SMTP_TLS", "true").strip().lower() in ("true", "1", "yes")
    brevo_api_key = os.getenv("BREVO_API_KEY", "").strip()

    if not host and "@gmail.com" in user:
        host = "smtp.gmail.com"
        port = 587
        use_tls = True

    smtp_configured = bool(host and user and password)
    brevo_configured = bool(brevo_api_key and explicit_from_address)
    is_configured = smtp_configured or brevo_configured

    return {
        "configured": is_configured,
        "smtp_configured": smtp_configured,
        "brevo_configured": brevo_configured,
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_address": from_address,
        "use_tls": use_tls,
        "brevo_api_key": brevo_api_key,
    }


def _send_via_brevo(smtp, recipient_email, subject, text_content, html_content=None):
    payload = {
        "sender": {"email": smtp["from_address"]},
        "to": [{"email": recipient_email}],
        "subject": subject,
        "textContent": text_content,
    }
    if html_content:
        payload["htmlContent"] = html_content

    response = httpx.post(
        "https://api.brevo.com/v3/smtp/email",
        headers={"api-key": smtp["brevo_api_key"]},
        json=payload,
        timeout=10.0,
    )
    response.raise_for_status()


def send_step_up_email(recipient_email, otp_code, transaction_details=None):
    """
    Dispatch an authentic 6-digit Step-Up OTP verification email.

    If SMTP is configured (e.g. Gmail App Password or company SMTP), sends a real
    email via TLS. In all cases, persists the message to the outbox log, records
    in memory, and publishes an SSE notification event.
    """
    transaction_details = transaction_details or {}
    amount = transaction_details.get("amount", "0.00")
    currency = transaction_details.get("currency", "GHS")
    recipient = transaction_details.get("recipient", "Unknown")
    customer_name = transaction_details.get("customer_name", "Valued Customer")
    risk_reasons = transaction_details.get("reasons", ["Unusual activity detected"])
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    subject = f"MoMo Sentinel security verification required (Transfer of {currency} {amount})"

    plain_text = f"""
MoMo Sentinel - Security Verification Code

Dear {customer_name},

A transfer of {currency} {amount} to {recipient} requires additional authorization.
Our fraud prevention engine flagged this transaction for security review.

Risk Indicators:
- {chr(10).join('- ' + str(r) for r in risk_reasons)}

Your One-Time Passcode (OTP):
>>> {otp_code} <<<

This code expires in 5 minutes.
DO NOT SHARE THIS CODE WITH ANYONE, including MoMo customer care agents.

If you did not initiate this transaction, freeze your wallet immediately in the app.

Timestamp: {now_str}
MoMo Sentinel Hybrid Fraud Protection
"""

    reasons_html = "".join(f"<li style='color: #b91c1c; margin-bottom: 4px;'>{r}</li>" for r in risk_reasons)
    html_content = f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>MoMo Sentinel Security Alert</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 24px; color: #1e293b;">
  <div style="max-width: 540px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
    <div style="background: linear-gradient(135deg, #073451 0%, #0c5272 100%); padding: 24px 32px; color: #ffffff;">
      <div style="display: flex; align-items: center; gap: 8px;">
        <span style="font-size: 20px; font-weight: 700; color: #ffcc00; letter-spacing: 0.5px;">MoMo Sentinel</span>
        <span style="background: rgba(255,255,255,0.15); padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase;">Security Gate</span>
      </div>
      <h1 style="margin: 16px 0 4px; font-size: 20px; font-weight: 600; color: #ffffff;">Action Required: Authorize Transfer</h1>
      <p style="margin: 0; font-size: 13px; color: #9ed8e8;">A suspicious transaction was intercepted by real-time risk controls.</p>
    </div>

    <div style="padding: 32px;">
      <p style="margin: 0 0 16px; font-size: 14px; line-height: 1.5;">Hello <strong>{customer_name}</strong>,</p>
      <p style="margin: 0 0 20px; font-size: 14px; line-height: 1.5; color: #475569;">
        A transfer request for <strong>{currency} {amount}</strong> to <strong>{recipient}</strong> was flagged for step-up verification. Please confirm you initiated this transfer.
      </p>

      <div style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 12px 16px; border-radius: 0 8px 8px 0; margin-bottom: 24px;">
        <p style="margin: 0 0 6px; font-size: 12px; font-weight: 700; color: #991b1b; text-transform: uppercase;">Suspicious Signals Detected:</p>
        <ul style="margin: 0; padding-left: 18px; font-size: 13px;">
          {reasons_html}
        </ul>
      </div>

      <div style="background: #f0fdf4; border: 2px dashed #22c55e; border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 24px;">
        <span style="font-size: 12px; font-weight: 600; color: #15803d; text-transform: uppercase; letter-spacing: 1px;">Your Step-Up Security Passcode</span>
        <div style="font-size: 36px; font-weight: 800; letter-spacing: 8px; color: #073451; margin: 8px 0; font-family: 'Courier New', Courier, monospace;">
          {otp_code}
        </div>
        <span style="font-size: 12px; color: #64748b;">Expires in 5 minutes · Single-use authorization</span>
      </div>

      <p style="margin: 0 0 16px; font-size: 12px; color: #64748b; line-height: 1.4;">
        <strong>Security Warning:</strong> Never share this code with anyone, including MTN staff. If you did not attempt this transfer, freeze your wallet immediately in the MoMo operations console.
      </p>

      <div style="border-top: 1px solid #f1f5f9; padding-top: 16px; font-size: 11px; color: #94a3b8; display: flex; justify-content: space-between;">
        <span>MoMo Sentinel Hybrid Fraud Protection</span>
        <span>{now_str}</span>
      </div>
    </div>
  </div>
</body>
</html>
"""

    smtp = get_smtp_config()
    delivery_status = "LOCAL_OUTBOX"
    delivery_error = None

    if smtp["configured"]:
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp["from_address"]
            msg["To"] = recipient_email
            msg["Date"] = email.utils.formatdate(localtime=True)
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            if smtp.get("brevo_configured", False):
                _send_via_brevo(
                    smtp, recipient_email, subject, plain_text, html_content
                )
            else:
                with smtplib.SMTP(smtp["host"], smtp["port"], timeout=10.0) as server:
                    if smtp["use_tls"]:
                        server.starttls()
                    server.login(smtp["user"], smtp["password"])
                    server.sendmail(
                        smtp["from_address"], [recipient_email], msg.as_string()
                    )
            delivery_status = (
                "SENT_VIA_BREVO_API"
                if smtp.get("brevo_configured", False)
                else "SENT_VIA_SMTP"
            )
            logger.info(
                "Real Step-Up OTP email sent via %s to %s",
                "Brevo API" if smtp.get("brevo_configured", False) else "SMTP",
                recipient_email,
            )
        except Exception as error:
            delivery_status = "SMTP_FAILED_SAVED_TO_OUTBOX"
            delivery_error = str(error)
            logger.warning("SMTP email dispatch to %s failed: %s; recorded in outbox", recipient_email, error)
    else:
        logger.info("SMTP not configured; Step-Up OTP email for %s saved to local outbox", recipient_email)

    # Save to disk outbox log
    record = {
        "id": f"mail_{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        "timestamp": now_str,
        "recipient": recipient_email,
        "subject": subject,
        "delivery_status": delivery_status,
        "delivery_error": delivery_error,
        "smtp_configured": smtp["configured"],
        "amount": amount,
        "currency": currency,
        "recipient_msisdn": recipient,
    }

    try:
        filename = OUTBOX_DIR / f"{record['id']}.json"
        filename.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as error:
        logger.warning("Could not write outbox record: %s", error)

    # In-memory recent outbox
    _RECENT_OUTBOX.insert(0, record)
    if len(_RECENT_OUTBOX) > MAX_RECENT_OUTBOX:
        _RECENT_OUTBOX.pop()

    # Publish SSE notification so frontend can show real-time email toast
    try:
        events.publish(
            "email_dispatched",
            {
                "recipient": recipient_email,
                "subject": subject,
                "delivery_status": delivery_status,
                "smtp_configured": smtp["configured"],
                "timestamp": now_str,
            },
        )
    except Exception:
        pass

    return {
        "delivered": delivery_status in ("SENT_VIA_SMTP", "SENT_VIA_BREVO_API"),
        "delivery_status": delivery_status,
        "delivery_error": delivery_error,
        "smtp_configured": smtp["configured"],
        "recipient": recipient_email,
        "timestamp": now_str,
    }


def send_email_verification_email(recipient_email, verification_code):
    """Send an email-ownership challenge without exposing the code to logs or outbox."""
    smtp = get_smtp_config()
    if not smtp["configured"]:
        return False

    message = MIMEText(
        "Your MoMo Sentinel email verification code is "
        f"{verification_code}. It expires in 10 minutes. "
        "If you did not request this, ignore this message.",
        "plain",
        "utf-8",
    )
    message["Subject"] = "Verify your MoMo Sentinel email address"
    message["From"] = smtp["from_address"]
    message["To"] = recipient_email
    message["Date"] = email.utils.formatdate(localtime=True)

    try:
        if smtp.get("brevo_configured", False):
            _send_via_brevo(
                smtp,
                recipient_email,
                message["Subject"],
                message.get_payload(decode=True).decode("utf-8"),
            )
        else:
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=10.0) as server:
                if smtp["use_tls"]:
                    server.starttls()
                server.login(smtp["user"], smtp["password"])
                server.sendmail(
                    smtp["from_address"], [recipient_email], message.as_string()
                )
    except (OSError, smtplib.SMTPException, httpx.HTTPError) as error:
        logger.warning(
            "Email ownership verification delivery failed (%s).",
            type(error).__name__,
        )
        return False
    return True


def send_medium_risk_otp_email(recipient_email, otp_code, transaction_details=None):
    """Deliver a short-lived MEDIUM-risk OTP without persisting or publishing it."""
    smtp = get_smtp_config()
    brevo_api_key = smtp.get("brevo_api_key", "")
    if brevo_api_key and not os.getenv("SMTP_FROM", "").strip():
        raise OtpDeliveryError(
            "Set SMTP_FROM to an email sender verified in Brevo before requesting email verification."
        )
    if not smtp["configured"]:
        raise OtpDeliveryError(
            "Email OTP delivery is not configured. Configure BREVO_API_KEY and a verified SMTP_FROM sender, or authenticated SMTP."
        )

    recipient_email = str(recipient_email or "").strip()
    if (
        not recipient_email
        or recipient_email.lower().endswith("@example.test")
    ):
        raise OtpDeliveryError(
            "A deliverable email address is required for MEDIUM-risk verification. Choose passport/ID verification if this wallet has no usable email."
        )

    details = transaction_details or {}
    transaction_type = str(details.get("transaction_type") or "MoMo").replace(
        "_", " "
    )
    amount = float(details.get("amount") or 0)
    message = MIMEText(
        "Your MoMo Sentinel transaction requires additional authorization.\n\n"
        f"Transaction: {transaction_type}\n"
        f"Amount: GHS {amount:,.2f}\n"
        f"One-time code: {otp_code}\n\n"
        "This code expires in 10 minutes and can be used once. "
        "Never share it with anyone.",
        "plain",
        "utf-8",
    )
    message["Subject"] = "Your MoMo Sentinel verification code"
    message["From"] = smtp["from_address"]
    message["To"] = recipient_email
    message["Date"] = email.utils.formatdate(localtime=True)

    try:
        if smtp.get("brevo_configured", False):
            _send_via_brevo(
                smtp,
                recipient_email,
                message["Subject"],
                message.get_payload(decode=True).decode("utf-8"),
            )
        else:
            with smtplib.SMTP(smtp["host"], smtp["port"], timeout=10.0) as server:
                if smtp["use_tls"]:
                    server.starttls()
                server.login(smtp["user"], smtp["password"])
                server.sendmail(
                    smtp["from_address"], [recipient_email], message.as_string()
                )
    except (OSError, smtplib.SMTPException, httpx.HTTPError) as error:
        logger.warning(
            "MEDIUM-risk OTP email delivery failed (%s).",
            type(error).__name__,
        )
        raise OtpDeliveryError(
            "Email delivery failed. No transaction was completed; please try again."
        ) from error
    return True


def get_recent_outbox(limit=10):
    """Retrieve recently dispatched emails for the developer outbox inspection."""
    return _RECENT_OUTBOX[:limit]
