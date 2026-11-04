// Delegated actions, confirm dialogs/modals, overlays, submit loading state
// (Part of the app's main script; loaded in order by base.html.)

// Replacements for inline on*= attributes, which the CSP no longer allows.
// Delegated on `document`, so they survive soft-nav content swaps:
//   data-call="fnName"            click -> window.fnName(element)
//   data-autosubmit (on <select>)  change -> submit its form
//   data-reveal-target="id" data-reveal-value="v"
//                                  change -> show #id only when value === v
function initDelegatedActions() {
    document.addEventListener('click', (e) => {
        const el = e.target.closest ? e.target.closest('[data-call]') : null;
        if (!el) return;
        const fn = window[el.dataset.call];
        if (typeof fn === 'function') fn(el);
    });
    document.addEventListener('change', (e) => {
        const el = e.target;
        if (!(el instanceof HTMLElement)) return;
        if (el.matches('select[data-autosubmit]') && el.form) el.form.submit();
        if (el.dataset.revealTarget) {
            const target = document.getElementById(el.dataset.revealTarget);
            if (target) target.style.display = el.value === el.dataset.revealValue ? 'block' : 'none';
        }
    });
}

// Unified confirm-dialog pattern for every "are you sure?" form in the
// app: give the <form> a data-confirm="Message" attribute and this one
// delegated (capture-phase) listener handles the rest — no per-page
// onsubmit="return confirm(...)" or bespoke button-click JS needed. See
// requests.html, request_detail.html, users.html, and package_detail.html
// for the template side of this (package_detail.html's message still
// varies per-row, built in Jinja, but lands in the same attribute).
//
// A toggle form (.../toggle) that doesn't set data-confirm explicitly
// still gets a sensible default built from its submit button's label
// ("Deactivate?" / "Reactivate?"), preserving the old behavior for any
// toggle form that hasn't been given an explicit message.
//
// Delegated on `document` (rather than looping over forms and attaching
// one listener each) so this keeps working after main.js's own
// softRefresh() swaps in fresh .content HTML from the server — no
// re-init call needed the way the old per-form version required.
function initConfirmDialogs() {
    initDelegatedActions();
    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement)) return;

        let message = form.dataset.confirm;
        if (!message && form.matches('form[action*="/toggle"]')) {
            const btn = form.querySelector('button[type="submit"]');
            const label = btn ? btn.textContent.trim() : 'do this';
            message = `${label}?`;
        }

        if (message && !confirm(message)) {
            e.preventDefault();
        }
    }, true);
}

// Styled, floating variant of the confirm pattern above, for forms that
// deserve more than the browser's confirm() popup (currently Sign out):
// data-confirm-dialog="Title", optional data-confirm-detail="Body" and
// data-confirm-ok="Button label". Built lazily and appended to <body> so
// it survives soft-nav content swaps, like the loading overlay below.
function initConfirmModal() {
    let overlay = null;
    let pendingForm = null;
    let returnFocus = null;

    function build() {
        overlay = document.createElement('div');
        overlay.className = 'confirm-overlay';
        overlay.hidden = true;
        overlay.innerHTML =
            '<div class="confirm-dialog" role="alertdialog" aria-modal="true" ' +
            'aria-labelledby="confirmDialogTitle" aria-describedby="confirmDialogDetail">' +
            '<h2 class="confirm-dialog-title" id="confirmDialogTitle"></h2>' +
            '<p class="confirm-dialog-detail" id="confirmDialogDetail"></p>' +
            '<div class="confirm-dialog-actions">' +
            '<button type="button" class="btn btn-ghost" data-confirm-cancel>Cancel</button>' +
            '<button type="button" class="btn btn-danger" data-confirm-accept></button>' +
            '</div></div>';
        document.body.appendChild(overlay);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay || e.target.closest('[data-confirm-cancel]')) close();
            else if (e.target.closest('[data-confirm-accept]')) accept();
        });
    }

    function close() {
        if (!overlay || overlay.hidden) return;
        overlay.hidden = true;
        pendingForm = null;
        if (returnFocus && returnFocus.focus) returnFocus.focus();
    }

    function accept() {
        const form = pendingForm;
        overlay.hidden = true;
        pendingForm = null;
        // submit() skips the submit event, so this doesn't re-open the dialog.
        if (form) form.submit();
    }

    document.addEventListener('keydown', (e) => {
        if (!overlay || overlay.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const btns = overlay.querySelectorAll('button');
            const first = btns[0];
            const last = btns[btns.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });

    document.addEventListener('submit', (e) => {
        const form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.dataset.confirmDialog) return;
        e.preventDefault();
        e.stopImmediatePropagation();
        if (!overlay) build();
        pendingForm = form;
        returnFocus = document.activeElement;
        overlay.querySelector('.confirm-dialog-title').textContent = form.dataset.confirmDialog;
        const detail = overlay.querySelector('.confirm-dialog-detail');
        detail.textContent = form.dataset.confirmDetail || '';
        detail.hidden = !detail.textContent;
        overlay.querySelector('[data-confirm-accept]').textContent = form.dataset.confirmOk || 'Confirm';
        overlay.hidden = false;
        overlay.querySelector('[data-confirm-cancel]').focus();
    }, true);
}

// A full-screen, centered "Loading…" dialog (see .loading-overlay in
// style.css) shown for the duration of a form submission. Built once,
// lazily, and appended straight to <body> — not .content — so it
// works identically on every page that loads this file (including
// standalone ones like login.html that don't extend base.html) and,
// more importantly, survives a soft-nav content swap: swapContent()
// (see initSoftNav() above) only ever replaces .content, so an
// overlay living outside it isn't torn down and rebuilt by that swap
// the way anything inside .content would be.
let loadingOverlayEl = null;

function getLoadingOverlay() {
    if (loadingOverlayEl) return loadingOverlayEl;
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<div class="loading-dialog">' +
        '<div class="loading-dialog-spinner"></div>' +
        '<div class="loading-dialog-text">Loading…</div>' +
        '</div>';
    document.body.appendChild(el);
    loadingOverlayEl = el;
    return el;
}

// Local/fast requests can resolve in well under a frame, which made
// the dialog flash on and immediately back off — too quick to
// register as having happened at all. Enforcing a small minimum
// visible time fixes that without slowing down anything: a request
// that's genuinely slower than this just isn't affected (hide already
// runs past the minimum by the time it's called), only a fast one
// gets held open a beat longer than strictly necessary. Also long
// enough to comfortably clear the CSS entrance animation's own 0.25s
// duration (see .loading-dialog-in in style.css) before a hide() can
// ever be applied.
const LOADING_OVERLAY_MIN_VISIBLE_MS = 400;
// Must match .loading-overlay-exit's own animation-duration in
// style.css — this is how long hide() waits, after starting that
// class's exit animation, before actually setting hidden = true.
const LOADING_OVERLAY_EXIT_MS = 180;
let loadingOverlayShownAt = 0;
// Bumped on every show, checked before a delayed hide actually applies
// — the overlay visually blocks clicks to everything under it, so two
// overlapping submissions can't happen from a mouse click, but a
// keyboard-triggered back/forward navigation still could while an
// earlier hide is mid-delay. Without this, that stale timeout would
// close the dialog a newer request just reopened.
let loadingOverlayToken = 0;

// Plain CSS animations (see .loading-overlay/.loading-overlay-exit in
// style.css) rather than Motion (window.Motion — still used above for
// the sidebar theme-toggle bounce and revealContent(), where nothing
// ever cuts the animation short) — this dialog's own lifecycle can
// end at any moment via a real page navigation (a plain, non-soft-nav
// form submit unloads the page almost immediately), and Motion's
// animate() rejects its controls with an AbortError when that
// happens, unreliably enough that even always attaching a rejection
// handler didn't keep it from surfacing as an unhandled rejection.
// Toggling a class and letting CSS run the animation sidesteps that
// category of failure entirely — there's no promise to reject.
function showLoadingOverlay() {
    loadingOverlayShownAt = Date.now();
    loadingOverlayToken += 1;
    const el = getLoadingOverlay();
    el.classList.remove('loading-overlay-exit');
    el.hidden = false;
}

function hideLoadingOverlay() {
    if (!loadingOverlayEl || loadingOverlayEl.hidden) return;
    const myToken = loadingOverlayToken;

    const doHide = () => {
        // Superseded by a newer show() while this was mid-delay —
        // leave it alone, that newer call owns the overlay's state now.
        if (myToken !== loadingOverlayToken) return;
        loadingOverlayEl.classList.add('loading-overlay-exit');
        setTimeout(() => {
            if (myToken === loadingOverlayToken) {
                loadingOverlayEl.hidden = true;
                loadingOverlayEl.classList.remove('loading-overlay-exit');
            }
        }, LOADING_OVERLAY_EXIT_MS);
    };

    // LOADING_OVERLAY_MIN_VISIBLE_MS > LOADING_OVERLAY_ENTER_MS by
    // construction, so doHide() never fires before the CSS entrance
    // animation (see style.css) has already finished on its own.
    const remaining = LOADING_OVERLAY_MIN_VISIBLE_MS - (Date.now() - loadingOverlayShownAt);
    if (remaining > 0) {
        setTimeout(doHide, remaining);
    } else {
        doHide();
    }
}

// ---------------------------------------------------------------
// Modal overlays (Add material, Edit product, Log usage, Remarks,
// etc.) — every one across the admin app shares the same shape: a
// position:fixed backdrop toggled via inline style.display, wrapping
// one .card panel, dismissed by a Cancel button, clicking the
// backdrop, or Escape. These two functions are the one place that
// actually shows/hides one, so every page gets the same animated
// open/close instead of each hand-rolling its own — see
// wireOverlay() below for the common "one open button, one cancel
// button" case, and openOverlay()/closeOverlay() directly for pages
// whose open trigger needs to run its own logic first (populating an
// edit form from a clicked row, say).
//
// anime.js (window.anime — see static/js/anime.js) rather than
// Motion here, same as the loading dialog's original version — but
// unlike that dialog, an overlay is only ever opened/closed by a
// stable, in-page interaction (a button click), never by something
// that might unload the page mid-animation the way a form submission
// could, so animate()'s own promise isn't a reliability concern here.
// It's still never awaited below regardless — the close delay is a
// plain setTimeout matching the exit duration, exactly like the
// loading dialog, so there's nothing to go wrong even if that changes
// later.
// Attached to window (not just a local const) so a page needing to
// sequence its own follow-up work after a close animation finishes —
// see users.html's tempPasswordOverlay, which removes itself from the
// DOM entirely once closed, not just hides — can time it against the
// same duration closeOverlay() itself uses, rather than a second
// independently-maintained number that could drift out of sync.
window.OVERLAY_EXIT_MS = 140;
const OVERLAY_EXIT_MS = window.OVERLAY_EXIT_MS;

function openOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay) return;
    overlay.style.display = 'flex';
    if (!window.anime || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const panel = overlay.querySelector('.card, .pi-modal');
    // anime.js's animate(targets, params) takes ONE params object with
    // the property keyframes and settings (duration, ease, …) merged
    // together — unlike Motion's animate(target, keyframes, options)
    // three-argument shape used elsewhere in this file. A duration/
    // ease passed as a separate third argument here is silently
    // ignored (falls back to anime's own 1000ms default) rather than
    // erroring, which is what actually happened here originally.
    window.anime.animate(overlay, { opacity: [0, 1], duration: 160, ease: 'outQuad' });
    if (panel) {
        // Rises into place with a soft ease-out, the same motion as the
        // public site's package dialog. The old scale-with-overshoot
        // ('outBack') made the panel pop and settle.
        window.anime.animate(panel, { opacity: [0, 1], translateY: [24, 0], duration: 360, ease: 'outCubic' });
    }
}

function closeOverlay(overlay) {
    if (typeof overlay === 'string') overlay = document.getElementById(overlay);
    if (!overlay || overlay.style.display === 'none') return;
    if (window.anime && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        const panel = overlay.querySelector('.card, .pi-modal');
        window.anime.animate(overlay, { opacity: [1, 0], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        if (panel) {
            window.anime.animate(panel, { opacity: [1, 0], translateY: [0, 10], duration: OVERLAY_EXIT_MS, ease: 'inQuad' });
        }
        setTimeout(() => { overlay.style.display = 'none'; }, OVERLAY_EXIT_MS);
    } else {
        overlay.style.display = 'none';
    }
}

// The common case: one button opens the overlay as-is (no per-click
// setup needed) and one Cancel button, the backdrop, or Escape closes
// it. Previously duplicated verbatim across pages (each defining its
// own copy, and each registering its own document-level Escape
// listener) — now the one shared version, called the same way from
// any page (materials.html alone now wires four separate overlays:
// add/edit material, add supplier, plus Suppliers' own edit/history
// modals since the two pages merged into one).
function wireOverlay(overlayId, openBtnId, cancelBtnId) {
    const overlay = document.getElementById(overlayId);
    const openBtn = openBtnId ? document.getElementById(openBtnId) : null;
    const cancelBtn = cancelBtnId ? document.getElementById(cancelBtnId) : null;
    if (!overlay || !openBtn) return;

    openBtn.addEventListener('click', () => openOverlay(overlay));
    if (cancelBtn) cancelBtn.addEventListener('click', () => closeOverlay(overlay));
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) closeOverlay(overlay);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && overlay.style.display !== 'none') closeOverlay(overlay);
    });
}

// Shows the loading overlay the instant a form is actually submitted,
// app-wide — no per-form opt-in needed. Delegated on `document` in
// the bubble phase, so a capture-phase listener that cancels the
// submission first (initConfirmDialogs()'s confirm() dialog) or a
// form's own bubble-phase listener closer to it in the tree (login.html's
// field validation, initDispatchQtyWarnings()'s over/under-limit
// checks) has already had its chance to call preventDefault() by the
// time this runs — checking e.defaultPrevented here means a cancelled
// submit never shows a dialog for a request that was never actually
// sent.
//
// MUST be registered before initSoftNav() (see the DOMContentLoaded
// call site) — initSoftNav()'s own submit listener also calls
// e.preventDefault() on nearly every form in the app, to take over
// with its own fetch, which e.defaultPrevented can't tell apart from
// an actual cancellation. Running first means that hasn't happened
// yet by the time this checks it.
//
// Hiding it again is initSoftNav()'s job (see go() above): a plain,
// non-soft-nav submission just lets the browser's own navigation tear
// the whole page — overlay included — down on its own, but a
// successful soft-nav swap only replaces .content, so go() explicitly
// hides this once its response comes back.
//
// That "the browser tears the whole page down" assumption doesn't hold
// for a form (or submit button) targeting a new tab/window — e.g. the
// Reports page's Download PDF/Excel buttons, formtarget="_blank" so the
// file opens in a new tab while this one stays put. isSoftSubmittableForm()
// above already excludes those from soft-nav (target !== '_self'), which
// used to leave them showing the overlay here with nothing left to ever
// hide it again: the current page never navigates, so it just sat there
// reading "Loading…" forever after every report download. Skipped with
// the same target-resolution isSoftSubmittableForm() uses, so only a
// submit that's actually about to navigate *this* tab shows it.
function initSubmitLoadingState() {
    // A submit marks its own button busy (label and width stay) and starts the
    // top progress bar; the page stays interactive. A second submit of the same
    // form is swallowed while the first is in flight.
    function clearBusy(form) {
        delete form.dataset.submitting;
        form.querySelectorAll('.is-loading').forEach((b) => {
            b.classList.remove('is-loading');
            b.removeAttribute('aria-busy');
            b.removeAttribute('aria-disabled');
        });
    }

    document.addEventListener('submit', (e) => {
        if (e.defaultPrevented) return;
        if (!(e.target instanceof HTMLFormElement)) return;
        const form = e.target;
        const target = (e.submitter && e.submitter.formTarget) || form.target;
        if (target && target !== '_self') return;
        if (form.dataset.submitting === '1') {
            e.preventDefault();
            return;
        }
        form.dataset.submitting = '1';
        const btn = e.submitter || form.querySelector('button[type="submit"], button:not([type])');
        if (btn) {
            btn.classList.add('is-loading');
            btn.setAttribute('aria-busy', 'true');
            btn.setAttribute('aria-disabled', 'true');
        }
        const bar = document.getElementById('topbarProgress');
        if (bar) bar.classList.add('is-loading');
        // A later handler may still cancel the submit (validation): undo then.
        setTimeout(() => {
            if (e.defaultPrevented) {
                clearBusy(form);
                if (bar) bar.classList.remove('is-loading');
            }
        }, 0);
    });

    // Back/forward restores the page from cache with the busy state still on.
    window.addEventListener('pageshow', (ev) => {
        if (!ev.persisted) return;
        document.querySelectorAll('form[data-submitting]').forEach(clearBusy);
        const bar = document.getElementById('topbarProgress');
        if (bar) bar.classList.remove('is-loading');
    });
}

// ---------------------------------------------------------------
// Receipt downloads, report PDF/Excel generation, and the product CSV
// export (every element with [data-generate-file] — see admin/reports.html,
// branch/reports.html, and the various Receipt/Export links across both
// admin and branch templates) all build a file server-side before
// sending anything back. Unlike a normal form submit, none of them
// navigate the current tab (they're target="_blank"/formtarget="_blank"
// specifically so initSoftNav() and initSubmitLoadingState() both
// leave them alone — see those functions' own comments), so there was
// nothing on screen between the click and the browser's own download
// indicator a moment later, which reads as "did that even do
// anything?" when generation takes more than an instant.
//
// Handled here with fetch instead of letting the browser follow the
// link/submit the form directly, so this dialog can show real
// progress once bytes start arriving and be dismissed exactly when
// the file is actually ready — not a guessed timeout. A response
// that isn't one of the file types these endpoints actually produce
// (e.g. generate_report() redirecting back to the report builder with
// a "no data matches those filters" flash) is treated as a normal
// page navigation instead of a download.
let generateOverlayEl = null;
let generateInFlight = false;
let generateOverlayShownAt = 0;
// A small/fast receipt can finish end-to-end well under a second,
// which made the dialog blip on and immediately back off — barely
// long enough to register as having happened. Same idea as
// LOADING_OVERLAY_MIN_VISIBLE_MS above, just held a beat longer since
// this one's showing a progress bar that's more noticeable mid-cut.
const GENERATE_OVERLAY_MIN_VISIBLE_MS = 900;

