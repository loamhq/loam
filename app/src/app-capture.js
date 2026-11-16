import { spawn } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import path from 'node:path'
import { StringDecoder } from 'node:string_decoder'

async function waitAtMost(promise, ms) {
  let timer
  try { return await Promise.race([promise, new Promise(resolve => { timer = setTimeout(resolve, ms) })]) }
  finally { clearTimeout(timer) }
}

// A short-lived browser with an empty profile. CDP travels on inherited pipes,
// never a listening debugging port. No broker token, login or host profile is
// passed to the child. The host's sandbox around the server is its boundary too.
export async function captureAppPage({ url, dir, executable = 'chromium', timeoutMs = 18000, hostResolverRules = null, oomScoreAdj = null }) {
  const profile = await mkdtemp(path.join(dir, 'capture-'))
  let child, timer, next = 0, buffered = '', failed = false
  const pending = new Map(), listeners = new Map()
  const decoder = new StringDecoder('utf8')
  const fail = error => {
    failed = true
    for (const task of pending.values()) task.reject(error)
    pending.clear()
    for (const task of listeners.values()) task.reject(error)
    listeners.clear()
  }
  try {
    const env = { PATH: process.env.PATH, LANG: 'C.UTF-8', TMPDIR: profile, XDG_CACHE_HOME: profile, XDG_CONFIG_HOME: profile }
    // Optional native-library/font paths are useful for a local installation;
    // credentials and the rest of the server environment are deliberately absent.
    for (const key of ['LD_LIBRARY_PATH', 'FONTCONFIG_PATH', 'FONTCONFIG_FILE']) if (process.env[key]) env[key] = process.env[key]
    // WebGL through SwiftShader, the software renderer Chromium ships: a
    // headless host has no GPU, and Debian's build no longer falls back to it
    // by itself, so without these two a WebGL page stalls the compositor and
    // its capture times out (caused 2026-11-16 on the host, where Alpine's
    // image had rendered it). The pages are the install's own agents' sites.
    const args = ['--headless', '--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader',
      '--disable-dev-shm-usage', '--hide-scrollbars', '--mute-audio',
      '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions',
      '--no-first-run', '--no-default-browser-check', `--user-data-dir=${profile}`,
      ...(hostResolverRules ? [`--host-resolver-rules=${hostResolverRules}`] : []), '--remote-debugging-pipe', 'about:blank']
    // At a higher OOM score than the server when one is set: util-linux's
    // `choom` sets it and becomes the browser, so every process the browser
    // starts inherits it. Raising one's own score needs no privilege.
    child = oomScoreAdj === null
      ? spawn(executable, args, { detached: true, stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], env })
      : spawn('choom', ['-n', String(oomScoreAdj), '--', executable, ...args], { detached: true, stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'], env })
    child.on('error', () => fail(new Error('Page preview browser is unavailable.')))
    child.on('exit', () => fail(new Error('Page preview browser ended.')))
    child.stdio[3].on('error', () => fail(new Error('Page preview pipe closed.')))
    child.stdio[4].on('error', () => fail(new Error('Page preview pipe closed.')))
    child.stdio[4].on('data', chunk => {
      buffered += decoder.write(chunk)
      let end
      while ((end = buffered.indexOf('\0')) >= 0) {
        const raw = buffered.slice(0, end); buffered = buffered.slice(end + 1)
        let message; try { message = JSON.parse(raw) } catch { continue }
        if (message.id) {
          const task = pending.get(message.id); pending.delete(message.id)
          if (message.error) task?.reject(new Error('Page preview command failed.'))
          else task?.resolve(message.result)
        } else listeners.get(message.method)?.resolve(message.params)
      }
    })
    const command = (method, params = {}, sessionId) => new Promise((resolve, reject) => {
      if (failed) return reject(new Error('Page preview browser is unavailable.'))
      const id = ++next; pending.set(id, { resolve, reject })
      child.stdio[3].write(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }) + '\0', error => { if (error) fail(new Error('Page preview pipe closed.')) })
    })
    timer = setTimeout(() => fail(new Error('Page preview timed out.')), timeoutMs)
    await command('Browser.getVersion')
    const { targetId } = await command('Target.createTarget', { url: 'about:blank' })
    const { sessionId } = await command('Target.attachToTarget', { targetId, flatten: true })
    const send = (method, params) => command(method, params, sessionId)
    await send('Page.enable'); await send('Network.enable')
    await send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: .625, mobile: false })
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }, { name: 'prefers-color-scheme', value: 'light' }] })
    // Do not accidentally photograph a cross-origin sign-in portal. Ordinary
    // page assets can still load from their CDNs; document navigation cannot.
    let navigationError = false, mainFrame
    const responses = new Map()
    listeners.set('Fetch.requestPaused', { reject() {}, resolve: event => {
      const allowed = new URL(event.request.url).origin === new URL(url).origin
      if (!allowed) navigationError = true
      send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', { requestId: event.requestId, ...(!allowed ? { errorReason: 'BlockedByClient' } : {}) }).catch(() => {})
    } })
    listeners.set('Network.responseReceived', { reject() {}, resolve: event => {
      if (event.type === 'Document') responses.set(event.frameId, event.response.status)
      if (event.type === 'Document' && event.frameId === mainFrame && event.response.status >= 400) navigationError = true
    } })
    await send('Fetch.enable', { patterns: [{ resourceType: 'Document', requestStage: 'Request' }] })
    const loaded = new Promise((resolve, reject) => listeners.set('Page.loadEventFired', { resolve, reject }))
    // The load promise may reject while navigate is still in flight.
    loaded.catch(() => {})
    const navigation = await send('Page.navigate', { url }); mainFrame = navigation.frameId
    if (responses.get(mainFrame) >= 400) navigationError = true
    if (navigation.errorText) throw new Error('Page preview navigation failed.')
    // A streaming asset may never finish loading. Photograph what has rendered
    // instead of spending the whole budget waiting for load/animation callbacks.
    await waitAtMost(loaded, 4000)
    if (navigationError) throw new Error('Page preview is not an available app page.')
    await waitAtMost(send('Runtime.evaluate', { expression: 'Promise.race([document.fonts.ready,new Promise(r=>setTimeout(r,1000))]).then(()=>new Promise(r=>setTimeout(()=>requestAnimationFrame(()=>requestAnimationFrame(r)),1200)))', awaitPromise: true }), 3000)
    if (navigationError) throw new Error('Page preview is not an available app page.')
    const screenshot = await send('Page.captureScreenshot', { format: 'jpeg', quality: 78, captureBeyondViewport: false })
    const body = Buffer.from(screenshot.data, 'base64')
    if (body.length < 4 || body.length > 2 * 1024 * 1024 || body[0] !== 0xff || body[1] !== 0xd8) throw new Error('Page preview did not produce an image.')
    // An unavailable graphics context can still produce a successful JPEG of
    // an empty canvas. Inspect the actual pixels in a clean browser target,
    // where the site's CSP cannot prevent decoding our screenshot. A nearly
    // uniform image is more useful as the designed fallback than a blank card.
    const inspector = await command('Target.createTarget', { url: 'about:blank' })
    const attached = await command('Target.attachToTarget', { targetId: inspector.targetId, flatten: true })
    const pixels = await command('Runtime.evaluate', { awaitPromise: true, returnByValue: true, expression:
      `(async()=>{const bitmap=await createImageBitmap(new Blob([Uint8Array.from(atob(${JSON.stringify(screenshot.data)}),c=>c.charCodeAt(0))],{type:'image/jpeg'}));const canvas=new OffscreenCanvas(40,25),ctx=canvas.getContext('2d',{willReadFrequently:true});ctx.drawImage(bitmap,0,0,40,25);const data=ctx.getImageData(0,0,40,25).data,min=[255,255,255],max=[0,0,0];let visible=0;for(let i=0;i<data.length;i+=4){if(data[i]>24||data[i+1]>24||data[i+2]>24)visible++;for(let c=0;c<3;c++){min[c]=Math.min(min[c],data[i+c]);max[c]=Math.max(max[c],data[i+c])}}return {range:max.reduce((sum,v,c)=>sum+v-min[c],0),visible}})()` }, attached.sessionId)
    // A tiny HUD on an otherwise black WebGL canvas is still a blank preview.
    if (!(pixels.result?.value?.range >= 24 && pixels.result.value.visible >= 20)) throw new Error('Page preview was empty.')
    return body
  } finally {
    clearTimeout(timer)
    fail(new Error('Page preview finished.'))
    if (child?.pid) { try { process.kill(-child.pid, 'SIGKILL') } catch {} }
    await rm(profile, { recursive: true, force: true }).catch(() => {})
  }
}
