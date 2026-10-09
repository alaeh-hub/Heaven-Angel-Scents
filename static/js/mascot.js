// The mascot: a sprite-sheet character whose head turns toward the
// pointer and who reacts when clicked.
//
// This is a vanilla port of the `page-mascot` React component. The
// partner portal (public-site/) could have used the npm package
// directly, but the signed-in app is Jinja + plain scripts with no
// React anywhere, and pulling a framework in for one ornament isn't a
// trade worth making. The behaviour below is the same: nine head
// directions and nine expressions, each sheet a 3x3 grid, swapped by
// moving `background-position` rather than by running an animation —
// so a mascot sitting idle costs nothing per frame.
//
// Both sheets live in static/mascots/. They are drawn as a matched
// pair: the body is the same pixels in the same place in all eighteen
// cells, which is what lets the two swap on click without the
// character appearing to jump.
(function () {
    'use strict';

    // Cell order within each sheet, left to right, top to bottom.
    const DIRECTIONS = ['up-left', 'up', 'up-right', 'left', 'center', 'right', 'down-left', 'down', 'down-right'];
    const REACTIONS = ['blink', 'heart', 'sparkle', 'surprised', 'wink', 'bashful', 'sleepy', 'dizzy', 'delighted'];

    // Clockwise from the right, matching atan2 with y pointing down.
    const CLOCKWISE = ['right', 'down-right', 'down', 'down-left', 'left', 'up-left', 'up', 'up-right'];
    const SECTOR = (Math.PI * 2) / CLOCKWISE.length;
    // Holding a sector slightly past its own edge stops the head
    // flickering between two cells when the pointer sits on a boundary.
    const HYSTERESIS = 0.12;
    // Inside this many pixels of the mascot's centre there's no
    // meaningful direction left to point, so it looks straight out.
    const DEAD_ZONE = 70;
    // How far (as a fraction of the mascot's size) the whole figure leans
    // toward the pointer, and how quickly it eases there. The head is
    // nine fixed drawings, so the lean is what makes turning feel fluid.
    const LEAN = 0.035;
    const LEAN_EASE = 0.16;
    // On touch there is no pointer to follow, so a tap is looked at for
    // this long and then forgotten.
    const TAP_LOOK_MS = 1800;

    const BOOP_PAYOFF = 120;
    const BOOP_END = 560;
    const SQUASH_MS = 420;
    // Four clicks inside DIZZY_WINDOW and it gives up and spins.
    const DIZZY_AFTER = 4;
    const DIZZY_WINDOW = 1600;
    const DIZZY_END = 1100;

    // `tilt` is a few degrees away from where the mascot was poked, so a
    // click on its left cheek rocks it right instead of squashing it
    // identically every time.
    function squashFrames(tilt) {
        return [
            { transform: 'rotate(0deg) scale(1, 1)', easing: 'ease-in' },
            { transform: 'rotate(' + (tilt * 0.6) + 'deg) scale(1.10, 0.86)', offset: 0.18, easing: 'ease-out' },
            { transform: 'rotate(' + (-tilt) + 'deg) scale(0.95, 1.08)', offset: 0.45, easing: 'ease-in-out' },
            { transform: 'rotate(' + (tilt * 0.35) + 'deg) scale(1.03, 0.97)', offset: 0.72, easing: 'ease-in-out' },
            { transform: 'rotate(0deg) scale(1, 1)' }
        ];
    }

    // Faces for the first three pokes, each a flinch then a payoff; the
    // fourth in a row tips it over into dizzy.
    const STAGES = [['blink', 'heart'], ['blink', 'sparkle'], ['surprised', 'delighted']];

    const BUBBLE_MS = 4800;

    const SHEETS = {
        directions: '/static/mascots/angel-directions.webp',
        reactions: '/static/mascots/angel-reactions.webp'
    };

    const FINE_POINTER = window.matchMedia('(hover: hover) and (pointer: fine)');
    const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

    // Every mounted mascot on the page. One pointermove listener drives
    // all of them: binding per instance would mean Halo's avatar and its
    // greeting each doing their own hit-testing on every mouse move.
    const live = new Set();

    // background-size 300% makes each cell a clean 0/50/100% step on both axes.
    function cellPosition(index) {
        return ((index % 3) * 50) + '% ' + (Math.floor(index / 3) * 50) + '%';
    }

    function wrap(angle) {
        return Math.atan2(Math.sin(angle), Math.cos(angle));
    }

    /** "Good morning" / "Good afternoon" / "Good evening" for right now. */
    function timeGreeting() {
        const hour = new Date().getHours();
        if (hour < 12) return 'Good morning';
        if (hour < 18) return 'Good afternoon';
        return 'Good evening';
    }

    function Mascot(host, options) {
        const opts = options || {};
        const size = opts.size || 0;

        host.classList.add('mascot-host');
        if (size) {
            host.style.setProperty('--mascot-size', size + 'px');
            host.classList.add('mascot-host-sized');
        }

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mascot';
        // It's an ornament, so it stays out of the tab order: a keyboard
        // user tabbing through the sign-in form or Halo's panel should
        // not have to pass through a decoration to reach the next
        // control. Pointer users can still click it.
        button.tabIndex = -1;
        button.setAttribute('aria-hidden', 'true');

        const squash = document.createElement('span');
        squash.className = 'mascot-squash';

        const dirLayer = document.createElement('span');
        dirLayer.className = 'mascot-layer';
        dirLayer.style.backgroundImage = 'url("' + (opts.directions || SHEETS.directions) + '")';
        // Explicitly centred: with no background-position of its own it
        // would sit at 0% 0%, which is the up-left cell — so a mascot
        // would start out looking away until the pointer first moved.
        dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));

        // Mounted from the start, not created on the first click, so the
        // reactions sheet is already fetched by the time anyone pokes it.
        const reactLayer = document.createElement('span');
        reactLayer.className = 'mascot-layer mascot-layer-react';
        reactLayer.style.backgroundImage = 'url("' + (opts.reactions || SHEETS.reactions) + '")';
        reactLayer.style.backgroundPosition = cellPosition(0);

        squash.appendChild(dirLayer);
        squash.appendChild(reactLayer);
        button.appendChild(squash);
        host.appendChild(button);

        let bubble = null;
        let bubbleTimer = 0;

        const timers = [];
        const boops = { count: 0, at: 0 };
        let sector = -1;

        this.host = host;
        this.button = button;

        /** Point the head at `pointer`, or straight out when it's close. */
        let leanX = 0, leanY = 0, targetX = 0, targetY = 0, leanFrame = 0;

        function stepLean() {
            leanFrame = 0;
            leanX += (targetX - leanX) * LEAN_EASE;
            leanY += (targetY - leanY) * LEAN_EASE;
            if (Math.abs(targetX - leanX) < 0.02 && Math.abs(targetY - leanY) < 0.02) {
                leanX = targetX;
                leanY = targetY;
            } else {
                leanFrame = requestAnimationFrame(stepLean);
            }
            button.style.translate = leanX.toFixed(2) + 'px ' + leanY.toFixed(2) + 'px';
            button.style.rotate = (leanX * 0.5).toFixed(2) + 'deg';
        }

        function leanTo(x, y) {
            if (REDUCED_MOTION.matches) return;
            targetX = x;
            targetY = y;
            if (!leanFrame) leanFrame = requestAnimationFrame(stepLean);
        }

        this.aim = function (pointer) {
            // A mascot in a closed panel has a zero-size box; aiming
            // from that gives a meaningless angle.
            const box = button.getBoundingClientRect();
            if (!box.width || !box.height) return;

            // Pointer left the window (or a tap faded): look straight out.
            if (!pointer) {
                leanTo(0, 0);
                if (sector === -1) return;
                sector = -1;
                dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));
                return;
            }

            const dx = pointer.x - (box.left + box.width / 2);
            const dy = pointer.y - (box.top + box.height / 2);

            const distance = Math.hypot(dx, dy);
            // Lean grows with distance and tops out, so a far-off pointer
            // nudges rather than drags.
            const pull = Math.min(1, distance / 240) * LEAN * box.width;
            leanTo(distance ? dx / distance * pull : 0, distance ? dy / distance * pull : 0);

            if (distance < Math.max(36, Math.min(DEAD_ZONE, box.width * 0.6))) {
                leanTo(0, 0);
                if (sector === -1) return;
                sector = -1;
                dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));
                return;
            }

            const angle = Math.atan2(dy, dx);
            // Hold the current sector until the pointer is well past its edge.
            if (sector !== -1 && Math.abs(wrap(angle - sector * SECTOR)) < SECTOR / 2 + HYSTERESIS) return;

            sector = (Math.round(angle / SECTOR) + CLOCKWISE.length) % CLOCKWISE.length;
            dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf(CLOCKWISE[sector]));
        };

        // An expression held until released, for as long as something on
        // the page calls for it (eyes shut while a password is typed).
        // Timed reactions fall back to it rather than to the pointer.
        let held = null;

        function setReaction(name) {
            if (name === null && held) name = held;
            if (name === null) {
                host.classList.remove('is-reacting');
                return;
            }
            reactLayer.style.backgroundPosition = cellPosition(REACTIONS.indexOf(name));
            host.classList.add('is-reacting');
        }

        /** Put `text` in a speech bubble beside the mascot for a moment. */
        this.say = function (text, ms) {
            if (!text) return;
            if (!bubble) {
                bubble = document.createElement('span');
                bubble.className = 'mascot-bubble';
                host.appendChild(bubble);
            }
            window.clearTimeout(bubbleTimer);
            bubble.textContent = text;
            // Two frames, not one: the element has to be laid out in its
            // hidden state before the class that transitions it in lands,
            // or the browser coalesces both and nothing animates.
            bubble.classList.remove('is-shown');
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    bubble.classList.add('is-shown');
                });
            });
            const hold = ms === undefined ? BUBBLE_MS : ms;
            if (hold > 0) {
                bubbleTimer = window.setTimeout(function () {
                    bubble.classList.remove('is-shown');
                }, hold);
            }
        };

        /**
         * Hold one named expression for `ms`, then drop back to
         * following the pointer. Lets a page react to something that
         * isn't a click — a wrong password, a field left empty — with
         * the face rather than only with words.
         */
        this.react = function (name, ms) {
            if (REACTIONS.indexOf(name) === -1) return;
            timers.forEach(window.clearTimeout);
            timers.length = 0;
            setReaction(name);
            timers.push(window.setTimeout(function () { setReaction(null); }, ms || 1400));
        };

        /** Keep `name` showing until `cover(null)`. */
        this.cover = function (name) {
            if (name !== null && REACTIONS.indexOf(name) === -1) return;
            timers.forEach(window.clearTimeout);
            timers.length = 0;
            held = name;
            setReaction(name);
        };

        /** Dismiss the speech bubble now. */
        this.hush = function () {
            window.clearTimeout(bubbleTimer);
            if (bubble) bubble.classList.remove('is-shown');
        };

        this.boop = function (originX) {
            timers.forEach(window.clearTimeout);
            timers.length = 0;

            const later = function (ms, next) {
                timers.push(window.setTimeout(function () { setReaction(next); }, ms));
            };

            const now = Date.now();
            boops.count = now - boops.at < DIZZY_WINDOW ? boops.count + 1 : 1;
            boops.at = now;
            const count = boops.count;
            const dizzy = count >= DIZZY_AFTER;

            if (dizzy) {
                boops.count = 0;
                setReaction('dizzy');
                later(DIZZY_END, null);
            } else {
                const stage = STAGES[count - 1];
                setReaction(stage[0]);
                later(BOOP_PAYOFF, stage[1]);
                later(BOOP_END, null);
            }

            // Lets the page answer with a line that fits how many pokes
            // this is, instead of a random one.
            host.dispatchEvent(new CustomEvent('mascot:boop', { detail: { count: count, dizzy: dizzy } }));

            if (REDUCED_MOTION.matches) return;
            const box = button.getBoundingClientRect();
            // Away from the poke; a keyboard or scripted boop has no
            // origin, so it rocks to the right.
            const side = originX === undefined || !box.width ? 1 : (originX < box.left + box.width / 2 ? 1 : -1);
            const tilt = side * (dizzy ? 9 : 4 + count);
            // Per-keyframe easing with the effect itself linear: an easing
            // on the effect would reinterpret every offset and front-load
            // the whole bounce.
            if (squash.animate) squash.animate(squashFrames(tilt), { duration: dizzy ? SQUASH_MS * 1.6 : SQUASH_MS, easing: 'linear' });
        };

        // Pressed-in feedback the instant the pointer lands, before the
        // click completes, so it feels like it is being pushed.
        const release = function () { host.classList.remove('is-pressed'); };
        button.addEventListener('pointerdown', function () { host.classList.add('is-pressed'); });
        ['pointerup', 'pointerleave', 'pointercancel'].forEach(function (name) {
            button.addEventListener(name, release);
        });

        const self = this;
        button.addEventListener('click', function (event) {
            // Halo's avatar sits in a panel header beside real controls
            // — a boop shouldn't also trip whatever it's nested in.
            event.preventDefault();
            event.stopPropagation();
            self.boop(event.clientX);
        });

        this.destroy = function () {
            timers.forEach(window.clearTimeout);
            window.clearTimeout(bubbleTimer);
            cancelAnimationFrame(leanFrame);
            live.delete(self);
            host.classList.remove('mascot-host', 'mascot-host-sized', 'is-reacting');
            button.remove();
            if (bubble) bubble.remove();
            delete host.dataset.mascotMounted;
        };

        live.add(this);
    }

    let pointer = null;

    function aimAll() {
        live.forEach(function (mascot) { mascot.aim(pointer); });
    }

    if (FINE_POINTER.matches) {
        window.addEventListener('pointermove', function (event) {
            pointer = { x: event.clientX, y: event.clientY };
            aimAll();
        }, { passive: true });
        // The pointer can sit still while the page moves under it, which
        // changes the angle just as much as moving the pointer does.
        window.addEventListener('scroll', function () { if (pointer) aimAll(); }, { passive: true });
        // Leaving the window leaves the last position behind, which would
        // keep it staring at the edge. Face front instead.
        document.documentElement.addEventListener('mouseleave', function () {
            pointer = null;
            aimAll();
        });
    } else {
        // Touch: look at wherever the last tap landed, briefly.
        let tapTimer = 0;
        window.addEventListener('pointerdown', function (event) {
            pointer = { x: event.clientX, y: event.clientY };
            aimAll();
            window.clearTimeout(tapTimer);
            tapTimer = window.setTimeout(function () {
                pointer = null;
                aimAll();
            }, TAP_LOOK_MS);
        }, { passive: true });
    }

    /**
     * Put a mascot inside `host`. Returns the instance, or the one
     * already there — mounting is idempotent so a re-init after a
     * soft-nav content swap can't stack two mascots in one slot.
     */
    function mount(host, options) {
        if (!host) return null;
        if (host.dataset.mascotMounted) return null;
        host.dataset.mascotMounted = '1';
        return new Mascot(host, options);
    }

    /**
     * Mount every [data-mascot] on the page, reading its options off the
     * element so templates stay declarative:
     *
     *     <div data-mascot data-mascot-size="120" data-mascot-say="auto"></div>
     *
     * `data-mascot-say="auto"` greets by time of day; any other value is
     * said verbatim. `data-mascot-say-name` adds a name to an auto
     * greeting ("Good morning, Ana").
     *
     * `data-mascot-replace` clears whatever the host already contains
     * first. The markup it replaces — Halo's own glyph — is left in the
     * template on purpose: it is what shows if this script fails to load
     * or throws, which is a better panel header than a blank circle.
     */
    function initAll(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-mascot]').forEach(function (host) {
            if (host.dataset.mascotReplace !== undefined && !host.dataset.mascotMounted) {
                host.textContent = '';
            }
            const size = parseInt(host.dataset.mascotSize, 10);
            const mascot = mount(host, { size: size > 0 ? size : 0 });
            if (!mascot) return;

            const say = host.dataset.mascotSay;
            if (!say) return;
            let text = say;
            if (say === 'auto') {
                const name = host.dataset.mascotSayName;
                text = timeGreeting() + (name ? ', ' + name : '');
            }
            // A beat after the page settles, so it reads as the mascot
            // noticing you rather than as part of the page load.
            window.setTimeout(function () { mascot.say(text); }, 600);
        });
    }

    window.Mascot = {
        mount: mount,
        initAll: initAll,
        timeGreeting: timeGreeting,
        sheets: SHEETS,
        /** The instance mounted in `host`, if there is one. */
        get: function (host) {
            let found = null;
            live.forEach(function (mascot) {
                if (mascot.host === host) found = mascot;
            });
            return found;
        }
    };
})();
