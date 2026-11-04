import threading
import time
from heaven_angel.routes.portal.inquiries import inquiry_reference


from heaven_angel.db import execute, query
from heaven_angel.services.mailer import send_partner_inquiry_email



_EMAIL_SWEEP_EVERY_SECONDS = 600


_EMAIL_SWEEP_MIN_AGE_SECONDS = 180      # the first attempt gets a few minutes to finish


_EMAIL_SWEEP_MAX_AGE_HOURS = 24         # stop chasing something that has failed all day


_EMAIL_SWEEP_BATCH = 20


def resend_unsent_inquiry_emails(app):
    """Try again for inquiries whose HQ notification never went out (the
    app restarted mid-send, or the mail server was down for longer than
    mailer.py's quick retries cover). The inquiry itself is always safe in
    partner_inquiries; this only closes the loop on the email, using the
    email_sent flag the first attempt already maintains. A MySQL advisory
    lock keeps two workers from sending the same one twice. Returns how
    many were sent."""
    with app.app_context():
        cfg = app.config
        if not (cfg.get("MAIL_SERVER") and cfg.get("MAIL_DEFAULT_SENDER")
                and cfg.get("PARTNER_INQUIRY_NOTIFY_EMAIL")):
            return 0
        got = query("SELECT GET_LOCK('ha_inquiry_mail_retry', 0) AS ok", fetchone=True)
        if not got or not got["ok"]:
            return 0
        sent = 0
        try:
            rows = query(
                """SELECT * FROM partner_inquiries
                   WHERE email_sent = FALSE
                     AND created_at <= (CURRENT_TIMESTAMP - INTERVAL %s SECOND)
                     AND created_at >= (CURRENT_TIMESTAMP - INTERVAL %s HOUR)
                   ORDER BY inquiry_id LIMIT %s""",
                (_EMAIL_SWEEP_MIN_AGE_SECONDS, _EMAIL_SWEEP_MAX_AGE_HOURS, _EMAIL_SWEEP_BATCH),
            )
            for row in rows:
                if send_partner_inquiry_email(
                    package_name=row["package_name_snapshot"],
                    partner_type=row["partner_type"],
                    company_name=row["company_name"],
                    contact_person=row["contact_person"],
                    phone=row["phone"],
                    email=row["email"],
                    address=row["address"],
                    message=row["message"],
                    preferred_contact=row["preferred_contact"],
                    reference=inquiry_reference(row["inquiry_id"]),
                    order_amount=row["order_amount"],
                ):
                    execute("UPDATE partner_inquiries SET email_sent = TRUE WHERE inquiry_id = %s",
                            (row["inquiry_id"],))
                    sent += 1
        finally:
            query("SELECT RELEASE_LOCK('ha_inquiry_mail_retry')", fetchone=True)
        return sent


def start_inquiry_email_retry(app):
    """Background thread that runs resend_unsent_inquiry_emails() every
    few minutes (one per process, same style as backup_jobs.start_scheduler)."""
    def loop():
        time.sleep(90)                      # let the app finish starting
        while True:
            try:
                n = resend_unsent_inquiry_emails(app)
                if n:
                    app.logger.info("Resent %s unsent partner-inquiry notification(s)", n)
            except Exception:
                app.logger.exception("Partner-inquiry email retry sweep failed")
            time.sleep(_EMAIL_SWEEP_EVERY_SECONDS)

    threading.Thread(target=loop, name="inquiry-email-retry", daemon=True).start()
