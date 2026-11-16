// Test-only observation of plugin-owned state. Production gets no debug hook.
export async function observeAudio(context) {
  await context.route(/\/plugins\/(dictation|read-aloud)\/(?:files\/)?page\/engine\.js(?:\?.*)?$/, async route => {
    const id = /\/plugins\/([^/]+)\//.exec(new URL(route.request().url()).pathname)[1]
    const response = await route.fetch(), source = await response.text()
    // Dictation now mounts once per pane. Existing single-pane drivers address
    // the main composer; the later side mount must not overwrite that probe.
    const body = source.replace(/(export function mount\(host[^)]*\)\s*\{)/, start => `${start}\n;if ((host.panelId ?? 'main') === 'main') (globalThis.__audio ??= {})[${JSON.stringify(id)}] = host\n`)
    await route.fulfill({ response, body })
  })
}
