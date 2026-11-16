// Browser audio focus is a platform service shared by independent plugins.
export function audioSessionType(type) {
  try {
    const session = globalThis.navigator?.audioSession
    if (session) session.type = type
  } catch { /* unsupported or refused */ }
}
