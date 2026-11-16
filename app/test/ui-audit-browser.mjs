import { observeAudio } from './audio-probe.mjs'
// Full surface walk against synthetic host answers through the real relay.
// Screenshots are reproducible; no host session, account or live data is used.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { oneProductFixture } from './one-product-fixture.mjs'
import { filesHost } from './files-host.js'
import { createFakeSchedules } from '../tools/fake-schedules.js'
import { row, readAnswer } from './fixtures/files-contract.js'

const out = process.env.UI_AUDIT_EVIDENCE || process.env.TMPDIR + '/ui-audit'
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage']
})
const source = await readFile('public/app.js', 'utf8'),
  reports = [],
  clocks = []
const title = 'Architecture, features and cleanup: making room for a calmer week with a long conversation'
const note = [
  '# The plan',
  '',
  'A clear place to collect the next useful step.',
  '',
  '## Next steps',
  '',
  'Choose one thing and leave room for questions.',
  '',
  '### What to bring',
  '',
  'The notes, a small example, and a little patience.',
  '',
  '#### Before we begin',
  '',
  'Check the details together.',
  '',
  '- [ ] Bring the notes',
  '- [x] Read the first draft',
  '',
  '| Question | Next step |',
  '| --- | --- |',
  '| What matters? | Make room to think |',
  '',
  '```js',
  'const next = "A long line keeps its own sideways scroll while the conversation stays within the screen, with enough room for all the useful details and a little space for the next question.";',
  '```',
  '',
  '> Leave a little room for the unexpected.',
  ''
].join('\n')
const json = (res, body, status = 200) => {
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(JSON.stringify(body))
}
const hook =
  '\nglobalThis.__audit = { state, theme, threads, files, picker, apps, activity, running, schedules, schedule, goChats, goFiles, goApps, goActivity, goProfile, goSettings, closeSheet, openSheet, openMenu, closeMenus, pickFromFiles, providerSignIn, loadAgents, loadProviders };'

function pdfFixture() {
  const text = 'BT /F1 20 Tf 60 720 Td (A fixture report) Tj ET'
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${Buffer.byteLength(text)} >>\nstream\n${text}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'
  ]
  let data = '%PDF-1.4\n'
  const offsets = [0]
  objects.forEach((object, i) => {
    offsets.push(Buffer.byteLength(data))
    data += `${i + 1} 0 obj\n${object}\nendobj\n`
  })
  const start = Buffer.byteLength(data)
  data += `xref\n0 ${offsets.length}\n0000000000 65535 f \n${offsets
    .slice(1)
    .map((n) => `${String(n).padStart(10, '0')} 00000 n \n`)
    .join('')}trailer\n<< /Size ${offsets.length} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF`
  return Buffer.from(data)
}

async function audit(page) {
  return page.evaluate(() => {
    const visible = (node) => {
      const r = node.getBoundingClientRect(),
        css = getComputedStyle(node)
      if (
        !r.width ||
        !r.height ||
        r.bottom <= 0 ||
        r.top >= innerHeight ||
        r.right <= 0 ||
        r.left >= innerWidth ||
        css.visibility !== 'visible' ||
        +css.opacity === 0 ||
        node.closest('[inert]')
      )
        return false
      const hit = document.elementFromPoint(
        Math.max(1, Math.min(innerWidth - 1, r.x + r.width / 2)),
        Math.max(1, Math.min(innerHeight - 1, r.y + r.height / 2))
      )
      return hit && (node.contains(hit) || hit.contains(node))
    }
    const name = (node) => (node.getAttribute('aria-label') || node.textContent.trim() || node.className).slice(0, 100)
    const rgba = (s) => (s.match(/[\d.]+/g) || []).map(Number)
    const over = (a, b) => {
      const alpha = a[3] ?? 1
      return a.slice(0, 3).map((n, i) => n * alpha + b[i] * (1 - alpha))
    }
    const background = (node) => {
      const chain = []
      for (let p = node; p; p = p.parentElement) chain.unshift(p)
      const ground = chain.reduce((bg, p) => over(rgba(getComputedStyle(p).backgroundColor), bg), [255, 255, 255])
      // The positioned code-copy action sits over its sibling pre, which paints
      // the ground underneath it even though the wrapper is transparent.
      const code = node.closest('.copyblk') && node.closest('.code-frame')?.querySelector('pre')
      return code ? over(rgba(getComputedStyle(code).backgroundColor), ground) : ground
    }
    const luminance = (rgb) =>
      rgb
        .map((n) => {
          n /= 255
          return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4
        })
        .reduce((n, v, i) => n + v * [0.2126, 0.7152, 0.0722][i], 0)
    const contrasts = []
    for (const node of document.querySelectorAll('body *')) {
      const editableText = node.matches(
        'textarea,input[type="text"],input[type="search"],input[type="email"],input[type="number"]'
      )
      if (
        !visible(node) ||
        (!editableText && ![...node.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) ||
        node.matches(':disabled') ||
        node.closest('svg,button:disabled,[aria-disabled="true"],.tt-board-item.done')
      )
        continue
      // Opacity is allowed for disabled controls only, never body copy.
      const css =
          editableText && !node.value && node.placeholder
            ? getComputedStyle(node, '::placeholder')
            : getComputedStyle(node),
        bg = background(node),
        color = rgba(css.color)
      let alpha = color[3] ?? 1
      for (let p = node; p; p = p.parentElement) alpha *= +getComputedStyle(p).opacity
      color[3] = alpha
      const ink = over(color, bg),
        l = [luminance(ink), luminance(bg)].sort((a, b) => b - a)
      const ratio = (l[0] + 0.05) / (l[1] + 0.05)
      if (alpha > 0.01 && ratio < 4.49)
        contrasts.push({ name: name(node), class: node.className, ratio: +ratio.toFixed(2), color: css.color, fontSize: css.fontSize, fontWeight: css.fontWeight, bg })
    }
    const targets = [
      ...document.querySelectorAll(
        'button,select,input:not([type="file"]):not([type="hidden"]),[role="button"],summary,.pillbtn[href]'
      )
    ]
      .filter(
        (n) =>
          visible(n) && !n.disabled && !n.matches('.scrim,input[type="checkbox"]:not(.md-task),input[type="radio"]')
      )
      .map((n) => {
        const r = n.getBoundingClientRect()
        return { name: name(n), class: n.className, width: +r.width.toFixed(1), height: +r.height.toFixed(1) }
      })
      .filter((r) => r.width < 43.9 || r.height < 43.9)
    return { overflow: document.documentElement.scrollWidth > innerWidth, targets, contrasts }
  })
}

try {
  for (const width of [390, 1440])
    for (const [theme, mode] of [
      ['latte', 'light'],
      ['latte', 'dark'],
      ['plain', 'light']
    ]) {
      if (process.env.UI_AUDIT_CASE && process.env.UI_AUDIT_CASE !== `${width}-${theme}-${mode}`) continue
      const app = await oneProductFixture(),
        id = 'a-quieter-week',
        kind = width === 390 ? 'phone' : 'desktop',
        label = `${width}-${theme}-${mode}`
      const values = { phone: { theme, mode, size: 100 }, desktop: { theme, mode, size: 100 } }
      app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
      app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
        Object.assign(values[kind], JSON.parse(raw)[kind])
        json(res, values)
      }
      const files = filesHost(),
        picture = await readFile('public/icon-32.png'),
        pdf = pdfFixture()
      for (const [key, handler] of Object.entries(files.routes))
        if (key.includes('/v1/files/') || key.includes('/v1/limits')) app.routes[key] = handler
      Object.assign(
        app.f.providers.find((p) => p.id === 'claude-code'),
        {
          sign_in: {
            kind: 'auth-code',
            start: '/v1/providers/claude-code/login',
            status: '/v1/providers/claude-code/login',
            cancel: '/v1/providers/claude-code/login',
            label: 'Sign in'
          }
        }
      )
      const record = await app.f.api('GET', `/threads/${id}`)
      const messages = Array.from({ length: 24 }, (_, i) => ({
        id: `audit-${i}`,
        kind: 'message',
        author: i % 2 ? 'sprout' : 'you',
        engine: 'claude-code',
        at: '2026-11-12T18:45:00Z',
        did: [],
        text:
          i === 23
            ? note
            : `A little room for the next step, ${i + 1}.\n\nLet’s collect the details here and give each question time to settle. A long conversation should still be easy to read, leave, and return to.`
      }))
      app.routes[`GET /v1/threads/${id}`] = (req, res) =>
        json(res, { ...record, thread: { ...record.thread, title }, messages, has_more: true, turn: null, turns: [] })
      app.routes[`GET /v1/threads/${id}/branch`] = (req, res) =>
        json(res, {
          thread: id,
          message: 'audit-23',
          persona: 'sprout',
          people: ['sprout', 'quill', 'scout', 'ledger', 'pip'],
          summarizer: { system_instruction: 'Fixture', input: 'Fixture', answer: { title: 'string', draft: 'string' } },
          fallback: { title: 'Room for the next step', draft: 'Let’s make room to consider the next step.' }
        })
      app.routes['GET /v1/files/read'] = (req, res, url) =>
        url.searchParams.get('path') === 'Notes/plan.md'
          ? json(res, readAnswer(row('vault', 'Notes/plan.md'), note, 'sha256:audit'))
          : files.routes['GET /v1/files/read'](req, res, url)
      app.routes['GET /v1/files/download'] = (req, res, url) => {
        const type = { 'Notes/pic.png': ['image/png', picture], 'Notes/report.pdf': ['application/pdf', pdf] }[
          url.searchParams.get('path')
        ]
        if (!type) return files.routes['GET /v1/files/download'](req, res, url)
        res.writeHead(200, { 'content-type': type[0] })
        res.end(type[1])
      }
      const owner = app.f.agents[0].id,
        schedules = createFakeSchedules(app.f.agents.map((a) => a.id))
      app.routes['GET /v1/schedules'] = (req, res, url) => {
        const answer = schedules.handleAnswer('GET', '/v1/schedules', url)
        json(res, answer.body, answer.status)
      }
      app.routes['GET /v1/activity'] = (req, res) =>
        json(res, {
          jobs: {
            slots: [
              { job: 'audit-job', title, project: 'Notebook', briefed_by: owner, since: '2026-11-12T18:00:00Z' },
              {
                job: 'project-job',
                title: 'Review the project notes',
                project: 'Notebook',
                since: '2026-11-12T18:00:00Z'
              }
            ],
            landing: [],
            queue: []
          },
          items: [
            {
              id: 'audit-landed',
              kind: 'job',
              title,
              agent: owner,
              outcome: 'completed',
              landing: 'landed',
              started: '2026-11-12T18:00:00Z',
              ts: '2026-11-12T18:30:00Z'
            }
          ],
          total: 1,
          next_offset: null
        })
      app.routes['GET /v1/sessions/live'] = (req, res) =>
        json(res, {
          sessions: [
            {
              id: 'resident',
              agent: owner,
              kind: 'thread',
              thread_id: id,
              thread_title: title,
              state: 'working',
              rss_mb: 256
            }
          ],
          box: { ram_total_mb: 32768, ram_used_mb: 16384, can_start: 12 }
        })
      app.routes['GET /v1/sessions/live/events'] = (req, res) => {
        res.writeHead(200, { 'content-type': 'text/event-stream' })
        res.write(': open ping=15000\n\n')
      }
      app.routes['GET /v1/apps'] = (req, res) =>
        json(res, {
          sources: ['yours'],
          apps: [
            {
              name: 'Notebook',
              url: 'https://notebook.example.com/',
              owner,
              source: 'yours',
              access: 'private',
              frame: true,
              expires: '2026-11-25',
              also_at: []
            }
          ]
        })
      app.routes['POST /v1/providers/claude-code/login'] = (req, res) =>
        json(
          res,
          {
            state: 'pending',
            url: 'https://example.org/sign-in',
            code: null,
            accepts_code: true,
            instructions: 'Open the URL, sign in, and paste the code here.',
            login: null
          },
          201
        )
      app.routes['GET /v1/providers/claude-code/login'] = app.routes['POST /v1/providers/claude-code/login']
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 1000 },
        locale: 'en-US',
        timezoneId: 'America/Los_Angeles',
        colorScheme: mode,
        isMobile: width === 390,
        hasTouch: width === 390,
        reducedMotion: 'reduce'
      })
      await observeAudio(context)
    await context.route('**/app.js', (route) =>
        route.fulfill({ contentType: 'text/javascript', body: source + hook })
      )
      await context.route('https://notebook.example.com/**', (route) =>
        route.fulfill({
          contentType: 'text/html',
          body: '<!doctype html><meta name="viewport" content="width=device-width"><title>Notebook fixture</title><body style="font:18px system-ui;padding:24px"><h1>Notebook</h1><p>A framed app, served by the browser fixture.</p></body>'
        })
      )
      await context.route('**/api/health', (route) =>
        route.fulfill({ json: { configured: true, ok: true, speech: true } })
      )
      await context.route('**/api/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
      await context.route('**/api/speech', (route) =>
        route.fulfill({ status: 503, json: { error: 'Audio is temporarily unavailable. Try again.' } })
      )
      await context.route('**/api/files/preview?**', (route) =>
        route.fulfill({
          contentType: 'text/html',
          body: '<!doctype html><meta name="viewport" content="width=device-width"><h1>A small page</h1><p>Preview in an isolated frame.</p>'
        })
      )
      const page = await context.newPage(),
        errors = [],
        states = []
      page.setDefaultTimeout(12000)
      page.on('pageerror', (error) => errors.push(error.message))
      const settle = async () => {
        await page.waitForTimeout(100)
        await page.evaluate(async () => {
          await document.fonts.ready
          await new Promise(requestAnimationFrame)
          await new Promise(requestAnimationFrame)
          // Measure settled text, not an entrance animation's transient opacity.
          // Infinite working indicators keep running and must not hold the tour.
          await Promise.allSettled(document.getAnimations()
            .filter(animation => animation.effect?.getComputedTiming().iterations !== Infinity)
            .map(animation => animation.finished))
        })
      }
      const shot = async (name) => {
        if (process.env.UI_AUDIT_TRACE) console.log(label, name)
        await settle()
        await page.mouse.move(0, 0)
        const checks = await audit(page)
        assert.equal(checks.overflow, false, `${label} ${name}: page overflow`)
        await page.screenshot({ path: path.join(out, `${name}-${label}.png`), animations: 'disabled' })
        await page.keyboard.press('Tab')
        const focus = await page.evaluate(() => {
          const before = document.activeElement,
            checked = [],
            missing = [],
            classes = new Set()
          for (const node of document.querySelectorAll(
            'button:not(:disabled),select:not(:disabled),input:not(:disabled):not([type="file"]):not([type="checkbox"]):not([type="radio"]),summary,[role="button"]'
          )) {
            const box = node.getBoundingClientRect(),
              css = getComputedStyle(node)
            if (
              !box.width ||
              !box.height ||
              box.top < 0 ||
              box.bottom > innerHeight ||
              node.closest('[inert]') ||
              css.visibility !== 'visible' ||
              +css.opacity === 0 ||
              css.pointerEvents === 'none' ||
              node.matches('.scrim') ||
              classes.has(node.className)
            )
              continue
            const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
            if (!hit || !(node.contains(hit) || hit.contains(node))) continue
            classes.add(node.className)
            node.focus({ preventScroll: true })
            const style = getComputedStyle(node),
              wrapper = node.matches('.file-speech-slider') ? node.nextElementSibling : node.closest('.field'),
              wrapperStyle = wrapper ? getComputedStyle(wrapper) : null,
              label = node.getAttribute('aria-label') || node.className || node.localName
            checked.push(label)
            if (node !== document.activeElement) missing.push(`${label} (not focusable)`)
            else if (
              node.matches(':focus-visible') &&
              (style.outlineStyle === 'none' || parseFloat(style.outlineWidth) === 0) &&
              style.boxShadow === 'none' &&
              (!wrapperStyle || wrapperStyle.outlineStyle === 'none' || parseFloat(wrapperStyle.outlineWidth) === 0)
            )
              missing.push(label)
          }
          before?.focus?.({ preventScroll: true })
          return { checked, missing }
        })
        assert.deepEqual(checks.targets, [], `${label} ${name}: controls below 44px`)
        if (!process.env.UI_AUDIT_RECORD_CONTRAST) assert.deepEqual(checks.contrasts, [], `${label} ${name}: contrast below 4.5:1`)
        assert.deepEqual(focus.missing, [], `${label} ${name}: missing keyboard focus`)
        let hover = null
        const hoverSelector = {
          'files-home': '.fs-rootcard',
          'files-folder': '.fs-add',
          'files-editor': '.fs-tool',
          'files-viewer': '.segmented button:not(.on)',
          apps: '.apps-row',
          'settings-appearance': '.appearance-modes button:not(.on)'
        }[name]
        if (width === 1440 && hoverSelector) {
          const control = page.locator(hoverSelector).first(),
            ink = (node) => [getComputedStyle(node).backgroundColor, getComputedStyle(node).color, getComputedStyle(node).textDecorationLine]
          const before = await control.evaluate(ink)
          await control.hover()
          const after = await control.evaluate(ink)
          assert.notDeepEqual(before, after, `${name}: hover does not change the control`)
          hover = { before, after }
          await page.mouse.move(0, 0)
        }
        states.push({ name, ...checks, focus, hover })
        await writeFile(path.join(out, `progress-${label}.json`), JSON.stringify(states, null, 2))
      }
      const mutate = async (fn) => {
        await page.evaluate(fn)
        await settle()
      }
      const close = () =>
        mutate(() => {
          if (__audit.threads.st.open) {
            __audit.threads.st.open.configuration = null
            __audit.threads.st.open.branch = null
            __audit.threads.st.open.headerMenu = false
          }
          __audit.state.speakMenu = null
          __audit.closeSheet()
          __audit.closeMenus()
          __audit.picker.close()
          __audit.files.st.sheet = null
          __audit.schedule()
        })
      try {
        await page.goto(app.url)
        await page.locator('.tt-row').first().waitFor()
        await shot('chats')
        await mutate(() => {
          globalThis.__auditRows = __audit.threads.st.threads
          __audit.threads.st.threads = []
          __audit.schedule()
        })
        await shot('chats-empty')
        await mutate(() => {
          __audit.threads.st.threads = globalThis.__auditRows
          __audit.schedule()
        })
        await page.getByRole('button', { name: 'Search threads', exact: true }).click()
        await page.getByRole('searchbox').fill('week')
        await shot('search')
        await page.getByRole('searchbox').fill('no such thread')
        await shot('search-empty')
        await page.getByRole('button', { name: 'Close search', exact: true }).click()
        await mutate(() => {
          __audit.threads.st.error = 'Threads could not be reached. Please try again.'
          __audit.schedule()
        })
        await shot('chats-error')
        await page.getByRole('button', { name: 'New thread', exact: true }).click()
        await page.waitForFunction(() => __audit.threads.st.open && !__audit.threads.st.open.creating)
        await shot('new-thread')
        await page.getByRole('button', { name: 'Providers & models', exact: true }).click()
        await shot('new-thread-configuration')
        await close()
        await mutate(() => {
          __audit.threads.st.open.createFailed = true
          __audit.threads.st.open.error = 'A new thread could not be created. Try again.'
          __audit.schedule()
        })
        await shot('new-thread-error')
        await mutate(() => {
          __audit.threads.st.error = ''
          __audit.threads.open('a-quieter-week')
        })
        await page.locator('[data-message-id="audit-23"]').waitFor()
        await shot('thread-long')
        const counterfactual = page.locator('[data-message-id="audit-23"] .tt-footer-reply')
        await counterfactual.evaluate((n) => {
          n.style.minWidth = '30px'
          n.style.minHeight = '26px'
          n.style.width = '30px'
          n.style.height = '26px'
        })
        assert.ok(
          (await audit(page)).targets.some((n) => n.name === 'Reply'),
          'the touch guard must detect the original compact footer'
        )
        await counterfactual.evaluate((n) => n.removeAttribute('style'))
        await page.locator('[data-message-id="audit-23"] .md h1').evaluate((n) => n.scrollIntoView({ block: 'start' }))
        await shot('thread-markdown')
        await mutate(() => {
          __audit.threads.st.open.permissions.set('audit-permission', {
            request_id: 'audit-permission',
            session_id: 'fixture',
            persona: 'sprout',
            kind: 'permission',
            tool_name: 'Bash',
            input: { command: 'npm test' },
            suggestions: [
              { destination: 'session', type: 'addRules', behavior: 'allow', rules: [{ toolName: 'Bash' }] }
            ],
            decision_reason: 'Run the project checks before continuing.'
          })
          __audit.schedule()
        })
        await page.locator('.tt-approval').evaluate((n) => n.scrollIntoView({ block: 'center' }))
        await shot('thread-permission')
        await mutate(() => {
          __audit.threads.st.open.permissions.clear()
          __audit.schedule()
        })
        const heading = await page.locator('.tt-title').boundingBox()
        if (width === 1440) assert.ok(heading.width > 420, 'thread title must use the available header width')
        const messageSizes = await page
          .locator('[data-message-id="audit-23"] .md')
          .evaluate((n) => [...n.querySelectorAll('h1,h2,h3,h4')].map((n) => +parseFloat(getComputedStyle(n).fontSize)))
        assert.ok(
          messageSizes.every((v, i) => !i || messageSizes[i - 1] > v),
          `message hierarchy ${messageSizes}`
        )
        await page.getByTitle('Attach a file', { exact: true }).click()
        await shot('composer-attach-menu')
        await close()
        await page.getByRole('button', { name: 'Mention an agent', exact: true }).click()
        await shot('composer-mention-menu')
        await mutate(() => {
          __audit.threads.st.mention = null
          __audit.schedule()
        })
        await page.getByRole('button', { name: 'Providers & models', exact: true }).click()
        await shot('thread-providers')
        await close()
        await page.locator('[data-message-id="audit-23"] .speakbtn').click()
        await shot('reply-read-aloud-menu')
        await close()
        await page
          .locator('#draft')
          .fill('A longer thought, with room for the details.\n\nKeep the next step close to the question.')
        await page.locator('.expandbtn.on').click()
        await shot('composer-editor')
        await page.locator('.editor-done').click()
        await page.locator('#draft').fill('')
        await page.locator('[data-message-id="audit-23"] .tt-footer-branch').click()
        await page.locator('.tt-branch-opening').waitFor()
        await shot('new-thread-branch')
        await mutate(() => {
          __audit.threads.st.open.branch.error = 'The opening could not be prepared. Try again.'
          __audit.schedule()
        })
        await shot('branch-error')
        await close()
        await page.getByRole('button', { name: 'Thread actions', exact: true }).click()
        await shot('thread-actions')
        await page.getByRole('button', { name: 'Thread details', exact: true }).click()
        await shot('thread-details')
        await mutate(() => {
          __audit.threads.st.manage = false
          __audit.threads.st.open.boardOpen = true
          __audit.schedule()
        })
        await shot('board')
        await page.locator('.tt-item-actions .iconbtn').first().click()
        await shot('board-item-menu')
        await page.getByRole('button', { name: 'Move to…', exact: true }).click()
        await shot('board-move-menu')
        await page.keyboard.press('Escape')
        await page.locator('.tt-list-add').first().click()
        await shot('board-add-item')
        await mutate(() => {
          __audit.threads.st.open.addSection = null
          __audit.schedule()
        })
        await mutate(() => {
          __audit.threads.st.open.editing = true
          __audit.threads.st.open.editText = __audit.threads.st.open.board.markdown
          __audit.schedule()
        })
        await shot('board-editor')
        await mutate(() => {
          __audit.threads.st.open.editing = false
          __audit.threads.st.open.board.markdown = ''
          __audit.schedule()
        })
        await shot('board-empty')
        await mutate(() => {
          __audit.threads.st.open.boardError = 'The board could not be saved. Your text is still here.'
          __audit.schedule()
        })
        await shot('board-error')
        await mutate(() => {
          __audit.threads.st.open.boardOpen = false
          __audit.threads.st.open.boardError = ''
          __audit.threads.st.open.error = ''
          __audit.threads.st.open.messages = []
          __audit.schedule()
        })
        await shot('thread-empty')
        await mutate(() => {
          __audit.threads.st.open.readFailed = true
          __audit.threads.st.open.error = 'This conversation could not be read. Try again.'
          __audit.schedule()
        })
        await shot('thread-error')
        await mutate(() => {
          __audit.threads.st.open.readFailed = false
          __audit.threads.st.open.error = ''
          __audit.schedule()
        })
        await page.getByTitle('Attach a file', { exact: true }).click()
        await page.getByRole('button', { name: /Pick from Files/ }).click()
        await page.locator('.fs-picker').waitFor()
        await shot('attachment-picker-home')
        assert.equal(await page.locator('.fs-picker .surface-heading').count(), 0)
        assert.equal(await page.locator('.fs-picker .sheet-x').count(), 1)
        if (width === 390) {
          await page.locator('.fs-picker .sheet-top').evaluate((handle) => {
            const sheet = handle.closest('.fs-picker'),
              y = handle.getBoundingClientRect().top + 12
            const fire = (type, at) => {
              const event = new Event(type, { bubbles: true, cancelable: true })
              Object.defineProperty(event, 'touches', { value: type === 'touchend' ? [] : [{ clientY: at }] })
              handle.dispatchEvent(event)
            }
            fire('touchstart', y)
            fire('touchmove', y + sheet.getBoundingClientRect().height * 0.75)
            fire('touchend', y)
          })
          await page.locator('.fs-picker').waitFor({ state: 'detached' })
          await page.getByTitle('Attach a file', { exact: true }).click()
          await page.getByRole('button', { name: /Pick from Files/ }).click()
          await page.locator('.fs-picker').waitFor()
        }
        await mutate(() => __audit.picker.go('vault', 'Notes'))
        await page.locator('.fs-pickfile').first().waitFor()
        await shot('attachment-picker-folder')
        await mutate(() => {
          __audit.picker.st.rows = []
          __audit.schedule()
        })
        await shot('attachment-picker-empty')
        await mutate(() => {
          __audit.picker.st.error = 'Files could not be reached. Try again.'
          __audit.schedule()
        })
        await shot('attachment-picker-error')
        await close()
        await mutate(() => __audit.goFiles(null, ''))
        await page.locator('.fs-rootcard').first().waitFor()
        await shot('files-home')
        await mutate(() => {
          globalThis.__auditRoots = __audit.files.st.roots.items
          __audit.files.st.roots.items = []
          __audit.schedule()
        })
        await shot('files-home-empty')
        await mutate(() => {
          __audit.files.st.roots.error = 'Files could not be reached. Try again.'
          __audit.schedule()
        })
        await shot('files-home-error')
        await mutate(() => {
          __audit.files.st.roots.items = globalThis.__auditRoots
          __audit.files.st.roots.error = ''
          __audit.schedule()
        })
        await mutate(() => __audit.goFiles('vault', 'Notes'))
        await page.locator('.fs-entry').first().waitFor()
        await shot('files-folder')
        await page.locator('.fs-add').click()
        await shot('files-add-sheet')
        await close()
        await mutate(() => __audit.goFiles('vault', 'Notes/plan.md', { isFile: true }))
        await page.locator('.fs-preview h4').waitFor()
        await page.evaluate(() => {
          globalThis.__auditNote = __audit.files.st.file.text
        })
        await shot('files-viewer')
        await page.locator('.fs-preview').evaluate((n) => {
          n.scrollTop = n.scrollHeight
        })
        await shot('files-viewer-end')
        await page.locator('.fs-preview').evaluate((n) => {
          n.scrollTop = 0
        })
        assert.equal(await page.locator('.fs-edtitle').innerText(), 'plan')
        const noteSizes = await page.locator('.fs-preview').evaluate((n) =>
          [...n.querySelectorAll('h1,h2,h3,h4')].map((n) => {
            const s = getComputedStyle(n)
            return { size: parseFloat(s.fontSize), above: parseFloat(s.marginTop), below: parseFloat(s.marginBottom) }
          })
        )
        assert.ok(
          noteSizes.every((v, i) => !i || (v.above > v.below && noteSizes[i - 1].size > v.size)),
          `note hierarchy ${JSON.stringify(noteSizes)}`
        )
        if (width === 1440) {
          const labels = await page
            .locator('.fs-file .segmented button')
            .evaluateAll((nodes) => nodes.map((n) => ({ width: n.clientWidth, content: n.scrollWidth })))
          assert.equal(labels.length, 2)
          assert.ok(
            labels.every((n) => n.width >= n.content),
            `segmented clipping ${JSON.stringify(labels)}`
          )
        }
        await mutate(() => {
          __audit.files.st.file.text = ''
          __audit.files.st.file.draft = ''
          __audit.schedule()
        })
        await shot('files-viewer-empty')
        await mutate(() => {
          __audit.files.st.file.text = globalThis.__auditNote
          __audit.files.st.file.draft = globalThis.__auditNote
          __audit.files.st.file.error = 'The file could not be read. Try again.'
          __audit.files.st.file.status = 503
          __audit.schedule()
        })
        await shot('files-viewer-error')
        await mutate(() => {
          __audit.files.st.file.error = ''
          __audit.files.st.file.status = 200
          __audit.files.setMode('edit')
        })
        await shot('files-editor')
        await page.locator('.fs-filemenu').click()
        await shot('files-actions-sheet')
        await close()
        await mutate(() => __audit.files.setMode('preview'))
        await page.locator('.fs-speak').click()
        await shot('files-read-aloud')
        await page.locator('.file-speech-action').click()
        await page.locator('.sheet.file-speech [role="alert"]').waitFor()
        await shot('files-read-aloud-error')
        await close()
        await mutate(() => __audit.files.setMode('edit'))
        await mutate(() => {
          __audit.files.st.file.failed = true
          __audit.files.st.file.flash = 'The file could not be saved.'
          __audit.schedule()
        })
        await shot('files-editor-error')
        await mutate(() => {
          __audit.files.st.file.failed = false
          __audit.files.st.file.flash = ''
          __audit.files.st.file.error = ''
          __audit.goFiles('spaces', '')
        })
        await page.getByText('Nothing here yet.', { exact: true }).waitFor()
        await shot('files-folder-empty')
        await mutate(() => {
          __audit.files.st.listing.error = 'This folder could not be read. Try again.'
          __audit.schedule()
        })
        await shot('files-folder-error')
        for (const [name, file] of [
          ['image', 'pic.png'],
          ['image-error', 'fake.png'],
          ['pdf', 'report.pdf'],
          ['download', 'sheet.bin'],
          ['html', 'page.html']
        ]) {
          await page.evaluate((file) => __audit.goFiles('vault', `Notes/${file}`, { isFile: true }), file)
          await page.waitForFunction(() => __audit.files.st.file && !__audit.files.st.file.loading)
          await shot(`files-${name}`)
        }
        await mutate(() => __audit.files.setMode('edit'))
        await shot('files-html-source')
        await mutate(() => __audit.goActivity())
        await page.locator('.activity-job').first().waitFor()
        await shot('activity-now')
        await page.locator('.activity-cancel').first().click()
        await page.locator('dialog.activity-confirm[open]').waitFor()
        await shot('activity-cancel')
        await mutate(() => {
          __audit.activity.st.confirm.error = 'This job has already finished.'
          __audit.schedule()
        })
        await shot('activity-cancel-error')
        await mutate(() => __audit.activity.dismiss())
        assert.equal(
          await page
            .locator('.run-title')
            .first()
            .evaluate((n) => getComputedStyle(n).webkitLineClamp),
          '2'
        )
        assert.equal(await page.locator('.activity-job .activity-briefing-face').count(), 1)
        assert.equal(await page.locator('.activity-job .activity-engineer-mark').count(), 2)
        await page
          .locator('.activity-item')
          .last()
          .evaluate((n) => n.scrollIntoView({ block: 'center' }))
        await shot('activity-earlier')
        await page
          .locator('.activity-item')
          .filter({ has: page.locator('.activity-action', { hasText: /^landed$/ }) })
          .click()
        await page.locator('dialog.activity-detail[open]').waitFor()
        await shot('activity-detail')
        await mutate(() => __audit.activity.closeDetail())
        await mutate(() => {
          __audit.activity.st.now = { slots: [], queue: [], landing: [] }
          __audit.activity.st.items = []
          __audit.activity.st.kind = 'job'
          __audit.activity.st.lastFetch = Date.now()
          __audit.threads.st.threads = []
          __audit.running.st.records.clear()
          __audit.running.st.liveRecords.clear()
          __audit.schedule()
        })
        await shot('activity-empty')
        await mutate(() => {
          __audit.activity.st.error = 'Activity could not be reached. Try again.'
          __audit.schedule()
        })
        await shot('activity-error')
        await page.evaluate((owner) => __audit.goProfile(owner), owner)
        await page.locator('.profile-hero').waitFor()
        await shot('agent-profile')
        await mutate(() => {
          __audit.state.profileTab = 'schedule'
          __audit.schedule()
        })
        await page.locator('.schedule-open').first().waitFor()
        await shot('agent-schedules')
        await page.locator('.schedule-add').click()
        await page.locator('.schedule-editor').evaluate((n) => n.scrollIntoView({ block: 'start' }))
        await shot('schedule-new')
        await page.getByRole('button', { name: 'One-off', exact: true }).click()
        await shot('schedule-one-off')
        await mutate(() => {
          __audit.schedules.st.editor.error = 'The schedule could not be saved.'
          __audit.schedule()
        })
        await shot('schedule-error')
        await mutate(() => {
          __audit.schedules.st.editor = null
          __audit.schedules.st.rows = []
          __audit.schedules.st.lastFetch = Date.now()
          __audit.schedule()
        })
        await shot('schedule-empty')
        const settings = async (section, provider = null) => {
          await close()
          await page.locator(width === 390 ? '.settings-utility' : '.railround[aria-label="Settings"]').click()
          await page.locator('.settings:popover-open').waitFor()
          await page.evaluate(([section, provider]) => __audit.goSettings(section, provider), [section, provider])
          await settle()
          assert.ok(await page.locator('.settings:popover-open').isVisible())
        }
        for (const section of ['root', 'appearance', 'agents', 'providers', 'integrations', 'voice', 'files', 'plugins']) {
          await settings(section)
          if (section === 'voice') {
            const gaps = await page
              .locator('.settings-section[aria-label="Dictation"] > .card')
              .evaluateAll((nodes) =>
                nodes.slice(1).map((n, i) => n.getBoundingClientRect().top - nodes[i].getBoundingClientRect().bottom)
              )
            assert.ok(gaps.length >= 3 && gaps.every((gap) => gap >= 13.9), `Settings cards touch: ${gaps}`)
          }
          await shot(`settings-${section}`)
          const scroll = page.locator(width === 390 ? '.settings' : '.settings .setmain').last()
          await scroll.evaluate((n) => {
            n.scrollTop = n.scrollHeight
          })
          await shot(`settings-${section}-end`)
          if (section === 'voice') {
            await page.locator('#files-voice').click()
            await shot('settings-voice-menu')
            await page.keyboard.press('Escape')
            await page.locator('.read-aloud-preview').click()
            await page.locator('.read-aloud-sample .player.failed').waitFor()
            await page.locator('.read-aloud-sample').evaluate((n) => n.scrollIntoView({ block: 'end' }))
            await shot('settings-voice-audio-error')
          }
        }
        await settings('provider', 'claude-code')
        await shot('settings-provider')
        assert.equal(await page.locator('.settings .surface-heading').count(), 0)
        assert.equal(await page.locator(width === 390 ? '.settings .sheet-x' : '.settings .dialog-x').count(), 1)
        await context.route('https://example.org/sign-in', (route) =>
          route.fulfill({ contentType: 'text/html', body: 'Synthetic sign-in page' })
        )
        await page.getByRole('button', { name: 'Sign in again', exact: true }).click()
        await page.locator('#login-code').waitFor()
        await shot('settings-provider-signin')
        await settings('appearance')
        await context.route('**/api/settings/appearance', (route) =>
          route.fulfill({ status: 503, json: { error: 'Appearance could not be saved. Try again.' } })
        )
        await mutate(() => __audit.theme.refresh())
        await shot('settings-appearance-error')
        await settings('voice')
        await mutate(() => {
          (globalThis.__audio?.dictation?.state ?? __audit.state).mics.error = 'Microphone permission was denied.'
          (globalThis.__audio?.dictation?.state ?? __audit.state).termsNote.error = 'Dictation terms could not be read.'
          __audit.schedule()
        })
        await shot('settings-voice-error')
        await page.locator(width === 390 ? '.settings' : '.settings .setmain').evaluate((n) => {
          n.scrollTop = n.scrollHeight
        })
        await shot('settings-voice-error-end')
        await settings('integrations')
        // Plugin-owned integration sheets have their own fixture-only walk:
        // test/acme-plugin-browser.mjs. This audit exercises core only.
        await settings('agents')
        await mutate(() => {
          __audit.state.agents.list = []
          __audit.schedule()
        })
        await shot('settings-agents-empty')
        await context.route('**/api/agents', (route) =>
          route.fulfill({ status: 503, json: { error: 'Agents could not be reached.' } })
        )
        await mutate(() => __audit.loadAgents())
        await shot('settings-agents-error')
        await settings('integrations')
        await mutate(() => {
          __audit.state.providers = []
          __audit.state.github.missing = false
          __audit.state.github.error = 'The connection could not be read.'
          __audit.schedule()
        })
        await shot('settings-integrations-error')
        await close()
        await mutate(() => __audit.goApps())
        await page.locator('.apps-row').first().waitFor()
        await shot('apps')
        if (width === 1440) {
          await page.locator('.apps-row').first().click()
          await page.locator('.apps-embed').waitFor()
          await shot('app-framed')
          await context.route('https://notebook.example.com/**', (route) =>
            route.fulfill({
              status: 503,
              contentType: 'text/html',
              body: '<!doctype html><title>Unavailable</title><p>Notebook unavailable. Try again later.</p>'
            })
          )
          await page.locator('.apps-embed').evaluate((n) => {
            n.src = n.src
          })
          await shot('app-framed-error')
          await page.getByRole('button', { name: 'Back to apps', exact: true }).click()
        } else {
          const popup = page.waitForEvent('popup')
          await page.locator('.apps-row').first().click()
          const external = await popup
          await external.waitForLoadState()
          await external.screenshot({ path: path.join(out, `app-browser-${label}.png`) })
          await external.close()
        }
        await mutate(() => {
          __audit.apps.st.apps = []
          __audit.schedule()
        })
        await shot('apps-empty')
        await mutate(() => {
          __audit.apps.st.error = 'Apps could not be reached. Try again.'
          __audit.schedule()
        })
        await shot('apps-error')
        assert.deepEqual(errors, [], `${label} browser errors`)
        reports.push({ width, theme, mode, states, errors, noteSizes, messageSizes })
        await writeFile(
          path.join(out, 'browser.json'),
          JSON.stringify({ chromium: browser.version(), reports }, null, 2) + '\n'
        )
        console.log(`PASS ${label}: ${states.length} surfaces`)
      } catch (error) {
        await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
        throw error
      } finally {
        await context.close()
        await app.close()
      }
    }
  const app = await oneProductFixture()
  try {
    for (const locale of ['en-US', 'en-GB'])
      for (const timezoneId of ['America/Los_Angeles', 'Asia/Tokyo']) {
        const context = await browser.newContext({ locale, timezoneId }),
          page = await context.newPage()
        await page.goto(app.url + '/up')
        const clock = await page.evaluate(async () => {
          const { formatTime } = await import('/time.js'),
            { when } = await import('/sessions.js'),
            { replyTimestamp } = await import('/thread-experience.js'),
            { activityTime } = await import('/activity.js'),
            { dateWords } = await import('/schedules.js')
          const at = '2026-11-12T18:45:00Z',
            now = +new Date(at) + 3600000,
            expected = new Intl.DateTimeFormat(undefined, { timeStyle: 'short' }).format(new Date(at))
          return {
            expected,
            helper: formatTime(at),
            list: when(at, now),
            footer: replyTimestamp(at, { now }).text,
            activity: activityTime({ ts: at }),
            schedule: dateWords(at),
            resolved: new Intl.DateTimeFormat(undefined, { hour: 'numeric' }).resolvedOptions()
          }
        })
        for (const key of ['helper', 'list', 'footer', 'activity'])
          assert.equal(clock[key], clock.expected, `${locale} ${timezoneId} ${key}`)
        assert.ok(clock.schedule.includes(clock.expected), JSON.stringify({ locale, timezoneId, clock }))
        clocks.push({ locale, timezoneId, ...clock })
        await context.close()
      }
  } finally {
    await app.close()
  }
  await writeFile(
    path.join(out, 'browser.json'),
    JSON.stringify({ chromium: browser.version(), reports, clocks }, null, 2) + '\n'
  )
  const contrastFindings = reports.flatMap(report => report.states.flatMap(state => state.contrasts ?? []))
  console.log(
    JSON.stringify({
      result: contrastFindings.length ? 'needs-contrast-review' : 'passed',
      contrastFindings: contrastFindings.length,
      variants: reports.length,
      surfaces: reports.reduce((n, report) => n + report.states.length, 0),
      clocks: clocks.length
    })
  )
  if (contrastFindings.length) process.exitCode = 2
} finally {
  await browser.close()
}
