// Full surface walk against synthetic host answers through the real relay.
// Screenshots are reproducible; no host session, account or live data is used.
import assert from 'node:assert/strict'
import { readFile, mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { acmePluginFixture } from './acme-plugin-fixture.mjs'
import { filesHost } from './files-host.js'
import { shippedPlugins } from '../src/shipped-plugins.js'
import { createFakeSchedules } from '../tools/fake-schedules.js'

const out = process.env.SETTINGS_REVIEW_EVIDENCE || process.env.TMPDIR + '/settings-review'
await mkdir(out, { recursive: true })
const { chromium } = await import(process.env.LOAM_PLAYWRIGHT || 'playwright')
const browser = await chromium.launch({
  executablePath: process.env.LOAM_CHROME,
  args: ['--no-sandbox', '--disable-dev-shm-usage']
})
const publicRoot = process.env.SETTINGS_REVIEW_BASELINE_PUBLIC || 'public'
const source = await readFile(path.join(publicRoot, 'app.js'), 'utf8'),
  reports = []
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
  '\nglobalThis.__audit = { state, store, theme, threads, files, picker, apps, activity, running, schedules, schedule, goChats, goFiles, goApps, goActivity, goProfile, goSettings, closeSheet, openSheet, openMenu, closeMenus, pickFromFiles, providerSignIn, loadAgents, loadProviders };'

async function audit(page) {
  return page.evaluate(() => {
    const visible = (node) => {
      for (let parent = node.parentElement; parent; parent = parent.parentElement) {
        if (parent.matches('details:not([open])') && !parent.querySelector(':scope > summary')?.contains(node)) return false
      }
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
      const app = await acmePluginFixture({ mode, look: theme }),
        id = 'a-quieter-week',
        kind = width === 390 ? 'phone' : 'desktop',
        label = `${width}-${theme}-${mode}`
      const values = { phone: { theme, mode, size: 100 }, desktop: { theme, mode, size: 100 } }
      app.routes['GET /v1/settings/appearance'] = (req, res) => json(res, values)
      app.routes['PATCH /v1/settings/appearance'] = (req, res, url, raw) => {
        Object.assign(values[kind], JSON.parse(raw)[kind])
        json(res, values)
      }
      const files = filesHost()
      for (const [key, handler] of Object.entries(files.routes))
        if (key.includes('/v1/files/') || key.includes('/v1/limits')) app.routes[key] = handler
      Object.assign(
        app.f.providers.find((p) => p.id === 'claude-code'),
        {
          login: { source: 'fixture', plan: 'Max plan' },
          usage: { seen_at: '2026-11-12T18:08:00Z', five_hour: { utilization: 0.25, resets_at: '2026-11-12T23:00:00Z' }, seven_day: { utilization: 0.24, resets_at: '2026-11-18T18:00:00Z' } },
          sign_in: {
            kind: 'auth-code',
            start: '/v1/providers/claude-code/login',
            status: '/v1/providers/claude-code/login',
            cancel: '/v1/providers/claude-code/login',
            label: 'Sign in'
          }
        }
      )
      app.f.providers.push({ id: 'grok', name: 'Grok', connected: false, reason: 'not wired' }, { id: 'opencode', name: 'OpenCode', connected: false, reason: 'not wired' })
      app.f.agents[0].role = 'Keeps projects moving'
      const plugins = [...await shippedPlugins(publicRoot), ...(process.env.SETTINGS_REVIEW_BASELINE ? [] : [app.plugin])]
      app.routes['GET /v1/plugins'] = (req, res) => json(res, { plugins })
      for (const plugin of plugins.filter(row => row !== app.plugin)) {
        app.routes[`GET /v1/plugins/${plugin.id}`] = (req, res) => json(res, { plugin: { ...plugin, guide: 'A fixture guide.' }, rows: [plugin] })
        for (const file of ['page/main.js', 'page/engine.js', 'theme.css', 'PLUGIN.md']) {
          app.routes[`GET /v1/plugins/${plugin.id}/files/${file}`] = async (req, res) => {
            try {
              const body = await readFile(path.join(publicRoot, plugin.folder, file))
              res.writeHead(200, { 'content-type': file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/plain' })
              res.end(body)
            } catch { json(res, { error: 'No such fixture file' }, 404) }
          }
        }
      }
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
      const owner = app.f.agents[0].id,
        schedules = createFakeSchedules(app.f.agents.map((a) => a.id))
      app.routes['GET /v1/schedules'] = (req, res, url) => {
        const answer = schedules.handleAnswer('GET', '/v1/schedules', url)
        json(res, answer.body, answer.status)
      }
      for (const agent of app.f.agents) for (const method of ['GET', 'POST']) app.routes[`${method} /v1/schedules/${agent.id}`] = (req, res, url, raw) => {
        const answer = schedules.handleAnswer(method, url.pathname, url, raw ? JSON.parse(raw) : {})
        json(res, answer.body, answer.status)
      }
      for (const agent of app.f.agents) for (const name of ['reflection', 'weekly-review', 'prepare-notes']) app.routes[`POST /v1/schedules/${agent.id}/${name}/seen`] = (req, res, url, raw) => {
        const answer = schedules.handleAnswer('POST', url.pathname, url, JSON.parse(raw || '{}'))
        json(res, answer.body, answer.status)
      }
      app.routes['GET /v1/activity'] = (req, res) =>
        json(res, {
          jobs: {
            slots: [
              { job: 'audit-job', title, project: 'Notebook', briefed_by: owner, run_kind: 'job', engine: 'codex', model: 'gpt-6.1-sol', caller: 'conversation', caller_session: 'resident', thread: id, thread_title: title, chain: 'audit-job', since: '2026-11-12T18:00:00Z' },
              {
                job: 'project-job',
                title: 'Review the project notes',
                project: 'Notebook', run_kind: 'job', engine: 'claude-code', model: 'claude-opus-5-5', briefed_by: 'quill', caller: 'wake',
                since: '2026-11-12T18:00:00Z'
              },
              { slot: 3, kind: 'wake', run_kind: 'cron', agent: 'scout', title: 'Morning check-in', engine: 'claude-code', model: 'claude-sonnet-5-5', since: '2026-11-12T18:00:00Z' },
            ],
            landing: [],
            queue: []
          },
          items: [
            {
              id: 'audit-landed',
              kind: 'job', chain: 'audit-chain', auto_continuation: 2, run_kind: 'job', engine: 'codex', model: 'gpt-6.1-sol', briefed_by: owner, caller: 'conversation', thread: id, thread_title: title,
              title,
              agent: owner,
              outcome: 'completed',
              landing: 'landed',
              started: '2026-11-12T18:00:00Z',
              ts: '2026-11-12T18:30:00Z'
            },
            { id: 'audit-parent', chain: 'audit-chain', kind: 'job', title, outcome: 'failed', ts: '2026-11-12T18:20:00Z' }
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
              kind: 'thread', run_kind: 'thread', engine: 'claude-code', engine_label: 'Claude Code', model: 'claude-opus-5-5',
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
      const { SHAPES, ORG_SHAPES, withOrgs } = await import('./fixtures/github.js')
      app.routes['GET /v1/integrations/github'] = (req, res) => json(res, withOrgs(SHAPES.authorized, ORG_SHAPES.authorized))
      if (process.env.SETTINGS_REVIEW_BASELINE) app.routes['GET /v1/integrations/acme'] = (req, res) => json(res, { status: 'unconfigured' })
      const context = await browser.newContext({
        viewport: { width, height: width === 390 ? 844 : 1000 },
        locale: 'en-US',
        timezoneId: 'America/Los_Angeles',
        colorScheme: mode,
        isMobile: width === 390,
        hasTouch: width === 390,
        reducedMotion: 'reduce'
      })
      if (process.env.SETTINGS_REVIEW_BASELINE_PUBLIC) await context.route('**/*', async route => {
        const url = new URL(route.request().url())
        if (url.origin === app.url && /\.(js|css)$/.test(url.pathname) && url.pathname !== '/themes.css') {
          try { return await route.fulfill({ path: path.join(publicRoot, url.pathname) }) } catch {}
        }
        return route.fallback()
      })
      await context.route('**/*', async route => {
        const url = new URL(route.request().url())
        if (url.origin !== app.url && !['blob:', 'data:'].includes(url.protocol)) return route.fulfill({ contentType: 'text/html', body: 'Synthetic external page' })
        return route.fallback()
      })
      await context.route('**/app.js', (route) =>
        route.fulfill({ contentType: 'text/javascript', body: source + hook })
      )
      await context.route('**/api/health', (route) =>
        route.fulfill({ json: { configured: true, ok: true, speech: true } })
      )
      await context.route('**/api/speech/lookup', (route) => route.fulfill({ json: { status: 'none' } }))
      await context.route('**/api/speech', (route) =>
        route.fulfill({ status: 503, json: { error: 'Audio is temporarily unavailable. Try again.' } })
      )
      await context.addInitScript(() => {
        globalThis.SpeechRecognition = class { addEventListener() {} start() {} stop() {} }
        navigator.mediaDevices.enumerateDevices = async () => [{ kind: 'audioinput', deviceId: 'studio', label: 'Studio microphone' }]
      })
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
              (node.closest('details:not([open])') && !node.closest('summary')) ||
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
        if (!process.env.SETTINGS_REVIEW_BASELINE) assert.deepEqual(checks.targets, [], `${label} ${name}: controls below 44px`)
        if (!process.env.SETTINGS_REVIEW_BASELINE && !process.env.UI_AUDIT_RECORD_CONTRAST) assert.deepEqual(checks.contrasts, [], `${label} ${name}: contrast below 4.5:1`)
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
        await page.clock.setFixedTime(new Date('2026-11-12T18:10:00Z'))
        await page.goto(app.url)
        await page.locator('.tt-row').first().waitFor()
        const settings = async (section, provider = null) => {
          await close()
          await page.locator(width === 390 ? '.settings-utility' : '.railround[aria-label="Settings"]').click()
          await page.locator('.settings:popover-open').waitFor()
          await page.evaluate(([section, provider]) => __audit.goSettings(section, provider), [section, provider])
          await settle()
        }
        const endShot = async name => {
          const scroll = page.locator(width === 390 ? '.settings' : '.settings .setmain').last()
          await scroll.evaluate(n => { n.scrollTop = n.scrollHeight })
          await shot(name + '-end')
        }
        const sections = process.env.SETTINGS_REVIEW_BASELINE
          ? ['root', 'appearance', 'agents', 'accounts', 'voice', 'files', 'plugins']
          : ['root', 'appearance', 'agents', 'providers', 'integrations', 'voice', 'files', 'plugins']
        for (const section of sections) {
          await settings(section)
          await shot('settings-' + section)
          if (!process.env.SETTINGS_REVIEW_BASELINE) assert.ok(await page.locator('.settings .segmented').evaluateAll(nodes => nodes.every(n => getComputedStyle(n).backgroundColor === 'rgba(0, 0, 0, 0)')))
          await endShot('settings-' + section)
          if (section === 'files' && !process.env.SETTINGS_REVIEW_BASELINE) {
            const originals = await page.locator('.fs-setfolder').evaluateAll(nodes => nodes.map(n => n.value))
            await page.getByRole('textbox', { name: 'Inbox folder', exact: true }).fill('Notes/Inbox')
            await page.getByRole('textbox', { name: 'Inbox folder', exact: true }).press('Tab')
            await settings('files')
            assert.equal(await page.getByRole('textbox', { name: 'Inbox folder', exact: true }).inputValue(), 'Notes/Inbox')
            await page.getByRole('textbox', { name: 'Daily notes folder', exact: true }).fill('Notes/Daily')
            await page.getByRole('textbox', { name: 'Daily notes folder', exact: true }).press('Enter')
            await settings('files')
            assert.equal(await page.getByRole('textbox', { name: 'Daily notes folder', exact: true }).inputValue(), 'Notes/Daily')
            for (const [i, name] of ['Inbox folder', 'Daily notes folder'].entries()) {
              await page.getByRole('textbox', { name, exact: true }).fill(originals[i])
              await page.getByRole('textbox', { name, exact: true }).press('Tab')
            }
            assert.ok(await page.getByText('Add shortcuts from a file or folder’s menu.', { exact: true }).count())
          }
        }
        if (process.env.SETTINGS_REVIEW_BASELINE) {
          await settings('accounts')
          const cards = page.locator('.settings .integration:not(.connect)')
          for (const [index, name] of ['github-account', 'github-organization', 'integration-acme'].entries()) {
            await cards.nth(index).evaluate(n => n.scrollIntoView({ block: 'center' }))
            await shot(name)
          }
          await page.getByRole('button', { name: 'Connect an organization', exact: true }).click()
          await shot('github-add-account')
          await page.getByRole('button', { name: 'Cancel', exact: true }).click()
        }
        for (const provider of app.f.providers) {
          await settings('provider', provider.id)
          if (!process.env.SETTINGS_REVIEW_BASELINE) {
            assert.equal(await page.locator('.settings .sheet-title').innerText(), provider.name)
            assert.equal(await page.locator('.settings .sign-in-heading').count(), 0)
          }
          await shot('provider-' + provider.id)
          await endShot('provider-' + provider.id)
        }
        for (const agent of app.f.agents) {
          await settings('agents')
          await page.locator('.settings').getByRole('button', { name: new RegExp(agent.name) }).click()
          await page.locator('.profile-hero').waitFor()
          await shot('agent-' + agent.id)
          await page.getByRole('button', { name: /^Schedule/ }).click()
          await page.locator('.schedule-open').first().waitFor()
          await shot('agent-' + agent.id + '-schedules')
          await page.getByRole('button', { name: 'Open workspace', exact: true }).click()
          await page.waitForFunction(() => __audit.state.screen === 'files' && !__audit.files.st.listing.loading)
          await shot('agent-' + agent.id + '-workspace')
        }
        await page.evaluate(() => __audit.goProfile('sprout'))
        await page.getByRole('button', { name: /^Schedule/ }).click()
        await page.locator('.schedule-add').click()
        await page.locator('.schedule-editor').evaluate(n => n.scrollIntoView({ block: 'start' }))
        await shot('schedule-recurring')
        await page.getByRole('button', { name: 'One-off', exact: true }).click()
        await shot('schedule-one-off')
        await page.getByRole('button', { name: 'Close', exact: true }).click()
        await settings('appearance')
        const appearanceGroups = await page.locator('.theme-option-choices').count()
        for (let i = 0; i < appearanceGroups; i++) {
          const labels = await page.locator('.theme-option-choices').nth(i).locator('button').allTextContents()
          for (const label of labels) {
            const button = page.locator('.theme-option-choices').filter({ has: page.getByRole('button', { name: label, exact: true }) }).getByRole('button', { name: label, exact: true })
            if (await button.count()) { await button.click(); await settle() }
          }
        }
        await shot('appearance-options')
        await settings('voice')
        for (const modeLabel of process.env.SETTINGS_REVIEW_BASELINE ? ['Live', 'Record and clean'] : ['As you speak', 'Record first']) {
          await page.getByRole(process.env.SETTINGS_REVIEW_BASELINE ? 'radio' : 'button', { name: modeLabel, exact: true }).click()
          await shot('voice-' + modeLabel.toLowerCase().replaceAll(' ', '-'))
        }
        for (const level of ['Off', 'Full', 'Light']) {
          await page.getByRole(process.env.SETTINGS_REVIEW_BASELINE ? 'radio' : 'button', { name: level, exact: true }).click()
          await shot('voice-polish-' + level.toLowerCase())
        }
        await page.getByRole('button', { name: 'Longer', exact: true }).click()
        await page.getByRole('button', { name: 'Shorter', exact: true }).click()
        await page.getByRole('button', { name: 'Studio microphone', exact: true }).click()
        await endShot('voice-microphone')
        await page.locator('#files-voice').click()
        await shot('voice-reading-voices')
        await page.keyboard.press('Escape')
        await settings('provider', 'claude-code')
        await page.getByRole('button', { name: 'Sign in again', exact: true }).click()
        await page.locator('#login-code').waitFor()
        await shot('provider-signin')
        if (!process.env.SETTINGS_REVIEW_BASELINE) {
          await settings('integrations')
          await page.getByRole('button', { name: /GitHub.*2 accounts/ }).click()
          await shot('integration-github')
          await page.getByText('Manage all GitHub accounts', { exact: true }).click()
          await mutate(() => __audit.schedule())
          assert.ok(await page.locator('.integration-shared-actions').evaluate(n => n.open))
          await shot('integration-github-manage')
          await page.getByRole('button', { name: /octo-owner.*repositories/ }).click()
          await shot('github-account')
          await page.getByText('Connection details', { exact: true }).click()
          await mutate(() => __audit.schedule())
          await shot('github-account-connection')
          await page.getByText('Refresh or disconnect', { exact: true }).click()
          await mutate(() => __audit.schedule())
          await shot('github-account-manage')
          await page.getByRole('button', { name: 'GitHub', exact: true }).click()
          await page.getByRole('button', { name: /octo-org.*repositories/ }).click()
          await shot('github-organization')
          await page.getByRole('button', { name: 'GitHub', exact: true }).click()
          await page.getByRole('button', { name: 'Add an account', exact: true }).click()
          await shot('github-add-account')
          await settings('integrations')
          await page.getByRole('button', { name: /Acme.*Not connected/ }).click()
          await shot('integration-acme')
        }
        await settings('plugins')
        const pluginNames = await page.locator('.plugin-row .setrow-title').allTextContents()
        for (const plugin of pluginNames) {
          await page.getByRole('button', { name: 'About ' + plugin, exact: true }).click()
          await shot('plugin-' + plugin.toLowerCase().replace(/[^a-z0-9]+/g, '-'))
          await endShot('plugin-' + plugin.toLowerCase().replace(/[^a-z0-9]+/g, '-'))
          await page.getByRole('button', { name: 'Back to Plugins', exact: true }).click()
        }
        await page.locator('.market-link-door .setrow').click()
        await page.locator('#market-link-field').waitFor()
        await shot('plugin-install')
        await page.locator('.market-install .pillbtn', { hasText: 'Cancel' }).click()
        await close()
        await mutate(() => __audit.goActivity())
        await page.locator('.activity-job').first().waitFor()
        await shot('activity-now')
        await page.locator('.activity-feed').evaluate(n => n.scrollIntoView({ block: 'start' }))
        await shot('activity-earlier')
        await page.locator('.activity-cancel').first().click()
        await shot('activity-cancel')
        await mutate(() => __audit.activity.dismiss())
        assert.deepEqual(errors, [], `${label} browser errors`)
        reports.push({ width, theme, mode, states, errors })
        await writeFile(path.join(out, 'browser.json'), JSON.stringify({ chromium: browser.version(), reports }, null, 2) + '\n')
        console.log(`PASS ${label}: ${states.length} surfaces`)
      } catch (error) {
        console.log(JSON.stringify({ errors, state: await page.evaluate(() => ({ sheet: __audit.state.sheet, screen: __audit.state.screen, persona: __audit.threads.st.personaMenu })) }))
        await page.screenshot({ path: path.join(out, `failure-${label}.png`) })
        throw error
      } finally {
        await context.close()
        await app.close()
      }
    }
  console.log(JSON.stringify({ result: 'passed', variants: reports.length, surfaces: reports.reduce((n, r) => n + r.states.length, 0) }))
} finally { await browser.close() }
