"""script-src must not allow 'unsafe-inline': every inline <script> carries
the response's nonce and no template uses on*= event-handler attributes
(those are blocked by the CSP and would silently stop working).
"""
import re

import pytest

from factories import login, make_branch, make_user

_INLINE_SCRIPT = re.compile(r"<script(?P<attrs>[^>]*)>", re.IGNORECASE)
_HANDLER_ATTR = re.compile(r"""\son(?:click|submit|change|input|load|error|focus|blur|key\w+)\s*=""", re.IGNORECASE)
_SKIP_ENDPOINT_PARTS = ("export", "generate", "import_template", "receipt", "reports_data", "api_")


def _script_src_directive(resp):
    csp = resp.headers.get("Content-Security-Policy", "")
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.startswith("script-src"):
            return directive
    return ""


def _assert_page_is_nonce_clean(resp, label):
    directive = _script_src_directive(resp)
    assert directive, f"{label}: no script-src directive"
    assert "'unsafe-inline'" not in directive, f"{label}: script-src still allows 'unsafe-inline'"
    nonce = re.search(r"'nonce-([^']+)'", directive)
    assert nonce, f"{label}: script-src has no nonce"
    body = resp.get_data(as_text=True)

    for match in _INLINE_SCRIPT.finditer(body):
        attrs = match.group("attrs")
        if "src=" in attrs or "application/json" in attrs or "application/ld+json" in attrs:
            continue
        assert f'nonce="{nonce.group(1)}"' in attrs, f"{label}: inline <script{attrs}> has no valid nonce"
    assert not _HANDLER_ATTR.search(body), f"{label}: inline on*= handler found"


def _static_get_endpoints(app, prefixes):
    rules = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.arguments:
            continue
        if not rule.endpoint.startswith(prefixes):
            continue
        if any(part in rule.endpoint for part in _SKIP_ENDPOINT_PARTS):
            continue
        rules.append(rule.rule)
    return sorted(rules)


def test_login_page_scripts_all_carry_the_nonce(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    _assert_page_is_nonce_clean(resp, "/login")


@pytest.mark.parametrize("role,prefixes", [("Admin", ("admin.", "ai.", "scan.")), ("Branch", ("branch.", "ai.", "scan."))])
def test_every_signed_in_page_is_nonce_clean(app, client, sql, role, prefixes):
    branch_id = make_branch(sql) if role == "Branch" else None
    user = make_user(sql, role=role, branch_id=branch_id)
    login(client, user["username"], user["password"], role)

    checked = 0
    for path in _static_get_endpoints(app, prefixes):
        resp = client.get(path)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("Content-Type", ""):
            continue
        _assert_page_is_nonce_clean(resp, f"{role} {path}")
        checked += 1
    assert checked >= 10, f"only {checked} pages were checked for {role}"
