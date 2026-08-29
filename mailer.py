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
import html as html_lib
import smtplib
from email.message import EmailMessage

from flask import current_app


def _smtp_configured(cfg):
    return bool(cfg.get("MAIL_SERVER") and cfg.get(
        "MAIL_DEFAULT_SENDER") and cfg.get("PARTNER_INQUIRY_NOTIFY_EMAIL"))


def _esc(value):
    """HTML-escape a value that may be None; render a plain em-dash for
    anything blank, matching how the admin templates show missing
    optional fields elsewhere in the app."""
    if not value:
        return "—"
    return html_lib.escape(str(value))


def _build_html_body(*, package_name, partner_type, company_name, contact_person,
                      phone, email, address, message):
    """A small, self-contained HTML email — no external stylesheet or
    images (some mail clients strip both), just inline styles so it
    renders consistently in Gmail, Outlook, and everything between.
    Kept intentionally simple: one info table plus the message, mirroring
    the plain-text version 1:1 so nothing is HTML-only.
    """
    rows = [
        ("Company", _esc(company_name)),
        ("Type", _esc(partner_type)),
        ("Contact person", _esc(contact_person)),
        ("Phone", _esc(phone)),
        ("Email", _esc(email)),
        ("Address", _esc(address)),
    ]
    row_html = "".join(
        f'<tr>'
        f'<td style="padding:9px 14px;border-bottom:1px solid #eee;'
        f'font-size:12px;font-weight:600;color:#8a8580;white-space:nowrap;'
        f'text-transform:uppercase;letter-spacing:.03em;">{label}</td>'
        f'<td style="padding:9px 14px;border-bottom:1px solid #eee;'
        f'font-size:14px;color:#1c1b19;">{value}</td>'
        f'</tr>'
        for label, value in rows
    )
    message_html = _esc(message).replace("\n", "<br>") if message else "—"

    return f"""\
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f4f2ef;font-family:-apple-system,
  BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    style="background:#f4f2ef;padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
        style="max-width:520px;background:#ffffff;border-radius:12px;
        overflow:hidden;border:1px solid #e7e3dd;">
        <tr><td style="padding:24px 24px 4px;">
          <div style="font-size:17px;font-weight:800;letter-spacing:-0.01em;">
            <span style="color:#3b6ef2;">Heaven</span>
            <span style="color:#1c1b19;">&amp;</span>
            <span style="color:#e2483d;">Angel</span>
            <span style="color:#1c1b19;"> Scents</span>
          </div>
          <div style="font-size:11.5px;color:#8a8580;margin-top:2px;">
            Partner Program &middot; New package inquiry
          </div>
        </td></tr>
        <tr><td style="padding:18px 24px 6px;">
          <span style="display:inline-block;font-size:11px;font-weight:700;
            color:#3b6ef2;background:#eaf0fe;padding:4px 10px;border-radius:999px;">
            {_esc(partner_type)} inquiry
          </span>
          <div style="font-size:18px;font-weight:700;margin-top:10px;color:#1c1b19;">
            {_esc(company_name)}
          </div>
          <div style="font-size:13px;color:#8a8580;margin-top:2px;">
            Interested in &ldquo;{_esc(package_name)}&rdquo;
          </div>
        </td></tr>
        <tr><td style="padding:12px 12px 0;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
            style="border-collapse:collapse;">
            {row_html}
          </table>
        </td></tr>
        <tr><td style="padding:16px 24px 4px;">
          <div style="font-size:11px;font-weight:700;color:#8a8580;
            text-transform:uppercase;letter-spacing:.03em;margin-bottom:6px;">
            Message
          </div>
          <div style="font-size:13.5px;color:#3a3733;line-height:1.6;
            background:#f9f7f4;border:1px solid #eee;border-radius:8px;
            padding:12px 14px;">
            {message_html}
          </div>
        </td></tr>
        <tr><td style="padding:22px 24px 26px;">
          <div style="font-size:11.5px;color:#a29c94;line-height:1.6;">
            Reply directly to this email to respond{f' to {_esc(email)}' if email else ''}.
            Full history is also saved on the Partner Inquiries page.
          </div>
        </td></tr>
      </table>
      <div style="font-size:11px;color:#a29c94;margin-top:16px;">
        Sent automatically from the Heaven &amp; Angel Scents partner portal.
      </div>
    </td></tr>
  </table>
</body>
</html>"""


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
    # Plain text stays the primary body (some clients / spam filters
    # prefer it, and it's what shows if HTML rendering is off); the HTML
    # version is attached as an alternative that most inboxes — Gmail
    # included — will prefer to display when available.
    msg.set_content("\n".join(body_lines))
    msg.add_alternative(
        _build_html_body(
            package_name=package_name, partner_type=partner_type, company_name=company_name,
            contact_person=contact_person, phone=phone, email=email, address=address,
            message=message,
        ),
        subtype="html",
    )

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
