// Reproduce the historical animation conflict using that revision's actual CSS.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import { oneProductFixture } from './one-product-fixture.mjs'
const out = process.env.SHEET_EVIDENCE, ref = process.env.SHEET_REGRESSION_REF
if (!out || !ref) throw new Error('Set SHEET_EVIDENCE and SHEET_REGRESSION_REF')
await mkdir(out, { recursive: true })
const css = execFileSync('git', ['show', `${ref}:public/menu-surface.css`], { encoding: 'utf8' })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] }), app = await oneProductFixture()
try {
 const ctx = await browser.newContext({ viewport: { width: 390, height: 900 }, isMobile: true, hasTouch: true })
 await ctx.route('**/menu-surface.css', r => r.fulfill({ contentType: 'text/css', body: css }))
 const page = await ctx.newPage(); await page.goto(app.url); await page.locator('.tt-row').first().waitFor()
 const result = await page.evaluate(async () => {
   const node = document.createElement('div')
   node.setAttribute('data-menu-surface', ''); node.setAttribute('data-menu-layout', 'sheet'); node.setAttribute('popover', 'manual')
   node.style.width = '390px'; node.style.height = '200px'; node.style.position = 'fixed'; node.style.bottom = '0'; node.style.margin = '0'
   node.textContent = 'Historical animation probe'; document.body.append(node); node.showPopover()
   await new Promise(r => setTimeout(r, 300))
   node.style.transform = 'translateY(40px)'
   const blocked = getComputedStyle(node).transform, inline = node.style.transform
   node.style.animation = 'none'
   const released = getComputedStyle(node).transform
   node.remove(); return { inline, blocked, released }
 })
 assert.equal(result.inline, 'translateY(40px)')
 assert.equal(result.blocked, 'matrix(1, 0, 0, 1, 0, 0)', 'finished animation overrides inline drag')
 assert.equal(result.released, 'matrix(1, 0, 0, 1, 0, 40)', 'releasing animation lets the sheet follow')
 await writeFile(`${out}/animation-regression.json`, JSON.stringify({ ref, browser: browser.version(), ...result }, null, 2) + '\n')
 console.log(`PASS historical ${ref}: inline drag requests 40px; forwards-filled animation paints 0px; releasing animation paints 40px`)
 await ctx.close()
} finally { await browser.close(); await app.close() }
