// Stills of a theme's preview (2026-11-19), for its README on GitHub, where
// the live preview cannot run: the same scene the Market draws
// (public/theme-scene.html), at 880×520 and 2x, light and dark, at the
// theme's default options, framed by tools/frame-shot.py. Against the fixture
// app with the theme installed from a folder; no person's data.
//
//   THEME_DIR=<plugin folder> PREVIEW_OUT=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> \
//     LOAM_CHROME=<chromium> node tools/capture-theme-previews.mjs [extra hash, e.g. o.agent-shapes=signature]
import { execFileSync } from 'node:child_process'
import { mkdir, readFile, readdir } from 'node:fs/promises'
import path from 'node:path'
import { marketFixture } from '../test/market-fixture.mjs'

const dir = process.env.THEME_DIR, out = process.env.PREVIEW_OUT
if (!dir || !out) throw new Error('Set THEME_DIR and PREVIEW_OUT')
await mkdir(out, { recursive: true })
const manifest = await readFile(path.join(dir, 'PLUGIN.md'), 'utf8')
const field = name => manifest.match(new RegExp(`^${name}: (.*)$`, 'm'))?.[1]
const id = field('id'), options = field('options') ? JSON.parse(field('options')) : []
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, args: ['--no-sandbox'] })
const app = await marketFixture({})
app.installed.set(id, { id, name: field('name'), version: field('version'), author: field('author'), api: 0, kind: ['theme'], places: [], overrides: [], layer: 'yours', folder: `plugins/${id}`,
  effective: true, on: true, switch: 'appearance', broken: null, warnings: [], has: { page_main: false, theme: true }, options, touches: { network: [], record: 'none', memory: 'none', server: false }, source: { kind: 'built' } })
const types = { css: 'text/css; charset=utf-8', woff2: 'font/woff2', woff: 'font/woff', webp: 'image/webp' }
for (const file of await readdir(dir)) app.routes[`GET /v1/plugins/${id}/files/${file}`] = async (req, res) => { res.writeHead(200, { 'content-type': types[file.split('.').pop()] ?? 'application/octet-stream' }); res.end(await readFile(path.join(dir, file))) }
const extra = process.argv[2] ? `&${process.argv[2]}` : ''
const suffix = process.argv[3] ? `-${process.argv[3]}` : ''
try {
  for (const mode of ['light', 'dark']) {
    const ctx = await browser.newContext({ viewport: { width: 880, height: 520 }, deviceScaleFactor: 2 })
    const page = await ctx.newPage(), errors = []
    page.on('pageerror', e => errors.push(e.message))
    const opts = options.map(o => `&o.${o.id}=${o.default}`).join('')
    await page.goto(`${app.url}/theme-scene.html#look=${id}&mode=${mode}&from=installed${opts}${extra}`)
    await page.waitForFunction(() => document.documentElement.dataset.ready)
    await page.evaluate(() => document.fonts.ready)
    await page.waitForTimeout(300)
    if (errors.length) throw new Error(errors.join('; '))
    const raw = path.join(out, `.preview-${mode}${suffix}.png`)
    await page.screenshot({ path: raw })
    execFileSync('python3', [new URL('frame-shot.py', import.meta.url).pathname, raw, path.join(out, `preview-${mode}${suffix}.webp`)], { stdio: 'inherit' })
    execFileSync('rm', ['-f', raw])
    await ctx.close()
  }
} finally { await app.close(); await browser.close() }
