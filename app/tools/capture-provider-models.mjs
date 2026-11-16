// Read only: retain model display fields, never login/usage/install data or URL.
import { writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

export function providerModelFixture(body, capturedAt = new Date().toISOString()) {
  if (!Array.isArray(body?.providers)) throw new Error('The app did not return a provider catalog.')
  return { source: 'Live app /api/providers; provider/model display fields only', live_capture: true, captured_at: capturedAt,
    providers: body.providers.map((provider) => {
      if (typeof provider.id !== 'string') throw new Error('A provider has no ID.')
      return { id: provider.id, ...(typeof provider.name === 'string' ? { name: provider.name } : {}),
        ...(typeof provider.connected === 'boolean' ? { connected: provider.connected } : {}),
        ...(Array.isArray(provider.models) ? { models: provider.models.map((entry) => {
          if (typeof entry === 'string') return entry
          if (!entry || typeof entry.id !== 'string') throw new Error('A catalog model has no ID.')
          return { id: entry.id, ...(typeof entry.name === 'string' ? { name: entry.name } : {}) }
        }) } : {}) }
    }) }
}

export async function captureProviderModels(baseURL, output) {
  if (!baseURL || !output) throw new Error('Set LOAM_LIVE_APP and pass an output fixture path.')
  const response = await fetch(new URL('/api/providers', baseURL), { redirect: 'error', signal: AbortSignal.timeout(10000) })
  if (!response.ok) throw new Error(`The provider read answered HTTP ${response.status}.`)
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('The provider read did not answer JSON.')
  const fixture = providerModelFixture(await response.json())
  await writeFile(output, JSON.stringify(fixture, null, 2) + '\n')
  console.log(`Pinned ${fixture.providers.length} providers; ${fixture.providers.reduce((n, p) => n + (p.models?.length ?? 0), 0)} model entries. No URL, login or usage retained.`)
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try { await captureProviderModels(process.env.LOAM_LIVE_APP, process.argv[2]) }
  catch (error) { console.error(error.message); process.exitCode = 1 }
}
