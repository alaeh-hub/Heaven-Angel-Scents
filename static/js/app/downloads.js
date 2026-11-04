// Report generation overlay and blob downloads
// (Part of the app's main script; loaded in order by base.html.)

function getGenerateOverlay() {
    if (generateOverlayEl) return generateOverlayEl;
    const el = document.createElement('div');
    el.className = 'loading-overlay';
    el.hidden = true;
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.innerHTML =
        '<div class="loading-dialog">' +
        '<div class="loading-dialog-text" id="genOverlayText">Preparing your file…</div>' +
        '<div class="gen-progress-track indeterminate" id="genProgressTrack">' +
        '<div class="gen-progress-fill" id="genProgressFill"></div>' +
        '</div>' +
        '</div>';
    document.body.appendChild(el);
    generateOverlayEl = el;
    return el;
}

function showGenerateOverlay() {
    generateOverlayShownAt = Date.now();
    const el = getGenerateOverlay();
    el.querySelector('#genProgressTrack').classList.add('indeterminate');
    el.querySelector('#genProgressFill').style.removeProperty('--p');
    el.querySelector('#genOverlayText').textContent = 'Preparing your file…';
    el.classList.remove('loading-overlay-exit');
    el.hidden = false;
}

// Called as real response bytes come in (see runFileGeneration()) —
// switches the bar from the indeterminate sliding state (generation
// has no measurable progress; send_file only hands back a
// Content-Length once the file is already fully built) to a real,
// determinate width.
function setGenerateProgress(fraction) {
    if (!generateOverlayEl) return;
    generateOverlayEl.querySelector('#genProgressTrack').classList.remove('indeterminate');
    generateOverlayEl.querySelector('#genProgressFill').style.setProperty('--p', String(Math.min(Math.max(fraction, 0), 1)));
    generateOverlayEl.querySelector('#genOverlayText').textContent = 'Downloading…';
}

function hideGenerateOverlay() {
    if (!generateOverlayEl || generateOverlayEl.hidden) return;

    const doHide = () => {
        generateOverlayEl.classList.add('loading-overlay-exit');
        setTimeout(() => {
            generateOverlayEl.hidden = true;
            generateOverlayEl.classList.remove('loading-overlay-exit');
        }, LOADING_OVERLAY_EXIT_MS);
    };

    const remaining = GENERATE_OVERLAY_MIN_VISIBLE_MS - (Date.now() - generateOverlayShownAt);
    if (remaining > 0) {
        setTimeout(doHide, remaining);
    } else {
        doHide();
    }
}

function generateFileNameFromHeader(contentDisposition) {
    if (!contentDisposition) return null;
    const starMatch = /filename\*=UTF-8''([^;]+)/i.exec(contentDisposition);
    if (starMatch) {
        try { return decodeURIComponent(starMatch[1]); } catch (e) { /* fall through */ }
    }
    const match = /filename="?([^";]+)"?/i.exec(contentDisposition);
    return match ? match[1] : null;
}

function triggerBlobDownload(blob, filename) {
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename || 'download';
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (window.Sfx) window.Sfx.play('download');
    // Not revoked immediately — some browsers start the actual save
    // asynchronously right after the click, and revoking too early can
    // cancel that in-flight save.
    setTimeout(() => URL.revokeObjectURL(blobUrl), 30000);
}

async function runFileGeneration(url) {
    if (generateInFlight) return;
    generateInFlight = true;
    showGenerateOverlay();
    try {
        const res = await fetch(url, { credentials: 'same-origin' });
        const contentType = res.headers.get('content-type') || '';
        const isFile = /application\/pdf|spreadsheetml|text\/csv/i.test(contentType);

        if (!res.ok || !isFile) {
            // Not a file — e.g. an expired session bouncing to /login,
            // or generate_report() redirecting back with a "no data
            // matches those filters" flash. fetch() already followed
            // any redirect, so res.url is wherever that landed.
            window.location.href = res.url || url;
            return;
        }

        const total = parseInt(res.headers.get('content-length') || '0', 10);
        const chunks = [];
        if (res.body && res.body.getReader && total) {
            const reader = res.body.getReader();
            let received = 0;
            setGenerateProgress(0);
            for (; ;) {
                const { done, value } = await reader.read();
                if (done) break;
                chunks.push(value);
                received += value.length;
                setGenerateProgress(received / total);
            }
        } else {
            chunks.push(new Uint8Array(await res.arrayBuffer()));
            setGenerateProgress(1);
        }

        const blob = new Blob(chunks, { type: contentType });
        const filename = generateFileNameFromHeader(res.headers.get('content-disposition'));
        triggerBlobDownload(blob, filename);
    } catch (err) {
        // Network hiccup, or a browser that doesn't support one of the
        // fetch/stream APIs used above — fall back to a real navigation
        // so the download still happens, just without the dialog.
        window.location.href = url;
    } finally {
        generateInFlight = false;
        hideGenerateOverlay();
    }
}

function initGenerateFileFeedback() {
    document.querySelectorAll('[data-generate-file]').forEach((el) => {
        if (el instanceof HTMLFormElement) {
            el.addEventListener('submit', (e) => {
                if (e.defaultPrevented) return;
                e.preventDefault();
                const params = new URLSearchParams(new FormData(el, e.submitter));
                const query = params.toString();
                runFileGeneration(query ? `${el.action}?${query}` : el.action);
            });
        } else {
            el.addEventListener('click', (e) => {
                e.preventDefault();
                runFileGeneration(el.href);
            });
        }
    });
}

