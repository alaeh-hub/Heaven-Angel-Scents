# Design: Heaven & Angel back office (admin and branch)

The locked system for every signed-in screen: `templates/admin/*`, `templates/branch/*`, `templates/ai/*`, `templates/scan/*`, `login.html`, `change_password.html`, `errors/error.html`. The public site (`public-site/`) has its own system and is out of scope. Every page reads this first and shares it. Change it on purpose; the file is the rule.

## System
- Genre: modern-minimal. Scope: app screen (a tool someone works in, not a page someone reads).
- Theme: brand, black and gold. The brand is the logo (`static/img/*`), `--brand-black` and `--brand-gold`.
- Axes: light and dark, both derived / grotesk-sans / warm gold. Posture: Restrained. Dark is the default theme, light is opt-in (`html[data-theme]`).
- Signature: the stock ledger. Tables are set as ledgers: sentence-case heads, horizontal hairlines only, a mono SKU column, right-aligned tabular figures, a stock bar that fills.

## Tokens
`static/css/tokens.css` is the source of truth. Roles: `--color-paper`, `-paper-2`, `-paper-3`, `--color-ink`, `-ink-2`, `--color-muted`, `--color-rule`, `--color-edge` (a field's 3:1 edge), `--color-accent` (gold fill), `--color-accent-text` (gold used as text), `--color-accent-soft`, `--color-accent-ink` (text ON a gold fill), `--color-focus`, `--color-error`, `--color-success`, `--color-warning`.
The older names templates already read stay valid and derive from the roles: `--bg --surface --surface-2 --ink --ink-soft --ink-faint --border --border-strong --field --accent --accent-ink --accent-soft --red --success --warning --danger` and their `-soft` / `-border` variants. Use these in templates; never type a colour or a font.
- Fonts: `var(--font)` for everything (Geist), `var(--font-mono)` only for SKUs, receipt and request numbers, codes. Never mono on a label, a table head or a badge.
- Spacing: `var(--space-3xs … 4xl)` (2 4 8 12 16 24 40 64 96 144 px, in rem). Never a raw padding, margin or gap length. `python <scratchpad>/snap_space.py file.html` snaps a file.
- Radii: `--radius-sm` 6 (chips, badges, small), `--radius` 8 (controls: buttons, inputs, selects), `--radius-lg` 12 (cards, panels, dialogs). Pills (`999px`) only for a toggle track or an avatar. One radius per nesting level.
- Surfaces: page = `--bg`. A card is `--surface` plus a 1px `--border`, no shadow. A well is `--surface-2`, no border. A card never sits in a card; a bordered box never sits in a well. Only overlays (menus, dialogs, toasts) take `--shadow-md` / `--shadow-lg`. No gradients, no glass blur, no glow.
- Colour: gold marks the primary action, the current selection and state, nothing else. Status uses `--red`, `--success`, `--warning` with a word or an icon beside it, never colour alone.

## Type
- Scale in rem: 0.75 (labels, meta), 0.875 (table cells, secondary), 1 (body, controls), 1.125 (card titles), 1.25 (page title), 1.5 (a lead figure). Weights 400, 500, 600 only.
- Labels, table heads, tags, chips, nav links, badges: body face, sentence case, no letter-spacing, 12px or more. No `text-transform: uppercase` anywhere except the wordmark.
- Figures that compare: `font-variant-numeric: tabular-nums`, right-aligned in tables.
- No eyebrow above a heading. No middle-dot-joined meta strings (`A · B · C`). No arrow glued to link text (`View all →`); the label is a verb or a noun, the link already goes somewhere. No em or en dashes in copy: a comma, colon, full stop or parentheses. Curly quotes in prose.

## Controls
- Primary: gold fill (`--accent`), ink `--gold-contrast`, `--radius`, 36px high (2.25rem), 44px hit target on coarse pointers. One primary per view (a dialog counts as its own view).
- Secondary: `--surface-2` fill, no outline. Tertiary: text with a hairline underline on hover. Destructive: secondary style with `--red-ink` text, never a red fill unless deleting is the screen's one action.
- Inputs: 36px high, 1px `--field-edge` border that never changes width, `--field` fill, focus is an `outline` (global `:focus-visible`), disabled is `opacity: .55` plus `cursor: not-allowed` plus the `disabled` attribute.
- All eight states on every control: default, hover (one signal), focus-visible (instant), active, disabled, loading (label stays, width stays), error (border plus message plus `aria-invalid`), success (the result itself, no toast).

## Screens (app scope)
- No hero, no fold budget, no section rhythm. Density is the design: tables before cards, one container recipe, 32 to 40px controls.
- Stat rows are figures with labels, set side by side with vertical hairlines, not four equal cards, no icon tile, no gradient, no trend arrow nobody measured. A figure the page already shows stays.
- Every region that loads data has four states: loading (a skeleton in the shape of what is coming), empty (what belongs here and the one action that fills it), error (what broke and what to do), full. Build the ones the screen has logic for, list the rest for the owner.
- State lives in the URL (filters, tabs, pagination). A destructive action is undone or confirmed (`confirm-dialog`).
- Motion is state, never decoration: `--dur-micro`, `--dur-short`, `transform` and `opacity` only, `--ease-out`, no overshoot. Every keyframe has a `prefers-reduced-motion: reduce` fallback. Content is visible with scripting off; hidden states sit under `.js`.
- Decorative `<svg>` and `<canvas>` carry `aria-hidden="true"` or a label. Every `<button>` has a `type`.

## What a redesign never touches
Routes, `url_for` targets, form `name`s and `id`s, `data-*` hooks, handlers, `{% %}` logic, JS-queried selectors. `.hallmark/before.json` records them; `sloplint --kept` checks them. The Jinja templates are the markup; edit inside them.

## Interface styles
`html[data-style]` in `themes.css` re-declares tokens and a few surface rules per style (14 of them, picked in Settings). Each ships a light and a dark palette and obeys the same floor: one anchor hue, no gradient past a single hue, no glow, no gradient text, every colour through a token declared in that style's block.

## Exports
`tokens.css` in this project. Other formats on request.
