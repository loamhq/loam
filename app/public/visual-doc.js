// Inline visual blocks: fence recognition, plain fallbacks, shared base styles,
// and standalone HTML export for Files. Pure module (no DOM required) so the
// app server, browser renderer, markdown parser and tests all share one truth.

// Every core token defined in public/tokens.css, plus --reading-ground.
// At runtime the page also copies any extra `--*` custom properties set on
// `:root` by an installed plugin theme.
export const VISUAL_TOKENS = [
  '--reading-ground',
  '--paper-page', '--paper-pane', '--paper-raised', '--paper-card', '--paper-hover',
  '--paper-ink', '--paper-ink-2', '--paper-ink-3',
  '--paper-rule', '--paper-rule-strong', '--paper-hover-alpha', '--paper-pressed',
  '--paper-page-glass', '--paper-pane-glass', '--paper-highlight',
  '--paper-page-ink', '--paper-page-ink-2', '--paper-page-ink-3',
  '--paper-pane-ink', '--paper-pane-ink-2', '--paper-pane-ink-3',
  '--paper-raised-ink', '--paper-raised-ink-2', '--paper-raised-ink-3',
  '--paper-card-ink', '--paper-card-ink-2', '--paper-card-ink-3',
  '--paper-hover-ink', '--paper-hover-ink-2', '--paper-hover-ink-3',
  '--paper-highlight-ink', '--paper-mark-ink', '--paper-mark-active', '--paper-mark-rule',
  '--latte-foam', '--latte-track', '--latte-hover',
  '--latte-coffee', '--latte-coffee-2',
  '--latte-espresso', '--latte-espresso-ink', '--latte-espresso-hover', '--latte-espresso-pressed',
  '--latte-disabled', '--latte-control-selected', '--latte-control-selected-ink',
  '--latte-foam-ink', '--latte-foam-ink-2', '--latte-track-ink', '--latte-track-ink-2',
  '--latte-hover-ink', '--latte-hover-ink-2', '--latte-disabled-ink', '--latte-disabled-ink-2',
  '--latte-espresso-hover-ink', '--latte-espresso-pressed-ink',
  '--signal-crema', '--danger', '--warning', '--success',
  '--destructive-button', '--destructive-button-hover', '--destructive-button-ink',
  '--surface-code', '--text-code', '--text-code-muted', '--text-code-danger', '--text-code-accent', '--rule-code',
  '--surface-attention', '--surface-danger', '--text-inverse',
  '--shadow-ink', '--shine', '--scrim', '--shadow-float', '--shadow-popover',
  '--font-thread', '--font-body', '--font-display', '--font-mono',
  '--type-caption', '--type-small', '--type-body', '--type-lead', '--type-title',
  '--space-xs', '--space-sm', '--space-md', '--space-lg', '--space-xl', '--space-2xl', '--space-3xl',
  '--radius-sm', '--radius-md', '--radius-lg', '--radius-xl', '--radius-pill',
  '--motion-fast', '--motion-normal', '--ease-standard',
  '--agent-latte-0', '--agent-latte-1', '--agent-latte-2', '--agent-latte-3', '--agent-latte-4',
  '--agent-latte-5', '--agent-latte-6', '--agent-latte-7', '--agent-latte-8', '--agent-latte-9',
  '--agent-latte-foam', '--agent-latte-coffee',
  '--persona-slate', '--persona-slate-tint',
  '--persona-olive', '--persona-olive-tint',
  '--persona-rust', '--persona-rust-ink', '--persona-rust-tint',
  '--persona-plum', '--persona-plum-tint',
  '--persona-teal', '--persona-teal-tint',
  '--persona-rose', '--persona-rose-tint',
  '--persona-gold', '--persona-gold-ink', '--persona-gold-tint',
  '--persona-blue', '--persona-blue-tint',
  '--persona-taupe', '--persona-taupe-tint',
  '--persona-sage', '--persona-sage-ink', '--persona-sage-tint',
  '--persona-mauve', '--persona-mauve-tint',
]

// Matches ```visual or ```visual Optional title (case-insensitive first token).
// Never matches ```html, ```svg, ```visualbasic, etc.
export function isVisualLang(lang) {
  const token = String(lang ?? '').trim().split(/\s+/)[0] ?? ''
  return token.toLowerCase() === 'visual'
}

export function visualInfoTitle(lang) {
  const trimmed = String(lang ?? '').trim()
  if (!isVisualLang(trimmed)) return ''
  const rest = trimmed.slice(6).trim().replace(/\s+/g, ' ')
  return rest.slice(0, 120)
}

function decodeBasicEntities(text) {
  return String(text ?? '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&#(\d+);/g, (_, n) => {
      const code = Number(n)
      return Number.isFinite(code) && code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : ''
    })
}

export function visualTitle(lang, html = '') {
  const fromInfo = visualInfoTitle(lang)
  if (fromInfo) return fromInfo
  const src = String(html ?? '')
  if (!src) return ''
  const clean = (raw) => decodeBasicEntities(String(raw ?? '').replace(/<[^>]*>/g, ' ')).replace(/\s+/g, ' ').trim().slice(0, 100)
  for (const rx of [
    /<h[1-4]\b[^>]*>([\s\S]*?)<\/h[1-4]\s*>/i,
    /<figcaption\b[^>]*>([\s\S]*?)<\/figcaption\s*>/i,
    /<title\b[^>]*>([\s\S]*?)<\/title\s*>/i,
    /\baria-label\s*=\s*"([^"]{2,100})"/i,
    /\baria-label\s*=\s*'([^']{2,100})'/i,
  ]) {
    const m = rx.exec(src)
    if (m) {
      const text = clean(m[1])
      if (text) return text
    }
  }
  return ''
}

export function visualFallbackLine(lang, html = '') {
  const title = visualTitle(lang, html)
  return title ? `Visual: ${title}` : 'Visual'
}

// Replace fenced visual blocks (closed or truncated/unclosed) with their short
// plain label so quotes, previews, copy and search never leak HTML/SVG markup.
export function stripVisualBlocks(text) {
  if (typeof text !== 'string' || !text.includes('```') && !text.includes('~~~')) return text
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let i = 0
  while (i < lines.length) {
    const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(lines[i])
    if (m && !(m[1][0] === '`' && m[2].includes('`'))) {
      const char = m[1][0]
      const len = m[1].length
      const lang = m[2].trim()
      const closeRe = new RegExp(`^ {0,3}${char === '`' ? '`' : '~'}{${len},}\\s*$`)
      if (isVisualLang(lang)) {
        const body = []
        i += 1
        while (i < lines.length && !closeRe.test(lines[i])) {
          body.push(lines[i])
          i += 1
        }
        if (i < lines.length) i += 1
        out.push(visualFallbackLine(lang, body.join('\n')))
        continue
      }
      // Leave non-visual fences untouched without scanning inside them.
      out.push(lines[i])
      i += 1
      while (i < lines.length && !closeRe.test(lines[i])) {
        out.push(lines[i])
        i += 1
      }
      if (i < lines.length) {
        out.push(lines[i])
        i += 1
      }
      continue
    }
    out.push(lines[i])
    i += 1
  }
  return out.join('\n')
}

// While streaming, hold back a trailing partial visual fence opener (` ```vis `)
// so raw backticks never flash before the placeholder takes over.
export function holdPartialVisualFence(text) {
  if (typeof text !== 'string') return text
  return text.replace(/(?:^|\n) {0,3}(?:`{3,}|~{3,})v(?:i(?:s(?:u(?:al?)?)?)?)?$/i, '')
}

// A safe vault filename for "Save to Files" (`.html`).
export function visualFilename(title = '', now = new Date(), suffix = 0) {
  const cleaned = String(title ?? '')
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .replace(/^\.+|\.+$/g, '')
    .trim()
    .slice(0, 64)
    .trim()
  const pad = (n) => String(n).padStart(2, '0')
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}${pad(now.getMinutes())}`
  const base = cleaned || `Visual ${stamp}`
  return `${base}${suffix ? ` ${suffix + 1}` : ''}.html`
}

// Zero-specificity `:where(...)` base stylesheet shared by the live sealed
// frame and standalone `.html` files saved to Files. Bare semantic elements
// and SVG look like Loam by default while any agent rule wins cleanly.
export const VISUAL_BASE_CSS = `
:where(*, *::before, *::after) { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: transparent;
  color: var(--paper-ink, CanvasText);
  font: 400 var(--type-body, 15px)/1.55 var(--font-body, system-ui, sans-serif);
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
html { overflow-x: clip; overflow-y: hidden; }
body { overflow-x: clip; overflow-y: hidden; }
#stage {
  display: flow-root;
  width: 100%;
  min-width: 0;
}
#stage > :first-child { margin-top: 0; }
#stage > :last-child { margin-bottom: 0; }
:where(div, section, article, aside, header, footer, main, figure) { min-width: 0; }
:where(h1, h2, h3, h4, h5, h6) {
  margin: 0 0 var(--space-sm, 8px);
  color: var(--paper-ink, CanvasText);
  font-family: var(--font-body, system-ui, sans-serif);
  font-weight: 650;
  line-height: 1.25;
  overflow-wrap: anywhere;
}
:where(h1) { font-size: calc(var(--type-title, 24px) * 0.92); }
:where(h2) { font-size: calc(var(--type-lead, 16px) * 1.15); }
:where(h3) { font-size: var(--type-lead, 16px); }
:where(h4, h5, h6) { font-size: var(--type-small, 13px); color: var(--paper-ink-2); }
:where(p) { margin: 0 0 var(--space-sm, 8px); color: var(--paper-ink, CanvasText); overflow-wrap: anywhere; }
:where(small) { font-size: var(--type-caption, 12px); color: var(--paper-ink-3); }
:where(strong, b) { font-weight: 700; }
:where(code) {
  font-family: var(--font-mono, ui-monospace, monospace);
  font-size: 0.88em;
  background: var(--paper-card);
  color: var(--paper-ink, CanvasText);
  padding: 1px 5px;
  border-radius: 5px;
}
:where(mark) {
  background: var(--paper-highlight);
  color: var(--paper-ink, CanvasText);
  padding: 0 4px;
  border-radius: 4px;
}
:where(hr) {
  border: 0;
  border-top: 1px solid var(--paper-rule);
  margin: var(--space-md, 12px) 0;
}
:where(ul, ol) { margin: 0 0 var(--space-sm, 8px); padding-left: 20px; }
:where(li) { margin: 3px 0; overflow-wrap: anywhere; }
:where(blockquote) {
  margin: var(--space-sm, 8px) 0;
  padding: 2px 0 2px var(--space-md, 12px);
  border-left: 2px solid var(--paper-rule-strong);
  color: var(--paper-ink-2);
}
:where(dl) {
  margin: 0 0 var(--space-sm, 8px);
  display: grid;
  grid-template-columns: minmax(88px, auto) 1fr;
  gap: 6px var(--space-lg, 16px);
  font-size: var(--type-small, 13px);
}
:where(dt) { color: var(--paper-ink-3); font-weight: 500; overflow-wrap: anywhere; }
:where(dd) { margin: 0; color: var(--paper-ink, CanvasText); font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
:where(figure) { margin: 0; }
:where(figcaption) {
  margin-top: var(--space-sm, 8px);
  font-size: var(--type-caption, 12px);
  color: var(--paper-ink-3);
  line-height: 1.45;
}
.loam-table-wrap {
  width: 100%;
  max-width: 100%;
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-x: contain;
}
:where(table) {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--type-body, 15px);
  line-height: 1.45;
  font-variant-numeric: tabular-nums;
  white-space: normal;
  overflow-wrap: break-word;
}
:where(th, td) {
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
}
:where(th:first-child, td:first-child) { padding-left: 0; }
:where(th:last-child, td:last-child) { padding-right: 0; }
:where(thead th) {
  padding-top: 4px;
  padding-bottom: 8px;
  background: transparent;
  font-size: 12.5px;
  line-height: 1.35;
  font-weight: 600;
  color: var(--paper-ink-3);
  vertical-align: bottom;
  white-space: nowrap;
  border-bottom: 1px solid var(--paper-rule-strong);
}
:where(thead th[data-loam-wrap]) { white-space: normal; }
:where(th code) { font-size: 0.92em; }
:where(tbody td) {
  color: var(--paper-ink, CanvasText);
}
:where(td[data-loam-col="compact"], td[data-loam-nowrap]) { white-space: nowrap; }
:where(td[data-loam-col="medium"]) { min-width: 5rem; }
:where(td[data-loam-col="prose"]) { min-width: 6.5rem; }
:where(td a, td code, th a, th code) { overflow-wrap: anywhere; }
:where(tbody tr + tr td) { border-top: 1px solid var(--paper-rule); }
@media (hover: hover) {
  :where(tbody tr:hover td) { background: var(--paper-hover-alpha); }
}
@media (min-width: 720px) {
  :where(th, td) { padding: 10px 12px; }
  :where(th:first-child, td:first-child) { padding-left: 0; }
  :where(th:last-child, td:last-child) { padding-right: 0; }
  :where(td[data-loam-col="medium"]) { min-width: 7.5rem; }
  :where(td[data-loam-col="prose"]) { min-width: 10.5rem; }
}
@media (max-width: 460px) {
  :where(dl) { grid-template-columns: minmax(76px, auto) 1fr; gap: 5px var(--space-md, 12px); }
}
:where(progress) {
  appearance: none;
  -webkit-appearance: none;
  display: block;
  width: 100%;
  height: 8px;
  border: 0;
  border-radius: var(--radius-pill, 999px);
  background: var(--paper-card);
  overflow: hidden;
}
:where(progress)::-webkit-progress-bar {
  background: var(--paper-card);
  border-radius: var(--radius-pill, 999px);
}
:where(progress)::-webkit-progress-value {
  background: var(--signal-crema);
  border-radius: var(--radius-pill, 999px);
}
:where(progress)::-moz-progress-bar {
  background: var(--signal-crema);
  border-radius: var(--radius-pill, 999px);
}
:where(meter) {
  display: block;
  width: 100%;
  height: 8px;
}
:where(details) {
  border-top: 1px solid var(--paper-rule);
  padding: var(--space-sm, 8px) 0;
}
:where(summary) {
  cursor: pointer;
  color: var(--paper-ink-2);
  font-size: var(--type-small, 13px);
  font-weight: 500;
}
:where(img, svg, canvas) { max-width: 100%; height: auto; }
:where(svg) {
  display: block;
  color: var(--paper-ink, CanvasText);
  font-family: var(--font-body, system-ui, sans-serif);
}
:where(svg[viewBox]:not([width])) {
  width: min(100%, var(--loam-vb-w, 100%));
  height: auto;
}
.loam-svg-wrap {
  max-width: 100%;
}
.loam-svg-wrap.loam-svg-pan {
  overflow-x: auto;
  overflow-y: hidden;
  -webkit-overflow-scrolling: touch;
  overscroll-behavior-x: contain;
}
:where(.loam-svg-pan svg[viewBox]:not([width])) {
  width: var(--loam-vb-w, 100%);
  max-width: none;
}
:where(svg text:not([fill])) {
  fill: var(--paper-ink, CanvasText);
}
:where(svg text) {
  font-family: var(--font-body, system-ui, sans-serif);
}
:where(svg text:not([font-size])) {
  font-size: 14px;
}
:where(svg line:not([stroke]), svg polyline:not([stroke])) {
  stroke: var(--paper-rule-strong);
}
:where(svg [data-tip], svg [data-loam-tip]) {
  transition: opacity var(--motion-fast, 160ms) var(--ease-standard, ease);
}
@media (hover: hover) {
  :where(svg [data-tip], svg [data-loam-tip]):hover { opacity: 0.82; }
}
:where(a) { color: inherit; text-decoration: none; }
#loam-tip {
  position: fixed;
  top: 0;
  left: 0;
  z-index: 9999;
  max-width: min(260px, calc(100vw - 16px));
  padding: 5px 9px;
  border-radius: var(--radius-sm, 8px);
  background: var(--paper-ink, CanvasText);
  color: var(--paper-page, Canvas);
  font: 500 var(--type-caption, 12px)/1.35 var(--font-body, system-ui, sans-serif);
  box-shadow: var(--shadow-float);
  pointer-events: none;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
#loam-tip[hidden] { display: none; }
`.trim()

export function serializeTokenCss(tokens = {}) {
  const lines = []
  for (const [k, v] of Object.entries(tokens ?? {})) {
    if (!/^--[a-z0-9-]+$/i.test(k)) continue
    const val = String(v ?? '').trim()
    if (!val || /[{};<>]/.test(val) || /url\s*\(/i.test(val)) continue
    lines.push(`  ${k}: ${val};`)
  }
  return lines.length ? `:root {\n${lines.join('\n')}\n}` : ''
}

// String-level sanitizer for standalone HTML saved to Files (preview.js also
// enforces `sandbox` + `default-src 'none'` when opened in Files).
export function sanitizeVisualHtmlString(raw) {
  let doc = String(raw ?? '')
  for (const tag of ['script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet', 'noscript', 'portal', 'audio', 'video', 'template']) {
    doc = doc.replace(new RegExp(`<${tag}\\b[\\s\\S]*?(?:<\\/${tag}\\s*>|$)`, 'gi'), '')
  }
  doc = doc.replace(/<(?:meta|base|link|source|track|param)\b[^>]*>/gi, '')
  doc = doc.replace(/<\/?form\b[^>]*>/gi, '')
  // Strip inline event handlers (`on*=...`).
  doc = doc.replace(/\s+on[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
  // Strip external URLs inside `<style>` blocks while preserving `#id` and `data:`.
  doc = doc.replace(/<style\b([^>]*)>([\s\S]*?)<\/style\s*>/gi, (_, attrs, css) => {
    const cleanCss = sanitizeCssUrls(css).replace(/<\/style/gi, '<\\/style')
    return `<style${attrs}>${cleanCss}</style>`
  })
  // Strip external `src`, `href`, `xlink:href`, `srcset`, `poster`, `formaction`, `ping`, `srcdoc`.
  doc = doc.replace(/\s+(?:srcdoc|srcset|ping|formaction|poster|background)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)/gi, '')
  doc = doc.replace(/\s+(src|href|xlink:href)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi, (whole, attr, v1, v2, v3) => {
    const val = (v1 ?? v2 ?? v3 ?? '').trim()
    if (val.startsWith('#') || /^data:image\/(?:png|jpe?g|gif|webp|avif|svg\+xml)[;,]/i.test(val)) {
      return ` ${attr}="${val.replace(/"/g, '&quot;')}"`
    }
    return ''
  })
  return doc
}

export function sanitizeCssUrls(css) {
  return String(css ?? '')
    .replace(/@import[^;]*;?/gi, '')
    .replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/gi, (whole, _, target) => {
      const t = target.trim()
      if (t.startsWith('#') || /^data:(?:image|font)\//i.test(t)) return whole
      return 'none'
    })
}

function escapeHtml(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function parseSvgViewBoxWidth(viewBox) {
  const parts = String(viewBox ?? '').trim().split(/[\s,]+/).map(Number)
  if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return 0
  return parts[2] > 0 && parts[3] > 0 ? parts[2] : 0
}

function annotateSvgViewBoxWidth(html) {
  return String(html ?? '').replace(/<svg\b([^>]*)>/gi, (whole, attrs) => {
    if (/\bwidth\s*=/i.test(attrs)) return whole
    const vbMatch = /\bviewBox\s*=\s*(?:"([^"]+)"|'([^']+)')/i.exec(attrs)
    const vbW = parseSvgViewBoxWidth(vbMatch?.[1] ?? vbMatch?.[2] ?? '')
    if (!vbW) return whole
    const prop = `--loam-vb-w:${vbW}px`
    if (/\bstyle\s*=\s*"/i.test(attrs)) {
      return `<svg${attrs.replace(/\bstyle\s*=\s*"([^"]*)"/i, (_, s) => `style="${s}${s && !s.trim().endsWith(';') ? ';' : ''}${prop}"`)}>`
    }
    if (/\bstyle\s*=\s*'/i.test(attrs)) {
      return `<svg${attrs.replace(/\bstyle\s*=\s*'([^']*)'/i, (_, s) => `style='${s}${s && !s.trim().endsWith(';') ? ';' : ''}${prop}'`)}>`
    }
    return `<svg${attrs} style="${prop}">`
  })
}

export function buildStandaloneVisualHtml({ html = '', title = '', tokens = {}, mode = 'light', fontsCss = '' } = {}) {
  const safeTitle = escapeHtml(title || 'Visual')
  const safeMode = mode === 'dark' ? 'dark' : 'light'
  const tokenCss = serializeTokenCss(tokens)
  const cleanHtml = annotateSvgViewBoxWidth(sanitizeVisualHtmlString(html))
  const safeFonts = String(fontsCss ?? '').replace(/<\/style/gi, '<\\/style')
  const standaloneCss = `
html, body {
  overflow: auto;
  background: var(--reading-ground, var(--paper-pane, Canvas));
  color: var(--paper-ink, CanvasText);
  min-height: 100%;
}
main.loam-visual-standalone {
  max-width: 780px;
  margin: 0 auto;
  padding: 24px 20px 40px;
}
`.trim()
  return [
    '<!doctype html>',
    `<html lang="en" data-theme="${safeMode}">`,
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    `<meta name="color-scheme" content="${safeMode}">`,
    `<title>${safeTitle}</title>`,
    safeFonts ? `<style>${safeFonts}</style>` : '',
    tokenCss ? `<style>${tokenCss}</style>` : '',
    `<style>${VISUAL_BASE_CSS}\n${standaloneCss}</style>`,
    '</head>',
    '<body>',
    `<main class="loam-visual-standalone"><div id="stage">${cleanHtml}</div></main>`,
    '</body>',
    '</html>\n',
  ].filter(Boolean).join('\n')
}
