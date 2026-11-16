import assert from 'node:assert/strict'

// Shared computed-style check: tokens are resolved by the browser itself.
export async function auditControls(page, selector = '.apps-row, .apps-toolbar button, .apps-toolbar a, .apps-page .pillbtn, .settings-utility, .activity-item, .activity-filter select, .tt-persona-card :is(button, a[href], input, select, textarea, [role=button])') {
  await page.mouse.move(0, 0)
  return page.evaluate((selector) => {
    const root = document.documentElement, colors = new Set(['rgba(0, 0, 0, 0)'])
    const probe = document.createElement('span'); document.body.append(probe)
    // Resolve every installed colour token through the browser, not a CSS parser.
    for (const name of getComputedStyle(root)) if (name.startsWith('--')) {
      probe.style.color = ''; probe.style.color = `var(${name})`
      const raw = getComputedStyle(root).getPropertyValue(name).trim()
      if (/^(#|rgb|hsl|color|transparent)/.test(raw)) colors.add(getComputedStyle(probe).color)
    }
    const fonts = new Set()
    for (const name of ['--font-body', '--font-display', '--font-mono']) { probe.style.fontFamily = `var(${name})`; fonts.add(getComputedStyle(probe).fontFamily) }
    probe.remove()
    const ua = document.createElement('button'); ua.style.all = 'revert'; document.body.append(ua)
    const defaults = { background: getComputedStyle(ua).backgroundColor, border: getComputedStyle(ua).borderTopStyle }; ua.remove()
    const nodes = [...document.querySelectorAll(selector)].filter(node => node.getClientRects().length)
    const rows = nodes.map(node => {
      const style = getComputedStyle(node), box = node.getBoundingClientRect()
      const borders = ['Top', 'Right', 'Bottom', 'Left'].map(side => ({ style: style[`border${side}Style`], width: style[`border${side}Width`], color: style[`border${side}Color`] }))
      return { card: Boolean(node.closest('.tt-persona-card')), font: style.fontFamily, paletteFont: fonts.has(style.fontFamily), text: style.color, paletteText: colors.has(style.color), name: node.getAttribute('aria-label') || node.textContent.trim().slice(0, 70), background: style.backgroundColor, paletteBackground: colors.has(style.backgroundColor),
        uaButton: style.backgroundColor === defaults.background && borders.some(border => border.style === defaults.border && border.style !== 'none'),
        paletteBorders: borders.every(border => border.width === '0px' || border.style === 'none' || (border.style === 'solid' && colors.has(border.color))), height: box.height }
    })
    return rows
  }, selector)
}
export function assertControls(rows) {
  assert.ok(rows.length)
  for (const row of rows) {
    assert.equal(row.uaButton, false, JSON.stringify(row))
    assert.equal(row.paletteBackground, true, JSON.stringify(row))
    assert.equal(row.paletteBorders, true, JSON.stringify(row))
    assert.equal(row.paletteFont, true, JSON.stringify(row))
    assert.equal(row.paletteText, true, JSON.stringify(row))
    assert.ok(row.height >= (row.card ? 44 : 40), JSON.stringify(row))
  }
}
