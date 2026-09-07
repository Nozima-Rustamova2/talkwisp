# Visual direction — Telegram-fluent utility

Chosen 2 Sep 2026 from a two-option comparison (`Direction Options.dc.html`,
options 1a / 1b). **1B won.** This supersedes the Industry design system for all
new work; Industry's tokens are no longer the reference.

## Why this direction

The interface idiom a Tashkent SMB owner already reads fluently, every day, is
Telegram: white surfaces on a cool ground, one confident blue, rounded
soft-shadowed cards instead of frames, generous touch targets, no chrome that
isn't load-bearing. We take its **plainness**, not its brand.

Explicitly not ManyChat's look — stock photography, checkerboard graphics, a
green CTA competing with a green banner, cards linking to a blog and YouTube.
Newer is not better. What differentiates Talkwisp is honesty about what the
agent knows and doesn't, and that is served by restraint rather than decoration.

Accepted cost: this direction has less personality than Industry's registration
marks. The personality budget goes into copy instead, which is already carrying
it (*"Nothing was added. Your other files kept going."*).

## Why Industry was dropped

It read wrong twice — the intent screen felt like a form to fill in, the sign-in
screen read as dated. Diagnosis, in order of size:

1. **No depth, no color hierarchy.** One plane, one weight, hairlines
   everywhere. That reads as a wireframe *of* a product rather than a product.
   The largest problem, and not fixable with tokens.
2. **Uniform framing.** Every container a bordered, corner-marked object, so
   nothing recedes and nothing advances. Rounding twelve frames yields twelve
   rounded frames; the fix is fewer frames.
3. **The accent didn't reach contrast.** #5980a6 fails 4.5:1 as text at body
   sizes — caught as a live defect on the sign-in screen, not a taste call.
4. **Grey input fills with hard borders** have almost no visible edge on a
   mid-range Android in daylight.
5. **Barlow.** A mid-2010s grotesque, and its Cyrillic is weaker than its
   Latin — so Russian rows sat at a different optical weight from Uzbek ones in
   the same table. A trilingual problem, not a taste one. Condensed *display*
   sizes also read as industrial catalog, which is an inspection signal.

Key insight that settled it: **what makes the ledger screens work is density and
alignment, not blueprint decoration.** Tight rows, a consistent grid, source
text physically beside the fact, one accented action per row — 1B does all of
that, so the ledger register loses nothing. Industry, conversely, cannot do
reassurance: condensed display type and registration marks are inspection
signals at any radius. Shipping both registers in two visual languages would be
worse than either.

## One register, not two

The register split recorded earlier is **withdrawn**. There is one visual
language now; the difference between onboarding and the ledger screens is
*density and chrome*, not styling:

- **Momentum screens** (auth, onboarding): one elevated card on the ground, one
  question, large heading, generous padding, no rail, no step component.
- **Ledger screens** (dashboard, review, knowledge, gaps, conversations): the
  same surfaces at higher density — tight rows, hairline row rules inside one
  card, a consistent grid, one accented action per row.

## Tokens

### Color

| Role | Value |
| --- | --- |
| Ground | `#f4f6f9` |
| Surface | `#ffffff` |
| Surface, subtle (source panels, table head) | `#f7f9fc` / `#fafbfd` |
| Text | `#14181f` |
| Text, secondary | `#3a4250` |
| Text, muted | `#5c6675` |
| Text, faint (labels, meta) | `#646c78` |
| Border | `#e4e8ee` (inputs `#dfe4ec`, row rules `#eef1f5`) |
| Accent | `#1f6feb` |
| Accent, pressed / link text | `#1657c0` |
| Accent tint (highlighted rows) | `#f5f9ff`, border `#d5e2f7` |
| Caution (low confidence) | bg `#fdf0dc`, text `#8a5a12` |

One accent. No second hue, no decorative color. Caution tint is for
low-confidence facts only — it is information, not decoration.

### Type

**Manrope**, one family, loaded 400/500/600/700/800. Drawn with Cyrillic, so
Cyrillic and Latin sit at matched optical weight in the same row — the reason it
beats a Latin-first face here. One variable file is also one request on a slow
connection.

- Display / page heading: 28–30px, 800, `letter-spacing: -0.02em`
- Section heading: 20–22px, 700
- Body: 15–16px, 400, `line-height: 1.45–1.55`
- Emphasis in body / button labels: 600–700
- Meta and labels: 12–14px. **Sentence case, medium weight — never
  letterspaced uppercase micro-labels** (a 2015 dashboard tic, and it wrecks
  Cyrillic).

**Contrast floor, non-negotiable — and it is measured against the SURFACE THE
TEXT SITS ON, not against white.** Meta text in this product mostly sits on the
ground (`#f4f6f9`) or inside a source panel (`#f7f9fc`), never on pure white,
so a token validated against white is a token that fails in use. The faint token
is `#646c78` — 4.66:1 on the ground, 4.79:1 on `#f7f9fc`, 5.3:1 on white.

Retired, do not reintroduce: `#8a93a1` (3.10:1 on white) and `#6b7480`
(4.37:1 on the ground — cleared white only). Both shipped and both failed
review. Nothing lighter than `#646c78` carries words on any surface; light
tints are for rules, dividers and icon strokes only. When in doubt use
`#5c6675` (5.4:1 on the ground).

**Text inputs use `defaultValue`, never `value`.** A `value` prop with no
`onChange` is a React controlled input: every keystroke and paste is
discarded, so the field looks broken. This shipped on the step 5 token field —
the single most important input in onboarding — and on step 3's inline fact
editor. Seeded states (a rejected token kept for correction, a fact pre-filled
for editing) are exactly the cases that need `defaultValue`: they show a
starting value **and** allow typing. Only reach for `value` when the logic
class also supplies `onChange` and reads the value back out of state.

**Touch floor, non-negotiable — and it applies to LINKS, not just buttons.**
A **standalone link** is a control: it sits in its own row or as the only thing
in a block (*Save and finish later*, *Skip anyway →*, *Try again*, *Use a
different email*, row actions like *Edit* / *Retry* / *Review 42 →*). Every one
of those clears 44px via
`min-height: 44px; display: inline-flex; align-items: center` — never via
font-size or margin. An **inline link** inside a sentence (*Terms*, *Privacy
Policy*, *Sign in.*) is prose, not a control, and is left alone; forcing it to
44px would break the line box.

Every control clears 44px in height, including small ones like the language
switcher — that switcher is the first thing a
Russian- or Uzbek-speaking owner has to hit on the first screen. Pill controls
get `min-height: 44px` with `display: inline-flex; align-items: center`
rather than vertical padding, so the 999px radius survives.

### Shape and depth

- Radius: 16px cards, 12px buttons and inputs, 10px inner panels and row
  actions, 999px pills.
- **No borders on cards.** Depth comes from elevation:
  `0 1px 2px rgba(20,24,31,0.06), 0 12px 28px -18px rgba(20,24,31,0.24)`.
  Borders are for inputs and for buttons that must read as secondary.
- Primary button: solid `#1f6feb`, radius 12, `padding: 15px 18px`, weight 700,
  glow `0 6px 16px -8px rgba(31,111,235,0.9)`.
- Row rules inside a card: 1px `#eef1f5`. This is the density device.

### Layout and touch

- Inputs: white fill, 1px `#dfe4ec`, radius 12, `padding: 14px 15px`, 16px text
  (prevents iOS zoom, legible on cheap Android).
- Minimum touch target 44px. Full-width primary buttons on phone.
- Body text never below 15px; row meta never below 12px.

## Hard constraints this direction must keep meeting

- **Russian runs ~30% longer.** No fixed-width labels; buttons size to content
  or go full-width; rows wrap rather than truncate.
- **Mixed Cyrillic and Latin in one list** — same face, same weight, same size.
- **360px, mid-range Android.** Three-column ledger rows must stack: source
  text under the fact, one full-width Confirm, two quiet links. A 360px probe of
  this row is in `Direction Options.dc.html`.
- No heavy animation, no spinners. Buttons state what they're doing in their own
  labels.

## Retrofit debt

**Cleared 3 Sep.** All three built-on-Industry screens were retrofitted:
`Sign In.dc.html` (2 Sep), `Add Knowledge.dc.html` (2 Sep, same pass as
onboarding step 2 so the shared input machinery couldn't drift), and
`Dashboard.dc.html` (3 Sep, last). No file loads the Industry stylesheet or
bundle any more.

Left deliberately on Industry as records, not product: `Onboarding
Intent.dc.html` (cut screen) and `Direction Options.dc.html` (frozen
comparison of both directions).

Both pending files carry a comment stamp at the top saying so. Migration status
also lives in `CLAUDE.md`, which is the authoritative stop-sign against the
detached Industry attachment.

Retrofitting is mechanical (tokens and shape, not structure or copy) but not
free. Mobile breakpoints are still outstanding on all three and should be done
in the same pass rather than twice.

Two Industry defects worked around inline on those screens, no longer relevant
once retrofitted: `.btn` fixed height with `white-space: normal` (clipped long
labels), and `.btn-ghost` supplying base-accent text (inverted weight hierarchy
and failed 4.5:1 on tertiary buttons).
