// What a permission request is about, in a few words: its target, why the host
// asked, and what "allow for this chat" would grant. No DOM and no network, so
// the words are tested without a page; the card and the line it leaves in the
// thread (`app.js`) both read them from here, so they cannot say it two ways.

// A path as a person reads it beside the agent's own folder: inside it,
// relative to it; elsewhere, relative to what the two share — the owner's home,
// usually — so `Read · agents/bin/session-broker` and not the whole absolute
// path. A path that shares nothing past the root with the workspace, or a page
// that does not know the workspace, keeps the path as given.
export function shortPath(path, workspace) {
  if (typeof path !== 'string' || path === '') return ''
  if (typeof workspace !== 'string' || !workspace.startsWith('/') || !path.startsWith('/')) return path
  const ws = workspace.replace(/\/+$/, '')
  if (path === ws) return '.'
  if (path.startsWith(`${ws}/`)) return path.slice(ws.length + 1)
  const a = ws.split('/')
  const b = path.split('/')
  let i = 0
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i += 1
  // `['', 'home', 'x']` shared is the home; a shared `['', 'home']` alone is
  // every user's and says nothing, so it is kept.
  if (i < 3) return path
  return b.slice(i).join('/')
}

const oneLine = (text, max = 80) => {
  const first = String(text).split('\n')[0].trim()
  return first.length > max ? `${first.slice(0, max - 1)}…` : first
}

// The thing a request is about, from its input: the file, the directory, the
// pattern and where it looks, the command. `''` when the input names none of
// them, and the tool's name stands alone.
export function askTarget(toolName, input, workspace) {
  if (!input || typeof input !== 'object') return ''
  const str = (k) => (typeof input[k] === 'string' && input[k].trim() !== '' ? input[k] : null)
  const file = str('file_path') ?? str('notebook_path')
  if (file) return shortPath(file, workspace)
  const pattern = str('pattern') ?? str('glob')
  const where = str('path')
  if (pattern && where) return `${oneLine(pattern)} in ${shortPath(where, workspace)}`
  if (where) return shortPath(where, workspace)
  if (pattern) return oneLine(pattern)
  const other = str('command') ?? str('url') ?? str('query') ?? str('skill')
  return other ? oneLine(other) : ''
}

// Why the host asked, in plain words. `workingDir` is the one category the page
// says itself — the host's sentence for it is "Path is outside allowed working
// directories", which is true and reads like a log line. Anything else is the
// host's own sentence, or the tool's description when the host gave none.
export function askReason(p, agentName) {
  if (p?.decision_reason_type === 'workingDir') return `outside ${agentName || 'the agent'}’s folders`
  return p?.decision_reason || p?.description || ''
}

// What a suggested rule grants, from the rule itself: `Read under agents/bin`.
// The CLI writes an absolute path in a rule as `//home/…`, and a trailing `/**`
// is "everything under".
function ruleWords(rule, workspace) {
  const tool = typeof rule?.toolName === 'string' ? rule.toolName : 'this tool'
  const content = typeof rule?.ruleContent === 'string' ? rule.ruleContent : ''
  if (content === '') return `any ${tool}`
  const path = content.replace(/^\/\//, '/')
  const under = /\/\*\*$/.test(path)
  const shown = path.startsWith('/') ? shortPath(path.replace(/\/\*\*$/, ''), workspace) : path.replace(/\/\*\*$/, '')
  return under ? `${tool} under ${shown}` : `${tool} ${shown}`
}

export function grantWords(suggestions, workspace) {
  const out = []
  for (const s of Array.isArray(suggestions) ? suggestions : []) {
    if (!s || typeof s !== 'object') continue
    if ((s.type === 'addRules' || s.type === 'replaceRules') && s.behavior !== 'deny' && Array.isArray(s.rules)) {
      for (const r of s.rules) out.push(ruleWords(r, workspace))
    } else if (s.type === 'addDirectories' && Array.isArray(s.directories)) {
      for (const d of s.directories) out.push(`everything under ${shortPath(d, workspace)}`)
    } else if (s.type === 'setMode' && typeof s.mode === 'string') {
      out.push(`${s.mode} mode`)
    }
  }
  return out.join(', ')
}

// The words a closed request leaves in the thread, by how it closed.
export function departureWords(line, agentName) {
  switch (line.behavior) {
    case 'allow':
      return line.scope ? 'Allowed for this chat' : 'Allowed'
    case 'deny':
      return 'Denied'
    case 'expired':
      return `Not answered · ${agentName || 'the agent'} went on without it`
    case 'cancelled':
      return 'Withdrawn'
    default:
      return 'Ended with the chat'
  }
}

// The whole line: how it closed, then the tool and its target — or, for a
// question, its header.
export function departureLine(line, agentName, workspace) {
  const parts = [departureWords(line, agentName)]
  if (line.header) parts.push(line.header)
  else {
    if (line.tool_name) parts.push(line.tool_name)
    const target = askTarget(line.tool_name, line.input, workspace)
    if (target) parts.push(target)
  }
  return parts.join(' · ')
}
