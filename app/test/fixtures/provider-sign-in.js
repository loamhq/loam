// Synthetic catalog/flow snapshots matching the provider contract, never a
// real URL, account, authorization code or credential.
export function providerFixture(id = 'antigravity', kind = 'paste-code') {
  const base = `/v1/providers/${id}`
  return {
    id, name: { antigravity: 'Antigravity', 'claude-code': 'Claude Code', codex: 'Codex' }[id] || 'Future provider',
    connected: false, reason: 'Not signed in', usage: null,
    login: { connected: false, reason: 'Not signed in', account: null, source: 'none', expires_at: null, days_left: null },
    sign_in: { kind, start: `${base}/login`, status: `${base}/login`, cancel: `${base}/login`,
      code: kind === 'paste-code' ? `${base}/login/code` : null, sign_out: id === 'antigravity' ? `${base}/logout` : null },
  }
}
export function flowFixture(provider, state = 'pending') {
  const device = provider.sign_in.kind === 'device-code'
  return { provider: provider.id, kind: provider.sign_in.kind, state,
    url: state === 'pending' ? `https://sign-in.example.invalid/${provider.id}` : null,
    code: state === 'pending' && device ? 'ABCD-EFGH' : null,
    accepts_code: state === 'pending' && !device,
    instructions: 'Old steps and documentation must never be drawn on the card.',
    message: state === 'failed' ? 'That code was not accepted. Try again.' : null,
    login: state === 'done' ? { connected: true, reason: null, source: 'oauth', expires_at: null, days_left: null,
      ...(provider.id === 'claude-code' ? {} : { account: 'person@example.invalid' }) } : provider.login }
}
