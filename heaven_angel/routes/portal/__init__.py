"""Public partner portal — no login required.

Distributors and resellers reach this from a private link HQ shares
with them directly — see PARTNER_PORTAL_SLUG in config.py. It is
deliberately NOT linked anywhere in the signed-in app or on the login
screen: the slug in the URL is the only thing standing between "public
internet" and "sees our packages", so the link itself has to stay out
of anything a stranger could stumble onto. An admin can always copy
the current link from the Partners page (see routes/admin/partners.py's
partners()).

The pages themselves are a React app (see public-site/ at the repo
root). This blueprint only does two things for it:

  * Serves the built app's index.html at the page URLs below (after
    the slug check), so the private link HQ hands out never changed:
      /packages             — browse active packages.
      /packages/<id>        — one package's full detail + inquiry form.
      /products             — the full product catalog.
    React Router takes over from there in the browser.
  * Exposes the JSON API that app reads and writes, under /api/:
      GET  /api/packages              — the package list (?scope= filter).
      GET  /api/packages/<id>         — one package, its products, a few
                                        other packages, and a CSRF token.
      GET  /api/products              — the catalog (?gender=, ?page=).
      GET  /api/site                  — portal-wide facts: contact details,
                                        catalog/package counts, and a CSRF
                                        token for the general inquiry form.
      POST /api/inquire               — a general inquiry, not tied to any
                                        one package (same steps as below,
                                        minus the order amount).
      POST /api/packages/<id>/inquire — submitting the inquiry form:
       a. Saves the inquiry permanently to partner_inquiries (see
          schema.sql) — the history admins review on the Partner
          Inquiries page.
       b. Matches an existing partners row by email/phone, or creates a
          new one, and links it to the inquiry — see
          _match_or_create_partner().
       c. Emails HQ a notification, off the request thread (best-effort —
          see mailer.py and _send_inquiry_notification_async() below). A
          failed or unconfigured mailer never loses the inquiry itself,
          since the save in step (a) already happened, and a slow SMTP
          server never holds up the visitor's response either.
       d. Pushes a realtime event so every open HQ Admin tab's "Partner
          Inquiries" sidebar badge updates immediately, and a bell
          notification for the new lead — see sockets.py.

Nothing here requires @login_required/@admin_required — nothing under
this blueprint should, since the whole point is that a prospect
doesn't have (and shouldn't need) an account to reach it. The slug
check below is what stands in for authentication instead. That also
means this blueprint is reachable by anyone who has (or guesses) the
slug, with no login to throttle via the usual account-lockout path —
see the rate limit on inquire() below.
"""

from heaven_angel.routes.portal._common import bp

from heaven_angel.routes.portal import (  # noqa: F401  (imported for route registration)
    catalog,
    email_retry,
    inquiries,
    pages,
)
from heaven_angel.routes.portal.email_retry import start_inquiry_email_retry

__all__ = ["bp", "start_inquiry_email_retry"]
