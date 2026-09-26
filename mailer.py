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
from urllib.parse import quote

from flask import current_app


def _smtp_configured(cfg):
    return bool(cfg.get("MAIL_SERVER") and cfg.get(
        "MAIL_DEFAULT_SENDER") and cfg.get("PARTNER_INQUIRY_NOTIFY_EMAIL"))


def _esc(value):
    """HTML-escape a value that may be None. Blank stays blank; each
    spot in the template decides how to show a missing field."""
    if not value:
        return ""
    return html_lib.escape(str(value))


# Brand palette for the inline styles below. Same black + gold as the
# admin UI and the PDF receipts/reports, so mail, receipts, and reports
# read as one system. Constants because email has no stylesheet to put
# them in: every element has to carry its own inline style.
_INK = "#17140D"         # body text
_MUTED = "#6B6453"       # labels, secondary text (passes AA on white)
_FAINT = "#9A927E"       # footer / "Not provided" only
_LINE = "#ECE5D3"        # hairlines
_CANVAS = "#F4F1EA"      # page behind the card
_TINT = "#FAF8F2"        # message + footer panels
_NIGHT = "#1C170D"       # header band / primary button
_GOLD = "#D4AF37"        # brand accent (on dark only)
_GOLD_DEEP = "#8A6D1F"   # gold text on white (passes AA)
_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
         "Helvetica,Arial,sans-serif")


def _dial_digits(phone):
    """tel:/sms: links want digits plus an optional leading +, nothing else."""
    phone = str(phone or "").strip()
    digits = "".join(ch for ch in phone if ch.isdigit())
    return ("+" + digits) if phone.startswith("+") and digits else digits


def _button(href, label, *, primary):
    """A "bulletproof" button: the link sits inside a padded, colored
    table cell, so Outlook desktop (which ignores padding on <a>) still
    draws a proper tappable block instead of a bare text link."""
    bg, fg, border = (_NIGHT, "#FFFFFF", _NIGHT) if primary else ("#FFFFFF", _INK, "#D6CDB6")
    kind = "ha-btn-primary" if primary else "ha-btn"
    return (
        f'<td class="{kind}" style="border-radius:8px;background:{bg};border:1px solid {border};">'
        f'<a class="{kind}-link" href="{html_lib.escape(href)}" target="_blank" '
        f'style="display:inline-block;'
        f'padding:12px 20px;font-family:{_FONT};font-size:14px;font-weight:600;'
        f'line-height:1;color:{fg};text-decoration:none;border-radius:8px;">{label}</a></td>'
    )


def _contact_actions(*, email, phone, preferred_contact, reference):
    """Up to two one-tap reply buttons. The inquirer's preferred channel
    (when they picked one) gets the primary button, so HQ answers the way
    they asked to be reached; the other main channel sits beside it."""
    subject = quote(f"Your Heaven & Angel Scents inquiry ({reference})" if reference
                    else "Your Heaven & Angel Scents inquiry")
    dial = _dial_digits(phone)
    options = {
        "Email": (f"mailto:{email}?subject={subject}", "Reply by email") if email else None,
        "Call": (f"tel:{dial}", "Call now") if dial else None,
        "SMS": (f"sms:{dial}", "Send SMS") if dial else None,
        # Viber has no deep link that works reliably from every mail
        # client for local-format numbers, so a Viber preference just
        # offers the number itself (tapping it lets the phone pick Viber).
        "Viber": (f"tel:{dial}", "Message on Viber") if dial else None,
    }
    picked = []
    for key in (preferred_contact, "Email", "Call"):
        opt = options.get(key)
        if opt and opt[0] not in {href for href, _ in picked}:
            picked.append(opt)
    if not picked:
        return ""
    gap = '<td style="width:10px;font-size:0;line-height:0;">&nbsp;</td>'
    cells = gap.join(_button(href, label, primary=(i == 0))
                     for i, (href, label) in enumerate(picked[:2]))
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
            f'<tr>{cells}</tr></table>')


def _detail_cell(label, value_html, *, span=1, pad_left=False):
    """One label-above-value block in the contact grid. A blank optional
    field reads "Not provided" in a lighter tone rather than a dash, so a
    missing value looks deliberately missing, not like a rendering glitch."""
    shown = value_html or f'<span style="color:{_FAINT};">Not provided</span>'
    width = '' if span > 1 else ' width="50%"'
    return (
        f'<td class="ha-col" valign="top" colspan="{span}"{width} '
        f'style="padding:0 0 18px {"14px" if pad_left else "0"};">'
        f'<div class="ha-muted" style="font-size:12px;font-weight:600;color:{_MUTED};'
        f'margin:0 0 4px;">{label}</div>'
        f'<div class="ha-ink" style="font-size:15px;line-height:1.45;color:{_INK};'
        f'word-break:break-word;">{shown}</div></td>'
    )


def _build_html_body(*, package_name, partner_type, company_name, contact_person,
                     phone, email, address, message, preferred_contact=None,
                     reference=None, order_amount=None):
    """A self-contained HTML email: table layout and inline styles only,
    no external stylesheet or images (many clients strip or block both),
    so it renders consistently in Gmail, Outlook, and Apple Mail. The one
    <style> block only layers on extras (stacking on phones, dark mode)
    for clients that honour it; without it everything still reads fine.
    Carries the same facts as the plain-text version, so nothing is
    HTML-only.
    """
    is_general = not package_name or package_name == "General inquiry"
    if is_general:
        subline = "General inquiry, not tied to a specific package."
    else:
        subline = (f'Interested in <strong class="ha-ink" style="color:{_INK};'
                   f'font-weight:600;">{_esc(package_name)}</strong>')
    amount_html = (
        f'<div class="ha-muted" style="margin-top:6px;font-size:15px;color:{_MUTED};">Package value '
        f'<strong class="ha-ink" style="color:{_INK};font-weight:600;">'
        f'&#8369;{order_amount:,.2f}</strong></div>'
    ) if order_amount is not None else ""

    email_html = (f'<a href="mailto:{_esc(email)}" class="ha-accent" style="color:{_GOLD_DEEP};'
                  f'text-decoration:none;">{_esc(email)}</a>') if email else ""
    phone_html = (f'<a href="tel:{_esc(_dial_digits(phone))}" class="ha-ink" '
                  f'style="color:{_INK};text-decoration:none;">{_esc(phone)}</a>') if phone else ""
    grid_html = (
        f'<tr>{_detail_cell("Contact person", _esc(contact_person))}'
        f'{_detail_cell("Preferred contact", _esc(preferred_contact), pad_left=True)}</tr>'
        f'<tr>{_detail_cell("Phone", phone_html)}'
        f'{_detail_cell("Email", email_html, pad_left=True)}</tr>'
        f'<tr>{_detail_cell("Address", _esc(address), span=2)}</tr>'
    )

    message_html = (
        f'<tr><td class="ha-pad" style="padding:4px 32px 30px;">'
        f'<div class="ha-muted" style="font-size:12px;font-weight:600;color:{_MUTED};'
        f'margin:0 0 8px;">Message</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td class="ha-quote" style="border-left:3px solid {_GOLD};background:{_TINT};'
        f'padding:14px 16px;font-size:15px;line-height:1.6;color:{_INK};">'
        f'{_esc(message).replace(chr(10), "<br>")}</td></tr></table></td></tr>'
    ) if message else ""

    actions = _contact_actions(email=email, phone=phone,
                               preferred_contact=preferred_contact, reference=reference)
    actions_html = (f'<tr><td class="ha-pad" style="padding:0 32px 28px;">{actions}</td></tr>'
                    if actions else "")

    reference_html = (
        f'<td align="right" valign="middle" style="font-size:12px;color:#BDB39A;'
        f'white-space:nowrap;">Ref&nbsp;<span style="color:{_GOLD};font-weight:600;">'
        f'{_esc(reference)}</span></td>'
    ) if reference else ""

    # Hidden preview text: what Gmail/Outlook show next to the subject in
    # the inbox list, instead of the first visible words of the email.
    preheader = f"{_esc(contact_person)} from {_esc(company_name)}"
    preheader += "" if is_general else f" is asking about {_esc(package_name)}"
    if preferred_contact:
        preheader += f". Prefers {_esc(preferred_contact)}"

    reply_line = (f"Replying to this email goes straight to {_esc(email)}."
                  if email else "No email address was given, so reach them by phone.")
    saved_line = (f"It is also saved on the Partner Inquiries page as {_esc(reference)}."
                  if reference else "It is also saved on the Partner Inquiries page.")

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>New partner inquiry</title>
<style>
  @media (max-width:600px) {{
    .ha-pad {{ padding-left:22px !important; padding-right:22px !important; }}
    .ha-col {{ display:block !important; width:100% !important; padding-left:0 !important; }}
    .ha-title {{ font-size:23px !important; }}
  }}
  @media (prefers-color-scheme: dark) {{
    body, .ha-canvas {{ background:#0F0D09 !important; }}
    .ha-card {{ background:#1A1711 !important; border-color:#2E2A20 !important; }}
    .ha-ink, .ha-title, .ha-quote {{ color:#F2EEE4 !important; }}
    .ha-quote, .ha-foot {{ background:#211E16 !important; border-color:#2E2A20 !important; }}
    .ha-rule {{ border-color:#2E2A20 !important; }}
    .ha-muted {{ color:#B3AB97 !important; }}
    .ha-accent {{ color:{_GOLD} !important; }}
    .ha-btn-primary {{ background:{_GOLD} !important; border-color:{_GOLD} !important; }}
    .ha-btn-primary-link {{ color:{_NIGHT} !important; }}
    .ha-btn {{ background:transparent !important; border-color:#4A4434 !important; }}
    .ha-btn-link {{ color:#F2EEE4 !important; }}
  }}
</style>
</head>
<body style="margin:0;padding:0;background:{_CANVAS};font-family:{_FONT};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">
    {preheader}.
  </div>
  <table role="presentation" class="ha-canvas" width="100%" cellpadding="0" cellspacing="0"
    border="0" style="background:{_CANVAS};">
    <tr><td align="center" style="padding:32px 12px;">
      <table role="presentation" class="ha-card" width="100%" cellpadding="0" cellspacing="0"
        border="0" style="max-width:600px;background:#FFFFFF;border:1px solid {_LINE};
        border-radius:14px;overflow:hidden;font-family:{_FONT};">

        <!-- Header band: wordmark + reference. Text rather than an <img>
             logo: many clients block remote images until the reader
             clicks "show images", while text renders immediately. The
             gold rule below is a flat color, not a gradient, because
             Outlook desktop drops CSS gradients. -->
        <tr><td class="ha-pad" style="background:{_NIGHT};padding:20px 32px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
            <td valign="middle" style="font-size:16px;font-weight:700;letter-spacing:-0.01em;
              color:#FFFFFF;">
              <span style="color:{_GOLD};">Heaven</span> &amp; Angel Scents
              <div style="font-size:12px;font-weight:400;color:#BDB39A;margin-top:3px;
                letter-spacing:0;">Partner Program</div>
            </td>
            {reference_html}
          </tr></table>
        </td></tr>
        <tr><td style="height:3px;line-height:3px;font-size:0;background:{_GOLD};">&nbsp;</td></tr>

        <tr><td class="ha-pad" style="padding:30px 32px 24px;">
          <div class="ha-accent" style="font-size:13px;font-weight:600;color:{_GOLD_DEEP};
            margin:0 0 8px;">
            New {_esc(partner_type).lower()} inquiry
          </div>
          <div class="ha-title" style="font-size:26px;line-height:1.2;font-weight:700;
            letter-spacing:-0.02em;color:{_INK};">{_esc(company_name)}</div>
          <div class="ha-muted" style="margin-top:8px;font-size:15px;line-height:1.5;
            color:{_MUTED};">
            {subline}
          </div>
          {amount_html}
        </td></tr>

        {actions_html}

        <tr><td class="ha-pad" style="padding:0 32px;">
          <div class="ha-rule" style="border-top:1px solid {_LINE};height:0;line-height:0;
            font-size:0;">&nbsp;</div>
        </td></tr>

        <!-- Contact details: two-column grid that stacks on phones -->
        <tr><td class="ha-pad" style="padding:24px 32px 8px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            {grid_html}
          </table>
        </td></tr>

        {message_html}

        <tr><td class="ha-pad ha-foot" style="padding:18px 32px 20px;background:{_TINT};
          border-top:1px solid {_LINE};">
          <div class="ha-muted" style="font-size:13px;line-height:1.6;color:{_MUTED};">
            {reply_line} {saved_line}
          </div>
        </td></tr>
      </table>
      <div style="font-size:12px;line-height:1.6;color:{_FAINT};margin-top:18px;
        font-family:{_FONT};">
        Sent automatically by the Heaven &amp; Angel Scents partner portal.
      </div>
    </td></tr>
  </table>
</body>
</html>"""


def send_partner_inquiry_email(
    *, package_name, partner_type, company_name, contact_person,
    phone, email, address, message, preferred_contact=None,
    reference=None, order_amount=None,
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

    not_given = "Not provided"
    body_lines = [
        f"New {partner_type.lower()} inquiry" + (f" ({reference})" if reference else ""),
        "",
        company_name,
        f"Package: {package_name}",
    ]
    if order_amount is not None:
        body_lines.append(f"Package value: PHP {order_amount:,.2f}")
    body_lines += [
        "",
        f"Contact person: {contact_person or not_given}",
        f"Preferred contact: {preferred_contact or not_given}",
        f"Phone: {phone or not_given}",
        f"Email: {email or not_given}",
        f"Address: {address or not_given}",
        "",
        "Message:",
        message or not_given,
        "",
        "Sent automatically by the Heaven & Angel Scents partner portal.",
    ]

    # Building the message (not just sending it) is inside this same
    # try/except: company_name/package_name land straight in the Subject
    # header below, and company_name is free text straight from the
    # public, unauthenticated inquiry form — parse_required_text() only
    # strips leading/trailing whitespace, so an embedded \r or \n in the
    # middle of it (trivially sent by anyone POSTing the form directly,
    # bypassing whatever a browser's <input> would normally allow) makes
    # Python's own email policy raise ValueError("Header values may not
    # contain linefeed or carriage return characters") the moment it's
    # assigned to msg["Subject"] — before smtplib is ever touched. That
    # used to happen outside this try/except entirely, breaking this
    # function's "never raises" contract for a plain bad-input case, not
    # just a real SMTP failure.
    try:
        msg = EmailMessage()
        msg["Subject"] = f"New {partner_type.lower()} inquiry: {company_name} ({package_name})"
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
                message=message, preferred_contact=preferred_contact,
                reference=reference, order_amount=order_amount,
            ),
            subtype="html",
        )

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
