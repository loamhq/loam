// Make a standalone before/after PNG from two original screenshots.
// Uses the same optional Playwright/Chromium setup as threads-browser.mjs.
import { readFile } from 'node:fs/promises'
const [beforePath, afterPath, output] = process.argv.slice(2)
if (!output) throw new Error('Usage: node tools/compare-captures.mjs <before.png> <after.png> <output.png>')
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const before = await readFile(beforePath), after = await readFile(afterPath)
const width = before.readUInt32BE(16) + after.readUInt32BE(16) + 48
const height = Math.max(before.readUInt32BE(20), after.readUInt32BE(20)) + 64
const browser = await chromium.launch({ executablePath: process.env.LOAM_CHROME, headless: true, args: ['--no-sandbox', '--disable-gpu'] })
try {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
  await page.setContent('<style>body{margin:0;padding:16px;background:#e4dfd7;font:12px Arial,sans-serif;color:#4d3e32}main{display:flex;gap:16px}figure{margin:0}figcaption{height:32px;letter-spacing:.06em}img{display:block;border-radius:12px}</style><main></main>')
  await page.evaluate((pictures) => {
    for (const { label, data } of pictures) {
      const figure = document.createElement('figure'), caption = document.createElement('figcaption'), img = document.createElement('img')
      caption.textContent = label; img.src = data; figure.append(caption, img); document.querySelector('main').append(figure)
    }
  }, [{ label: 'BEFORE · 390 px · light', data: `data:image/png;base64,${before.toString('base64')}` }, { label: 'AFTER · 390 px · light', data: `data:image/png;base64,${after.toString('base64')}` }])
  await page.evaluate(() => Promise.all([...document.images].map((img) => img.decode())))
  await page.screenshot({ path: output })
} finally { await browser.close() }
