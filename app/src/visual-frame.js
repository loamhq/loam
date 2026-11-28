// Sealed iframe shell for inline agent visuals (`/visual-frame.html`).
//
// Security model:
// - Served under HTTP `Content-Security-Policy` with `sandbox allow-scripts`
//   (unique opaque origin: no parent DOM, cookies, storage, broker or vault),
//   `default-src 'none'`, `connect-src 'none'`, `frame-src 'none'`,
//   `img-src data:`, `font-src data:`, and `script-src 'nonce-<random>'`
//   (NO `'unsafe-inline'` for scripts!).
// - Only Loam's own tiny nonce'd bootstrap script runs inside this frame; it
//   sanitizes untrusted visual markup before inserting it into `#stage`,
//   syncs active theme tokens and local data-URI fonts live via postMessage,
//   reports exact content height for zero-scrollbar auto-sizing, and drives
//   safe textContent-only hover tooltips for `[data-tip]` and SVG `<title>`.
// - Untrusted `<script>` tags and inline event handlers cannot execute — both
//   stripped by the DOM sanitizer and hard-blocked by `script-src 'nonce-…'`.
//   (Why untrusted scripts are off: Chromium's `RTCPeerConnection` STUN sends
//   UDP packets past `connect-src 'none'`, and an untrusted script could spawn
//   a grandchild `srcdoc` frame to obtain a fresh `RTCPeerConnection`.)

import crypto from 'node:crypto'
import { VISUAL_BASE_CSS } from '../public/visual-doc.js'

const PERMISSIONS_POLICY =
  'accelerometer=(), autoplay=(), camera=(), clipboard-read=(), clipboard-write=(), ' +
  'display-capture=(), fullscreen=(), geolocation=(), gyroscope=(), magnetometer=(), ' +
  'microphone=(), midi=(), payment=(), usb=()'

export function visualFrameNonce() {
  return crypto.randomBytes(16).toString('base64')
}

export function visualFrameCsp(nonce) {
  return (
    `sandbox allow-scripts; default-src 'none'; style-src 'unsafe-inline'; ` +
    `script-src 'nonce-${nonce}'; img-src data:; font-src data:; ` +
    `connect-src 'none'; frame-src 'none'; child-src 'none'; worker-src 'none'; ` +
    `object-src 'none'; media-src 'none'; manifest-src 'none'; ` +
    `base-uri 'none'; form-action 'none'; frame-ancestors 'self'`
  )
}

export function visualFrameHeaders(nonce) {
  return {
    'content-type': 'text/html; charset=utf-8',
    'cache-control': 'no-store',
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff',
    'permissions-policy': PERMISSIONS_POLICY,
    'content-security-policy': visualFrameCsp(nonce),
  }
}

const BOOTSTRAP_JS = `(() => {
  'use strict';
  const selfScript = document.currentScript;
  if (selfScript) {
    try { selfScript.removeAttribute('nonce'); } catch {}
    selfScript.remove();
  }
  const params = new URLSearchParams(location.hash.slice(1));
  const frameId = params.get('v') || '';
  const stage = document.getElementById('stage');
  const tipEl = document.getElementById('loam-tip');
  const tokensStyle = document.getElementById('loam-tokens');
  const fontsStyle = document.getElementById('loam-fonts');
  const agentStyle = document.getElementById('loam-agent-css');

  const BANNED_TAGS = new Set([
    'script', 'iframe', 'frame', 'frameset', 'object', 'embed', 'applet',
    'meta', 'base', 'link', 'form', 'noscript', 'portal', 'audio', 'video',
    'source', 'track', 'param', 'template',
  ]);
  const STRIP_ATTRS = new Set([
    'srcdoc', 'srcset', 'ping', 'formaction', 'poster', 'background', 'dynsrc', 'lowsrc',
  ]);
  const URL_ATTRS = new Set(['src', 'href', 'xlink:href', 'data', 'action']);
  const SAFE_DATA_IMG = /^data:image\\/(?:png|jpe?g|gif|webp|avif|svg\\+xml)[;,]/i;

  function sanitizeCss(css) {
    return String(css || '')
      .replace(/@import[^;]*;?/gi, '')
      .replace(/url\\(\\s*(['"]?)([^'")]+)\\1\\s*\\)/gi, (whole, _q, target) => {
        const t = target.trim();
        if (t.startsWith('#') || /^data:(?:image|font)\\//i.test(t)) return whole;
        return 'none';
      });
  }

  function sanitizeTree(root, collectedCss) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT);
    const remove = [];
    let el = walker.nextNode();
    while (el) {
      const tag = el.localName ? el.localName.toLowerCase() : '';
      if (BANNED_TAGS.has(tag)) {
        remove.push(el);
        el = walker.nextNode();
        continue;
      }
      if (tag === 'style') {
        collectedCss.push(sanitizeCss(el.textContent || ''));
        remove.push(el);
        el = walker.nextNode();
        continue;
      }
      if ((tag === 'animate' || tag === 'set' || tag === 'mpath') &&
          /^(?:on|href|xlink:href|src|action|formaction)/i.test((el.getAttribute('attributeName') || '').trim())) {
        remove.push(el);
        el = walker.nextNode();
        continue;
      }
      for (const attr of [...el.attributes]) {
        const name = attr.name.toLowerCase();
        if (name.startsWith('on') || STRIP_ATTRS.has(name)) {
          el.removeAttribute(attr.name);
          continue;
        }
        if (name === 'style') {
          el.setAttribute('style', sanitizeCss(attr.value));
          continue;
        }
        if (URL_ATTRS.has(name)) {
          const val = attr.value.trim();
          const isImg = tag === 'img' || tag === 'image';
          if (val.startsWith('#') || (isImg && SAFE_DATA_IMG.test(val))) {
            el.setAttribute(attr.name, val);
          } else {
            el.removeAttribute(attr.name);
          }
        }
      }
      el = walker.nextNode();
    }
    for (const node of remove) node.remove();
  }

  function parseViewBoxWidth(vb) {
    const parts = String(vb || '').trim().split(/[\\s,]+/).map(Number);
    if (parts.length !== 4 || parts.some((n) => !Number.isFinite(n))) return 0;
    return parts[2] > 0 && parts[3] > 0 ? parts[2] : 0;
  }

  function enhanceStage() {
    // Convert direct SVG <title> children to instant Loam tooltips.
    for (const titleNode of [...stage.querySelectorAll('svg title')]) {
      const parent = titleNode.parentElement;
      const text = (titleNode.textContent || '').replace(/\\s+/g, ' ').trim();
      if (parent && text && parent.localName.toLowerCase() !== 'svg') {
        if (!parent.hasAttribute('data-tip')) parent.setAttribute('data-loam-tip', text);
        if (!parent.hasAttribute('aria-label')) parent.setAttribute('aria-label', text);
        titleNode.remove();
      }
    }
    // Keep bare viewBox SVGs at their natural viewBox width on desktop while
    // still shrinking to fit narrow screens (zero-specificity CSS rule lets
    // explicit width="100%" or agent CSS override cleanly).
    const isExpanded = document.documentElement.dataset.expanded === '1';
    for (const svg of [...stage.querySelectorAll('svg')]) {
      if (svg.parentElement?.closest('svg')) continue;
      const vbW = parseViewBoxWidth(svg.getAttribute('viewBox'));
      if (!svg.hasAttribute('width')) {
        if (vbW > 0) {
          svg.style.setProperty('--loam-vb-w', vbW + 'px');
          svg.dataset.loamVbW = String(vbW);
          if (isExpanded) {
            const p = svg.parentElement;
            if (p && !p.classList.contains('loam-svg-wrap')) {
              const wrap = document.createElement('div');
              wrap.className = 'loam-svg-wrap';
              p.insertBefore(wrap, svg);
              wrap.appendChild(svg);
            }
          }
        }
      } else {
        const rawW = (svg.getAttribute('width') || '').trim();
        const m = /^(\\d+(?:\\.\\d+)?)(?:px)?$/i.exec(rawW);
        if (m) {
          const numW = Number(m[1]);
          if (Number.isFinite(numW) && numW > 0) svg.dataset.loamVbW = String(numW);
        }
      }
    }
    // Match Markdown table column behaviour and wrap bare tables in an isolated
    // horizontal scroller for narrow screens.
    for (const tbl of [...stage.querySelectorAll('table')]) {
      for (const th of tbl.querySelectorAll('thead th')) {
        if ((th.textContent || '').trim().length > 18) th.setAttribute('data-loam-wrap', '');
      }
      const rows = [...tbl.querySelectorAll('tbody tr')];
      const hasSpans = [...tbl.querySelectorAll('th, td')].some((c) => {
        const cs = Number(c.getAttribute('colspan') || 1);
        const rs = Number(c.getAttribute('rowspan') || 1);
        return cs > 1 || rs > 1;
      });
      if (rows.length && !hasSpans) {
        const colMax = [];
        for (const row of rows) {
          const cells = [...row.children].filter((c) => c.localName.toLowerCase() === 'td');
          cells.forEach((cell, idx) => {
            const len = (cell.textContent || '').trim().length;
            if (len > (colMax[idx] || 0)) colMax[idx] = len;
          });
        }
        const cols = colMax.map((maxLen) => {
          if (!maxLen) return null;
          if (maxLen <= 14) return 'compact';
          if (maxLen <= 28) return 'medium';
          return 'prose';
        });
        for (const row of rows) {
          const cells = [...row.children].filter((c) => c.localName.toLowerCase() === 'td');
          cells.forEach((cell, idx) => {
            const kind = cols[idx];
            if (kind && !cell.hasAttribute('data-loam-col')) cell.setAttribute('data-loam-col', kind);
            if (kind !== 'compact') {
              const len = (cell.textContent || '').trim().length;
              if (len > 0 && len <= 14) cell.setAttribute('data-loam-nowrap', '');
            }
          });
        }
      }
      const p = tbl.parentElement;
      if (!p || p.classList.contains('loam-table-wrap')) continue;
      if (p !== stage) {
        const ox = getComputedStyle(p).overflowX;
        if (ox === 'auto' || ox === 'scroll') continue;
      }
      const wrap = document.createElement('div');
      wrap.className = 'loam-table-wrap';
      p.insertBefore(wrap, tbl);
      wrap.appendChild(tbl);
    }
  }

  let lastSizeKey = '';
  let measureQueued = false;
  let measureTimer = null;
  function send(payload) {
    window.parent.postMessage({ loamVisual: true, id: frameId, ...payload }, '*');
  }
  function measureNow() {
    measureQueued = false;
    if (measureTimer) { clearTimeout(measureTimer); measureTimer = null; }
    const viewportW = document.documentElement.clientWidth || window.innerWidth || 0;
    if (document.documentElement.dataset.expanded === '1' && viewportW > 0) {
      for (const wrap of stage.querySelectorAll('.loam-svg-wrap')) {
        const svg = wrap.querySelector('svg');
        const vbW = Number(svg?.dataset?.loamVbW || 0);
        wrap.classList.toggle('loam-svg-pan', vbW > viewportW * 1.25);
      }
    }
    const sh = Math.max(
      stage.getBoundingClientRect().height,
      document.body.getBoundingClientRect().height,
      stage.scrollHeight,
    );
    const height = Math.max(1, Math.min(6000, Math.ceil(sh)));
    let overflowX = false;
    let naturalWidth = 0;
    for (const wrap of stage.querySelectorAll('.loam-table-wrap')) {
      if (wrap.scrollWidth > wrap.clientWidth + 2) {
        overflowX = true;
        if (wrap.scrollWidth > naturalWidth) naturalWidth = Math.ceil(wrap.scrollWidth);
      }
    }
    if (viewportW > 0 && stage.scrollWidth > viewportW + 2) {
      overflowX = true;
      if (stage.scrollWidth > naturalWidth) naturalWidth = Math.ceil(stage.scrollWidth);
    }
    let shrunkSvg = false;
    for (const svg of stage.querySelectorAll('svg')) {
      if (svg.parentElement?.closest('svg')) continue;
      const vbW = Number(svg.dataset.loamVbW || 0);
      if (vbW > 0) {
        const renderedW = svg.getBoundingClientRect().width;
        if (vbW > renderedW + 8) {
          shrunkSvg = true;
          if (vbW > naturalWidth) naturalWidth = Math.ceil(vbW);
        }
      }
    }
    const tall = height >= (viewportW > 0 && viewportW <= 500 ? 340 : 420);
    const canExpand = overflowX || shrunkSvg || tall;
    const sizeKey = height + '|' + (canExpand ? 1 : 0) + '|' + naturalWidth;
    if (sizeKey !== lastSizeKey) {
      lastSizeKey = sizeKey;
      send({ type: 'size', height, canExpand, naturalWidth });
    }
  }
  function scheduleMeasure() {
    if (measureQueued) return;
    measureQueued = true;
    // Opaque-origin iframes outside the viewport throttle requestAnimationFrame
    // in Chromium/WebKit; pair rAF with a short timer so off-screen messages
    // still settle to their exact height immediately.
    requestAnimationFrame(measureNow);
    measureTimer = setTimeout(measureNow, 40);
  }

  let lastFontsCss = '';
  function applyTheme(data) {
    const mode = data.mode === 'dark' ? 'dark' : 'light';
    document.documentElement.dataset.theme = mode;
    document.documentElement.style.colorScheme = mode;
    const lines = [];
    if (data.tokens && typeof data.tokens === 'object') {
      for (const [k, v] of Object.entries(data.tokens)) {
        if (!/^--[a-z0-9-]+$/i.test(k)) continue;
        const val = String(v ?? '').trim();
        if (!val || /[{};<>]/.test(val) || /url\\s*\\(/i.test(val)) continue;
        lines.push('  ' + k + ': ' + val + ';');
      }
    }
    tokensStyle.textContent = lines.length ? ':root {\\n' + lines.join('\\n') + '\\n}' : '';
    if (typeof data.fontsCss === 'string' && data.fontsCss !== lastFontsCss) {
      lastFontsCss = data.fontsCss;
      fontsStyle.textContent = data.fontsCss;
    }
    scheduleMeasure();
    document.fonts?.ready?.then(scheduleMeasure);
  }

  function renderHtml(rawHtml) {
    hideTip();
    const doc = new DOMParser().parseFromString(String(rawHtml || ''), 'text/html');
    const collectedCss = [];
    for (const styleEl of [...doc.head.querySelectorAll('style')]) {
      collectedCss.push(sanitizeCss(styleEl.textContent || ''));
    }
    sanitizeTree(doc.body, collectedCss);
    agentStyle.textContent = collectedCss.join('\\n');
    const frag = document.createDocumentFragment();
    while (doc.body.firstChild) {
      frag.appendChild(document.adoptNode(doc.body.firstChild));
    }
    stage.replaceChildren(frag);
    enhanceStage();
    lastSizeKey = '';
    measureNow();
    setTimeout(scheduleMeasure, 60);
    document.fonts?.ready?.then(scheduleMeasure);
  }

  // Safe plain-text tooltips for [data-tip] and SVG <title>.
  let tipTarget = null;
  function tipTextOf(el) {
    const host = el?.closest?.('[data-tip],[data-loam-tip]');
    if (!host || !stage.contains(host)) return null;
    const text = (host.getAttribute('data-tip') || host.getAttribute('data-loam-tip') || '').trim();
    return text ? { host, text } : null;
  }
  function placeTip(x, y) {
    const pad = 8;
    const rect = tipEl.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = Math.min(Math.max(pad, x - rect.width / 2), Math.max(pad, vw - rect.width - pad));
    let top = y - rect.height - 10;
    if (top < pad) top = Math.min(vh - rect.height - pad, y + 14);
    tipEl.style.transform = 'translate(' + Math.round(left) + 'px,' + Math.round(top) + 'px)';
  }
  function showTip(host, text, x, y) {
    tipTarget = host;
    tipEl.textContent = text;
    tipEl.hidden = false;
    placeTip(x, y);
  }
  function hideTip() {
    tipTarget = null;
    tipEl.hidden = true;
  }
  stage.addEventListener('pointerover', (e) => {
    const found = tipTextOf(e.target);
    if (!found) return;
    showTip(found.host, found.text, e.clientX, e.clientY);
  });
  stage.addEventListener('pointermove', (e) => {
    if (!tipTarget) return;
    const found = tipTextOf(e.target);
    if (!found) { hideTip(); return; }
    if (found.host !== tipTarget) showTip(found.host, found.text, e.clientX, e.clientY);
    else placeTip(e.clientX, e.clientY);
  });
  stage.addEventListener('pointerout', (e) => {
    if (tipTarget && (!e.relatedTarget || !tipTarget.contains(e.relatedTarget))) hideTip();
  });
  stage.addEventListener('focusin', (e) => {
    const found = tipTextOf(e.target);
    if (!found) return;
    const r = found.host.getBoundingClientRect();
    showTip(found.host, found.text, r.left + r.width / 2, r.top);
  });
  stage.addEventListener('focusout', hideTip);
  stage.addEventListener('click', (e) => {
    const link = e.target?.closest?.('a[href]');
    if (link) {
      const href = (link.getAttribute('href') || '').trim();
      if (!href.startsWith('#')) e.preventDefault();
    }
    const found = tipTextOf(e.target);
    if (found) showTip(found.host, found.text, e.clientX, e.clientY);
    else hideTip();
  });
  stage.addEventListener('toggle', scheduleMeasure, true);

  if (typeof ResizeObserver !== 'undefined') {
    const ro = new ResizeObserver(scheduleMeasure);
    ro.observe(stage);
    ro.observe(document.documentElement);
  }
  window.addEventListener('resize', scheduleMeasure);
  window.addEventListener('message', (e) => {
    if (e.source !== window.parent) return;
    const data = e.data;
    if (!data || data.loamVisual !== true || data.id !== frameId) return;
    if (data.type === 'theme') applyTheme(data);
    else if (data.type === 'render') {
      document.documentElement.dataset.expanded = data.expanded ? '1' : '0';
      if (data.tokens || data.mode || typeof data.fontsCss === 'string') applyTheme(data);
      renderHtml(data.html);
    }
  });
  send({ type: 'ready' });
})();`

export function renderVisualFrame(nonce) {
  return [
    '<!doctype html>',
    '<html lang="en" data-theme="light">',
    '<head>',
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    '<meta name="color-scheme" content="light dark">',
    '<title>Visual</title>',
    '<style id="loam-fonts"></style>',
    '<style id="loam-tokens"></style>',
    `<style id="loam-base">${VISUAL_BASE_CSS}</style>`,
    '<style id="loam-agent-css"></style>',
    '</head>',
    '<body>',
    '<div id="stage"></div>',
    '<div id="loam-tip" role="tooltip" hidden></div>',
    `<script nonce="${nonce}">${BOOTSTRAP_JS}</script>`,
    '</body>',
    '</html>',
  ].join('\n')
}
