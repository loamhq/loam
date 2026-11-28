---
name: loam-inline-visuals
description: Draw inline HTML and SVG visuals inside thread replies — comparisons, charts, diagrams, or compact cards that inherit the owner's active Loam theme and mode. Use when a visual carries what prose cannot.
---

# Inline visuals in replies

Use a visual when it carries what prose cannot — a side-by-side comparison, a quantity or trend, a shape, a tradeoff. Otherwise write prose.

## How to write one

Put a fenced code block with the info string `visual`, optionally followed by a short plain title on the opening line, anywhere in your reply:

````markdown
```visual Short title
<!-- HTML, inline SVG, and optional <style> -->
```
````

Loam draws the block live at that place in your message, between the paragraphs around it, with no added card frame or title bar. Several blocks in one reply are fine. Ordinary ```` ```html ```` and ```` ```svg ```` blocks still display as code.

Give the opening line a short plain title (or start the block with a heading) so surfaces that do not draw visuals — quotes, previews, Files, read-aloud — show a sensible label instead of markup.

## What you get for free

Every visual sits on the reading column's ground and receives the active theme's live tokens (automatically recoloured on light/dark switches, theme changes, and installed plugin themes), plus a low-specificity base style so bare `<table>`, `<h2>`–`<h4>`, `<dl>`, `<figure>`, `<figcaption>`, `<progress>`, `<details>`, and `<svg>` elements already look like Loam. Any `<style>` you write overrides the defaults cleanly.

**Design tokens available as `var(...)`:**

- **Reading surfaces & inks:** `--reading-ground`, `--paper-page`, `--paper-pane`, `--paper-raised`, `--paper-card`, `--paper-hover`, `--paper-highlight`, `--paper-ink`, `--paper-ink-2`, `--paper-ink-3`, `--paper-rule`, `--paper-rule-strong`, `--paper-hover-alpha`
- **Controls & status:** `--latte-foam`, `--latte-coffee`, `--latte-espresso`, `--latte-espresso-ink`, `--signal-crema` (accent), `--success`, `--warning`, `--danger`
- **Chart / series palette** (each also has `-tint` for soft fills): `--persona-slate`, `--persona-olive`, `--persona-rust`, `--persona-plum`, `--persona-teal`, `--persona-rose`, `--persona-gold`, `--persona-blue`, `--persona-taupe`, `--persona-sage`, `--persona-mauve`
- **Type, spacing & radii:** `--font-body`, `--font-display`, `--font-mono`, `--type-caption` (12px), `--type-small` (13px), `--type-body` (15px), `--type-lead` (16px), `--type-title` (24px), `--space-xs` (4px) through `--space-2xl` (32px), `--radius-sm`, `--radius-md`, `--radius-lg`, `--radius-pill`

**Hover tooltips without JavaScript:** put `data-tip="Label: value"` on any HTML or SVG element — or `<title>Label: value</title>` inside an SVG shape — and Loam renders a themed tooltip on hover, focus, or tap. CSS `:hover`, `<details>`, and radio-input CSS toggles also work.

## Limits

- **Sealed:** no `<script>` runs, and there is no network access (no remote images, external fonts, CDNs, or fetches). Keep everything self-contained inline HTML, CSS, and SVG.
- **Phone width:** replies are read at 390 px on phones as well as 760 px on desktop. Prefer `viewBox` on `<svg>` (omit fixed pixel `width` so it scales cleanly) and responsive grids (`grid-template-columns: repeat(auto-fit, minmax(150px, 1fr))`). Bare `<table>` elements scroll horizontally if wide.
- **No forced height:** the visual grows to fit its content cleanly without inner scrollbars.

## Examples

### Side-by-side comparison

````markdown
```visual Pour-over grinders
<style>
  .pick { display: inline-block; padding: 1px 7px; border-radius: var(--radius-pill); background: var(--persona-sage-tint); color: var(--paper-ink); font-size: var(--type-caption); font-weight: 600; }
  .num { text-align: right; white-space: nowrap; }
</style>
<table>
  <thead>
    <tr><th>Grinder</th><th class="num">Price</th><th>Burr</th><th>Best for</th></tr>
  </thead>
  <tbody>
    <tr><td><strong>Fellow Ode Gen 2</strong> <span class="pick">Pick</span></td><td class="num">$345</td><td>64 mm flat</td><td>Clarity, quiet mornings</td></tr>
    <tr><td><strong>Baratza Encore ESP</strong></td><td class="num">$199</td><td>40 mm conical</td><td>Filter + entry espresso</td></tr>
    <tr><td><strong>Comandante C40</strong></td><td class="num">$295</td><td>39 mm hand</td><td>Travel, zero counter space</td></tr>
  </tbody>
</table>
```
````

### Plain SVG chart with hover tooltips

````markdown
```visual Monthly coffee spend
<figure>
  <svg viewBox="0 0 420 148" role="img" aria-label="Monthly coffee spend from July to October">
    <line x1="88" y1="16" x2="88" y2="122" stroke="var(--paper-rule-strong)" />
    <g>
      <text x="80" y="34" text-anchor="end" fill="var(--paper-ink-2)">July</text>
      <rect x="88" y="21" width="180" height="18" rx="4" fill="var(--persona-slate)"><title>July: $72</title></rect>
      <text x="276" y="34" fill="var(--paper-ink-3)">$72</text>
    </g>
    <g>
      <text x="80" y="62" text-anchor="end" fill="var(--paper-ink-2)">August</text>
      <rect x="88" y="49" width="235" height="18" rx="4" fill="var(--persona-teal)"><title>August: $94 (two bags + café)</title></rect>
      <text x="331" y="62" fill="var(--paper-ink-3)">$94</text>
    </g>
    <g>
      <text x="80" y="90" text-anchor="end" fill="var(--paper-ink-2)">September</text>
      <rect x="88" y="77" width="145" height="18" rx="4" fill="var(--persona-slate)"><title>September: $58</title></rect>
      <text x="241" y="90" fill="var(--paper-ink-3)">$58</text>
    </g>
    <g>
      <text x="80" y="118" text-anchor="end" fill="var(--paper-ink-2)">October</text>
      <rect x="88" y="105" width="160" height="18" rx="4" fill="var(--signal-crema)"><title>October: $64 (target: $65)</title></rect>
      <text x="256" y="118" fill="var(--paper-ink)">$64</text>
    </g>
  </svg>
  <figcaption>Whole beans plus café stops; October back under the $65 target.</figcaption>
</figure>
```
````

### Compact recommendation

````markdown
```visual Recommendation: Fellow Ode Gen 2
<style>
  .rec { padding: var(--space-lg); border-radius: var(--radius-md); background: var(--paper-raised); border: 1px solid var(--paper-rule); }
  .rec-kicker { font-size: var(--type-caption); font-weight: 600; color: var(--signal-crema); margin-bottom: 4px; }
  .rec-top { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline; gap: 6px; margin-bottom: var(--space-sm); }
  .rec-price { font-family: var(--font-mono); font-size: var(--type-small); color: var(--paper-ink-2); }
</style>
<div class="rec">
  <div class="rec-kicker">Recommended for pour-over</div>
  <div class="rec-top"><h3>Fellow Ode Gen 2</h3><span class="rec-price">$345</span></div>
  <p>Cleanest cup under $400 with almost no static mess; skip only if you also pull espresso.</p>
  <dl>
    <dt>Strength</dt><dd>64 mm flat burrs, quiet motor, auto-stop</dd>
    <dt>Tradeoff</dt><dd>Filter &amp; immersion only — will not grind fine enough for espresso</dd>
  </dl>
</div>
```
````
