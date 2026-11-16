// This capability is server-only. The browser relay allowlist deliberately has
// no /value route, and failures never include the response body or credential.
export function brokerSecret(broker, plugin) {
  return async () => {
    try {
      const out = await broker.json('GET', `/v1/plugins/${encodeURIComponent(plugin)}/secrets/GEMINI_API_KEY/value`, undefined, { signal: AbortSignal.timeout(5000) })
      if (out.status !== 200) return ''
      const value = JSON.parse(out.body).value
      return typeof value === 'string' ? value : ''
    } catch { return '' }
  }
}
