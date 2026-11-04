// Settings modal (appearance, backup, restore, etc.)
// (Part of the app's main script; loaded in order by base.html.)

// Settings dialog (sidebar footer → Settings): light/dark mode plus the
// interface style (html[data-style], defined in static/css/themes.css).
// Both are per-browser, stored in localStorage; base.html's head script
// re-applies them before first paint.
function initSettings() {
    const overlay = document.getElementById('settingsOverlay');
    const opener = document.getElementById('settingsOpen');
    if (!overlay || !opener) return;

    const root = document.documentElement;
    const STYLES = ['classic', 'neobrutalism', 'minimalism', 'glassmorphism',
        'skeuomorphism', 'neumorphism', 'claymorphism', 'cyberpunk', 'terminal',
        'paper', 'vaporwave', 'pixel', 'aero', 'aurora', 'bauhaus'];
    const store = (key, value) => {
        try { localStorage.setItem(key, value); } catch (e) { }
    };
    const modeBtns = overlay.querySelectorAll('[data-mode]');
    const styleBtns = overlay.querySelectorAll('[data-style-option]');
    let lastFocus = null;

    function sync() {
        const mode = root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        const style = root.getAttribute('data-style') || 'classic';
        modeBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === mode)));
        styleBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.styleOption === style)));
    }

    // ---- Tabs (General / Appearance / Backup) ----
    const tabs = Array.from(overlay.querySelectorAll('.settings-tab'));
    const panels = Array.from(overlay.querySelectorAll('.settings-panel'));
    function showTab(name, focus) {
        if (!tabs.some((t) => t.dataset.tab === name)) name = 'general';
        tabs.forEach((t) => {
            const on = t.dataset.tab === name;
            t.setAttribute('aria-selected', String(on));
            t.tabIndex = on ? 0 : -1;
            if (on && focus) t.focus();
        });
        panels.forEach((p) => { p.hidden = p.dataset.panel !== name; });
        const body = overlay.querySelector('.settings-body');
        if (body) body.scrollTop = 0;
        store('settingsTab', name);
    }
    tabs.forEach((t, i) => {
        t.addEventListener('click', () => showTab(t.dataset.tab));
        t.addEventListener('keydown', (e) => {
            const horizontal = window.matchMedia('(max-width: 640px)').matches;
            const next = horizontal ? 'ArrowRight' : 'ArrowDown';
            const prev = horizontal ? 'ArrowLeft' : 'ArrowUp';
            let to = null;
            if (e.key === next) to = tabs[(i + 1) % tabs.length];
            else if (e.key === prev) to = tabs[(i - 1 + tabs.length) % tabs.length];
            else if (e.key === 'Home') to = tabs[0];
            else if (e.key === 'End') to = tabs[tabs.length - 1];
            if (to) { e.preventDefault(); showTab(to.dataset.tab, true); }
        });
    });

    // Re-measure anything sized from the old style's fonts/padding (the
    // sidebar's active-link indicator, charts) once the new style is live.
    function relayout() {
        requestAnimationFrame(() => window.dispatchEvent(new Event('resize')));
    }

    function setStyle(style) {
        if (!STYLES.includes(style)) return;
        if ((root.getAttribute('data-style') || 'classic') === style) { sync(); return; }
        store('uiStyle', style);
        const apply = () => {
            root.setAttribute('data-style', style);
            sync();
        };
        // One snapshot cross-fade on the compositor instead of every
        // element restyling in view at once (these styles swap fonts,
        // borders, radii and shadows, which CSS transitions can't tween).
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            root.classList.add('vt-style');
            const t = document.startViewTransition(apply);
            t.finished.finally(() => { root.classList.remove('vt-style'); relayout(); });
        } else {
            apply();
            relayout();
        }
    }

    function setMode(mode) {
        if (root.getAttribute('data-theme') === mode) { sync(); return; }
        store('theme', mode);
        if (typeof document.startViewTransition === 'function' && !PREFERS_REDUCED_MOTION.matches) {
            root.classList.add('vt-style');
            const t = document.startViewTransition(() => { root.setAttribute('data-theme', mode); sync(); });
            t.finished.finally(() => root.classList.remove('vt-style'));
        } else {
            root.setAttribute('data-theme', mode);
            sync();
        }
    }

    function open(tab) {
        lastFocus = document.activeElement;
        sync();
        let want = typeof tab === 'string' ? tab : null;
        if (!want) {
            // A due backup (the dot on Settings) lands on the Backup tab.
            if (opener.hasAttribute('data-alert') && tabs.some((t) => t.dataset.tab === 'backup')) want = 'backup';
            else { try { want = localStorage.getItem('settingsTab'); } catch (e) { } }
        }
        showTab(want || 'general');
        overlay.hidden = false;
        const selected = tabs.find((t) => t.getAttribute('aria-selected') === 'true');
        (selected || overlay.querySelector('.settings-close')).focus();
    }

    function close() {
        overlay.hidden = true;
        if (lastFocus && typeof lastFocus.focus === 'function') lastFocus.focus();
    }

    opener.addEventListener('click', () => open());
    // The account link may be handled by soft nav, which would leave the
    // dialog open over the new page.
    overlay.querySelectorAll('a').forEach((a) => a.addEventListener('click', () => { overlay.hidden = true; }));
    overlay.querySelector('#settingsClose').addEventListener('click', close);
    overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) close(); });
    modeBtns.forEach((b) => b.addEventListener('click', () => setMode(b.dataset.mode)));
    styleBtns.forEach((b) => b.addEventListener('click', () => setStyle(b.dataset.styleOption)));
    overlay.querySelector('#settingsReset').addEventListener('click', () => {
        setStyle('classic');
        setAccent('');
    });

    // ---- Accent color (window.applyAccent lives in base.html's head) ----
    const accentBtns = overlay.querySelectorAll('[data-accent]');
    const accentCustom = overlay.querySelector('#accentCustom');
    function syncAccent() {
        let cur = '';
        try { cur = (localStorage.getItem('accent') || '').toLowerCase(); } catch (e) { }
        let preset = false;
        accentBtns.forEach((b) => {
            const on = b.dataset.accent.toLowerCase() === cur;
            preset = preset || (on && cur !== '');
            b.setAttribute('aria-pressed', String(on));
        });
        accentCustom.parentElement.classList.toggle('is-active', cur !== '' && !preset);
        if (cur) accentCustom.value = cur;
    }
    function setAccent(hex) {
        try {
            if (hex) localStorage.setItem('accent', hex);
            else localStorage.removeItem('accent');
        } catch (e) { }
        if (typeof window.applyAccent === 'function') window.applyAccent(hex);
        syncAccent();
    }
    accentBtns.forEach((b) => b.addEventListener('click', () => setAccent(b.dataset.accent)));
    accentCustom.addEventListener('input', () => setAccent(accentCustom.value));
    syncAccent();

    // ---- Clear data (dev only): button stays disabled until the phrase matches ----
    const clearPhrase = overlay.querySelector('#clearDataPhrase');
    if (clearPhrase) {
        const clearBtn = overlay.querySelector('#clearDataBtn');
        clearPhrase.addEventListener('input', () => {
            clearBtn.disabled = clearPhrase.value.trim() !== 'CLEAR ALL DATA';
        });
    }

    // Sidebar group behaviour (handled by initNavAccordion via events).
    const exclusiveBtn = overlay.querySelector('#navExclusive');
    const syncExclusive = () => {
        let on = false;
        try { on = localStorage.getItem('navExclusive') === 'true'; } catch (e) { }
        exclusiveBtn.setAttribute('aria-checked', String(on));
    };
    syncExclusive();
    exclusiveBtn.addEventListener('click', () => {
        const on = exclusiveBtn.getAttribute('aria-checked') !== 'true';
        store('navExclusive', String(on));
        syncExclusive();
        document.dispatchEvent(new Event('nav:exclusive'));
    });
    // ---- Backup + restore (admins only) --------------------------------
    // Shared status helper: text with a busy/ok/bad mark that slides in
    // each time it changes, plus an optional progress bar (a number is a
    // percentage, null is "working, unknown length", false hides it).
    const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    const makeUi = (textEl, barEl, warnEl) => ({
        say(text, tone, busy) {
            if (textEl.textContent !== text || textEl.dataset.tone !== (tone || '')) {
                textEl.textContent = text;
                textEl.dataset.tone = tone || '';
                textEl.classList.remove('is-swap');
                void textEl.offsetWidth;            // restart the animation
                textEl.classList.add('is-swap');
            }
            textEl.classList.toggle('is-ok', tone === 'ok');
            textEl.classList.toggle('is-bad', tone === 'bad');
            textEl.classList.toggle('is-busy', !!busy);
        },
        warn(text) {
            if (!warnEl) return;
            warnEl.hidden = !text;
            if (!text) return;
            if (warnEl.textContent !== text) {
                warnEl.textContent = text;
                warnEl.classList.remove('is-swap');
                void warnEl.offsetWidth;
                warnEl.classList.add('is-swap');
            }
            warnEl.classList.add('is-bad');
        },
        bar(pct) {
            if (!barEl) return;
            barEl.hidden = pct === false;
            barEl.classList.toggle('is-indeterminate', pct === null);
            barEl.firstElementChild.style.width = typeof pct === 'number' ? pct + '%' : '';
        },
    });
    const getJson = (url) => fetch(url, { headers: { Accept: 'application/json' } }).then((r) => {
        if (r.redirected || r.status === 401 || r.status === 403) throw new Error('signed-out');
        return r.json();
    });

    // Backup: email a full backup, then watch for the result.
    const backupBtn = overlay.querySelector('#backupNow');
    if (backupBtn) {
        const ui = makeUi(overlay.querySelector('#backupStatus'), overlay.querySelector('#backupBar'),
            overlay.querySelector('#backupWarn'));
        const toEl = overlay.querySelector('#backupTo');
        let baseline = null;     // latest backup log id before we started
        let timer = 0;

        // ---- schedule: how often, automatic on/off, what time ----
        const daysEl = overlay.querySelector('#backupDays');
        const hourEl = overlay.querySelector('#backupHour');
        const autoEl = overlay.querySelector('#backupAuto');
        const noteEl = overlay.querySelector('#scheduleNote');
        const schedUi = makeUi(overlay.querySelector('#scheduleStatus'), null, null);
        let syncing = false;
        let schedTimer = 0;

        const explain = (sch, runSoon, nextRun) => {
            const every = sch.days === 7 ? 'week' : (sch.days === 30 ? 'month' : sch.days + ' days');
            if (sch.auto) {
                noteEl.textContent = runSoon
                    ? 'A backup is due, so the next automatic one starts within a few minutes.'
                    : 'Next automatic backup: ' + nextRun + '. A manual backup restarts the count.';
            } else {
                noteEl.textContent = 'You will get a reminder here once a backup is more than 1 ' + every
                    + ' old' + (sch.days === 7 ? '' : ' (' + sch.days + ' days)')
                    + '. Weekly suits a shop that records sales every day; a month can lose a lot if something goes wrong.';
            }
        };
        function syncSchedule(d) {
            if (!d || !d.schedule) return;
            syncing = true;
            daysEl.value = String(d.schedule.days);
            hourEl.value = String(d.schedule.hour);
            autoEl.setAttribute('aria-checked', String(d.schedule.auto));
            hourEl.disabled = !d.schedule.auto;
            const wrap = hourEl.closest('.cs-select');
            if (wrap) wrap.classList.toggle('cs-disabled', !d.schedule.auto);
            explain(d.schedule, d.schedule.run_soon, d.schedule.next_run);
            syncing = false;
        }
        const saveSchedule = () => {
            if (syncing) return;
            const sch = {
                days: parseInt(daysEl.value, 10),
                hour: parseInt(hourEl.value, 10),
                auto: autoEl.getAttribute('aria-checked') === 'true',
            };
            schedUi.say('Saving\u2026', '', true);
            fetch(backupBtn.dataset.scheduleUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                body: JSON.stringify(sch),
            }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                .then(({ ok, b }) => {
                    if (!ok) {
                        schedUi.say(b.error || 'Could not save the schedule.', 'bad');
                        refresh();                       // put the controls back to what is saved
                        return;
                    }
                    hourEl.disabled = !sch.auto;
                    const wrap = hourEl.closest('.cs-select');
                    if (wrap) wrap.classList.toggle('cs-disabled', !sch.auto);
                    explain(sch, b.run_soon, b.next_run);
                    schedUi.say('Saved', 'ok');
                    window.clearTimeout(schedTimer);
                    schedTimer = window.setTimeout(() => schedUi.say('', ''), 2500);
                })
                .catch(() => schedUi.say('Could not reach the server.', 'bad'));
        };
        daysEl.addEventListener('change', saveSchedule);
        hourEl.addEventListener('change', saveSchedule);
        autoEl.addEventListener('click', () => {
            const on = autoEl.getAttribute('aria-checked') !== 'true';
            autoEl.setAttribute('aria-checked', String(on));
            autoEl.classList.remove('is-pulse');
            hourEl.disabled = !on;
            saveSchedule();
        });

        // ---- reminder banner (outside the dialog) + the dot on Settings ----
        const banner = document.getElementById('backupReminder');
        function clearReminder() {
            opener.removeAttribute('data-alert');
            opener.removeAttribute('title');
            const el = document.getElementById('backupReminder');
            if (!el) return;
            el.classList.add('is-leaving');
            window.setTimeout(() => el.remove(), 450);
        }
        if (banner) {
            banner.addEventListener('click', (e) => {
                const btn = e.target.closest('[data-reminder]');
                if (!btn) return;
                const what = btn.dataset.reminder;
                if (what === 'snooze') {
                    fetch(backupBtn.dataset.snoozeUrl, {
                        method: 'POST', headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                    }).catch(() => { });
                    clearReminder();
                } else if (what === 'now') {
                    open();
                    window.setTimeout(() => { if (!backupBtn.disabled) backupBtn.click(); }, 350);
                } else if (what === 'auto') {
                    open();
                    window.setTimeout(() => {
                        autoEl.scrollIntoView({ block: 'center', behavior: 'smooth' });
                        autoEl.classList.add('is-pulse');
                        autoEl.focus({ preventScroll: true });
                    }, 300);
                }
            });
        }

        const idle = (d) => {
            ui.bar(false);
            backupBtn.disabled = false;
            if (d.last_ok) ui.say('Last backup: ' + d.last_ok.at + ' (' + d.last_ok.details + ').', 'ok');
            else ui.say('No backup has been sent yet.');
            ui.warn(d.last_failed
                ? (d.last_ok ? 'A later attempt failed' : 'The last attempt failed') + ' (' + d.last_failed.at + '): ' + d.last_failed.details
                : '');
        };
        const describe = (d) => {
            syncSchedule(d);
            if (!d.configured) {
                backupBtn.disabled = true;
                ui.bar(false);
                ui.say('Email is not set up yet. Add the mail settings to .env first.', 'bad');
                return;
            }
            if (d.recipient && toEl) toEl.textContent = 'to ' + d.recipient;
            if (d.running) {
                backupBtn.disabled = true;
                ui.say((d.phase || 'Working') + '\u2026', '', true);
                ui.bar(null);
                watch();
            } else {
                idle(d);
            }
        };
        const refresh = () => getJson(backupBtn.dataset.statusUrl).then((d) => {
            baseline = d.latest_id;
            describe(d);
        }).catch(() => { });

        // Poll until the server has logged a new result.
        const watch = () => {
            if (timer) return;
            const started = Date.now();
            let misses = 0;
            timer = window.setInterval(() => {
                getJson(backupBtn.dataset.statusUrl).then((d) => {
                    misses = 0;
                    if (d.running) {
                        ui.say((d.phase || 'Working') + '\u2026', '', true);
                    } else if (d.latest_id !== baseline) {
                        window.clearInterval(timer);
                        timer = 0;
                        baseline = d.latest_id;
                        const newest = d.last_failed && (!d.last_ok || d.last_failed.id > d.last_ok.id);
                        if (newest) {
                            backupBtn.disabled = false;
                            ui.bar(false);
                            ui.say('Backup failed: ' + d.last_failed.details, 'bad');
                            ui.warn('');
                        } else {
                            idle(d);
                            ui.say('Backup sent ' + d.last_ok.at + ' (' + d.last_ok.details + ').', 'ok');
                            clearReminder();
                            syncSchedule(d);
                        }
                    }
                }).catch(() => {
                    if (++misses >= 5) {
                        window.clearInterval(timer);
                        timer = 0;
                        backupBtn.disabled = false;
                        ui.bar(false);
                        ui.say('Lost contact with the server. Check your inbox in a few minutes.', 'bad');
                    }
                });
                if (Date.now() - started > 300000) {
                    window.clearInterval(timer);
                    timer = 0;
                    backupBtn.disabled = false;
                    ui.bar(false);
                    ui.say('Still working. Check your inbox in a few minutes.');
                }
            }, 1500);
        };

        backupBtn.addEventListener('click', () => {
            backupBtn.disabled = true;
            ui.warn('');
            ui.say('Starting the backup\u2026', '', true);
            ui.bar(null);
            fetch(backupBtn.dataset.emailUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrfToken, Accept: 'application/json' },
            }).then((r) => r.json().then((body) => ({ ok: r.ok, body })))
                .then(({ ok, body }) => {
                    if (!ok) {
                        ui.bar(false);
                        ui.say(body.error || 'Could not start the backup.', 'bad');
                        backupBtn.disabled = false;
                        return;
                    }
                    watch();
                })
                .catch(() => {
                    ui.bar(false);
                    ui.say('Could not reach the server.', 'bad');
                    backupBtn.disabled = false;
                });
        });

        opener.addEventListener('click', refresh);
        refresh();
    }

    // Restore: confirm with password + phrase, upload in 4 MB pieces, then
    // follow the server's progress until it is finished.
    const restoreBtn = overlay.querySelector('#restoreNow');
    if (restoreBtn) {
        const ui = makeUi(overlay.querySelector('#restoreStatus'), overlay.querySelector('#restoreBar'));
        const fileEl = overlay.querySelector('#restoreFile');
        const pwEl = overlay.querySelector('#restorePassword');
        // The password field sits in a form so browsers stop warning about it;
        // the Restore button does the work, so Enter must not submit the form.
        const restoreForm = overlay.querySelector('#restoreForm');
        if (restoreForm) restoreForm.addEventListener('submit', (e) => e.preventDefault());
        const phraseEl = overlay.querySelector('#restorePhrase');
        const CHUNK = 4 * 1024 * 1024;
        const fail = (text) => {
            ui.bar(false);
            ui.say(text, 'bad');
            restoreBtn.disabled = false;
        };
        const finished = (text) => {
            ui.bar(100);
            ui.say(text, 'ok');
            // Restored accounts/passwords may be identical, which keeps this session
            // valid, so sign out for real (POST /logout) instead of only redirecting.
            const signout = document.createElement('div');
            signout.className = 'signout-overlay';
            signout.setAttribute('role', 'alertdialog');
            signout.setAttribute('aria-live', 'assertive');
            signout.setAttribute('aria-label', 'Signing you out');
            signout.innerHTML = '<div class="signout-dialog"><div class="signout-spinner" aria-hidden="true"></div>'
                + '<strong>Signing you out…</strong>'
                + '<span>The restore is complete. Please sign in again.</span></div>';
            document.body.appendChild(signout);
            const minShown = new Promise((resolve) => window.setTimeout(resolve, 1800));
            const loggedOut = fetch('/logout', { method: 'POST', headers: { 'X-CSRFToken': csrfToken }, credentials: 'same-origin' })
                .catch(() => {});
            Promise.all([minShown, loggedOut]).then(() => { window.location.href = '/login'; });
        };

        const follow = () => {
            ui.bar(null);
            const started = Date.now();
            let misses = 0;
            const timer = window.setInterval(() => {
                getJson(restoreBtn.dataset.statusUrl).then((d) => {
                    misses = 0;
                    if (d.running) {
                        ui.say((d.phase || 'Working') + '\u2026', '', true);
                    } else if (d.done) {
                        window.clearInterval(timer);
                        if (d.error) fail(d.error);
                        else finished('Restore complete. ' + d.summary + ' Signing you out\u2026');
                    }
                }).catch((err) => {
                    // Signed out = the restored data has no session for us: it worked.
                    if (err && err.message === 'signed-out') {
                        window.clearInterval(timer);
                        finished('Restore complete. Signing you out\u2026');
                    } else if (++misses >= 6) {
                        window.clearInterval(timer);
                        fail('Lost contact with the server. Wait a minute, then refresh the page.');
                    }
                });
                if (Date.now() - started > 900000) window.clearInterval(timer);
            }, 2000);
        };

        const sendChunks = (file) => {
            const piece = (offset) => {
                const end = Math.min(offset + CHUNK, file.size);
                ui.say('Uploading the backup\u2026 ' + Math.round((end / file.size) * 100) + '%', '', true);
                ui.bar(Math.round((end / file.size) * 100));
                return fetch(restoreBtn.dataset.chunkUrl + '?offset=' + offset + '&total=' + file.size, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/octet-stream', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                    body: file.slice(offset, end),
                }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                    .then(({ ok, b }) => {
                        if (!ok) return fail(b.error || 'The upload was refused.');
                        if (end < file.size) return piece(end);
                        ui.say('Restoring\u2026', '', true);
                        follow();
                    });
            };
            return piece(0);
        };

        restoreBtn.addEventListener('click', () => {
            const file = fileEl.files && fileEl.files[0];
            if (!file) return fail('Choose the backup file first.');
            if (!pwEl.value) return fail('Enter your password.');
            if (phraseEl.value !== 'RESTORE') return fail('Type RESTORE exactly to confirm.');
            restoreBtn.disabled = true;
            ui.say('Checking your password\u2026', '', true);
            ui.bar(null);
            fetch(restoreBtn.dataset.verifyUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken, Accept: 'application/json' },
                body: JSON.stringify({ password: pwEl.value, phrase: phraseEl.value }),
            }).then((r) => r.json().then((b) => ({ ok: r.ok, b })))
                .then(({ ok, b }) => {
                    if (!ok) return fail(b.error || 'Could not confirm.');
                    pwEl.value = '';
                    return sendChunks(file);
                })
                .catch(() => fail('Could not reach the server.'));
        });
    }

    overlay.querySelector('#navExpandAll').addEventListener('click', () => document.dispatchEvent(new Event('nav:expand-all')));
    overlay.querySelector('#navCollapseAll').addEventListener('click', () => document.dispatchEvent(new Event('nav:collapse-all')));

    // Esc closes; Tab stays inside the dialog while it is open.
    document.addEventListener('keydown', (e) => {
        if (overlay.hidden) return;
        if (e.key === 'Escape') {
            e.preventDefault();
            close();
        } else if (e.key === 'Tab') {
            const items = Array.from(overlay.querySelectorAll('button')).filter((b) => b.offsetParent !== null);
            if (!items.length) return;
            const first = items[0];
            const last = items[items.length - 1];
            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }
    });
}

