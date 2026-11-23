// Browser regression test and before/after captures for portrait images,
// captions, multi-image rows, owner phone attachments, and full-size image view
// at 1440x900 and 390x844 in light and dark modes.
//
// Run from app/:
//   LOAM_PLAYWRIGHT=/path/to/playwright/index.mjs \
//   LOAM_CHROME=/usr/bin/chromium-headless-shell \
//   PORTRAIT_IMAGES_EVIDENCE=/tmp/out node test/portrait-images-browser.mjs
//
// Pass PORTRAIT_IMAGES_BASELINE=1 against an archive of main to capture and
// verify the pre-fix regression.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import zlib from 'node:zlib'
import { oneProductFixture } from './one-product-fixture.mjs'

const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const out = process.env.PORTRAIT_IMAGES_EVIDENCE
const baseline = process.env.PORTRAIT_IMAGES_BASELINE === '1'
if (out) await mkdir(out, { recursive: true })

function png(w, h, paint) {
  const rows = Buffer.alloc((w * 3 + 1) * h)
  for (let y = 0, o = 0; y < h; y++) {
    rows[o++] = 0
    for (let x = 0; x < w; x++) {
      const [r, g, b] = paint(x / w, y / h, x, y)
      rows[o++] = r; rows[o++] = g; rows[o++] = b
    }
  }
  const chunk = (kind, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(kind), data])
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32(body))
    return Buffer.concat([len, body, crc])
  }
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(rows)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

function phoneShot(w, h, dark = false) {
  const bg = dark ? [28, 30, 34] : [245, 241, 234]
  const bar = dark ? [22, 24, 28] : [235, 229, 220]
  const card = dark ? [42, 46, 52] : [255, 253, 249]
  const ink = dark ? [224, 220, 214] : [56, 50, 44]
  const muted = dark ? [118, 122, 128] : [168, 160, 150]
  const accent = dark ? [204, 152, 98] : [172, 118, 68]
  return png(w, h, (x, y, px, py) => {
    if (py < 44) return bar
    const slot = (py - 60) % 116
    if (py >= 60 && slot < 96 && x > 0.06 && x < 0.94) {
      if (slot > 16 && slot < 30 && x > 0.12 && x < 0.58) return ink
      if (slot > 42 && slot < 52 && x > 0.12 && x < 0.84) return muted
      if (slot > 62 && slot < 72 && x > 0.12 && x < 0.68) return muted
      if (slot > 14 && slot < 36 && x > 0.78 && x < 0.88) return accent
      return card
    }
    return bg
  })
}

function desktopShot(w, h, dark = false) {
  const bg = dark ? [26, 28, 32] : [247, 244, 238]
  const rail = dark ? [34, 37, 42] : [236, 231, 222]
  const card = dark ? [44, 48, 55] : [255, 253, 249]
  const ink = dark ? [224, 220, 214] : [56, 50, 44]
  const muted = dark ? [116, 120, 126] : [172, 164, 154]
  const accent = dark ? [204, 152, 98] : [172, 118, 68]
  return png(w, h, (x, y) => {
    if (x < 0.24) return rail
    if (x > 0.28 && x < 0.94 && y > 0.12 && y < 0.88) {
      if (y > 0.18 && y < 0.24 && x > 0.32 && x < 0.56) return accent
      if (y > 0.30 && y < 0.35 && x > 0.32 && x < 0.78) return ink
      if (y > 0.42 && y < 0.46 && x > 0.32 && x < 0.84) return muted
      if (y > 0.52 && y < 0.56 && x > 0.32 && x < 0.70) return muted
      return card
    }
    return bg
  })
}

const pictures = {
  'Shots/phone-light.png': { w: 390, h: 844, data: phoneShot(390, 844, false) },
  'Shots/phone-dark.png': { w: 390, h: 844, data: phoneShot(390, 844, true) },
  'Shots/phone-tall.png': { w: 390, h: 2000, data: phoneShot(390, 2000, true) },
  'Shots/desktop-light.png': { w: 1440, h: 900, data: desktopShot(720, 450, false) },
  'Shots/desktop-dark.png': { w: 1440, h: 900, data: desktopShot(720, 450, true) },
  'Shots/square.png': { w: 800, h: 800, data: desktopShot(400, 400, false) },
  '98_ATTACHMENTS/sample/phone-upload.png': { w: 1179, h: 2556, data: phoneShot(390, 844, false) },
}

const imageFile = (p, alt) => ({
  path: p, root: 'vault', name: path.basename(p), type: 'image/png',
  size: pictures[p].data.length, width: pictures[p].w, height: pictures[p].h,
  ...(alt ? { alt } : {}), refs: [p],
})

const at = '2026-11-23T10:00:00Z'
const threadId = 'a-quieter-week'
const messages = [
  { id: 'm1', author: 'you', kind: 'message', at, text: 'Can you share the onboarding captures across phone and desktop?' },
  {
    id: 'm2', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Here is the standard phone screenshot (390×844):\n\n![Phone onboarding view in light mode, showing the welcome card and sign-in button](Shots/phone-light.png)\n\nAnd here is the full-page phone capture (390×2000):\n\n![Full-page phone capture of the onboarding screen showing every section down the page](Shots/phone-tall.png)',
    attachments: [
      imageFile('Shots/phone-light.png', 'Phone onboarding view in light mode, showing the welcome card and sign-in button'),
      imageFile('Shots/phone-tall.png', 'Full-page phone capture of the onboarding screen showing every section down the page'),
    ],
  },
  {
    id: 'm3', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Desktop landscape and square views:\n\n![Desktop workspace layout at 1440px with the list column and thread open](Shots/desktop-light.png)\n\nSquare theme preview:\n\n![Square swatch preview for the theme picker](Shots/square.png)',
    attachments: [
      imageFile('Shots/desktop-light.png', 'Desktop workspace layout at 1440px with the list column and thread open'),
      imageFile('Shots/square.png', 'Square swatch preview for the theme picker'),
    ],
  },
  {
    id: 'm4', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Three captures together — two desktop, one phone:\n\n![Desktop light mode workspace](Shots/desktop-light.png)\n![Desktop dark mode workspace](Shots/desktop-dark.png)\n![Phone light mode onboarding screen](Shots/phone-light.png)',
    attachments: [
      imageFile('Shots/desktop-light.png', 'Desktop light mode workspace'),
      imageFile('Shots/desktop-dark.png', 'Desktop dark mode workspace'),
      imageFile('Shots/phone-light.png', 'Phone light mode onboarding screen'),
    ],
  },
  {
    id: 'm5', author: 'sprout', kind: 'message', at, engine: 'claude-code', did: [],
    text: 'Two phone captures side by side:\n\n![Phone light mode onboarding](Shots/phone-light.png)\n![Phone dark mode onboarding](Shots/phone-dark.png)',
    attachments: [
      imageFile('Shots/phone-light.png', 'Phone light mode onboarding'),
      imageFile('Shots/phone-dark.png', 'Phone dark mode onboarding'),
    ],
  },
  {
    id: 'm6', author: 'you', kind: 'message', at, text: 'Here is what I see on my phone right now:',
    attachments: [{
      path: '98_ATTACHMENTS/sample/phone-upload.png',
      name: 'IMG_4821.png',
      type: 'image/png',
      size: pictures['98_ATTACHMENTS/sample/phone-upload.png'].data.length,
    }],
  },
]

const metrics = []
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
})

try {
  for (const width of [1440, 390]) for (const mode of ['light', 'dark']) {
    const wide = width === 1440
    const app = await oneProductFixture()
    const record = await app.f.api('GET', `/threads/${threadId}`)
    record.thread.unread = 0
    const json = (res, body, status = 200) => { res.writeHead(status, { 'content-type': 'application/json' }); res.end(JSON.stringify(body)) }
    app.routes[`GET /v1/threads/${threadId}`] = (req, res) => json(res, { ...record, messages, has_more: false, turn: null, turns: [] })
    app.routes[`GET /v1/threads/${threadId}/steps`] = (req, res) => json(res, { error: 'No full record' }, 404)
    const appearance = { theme: 'latte', mode, size: 100, options: {} }
    app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, { phone: appearance, desktop: appearance })

    const context = await browser.newContext({
      viewport: { width, height: wide ? 900 : 844 },
      colorScheme: mode,
      isMobile: !wide,
      hasTouch: !wide,
      reducedMotion: 'reduce',
    })
    await context.route('**/api/files/download**', (route) => {
      const p = new URL(route.request().url()).searchParams.get('path')
      const pic = pictures[p]
      return pic ? route.fulfill({ contentType: 'image/png', body: pic.data }) : route.fulfill({ status: 404, body: 'not found' })
    })

    const page = await context.newPage()
    const errors = []
    page.setDefaultTimeout(15000)
    page.on('pageerror', (e) => errors.push(e.message))

    const settle = async () => {
      await page.mouse.move(0, 0)
      await page.evaluate(async () => {
        document.activeElement?.blur()
        await document.fonts.ready
        await new Promise(requestAnimationFrame)
        await new Promise(requestAnimationFrame)
      })
      await page.waitForTimeout(250)
    }

    const scrollToMessage = async (mid, offset = 56) => {
      await page.locator(`[data-message-id="${mid}"]`).evaluate((node, off) => {
        const pane = node.closest('.tt-stream')
        if (pane) pane.scrollTop += node.getBoundingClientRect().top - pane.getBoundingClientRect().top - off
      }, offset)
      await settle()
    }

    const tag = `${baseline ? 'before' : 'after'}-${width}-${mode}`
    try {
      await page.goto(`${app.url}/threads/${threadId}`)
      await page.locator('[data-message-id="m6"]').waitFor()
      for (const mid of ['m2', 'm3', 'm4', 'm5', 'm6']) {
        await scrollToMessage(mid)
      }
      await page.waitForFunction(() => [...document.querySelectorAll('.reply-image img, .message-image img')].every((i) => i.complete && i.naturalWidth > 0))
      await page.waitForFunction(() => [...document.querySelectorAll('.reply-figure')].every((f) => /\bloaded\b/.test(f.className)))

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
      assert.equal(overflow, false, `${tag}: horizontal overflow`)

      const measured = await page.evaluate(() => {
        const box = (el) => {
          if (!el) return null
          const r = el.getBoundingClientRect()
          return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), bottom: Math.round(r.bottom) }
        }
        const readFigs = (mid) => [...document.querySelectorAll(`[data-message-id="${mid}"] .reply-figure`)].map((f) => ({
          img: box(f.querySelector('.reply-image')),
          cap: box(f.querySelector('.reply-caption')),
        }))
        const ownerEl = document.querySelector('[data-message-id="m6"] .message-image')
        const ownerImg = ownerEl?.querySelector('img')
        return {
          m2: readFigs('m2'),
          m3: readFigs('m3'),
          m4: readFigs('m4'),
          m5: readFigs('m5'),
          owner: {
            box: box(ownerEl),
            img: box(ownerImg),
            fit: ownerImg ? getComputedStyle(ownerImg).objectFit : null,
          },
        }
      })

      if (baseline) {
        if (wide) {
          assert.ok(measured.m2[0].img.w < 110, `${tag}: baseline portrait expected < 110px, got ${measured.m2[0].img.w}`)
          assert.ok(measured.m2[0].cap.w < 110, `${tag}: baseline portrait caption expected < 110px, got ${measured.m2[0].cap.w}`)
          assert.ok(measured.m2[1].img.w < 30, `${tag}: baseline tall portrait expected < 30px, got ${measured.m2[1].img.w}`)
          assert.ok(measured.m4[2].img.w < 110, `${tag}: baseline mixed-row portrait expected < 110px, got ${measured.m4[2].img.w}`)
        }
      } else {
        // Portrait standard phone + tall phone
        assert.ok(measured.m2[0].img.w >= 230 && measured.m2[0].img.h <= 530, `${tag}: m2 phone ${JSON.stringify(measured.m2[0])}`)
        assert.ok(measured.m2[1].img.w >= 225 && measured.m2[1].img.h <= 530, `${tag}: m2 tall ${JSON.stringify(measured.m2[1])}`)
        assert.ok(measured.m2[0].cap.w >= (wide ? 420 : 340), `${tag}: m2 phone caption measure ${JSON.stringify(measured.m2[0].cap)}`)
        assert.ok(measured.m2[1].cap.w >= (wide ? 420 : 340), `${tag}: m2 tall caption measure ${JSON.stringify(measured.m2[1].cap)}`)

        // Landscape and square preserved
        assert.ok(measured.m3[0].img.w >= (wide ? 680 : 350), `${tag}: m3 landscape ${JSON.stringify(measured.m3[0])}`)
        assert.ok(Math.abs(measured.m3[1].img.w - measured.m3[1].img.h) <= 2, `${tag}: m3 square ${JSON.stringify(measured.m3[1])}`)

        // Multi-image: 2 desktop + 1 phone
        if (wide) {
          assert.equal(measured.m4[0].img.y, measured.m4[1].img.y, `${tag}: m4 desktops share row 1`)
          assert.ok(measured.m4[2].img.y > measured.m4[0].img.bottom, `${tag}: m4 phone moves to row 2`)
          assert.ok(measured.m4[2].img.w >= 230 && measured.m4[2].cap.w >= 420, `${tag}: m4 phone size & caption ${JSON.stringify(measured.m4[2])}`)
          // Multi-image: 2 phones share one equal-height row
          assert.equal(measured.m5[0].img.y, measured.m5[1].img.y, `${tag}: m5 phones share row`)
          assert.ok(Math.abs(measured.m5[0].img.h - measured.m5[1].img.h) <= 2, `${tag}: m5 phones match height`)
          assert.ok(measured.m5[0].img.w >= 230 && measured.m5[0].cap.w >= 220, `${tag}: m5 phone width & caption ${JSON.stringify(measured.m5[0])}`)
        } else {
          assert.ok(measured.m4[1].img.y > measured.m4[0].img.bottom, `${tag}: m4 stacks on phone`)
          assert.ok(measured.m5[1].img.y > measured.m5[0].img.bottom, `${tag}: m5 stacks on phone`)
          assert.ok(measured.m4[2].cap.w >= 340 && measured.m5[0].cap.w >= 340, `${tag}: captions use full phone column`)
        }

        // Owner's attached phone screenshot
        assert.equal(measured.owner.fit, 'contain', `${tag}: owner image keeps object-fit: contain`)
        assert.ok(measured.owner.img.w >= 200 && measured.owner.img.h <= 470, `${tag}: owner phone size ${JSON.stringify(measured.owner)}`)
        assert.ok(Math.abs(measured.owner.img.w / measured.owner.img.h - 1179 / 2556) < 0.03, `${tag}: owner phone box matches aspect`)
      }

      if (out) {
        await scrollToMessage('m2')
        await page.screenshot({ path: path.join(out, `${tag}-portrait.png`), animations: 'disabled' })
        if (!baseline) {
          await scrollToMessage('m3')
          await page.screenshot({ path: path.join(out, `${tag}-landscape.png`), animations: 'disabled' })
          await scrollToMessage('m4')
          await page.screenshot({ path: path.join(out, `${tag}-multi.png`), animations: 'disabled' })
          await scrollToMessage('m6')
          await page.screenshot({ path: path.join(out, `${tag}-owner.png`), animations: 'disabled' })
        }
      }

      // Tap portrait image open -> step -> close with Escape; tap owner phone image -> close button.
      await scrollToMessage('m4')
      await page.locator('[data-message-id="m4"] .reply-image').nth(2).click()
      await page.locator('.image-view').waitFor()
      await page.waitForFunction(() => {
        const img = document.querySelector('.image-view-img')
        return Boolean(img?.complete && img.naturalWidth > 0)
      })
      assert.equal(await page.locator('.image-view-count').innerText(), '3 of 3')
      if (out && !baseline) {
        await settle()
        await page.screenshot({ path: path.join(out, `${tag}-viewer.png`), animations: 'disabled' })
      }
      await page.keyboard.press('ArrowLeft')
      assert.equal(await page.locator('.image-view-count').innerText(), '2 of 3')
      await page.keyboard.press('Escape')
      await page.locator('.image-view').waitFor({ state: 'detached' })

      await scrollToMessage('m6')
      await page.locator('[data-message-id="m6"] .message-image').click()
      await page.locator('.image-view').waitFor()
      await page.locator('.image-view-close').click()
      await page.locator('.image-view').waitFor({ state: 'detached' })

      assert.deepEqual(errors, [], `${tag}: page errors`)
      metrics.push({ tag, measured })
      console.log(`PASS ${tag}`)
    } finally {
      await context.close()
      await app.close()
    }
  }
} finally {
  await browser.close()
}

if (out) await writeFile(path.join(out, `${baseline ? 'before' : 'after'}-metrics.json`), JSON.stringify(metrics, null, 2) + '\n')
