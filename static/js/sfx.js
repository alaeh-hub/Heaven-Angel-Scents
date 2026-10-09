// System sound effects.
//
// Deliberately sparse: nothing plays for ordinary clicks, typing, tabs,
// menus or navigation. Sounds mark things the SYSTEM did and the user
// should notice: a success or failure toast, a bell notification, a
// scan result, a finished download.
//
// Every sound is synthesised with the Web Audio API at the moment it
// plays, so there are no audio files to download, cache or license.
// Listening is delegated from `document`, which means it keeps working
// across soft navigation and for controls that are built at runtime
// (custom selects, date pickers, toasts) without any per-page wiring.
//
// Opt-outs and overrides, on any element:
//     data-sfx="off"       stay silent
//     data-sfx="success"   play that sound instead of the guessed one
//
// Other scripts can call window.Sfx.play('name') directly. The speaker
// button (#sfxToggle / .sfx-toggle) mutes everything; the choice is
// remembered per browser.
(function () {
    'use strict';

    const STORE_KEY = 'sfx:on';
    const VOLUME = 0.5;

    let ctx = null;
    let master = null;
    let enabled = true;
    try { enabled = localStorage.getItem(STORE_KEY) !== '0'; } catch (e) { /* storage blocked */ }

    // ---- engine -------------------------------------------------------

    function context() {
        if (ctx) return ctx;
        const Ctor = window.AudioContext || window.webkitAudioContext;
        if (!Ctor) return null;
        ctx = new Ctor();
        const comp = ctx.createDynamicsCompressor();
        comp.threshold.value = -18;
        comp.ratio.value = 6;
        master = ctx.createGain();
        master.gain.value = VOLUME;
        master.connect(comp);
        comp.connect(ctx.destination);
        return ctx;
    }

    // Browsers keep audio suspended until a user gesture. Resume on every
    // early gesture so the very first click is already audible.
    function unlock() {
        const c = context();
        if (c && c.state === 'suspended') c.resume();
    }
    ['pointerdown', 'keydown', 'touchstart'].forEach(function (evt) {
        window.addEventListener(evt, unlock, { capture: true, passive: true });
    });

    /** One shaped tone. `freq` may be a number or [from, to] for a glide. */
    function tone(o) {
        const c = ctx;
        const t0 = c.currentTime + (o.at || 0);
        const dur = o.dur || 0.1;
        const osc = c.createOscillator();
        const gain = c.createGain();
        osc.type = o.type || 'sine';
        const from = Array.isArray(o.freq) ? o.freq[0] : o.freq;
        const to = Array.isArray(o.freq) ? o.freq[1] : o.freq;
        osc.frequency.setValueAtTime(from, t0);
        if (to !== from) osc.frequency.exponentialRampToValueAtTime(Math.max(to, 20), t0 + dur);
        const peak = (o.gain === undefined ? 0.3 : o.gain);
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(peak, t0 + (o.attack || 0.004));
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        let node = osc;
        if (o.filter) {
            const f = c.createBiquadFilter();
            f.type = o.filter.type || 'lowpass';
            f.frequency.value = o.filter.freq;
            node.connect(f);
            node = f;
        }
        node.connect(gain);
        gain.connect(master);
        osc.start(t0);
        osc.stop(t0 + dur + 0.05);
    }

    /** A burst of filtered noise: the "air" in a swoosh. */
    function noise(o) {
        const c = ctx;
        const t0 = c.currentTime + (o.at || 0);
        const dur = o.dur || 0.12;
        const len = Math.max(1, Math.floor(c.sampleRate * dur));
        const buf = c.createBuffer(1, len, c.sampleRate);
        const data = buf.getChannelData(0);
        for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
        const src = c.createBufferSource();
        src.buffer = buf;
        const f = c.createBiquadFilter();
        f.type = o.type || 'bandpass';
        f.Q.value = o.q || 1.2;
        const from = Array.isArray(o.freq) ? o.freq[0] : o.freq;
        const to = Array.isArray(o.freq) ? o.freq[1] : o.freq;
        f.frequency.setValueAtTime(from, t0);
        if (to !== from) f.frequency.exponentialRampToValueAtTime(Math.max(to, 20), t0 + dur);
        const gain = c.createGain();
        const peak = o.gain === undefined ? 0.2 : o.gain;
        gain.gain.setValueAtTime(0.0001, t0);
        gain.gain.exponentialRampToValueAtTime(peak, t0 + dur * 0.3);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
        src.connect(f);
        f.connect(gain);
        gain.connect(master);
        src.start(t0);
    }

    /** A bell: a fundamental plus inharmonic partials, long soft decay. */
    function bell(freq, at, gain, dur) {
        const d = dur || 0.7;
        tone({ freq: freq, at: at, dur: d, gain: gain, attack: 0.003 });
        tone({ freq: freq * 2.01, at: at, dur: d * 0.6, gain: gain * 0.35, attack: 0.003 });
        tone({ freq: freq * 3.97, at: at, dur: d * 0.3, gain: gain * 0.15, attack: 0.003 });
    }

    // The iOS family of sounds is built from three things: glass (a pure
    // high sine with an inharmonic overtone that dies quickly), wood (the
    // keyboard "tock", a short filtered knock) and air (soft noise sweeps).
    function glass(freq, at, gain, dur) {
        const d = dur || 0.35;
        tone({ freq: freq, at: at, dur: d, gain: gain, attack: 0.002 });
        tone({ freq: freq * 2.76, at: at, dur: d * 0.35, gain: gain * 0.3, attack: 0.002 });
    }

    function tock(freq, at, gain) {
        noise({ freq: freq * 1.6, at: at, dur: 0.02, gain: gain * 0.9, q: 2.5 });
        tone({ freq: [freq, freq * 0.8], at: at, dur: 0.025, gain: gain, type: 'sine', attack: 0.001 });
    }

    // A low haptic-style thump, for things that should feel heavy.
    function thump(at, gain) {
        tone({ freq: [190, 90], at: at, dur: 0.09, gain: gain, type: 'sine', attack: 0.002 });
    }

    // Pitch alternates a little between repeats so identical sounds in a
    // row do not read as a machine gun.
    let flip = 0;
    function drift() { flip = (flip + 1) % 3; return 1 + (flip - 1) * 0.025; }

    // ---- the sounds ---------------------------------------------------

    const SOUNDS = {
        // the keyboard "tock" for a button press
        click: function () { tock(1250 * drift(), 0, 0.2); },
        // "Tink": a small, bright glass tap
        tap: function () { glass(1976 * drift(), 0, 0.14, 0.16); },
        // primary action: Tink then a higher glass note, like a submit
        press: function () {
            glass(1568, 0, 0.17, 0.2);
            glass(2349, 0.07, 0.16, 0.3);
        },
        // moving around the app: the lightest key tick
        nav: function () { tock(1050 * drift(), 0, 0.15); },
        // tabs and segmented controls: the picker "tick"
        tab: function () { tock(1500 * drift(), 0, 0.17); glass(2637, 0, 0.05, 0.08); },
        // switches: a firm tick, higher when on, lower when off
        on: function () { tock(1700, 0, 0.2); glass(2093, 0.015, 0.1, 0.12); },
        off: function () { tock(1050, 0, 0.2); },
        // iOS has no hover sound; kept for completeness but silent
        hover: function () {},
        // the keyboard key click
        key: function () { tock(1400 * (0.94 + Math.random() * 0.12), 0, 0.075); },
        // menus and popovers
        pop: function () { tone({ freq: [520, 880], dur: 0.07, gain: 0.2, attack: 0.003 }); },
        popClose: function () { tone({ freq: [760, 440], dur: 0.06, gain: 0.14, attack: 0.003 }); },
        // picking from a list
        select: function () { glass(1760, 0, 0.13, 0.14); },
        // sheets and modals presenting and dismissing
        open: function () {
            noise({ freq: [350, 2200], dur: 0.2, gain: 0.1, q: 0.6 });
            glass(1318.5, 0.06, 0.1, 0.25);
        },
        close: function () {
            noise({ freq: [2200, 350], dur: 0.16, gain: 0.08, q: 0.6 });
        },
        // Messages "sent": a rising whoosh
        send: function () {
            noise({ freq: [500, 4200], dur: 0.3, gain: 0.14, q: 0.5, type: 'bandpass' });
            tone({ freq: [500, 1200], dur: 0.2, gain: 0.06, attack: 0.02 });
        },
        arrive: function () { noise({ freq: [2200, 700], dur: 0.1, gain: 0.05, q: 0.6 }); },
        // Apple Pay style: two clean rising glass notes
        success: function () {
            glass(1318.5, 0, 0.2, 0.4);
            glass(1976, 0.11, 0.2, 0.7);
        },
        // payments: the same ding, a touch richer
        cashier: function () {
            glass(1568, 0, 0.2, 0.4);
            glass(2093, 0.1, 0.2, 0.5);
            glass(3136, 0.2, 0.14, 0.9);
        },
        // Face ID miss: two quick low taps
        error: function () {
            thump(0, 0.36);
            thump(0.13, 0.36);
            tone({ freq: 220, at: 0.02, dur: 0.18, type: 'triangle', gain: 0.07, filter: { freq: 700 } });
        },
        warning: function () { glass(1175, 0, 0.16, 0.2); glass(1175, 0.14, 0.16, 0.3); },
        // Tri-tone: three glass notes stepping up
        notify: function () {
            glass(1318.5, 0, 0.2, 0.45);
            glass(1568, 0.13, 0.2, 0.45);
            glass(2093, 0.26, 0.22, 0.9);
        },
        // destructive confirms: a heavy thump
        danger: function () { thump(0, 0.5); tock(500, 0.01, 0.12); },
        invalid: function () { thump(0, 0.22); },
        // scanner: two bright ticks
        scanOk: function () { glass(2349, 0, 0.15, 0.12); glass(3136, 0.09, 0.15, 0.2); },
        scanBad: function () { thump(0, 0.34); thump(0.14, 0.34); },
        // theme: unlock-style tock then a glass note up or down
        themeLight: function () { tock(1300, 0, 0.18); glass(2093, 0.05, 0.12, 0.3); },
        themeDark: function () { tock(900, 0, 0.18); glass(1397, 0.05, 0.12, 0.35); },
        download: function () { tone({ freq: [400, 1100], dur: 0.12, gain: 0.1, attack: 0.01 }); glass(2349, 0.1, 0.16, 0.5); },
        // ---- the angel ----
        boop: function () {
            tone({ freq: [380, 820], dur: 0.08, gain: 0.26, attack: 0.003 });
            tone({ freq: [820, 480], at: 0.08, dur: 0.11, gain: 0.2, attack: 0.003 });
        },
        heart: function () { glass(1568, 0, 0.14, 0.4); glass(2093, 0.09, 0.14, 0.5); },
        sparkle: function () {
            [2093, 2637, 3136, 3520].forEach(function (f, i) { glass(f, i * 0.05, 0.08, 0.2); });
        },
        surprise: function () { tone({ freq: [440, 1200], dur: 0.1, gain: 0.2, attack: 0.004 }); },
        dizzy: function () {
            for (let i = 0; i < 6; i++) {
                tone({ freq: [560 + (i % 2) * 280, 420 + (i % 2) * 280], at: i * 0.07, dur: 0.08, gain: 0.12, attack: 0.004 });
            }
        },
        yawn: function () { tone({ freq: [420, 210], dur: 0.45, gain: 0.12, attack: 0.08 }); },
        chime: function () { glass(1760, 0, 0.08, 0.3); }
    };

    // ---- public API ---------------------------------------------------

    // Sounds that fire this often get squashed into one: a burst of ten
    // table rows hovering past is one tick, not ten.
    const lastPlayed = {};
    const MIN_GAP = {
        key: 38, tab: 40, click: 30, tap: 30, nav: 60, arrive: 400,
        select: 40, pop: 60, popClose: 60, invalid: 250, notify: 300
    };

    function play(name) {
        if (!enabled) return;
        const fn = SOUNDS[name];
        if (!fn) return;
        const now = performance.now();
        const gap = MIN_GAP[name] || 0;
        if (gap && now - (lastPlayed[name] || 0) < gap) return;
        lastPlayed[name] = now;
        const c = context();
        if (!c) return;
        if (c.state === 'suspended') {
            // No gesture yet: queue nothing, rather than a burst of stale
            // sounds the moment the first click unlocks audio.
            c.resume();
            if (c.state !== 'running') return;
        }
        try { fn(); } catch (e) { /* audio must never break the page */ }
    }

    function setEnabled(on) {
        enabled = !!on;
        try { localStorage.setItem(STORE_KEY, enabled ? '1' : '0'); } catch (e) { /* storage blocked */ }
        syncToggles();
        if (enabled) { unlock(); play('tap'); }
    }

    function syncToggles() {
        document.querySelectorAll('[data-sfx-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-pressed', enabled ? 'true' : 'false');
            btn.setAttribute('aria-label', enabled ? 'Sound effects on. Click to mute.' : 'Sound effects off. Click to unmute.');
            btn.title = enabled ? 'Sound on' : 'Sound off';
            btn.classList.toggle('is-muted', !enabled);
        });
    }

    // Toasts and flashes appear as new nodes, server-rendered or built by
    // showToast(). They are the app's way of reporting what happened, so
    // they are the main thing that makes a sound.
    function watchFlashes() {
        const seen = new WeakSet();
        const obs = new MutationObserver(function (records) {
            records.forEach(function (rec) {
                rec.addedNodes.forEach(function (node) {
                    if (!(node instanceof Element) || seen.has(node)) return;
                    const flash = node.matches('.flash') ? node : node.querySelector && node.querySelector('.flash');
                    if (flash) { seen.add(node); playFlash(flash); }
                });
            });
        });
        obs.observe(document.body, { subtree: true, childList: true });
    }

    // Toasts: success, error, warning or plain, with the till's ring for
    // anything about money changing hands.
    function playFlash(el) {
        if (el.dataset.sfx === 'off') return;
        const text = (el.textContent || '').toLowerCase();
        if (el.classList.contains('flash-error') || el.classList.contains('flash-danger')) return play('error');
        if (el.classList.contains('flash-warning')) return play('warning');
        if (el.classList.contains('flash-success')) {
            if (/sale|paid|payment|receipt|credit|recorded/.test(text)) return play('cashier');
            if (/delet|remov|void/.test(text)) return play('danger');
            return play('success');
        }
        play('notify');
    }

    window.Sfx = {
        play: play,
        isOn: function () { return enabled; },
        setEnabled: setEnabled,
        toggle: function () { setEnabled(!enabled); },
        names: Object.keys(SOUNDS)
    };

    // The speaker button.
    document.addEventListener('click', function (e) {
        const btn = e.target instanceof Element && e.target.closest('[data-sfx-toggle]');
        if (!btn) return;
        e.preventDefault();
        window.Sfx.toggle();
    }, true);

    function init() {
        syncToggles();
        watchFlashes();
        // Flashes already on the page when it loads: the sign-in failure
        // buzz, the signed-out chime. A beat after load so it reads as a
        // response, and only if audio is already unlocked.
        document.querySelectorAll('.flashes .flash').forEach(function (flash, i) {
            setTimeout(function () { playFlash(flash); }, 350 + i * 220);
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
