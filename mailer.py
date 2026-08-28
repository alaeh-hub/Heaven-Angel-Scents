"""Best-effort outbound email — currently used only to notify HQ when
someone submits a package inquiry from the public partner portal (see
routes/portal.py).

Deliberately plain smtplib rather than Flask-Mail: this is the app's
only outbound email need so far, and a single function here avoids
pulling in + configuring a whole extension for it. If more email
use cases show up later, this is the natural place to grow, or to
swap for Flask-Mail if the surface area justifies it.

Every function here follows the same "never raise" contract as
audit.log_action: a broken or unconfigured mailer should never be the
reason a request fails — the caller is expected to still save whatever
it was emailing about (see partner_inquiries.email_sent in schema.sql),
and just treat a False return as "the notification didn't go out,
but the record is safe."
"""
import smtplib
from email.message import EmailMessage

from flask import current_app


def _smtp_configured(cfg):
    return bool(cfg.get("MAIL_SERVER") and cfg.get(
        "MAIL_DEFAULT_SENDER") and cfg.get("PARTNER_INQUIRY_NOTIFY_EMAIL"))


def send_partner_inquiry_email(
    *, package_name, partner_type, company_name, contact_person,
    phone, email, address, message,
):
    """Notify HQ of a new package inquiry from the public partner portal.

    Returns True if the email was sent, False if mail isn't configured
    or sending failed. Either way the caller should already have saved
    the inquiry to partner_inquiries — this function never raises, so
    it's safe to call after that insert without needing its own
    try/except at the call site.
    """
    cfg = current_app.config
    if not _smtp_configured(cfg):
        current_app.logger.warning(
            "Partner inquiry email not sent — mail is not configured "
            "(set MAIL_SERVER, MAIL_DEFAULT_SENDER, and "
            "PARTNER_INQUIRY_NOTIFY_EMAIL to enable it)."
        )
        return False

    body_lines = [
        f"New {partner_type.lower()} inquiry — {package_name}",
        "",
        f"Company: {company_name}",
        f"Contact person: {contact_person or '—'}",
        f"Phone: {phone or '—'}",
        f"Email: {email or '—'}",
        f"Address: {address or '—'}",
        "",
        "Message:",
        message or "—",
        "",
        "— Sent automatically from the Heaven & Angel Scents partner portal.",
    ]

    msg = EmailMessage()
    msg["Subject"] = f"Package inquiry: {company_name} — {package_name}"
    msg["From"] = cfg["MAIL_DEFAULT_SENDER"]
    msg["To"] = cfg["PARTNER_INQUIRY_NOTIFY_EMAIL"]
    if email:
        # So HQ can just hit "Reply" in their inbox to answer the
        # inquirer directly, instead of copying their address by hand.
        msg["Reply-To"] = email
    msg.set_content("\n".join(body_lines))

    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg.get("MAIL_PORT", 587), timeout=10) as smtp:
            if cfg.get("MAIL_USE_TLS", True):
                smtp.starttls()
            if cfg.get("MAIL_USERNAME") and cfg.get("MAIL_PASSWORD"):
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
        return True
    except Exception:
        current_app.logger.exception(
            "Failed to send partner inquiry email for %s", company_name)
        return False
