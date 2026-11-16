// The agent writes markdown, so the page reads markdown.
//
// The personas answer in markdown — `**Shaun, Monday.**`, dashes for lists,
// backticks around a path — and a page that drew that text verbatim showed the
// marks. This turns it into the shape the design draws: paragraphs, emphasis,
// inline code and fenced blocks in JetBrains Mono, lists, headings at sizes the
// bubble can carry, links, quotes and line breaks.
//
// **Nothing here ever produces HTML.** There is no string of markup anywhere in
// this file and nothing is ever assigned to `innerHTML`: a block becomes
// `document.createElement` and its text becomes `document.createTextNode`, so
// a reply containing `<img onerror=…>` or `<script>` is a run of characters in
// a text node and is drawn as the characters it is. That is a property of how
// this is built rather than of an escaping function that could be forgotten at
// one call site, which is why it is built this way and not by marking up a
// string.
//
// The second half of that rule is the one thing a node *can* carry away: a
// link's `href`. Only `http`, `https` and `mailto` become links at all; every
// other scheme, and every relative destination, is left as the literal
// markdown it was written as, so a reader sees `[click](javascript:…)` rather
// than a link they cannot see the inside of. Links carry `rel="noopener
// noreferrer"` and open in a new tab, because this page is a session and
// leaving it loses one.
//
// It is deliberately a subset, and the parts left out are named in the README:
// footnotes, reference links, and raw HTML — which is not an omission but the
// rule above. **Tables came in on 2026-09-22**, because they are the one thing
// agents write often enough that a screen of raw pipes is what a person sees
// most days; they are built out of `table`/`tr`/`td` elements like every other
// block here, with no string of markup anywhere near them.
//
// It renders while a message is still being written. A fence with no closing
// fence yet is a code block, an unfinished `**` is the characters `**`, and the
// message is re-parsed whole on every delta — which is affordable because the
// reconciler in `ui.js` writes only what changed, so a delta touches the text
// node at the end of one message and nothing else on the screen.

import { h, icon } from './ui.js'
import { layoutPixels } from './scale.js'
import { SIDE_MIN } from './panels.js'

// ------------------------------------------------------------------ the page

// The nodes for a piece of agent text, ready to be handed to `h()` or `morph()`.
//
// `copy`, when it is given, is how a code block or a table gets its copy
// button: a function from the block's source — the code as written, the table
// as the markdown it was written in — to the node that copies it. The button is
// the page's, which knows the clipboard and what "Copied" looks like; this file
// only says what a block's source is and where the button goes.
//
// `vault`, in a Files preview, draws a note the way Obsidian does for what the
// vault's notes use: the frontmatter as a properties block rather than raw
// YAML, tags, and — through the page's two callbacks — a task ticked
// (`onTask(n, checked)`, the nth task in the note) and an embed drawn
// (`embed(target)`, a node). In a chat none of that is asked for and a task is
// a box nobody can tick.
export function markdown(text, { copy = null, from = null, onLink = null, anchors = false, vault = false, onTask = null, embed = null } = {}) {
  const before = linking
  const beforeAnchors = anchoring
  const beforeVault = vaulting
  const handler = onLink ?? fileLinks
  linking = handler ? { handler, from } : null
  anchoring = anchors
  vaulting = vault ? { onTask, embed, tasks: 0 } : null
  try {
    if (!vault) return renderBlocks(parse(text), copy)
    const fm = frontmatterOf(text)
    const props = fm ? renderProps(fm.props) : null
    return [...(props ? [props] : []), ...renderBlocks(parse(fm ? fm.body : text), copy)]
  } finally {
    linking = before
    anchoring = beforeAnchors
    vaulting = beforeVault
  }
}

let vaulting = null

// The note with its nth task ticked or cleared — the nth in the order the
// preview draws them, which is the order they are written in, frontmatter and
// fenced code passed over. `null` when the nth task is not in the state the
// preview showed, so a tick made against a note that has moved under it
// writes nothing.
export function toggleTask(text, n, checked) {
  if (typeof text !== 'string') return null
  const fm = frontmatterOf(text)
  const lines = text.split('\n')
  let i = fm ? text.replace(/\r\n?/g, '\n').slice(0, text.length - fm.body.length).split('\n').length - 1 : 0
  let fence = null
  let seen = 0
  for (; i < lines.length; i += 1) {
    const line = lines[i]
    const bare = line.replace(/^\s*(?:>\s?)*/, '')
    if (fence) {
      if (closesFence(bare, fence)) fence = null
      continue
    }
    const opens = fenceAt(bare)
    if (opens) {
      fence = opens
      continue
    }
    const m = /^(\s*(?:>\s?)*(?:[-*+]|\d{1,9}[.)])\s+\[)([ xX])(\](?=\s|$))/.exec(line)
    if (!m) continue
    if (seen++ !== n) continue
    if ((m[2] !== ' ') === checked) return null
    lines[i] = m[1] + (checked ? 'x' : ' ') + line.slice(m[1].length + 1)
    return lines.join('\n')
  }
  return null
}

// ------------------------------------------------------------ the properties
//
// A note's frontmatter, read as far as the vault's notes write it: `key: value`,
// a value quoted or not, an inline list `[a, "b"]`, and a block list of `- item`
// lines under a key with nothing after its colon. A line that is none of those
// is kept as its own text under the key above it, so nothing written there is
// lost from the screen — it is only drawn plainly.
export function frontmatterOf(text) {
  if (typeof text !== 'string') return null
  const src = text.replace(/\r\n?/g, '\n')
  if (!src.startsWith('---\n')) return null
  const end = src.indexOf('\n---', 3)
  if (end < 0) return null
  const after = src.slice(end + 4)
  if (after !== '' && !/^[ \t]*(\n|$)/.test(after)) return null
  const props = []
  for (const line of src.slice(4, end).split('\n')) {
    if (line.trim() === '' || /^\s*#/.test(line)) continue
    const item = /^\s+-\s*(.*)$/.exec(line) ?? (/^-\s+(.*)$/.exec(line) && props.length ? /^-\s+(.*)$/.exec(line) : null)
    if (item && props.length) {
      props[props.length - 1].values.push(unquote(item[1]))
      props[props.length - 1].list = true
      continue
    }
    const kv = /^([^\s:#][^:]*?):(?:\s+(.*)|\s*)$/.exec(line)
    if (kv) {
      const raw = (kv[2] ?? '').trim()
      props.push({ key: kv[1].trim(), values: valuesOf(raw), list: raw.startsWith('[') && raw.endsWith(']') })
      continue
    }
    if (props.length) props[props.length - 1].values.push(line.trim())
    else props.push({ key: '', values: [line.trim()], list: false })
  }
  return { props, body: after.replace(/^[ \t]*\n?/, '') }
}

function unquote(raw) {
  const v = raw.trim()
  if (v.length >= 2 && ((v[0] === '"' && v.endsWith('"')) || (v[0] === "'" && v.endsWith("'")))) return v.slice(1, -1).replace(/\\"/g, '"')
  return v
}

function valuesOf(raw) {
  const v = raw.trim()
  if (v === '') return []
  if (!(v.startsWith('[') && v.endsWith(']'))) return [unquote(v)]
  const out = []
  let cell = ''
  let quote = null
  for (const c of v.slice(1, -1)) {
    if (quote) {
      if (c === quote) quote = null
      cell += c
    } else if (c === '"' || c === "'") {
      quote = c
      cell += c
    } else if (c === ',') {
      out.push(cell)
      cell = ''
    } else cell += c
  }
  out.push(cell)
  return out.map(unquote).filter((x) => x !== '')
}

function renderProps(props) {
  if (props.length === 0) return null
  return h(
    'div',
    { class: 'md-props', role: 'group', 'aria-label': 'Properties' },
    ...props.map(({ key, values, list }) =>
      h(
        'div',
        { class: 'md-prop' },
        h('span', { class: 'md-propkey', text: key }),
        h(
          'span',
          { class: `md-propval${list ? ' md-propchips' : ''}` },
          ...(values.length === 0
            ? [h('span', { class: 'md-propempty', text: 'Empty' })]
            : list
              ? values.map((v) => h('span', { class: 'md-chip' }, ...renderInline(parseInline(v))))
              : values.map((v) => h('span', {}, ...renderInline(parseInline(v))))),
        ),
      ),
    ),
  )
}

// In a Files preview each heading carries the anchor a link's `#fragment` is
// matched against, so a link to `note#Some heading` lands on it.
let anchoring = false

export function anchorOf(words) {
  return String(words ?? '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, '-')
    .replace(/^-+|-+$/g, '')
}

// Published vault URLs — the `FILES_VAULT_URLS` prefixes the server hands the
// page — are links into Files, not out of the app.
let vaultUrls = []

export function setVaultUrls(list) {
  vaultUrls = Array.isArray(list) ? list.filter((u) => typeof u === 'string' && /^https:\/\//i.test(u)) : []
}

function isVaultUrl(url) {
  const u = url.trim().toLowerCase()
  return vaultUrls.some((p) => {
    const prefix = p.replace(/\/+$/, '').toLowerCase()
    return u === prefix || u.startsWith(`${prefix}/`)
  })
}

// **A link into Files** is the one destination besides `http`, `https` and
// `mailto` that becomes a link, and it never becomes an `href` a browser
// follows on its own: it is a button-like `<a>` whose click hands the
// reference to the page, which resolves it through the host (`/api/files/
// resolve`) or reads it as an app address. Four shapes are recognised — an
// app path (`/files/vault/…`), an Obsidian vault URL, an absolute path, and a
// wikilink (`[[note]]`) — and, inside a Files preview, a relative path, which
// is relative to the file being read. Whether any of them names a file the
// person may open is the host's answer; this only decides what is worth asking.
let fileLinks = null
let linking = null

export function setFileLinks(handler) {
  fileLinks = handler
}

export function fileRef(raw, from = null) {
  if (typeof raw !== 'string') return null
  const ref = raw.trim().replace(/^<|>$/g, '')
  if (ref === '' || /[\u0000-\u001f\u007f]/.test(ref)) return null
  if (/^obsidian:\/\/open\?/i.test(ref)) return ref
  if (/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(ref)) return null
  if (ref.startsWith('/files/') || ref.startsWith('/')) return ref
  if (from && !ref.startsWith('#')) return ref
  return null
}

// A path written in backticks is linked only when it plainly is one: absolute,
// more than one segment, and ending in a name with an extension.
function codePathRef(text) {
  return /^\/[^\s`]*\/[^\s`/]+\.[A-Za-z0-9]{1,8}$/.test(text) ? text : null
}

// The same text with its marks taken off rather than drawn: what the home
// screen's status box shows, where `last_message` is one clamped line or two
// and a `**` in it would be the same defect in a smaller box. Whitespace is
// collapsed because that box is two lines of prose, not a transcript.
export function strip(text) {
  if (typeof text !== 'string') return text
  const out = []
  for (const block of parse(text)) stripBlock(block, out)
  return out.join(' ').replace(/\s+/g, ' ').trim()
}

function stripBlock(block, out) {
  switch (block.type) {
    case 'paragraph':
    case 'heading':
      out.push(stripInline(block.children))
      return
    case 'code':
      out.push(block.text)
      return
    case 'quote':
    case 'callout':
      for (const child of block.blocks) stripBlock(child, out)
      return
    case 'list':
      for (const item of block.items) for (const child of item) stripBlock(child, out)
      return
    case 'table':
      for (const cell of block.head) out.push(stripInline(cell))
      for (const row of block.rows) for (const cell of row) out.push(stripInline(cell))
      return
    default:
      return
  }
}

function stripInline(nodes) {
  let text = ''
  for (const node of nodes) {
    if (node.type === 'text') text += node.text
    else if (node.type === 'code' || node.type === 'tag') text += node.text
    else if (node.type === 'embed') text += node.label || node.target
    else if (node.type === 'break') text += ' '
    else if (node.children) text += stripInline(node.children)
  }
  return text
}

// ---------------------------------------------------------------- the blocks

// A markdown document is a list of blocks, and a block is either a leaf with
// inline content or a container holding more blocks. Everything below works on
// lines, because every block boundary in markdown is one.

// A screen is built whole on every change, so every message in a transcript is
// parsed on every redraw, and markdown is the expensive half of building one.
// The parse is a pure function of the text, so it is remembered *by* the text:
// a delta re-parses the message being written and nothing else, and a
// remembered answer can never be stale because the key is the whole input. The
// table is dropped rather than aged — a streaming message makes a new key per
// delta, and none of them is worth keeping once it has stopped.
const remembered = new Map()
const REMEMBER = 240

export function parse(text) {
  if (typeof text !== 'string' || text === '') return []
  const had = remembered.get(text)
  if (had) return had
  const blocks = parseBlocks(text.replace(/\r\n?/g, '\n').split('\n'))
  if (remembered.size >= REMEMBER) remembered.clear()
  remembered.set(text, blocks)
  return blocks
}

function parseBlocks(lines) {
  const out = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    if (line.trim() === '') {
      i += 1
      continue
    }

    const fence = fenceAt(line)
    if (fence) {
      const body = []
      i += 1
      while (i < lines.length && !closesFence(lines[i], fence)) {
        body.push(lines[i])
        i += 1
      }
      // A fence with no closing fence is a message still being written. It is a
      // code block now and will still be one when the rest arrives.
      if (i < lines.length) i += 1
      out.push({ type: 'code', lang: fence.lang, text: body.join('\n') })
      continue
    }

    const heading = /^ {0,3}(#{1,6})(?:\s+(.*?))?\s*$/.exec(line)
    if (heading) {
      const words = (heading[2] ?? '').replace(/\s+#+\s*$/, '')
      out.push({ type: 'heading', level: heading[1].length, children: parseInline(words) })
      i += 1
      continue
    }

    if (isRule(line)) {
      out.push({ type: 'rule' })
      i += 1
      continue
    }

    if (/^ {0,3}>/.test(line)) {
      const body = []
      while (i < lines.length && lines[i].trim() !== '') {
        // A line inside a quote that forgot its `>` is still inside it: that is
        // markdown's lazy continuation, and agents write it constantly.
        body.push(/^ {0,3}>/.test(lines[i]) ? lines[i].replace(/^ {0,3}> ?/, '') : lines[i])
        i += 1
      }
      // `> [!note] Title` is Obsidian's callout: a quote with a kind and a
      // title line. An unknown kind is drawn as a note, as Obsidian draws it.
      const callout = /^\[!([A-Za-z-]+)\][+-]?\s*(.*)$/.exec(body[0] ?? '')
      if (callout) out.push({ type: 'callout', kind: callout[1].toLowerCase(), title: parseInline(callout[2] || callout[1][0].toUpperCase() + callout[1].slice(1).toLowerCase()), blocks: parseBlocks(body.slice(1)) })
      else out.push({ type: 'quote', blocks: parseBlocks(body) })
      continue
    }

    if (bulletAt(line)) {
      const [list, next] = parseList(lines, i)
      out.push(list)
      i = next
      continue
    }

    const table = tableAt(lines, i)
    if (table) {
      out.push(table.block)
      i = table.end
      continue
    }

    // Four spaces is a code block only where a block can start — never as the
    // second line of a paragraph, which is how an agent's wrapped sentence
    // would otherwise turn into code.
    if (/^ {4,}\S/.test(line)) {
      const body = []
      while (i < lines.length && (/^ {4,}/.test(lines[i]) || lines[i].trim() === '')) {
        body.push(lines[i].slice(4))
        i += 1
      }
      while (body.length > 0 && body[body.length - 1].trim() === '') body.pop()
      out.push({ type: 'code', lang: '', text: body.join('\n') })
      continue
    }

    // The line itself first, and only then its continuations: a paragraph is
    // what is left when nothing above matched, so it must always take the line
    // that got here — a loop that could take none of them would not move.
    const words = [lines[i].trim()]
    i += 1
    // A table is the one block that needs two lines to be recognised, so it
    // cannot be on `startsBlock()`'s list and is asked for here instead: an
    // agent writing "Here are the numbers:" and then a table with no blank line
    // between them is a table, which is what GitHub draws and what the writer
    // meant.
    while (i < lines.length && lines[i].trim() !== '' && !startsBlock(lines[i]) && !opensTable(lines, i)) {
      words.push(lines[i].trim())
      i += 1
    }
    out.push({ type: 'paragraph', children: parseInline(words.join('\n')) })
  }
  return out
}

// What can end a paragraph by starting something of its own. An indented code
// block is deliberately not on this list.
function startsBlock(line) {
  return Boolean(fenceAt(line)) || /^ {0,3}#{1,6}(\s|$)/.test(line) || isRule(line) || /^ {0,3}>/.test(line) || Boolean(bulletAt(line))
}

function fenceAt(line) {
  const m = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(line)
  // An info string cannot contain a backtick, which is what keeps a paragraph
  // saying ```` ``a`` ```` from opening a fence.
  if (!m || (m[1][0] === '`' && m[2].includes('`'))) return null
  return { char: m[1][0], length: m[1].length, lang: m[2].trim() }
}

function closesFence(line, fence) {
  const m = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(line)
  return Boolean(m) && m[1][0] === fence.char && m[1].length >= fence.length
}

function isRule(line) {
  return /^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$/.test(line)
}

// A list marker, and how far in the item's own content starts. `-foo` is not a
// marker and neither is `*emphasis*`: a marker is followed by a space or by the
// end of the line.
function bulletAt(line) {
  const m = /^(\s*)(?:([-*+])|(\d{1,9})([.)]))(\s+|$)/.exec(line)
  if (!m) return null
  if (isRule(line)) return null
  return {
    indent: m[1].length,
    ordered: m[3] !== undefined,
    start: m[3] === undefined ? 1 : Number(m[3]),
    width: m[0].length,
  }
}

function indentOf(line) {
  return /^\s*/.exec(line)[0].length
}

function parseList(lines, start) {
  const first = bulletAt(lines[start])
  const items = []
  // A list with a blank line anywhere in it is markdown's "loose" list, and the
  // difference is visible: its items are paragraphs with air between them,
  // where a tight list's items are single lines.
  let loose = false
  let i = start

  while (i < lines.length) {
    let at = i
    while (at < lines.length && lines[at].trim() === '') at += 1
    const marker = bulletAt(lines[at] ?? '')
    // A marker further in belongs to the item above, and one further out, or of
    // the other kind, is a different list.
    if (!marker || marker.ordered !== first.ordered || marker.indent !== first.indent) break
    if (at > i) loose = true
    i = at

    const first_ = lines[i].slice(marker.width)
    const task = /^\[([ xX])\](?=\s|$)\s?/.exec(first_)
    const body = [task ? first_.slice(task[0].length) : first_]
    i += 1
    while (i < lines.length) {
      if (lines[i].trim() === '') {
        let j = i
        while (j < lines.length && lines[j].trim() === '') j += 1
        // A blank line only stays in the item if something indented follows it.
        if (j >= lines.length || indentOf(lines[j]) < marker.width) break
        for (let k = i; k < j; k += 1) body.push('')
        loose = true
        i = j
        continue
      }
      if (indentOf(lines[i]) >= marker.width) {
        body.push(lines[i].slice(marker.width))
        i += 1
        continue
      }
      // A wrapped sentence under an item, with no indent of its own.
      if (!startsBlock(lines[i])) {
        body.push(lines[i].trim())
        i += 1
        continue
      }
      break
    }
    const blocks = parseBlocks(body)
    if (task) blocks.task = task[1] !== ' '
    items.push(blocks)
  }

  return [{ type: 'list', ordered: first.ordered, start: first.start, items, loose }, i]
}

// ----------------------------------------------------------------- the tables
//
// A table is a header row, a row of dashes that says how wide it is and how
// each column is aligned, and then body rows — which means it is the one block
// in this file that cannot be recognised from a single line. `opensTable()` is
// that two-line question, asked wherever a block can start and again at every
// line of a paragraph, because an agent writes a sentence and then a table
// under it as often as it leaves a blank line between them.
//
// The cells are inline markdown like anything else: bold in a cell is bold, a
// code span in a cell is a code span, and a `|` a writer meant literally is
// written `\|` and is a character.

// The delimiter row: `---`, `:--`, `--:` or `:-:` per column, and nothing else.
function isDivider(cells) {
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell.trim()))
}

// What each column's dashes asked for. `null` is a column that asked for
// nothing, which is where the numbers below get their say.
function alignOf(cell) {
  const text = cell.trim()
  const left = text.startsWith(':')
  const right = text.endsWith(':')
  if (left && right) return 'center'
  if (right) return 'right'
  if (left) return 'left'
  return null
}

// One row, split on the pipes that are pipes. A `\|` is a character and stays
// in the cell; the outer pipes a writer puts at each end of a row are a frame
// and are not a cell of their own.
function cellsOf(line) {
  const out = []
  let cell = ''
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i]
    if (c === '\\' && line[i + 1] === '|') {
      cell += '\\|'
      i += 1
      continue
    }
    if (c === '|') {
      out.push(cell)
      cell = ''
      continue
    }
    cell += c
  }
  out.push(cell)
  if (out.length > 1 && out[0].trim() === '') out.shift()
  if (out.length > 1 && out[out.length - 1].trim() === '') out.pop()
  return out
}

// Whether a table starts on this line. Cheap on purpose: it is asked once per
// line of every paragraph, so it reads two lines and counts, and the parse
// below is only ever run on a line that answered yes.
function opensTable(lines, at) {
  const head = lines[at]
  const rule = lines[at + 1]
  if (head === undefined || rule === undefined) return false
  if (!head.includes('|') || !rule.includes('-')) return false
  if (fenceAt(head) || /^ {0,3}>/.test(head) || bulletAt(head)) return false
  const cells = cellsOf(head)
  const divider = cellsOf(rule)
  // One column is a table if the writer framed it in pipes, which is the only
  // way a single cell can be told from a sentence — and `cellsOf()` above has
  // already taken that frame off, so the count is what is left.
  return cells.length >= 1 && isDivider(divider) && divider.length === cells.length
}

// A number a column can be lined up by its last digit: a plain figure, a
// percentage, money, a count with thousands separators, a signed delta. It is
// deliberately narrow — a cell this does not recognise leaves its whole column
// aligned the way text is, which is the answer that is never wrong.
function isNumeric(text) {
  return /^[-+(]?\s*[£$€¥]?\s*\d[\d\s,_]*(\.\d+)?\s*[%x×]?\)?$/.test(text.trim())
}

function tableAt(lines, at) {
  if (!opensTable(lines, at)) return null
  const head = cellsOf(lines[at]).map((cell) => cell.trim())
  const asked = cellsOf(lines[at + 1]).map(alignOf)
  const rows = []
  let i = at + 2
  // The table runs to the first blank line or the first thing that is a block
  // of its own. A row with too few cells is padded and one with too many is cut
  // to the header's width, which is what the header row promised.
  while (i < lines.length && lines[i].trim() !== '' && !startsBlock(lines[i]) && lines[i].includes('|')) {
    const cells = cellsOf(lines[i]).map((cell) => cell.trim())
    rows.push(Array.from({ length: head.length }, (_, n) => cells[n] ?? ''))
    i += 1
  }

  // A column the dashes said nothing about, whose every filled cell is a
  // number, is a column of numbers and is lined up on the right. This is the
  // whole of "right-aligned if the renderer can tell": one cell of prose in it
  // and the column is prose.
  const align = asked.map((asked_, n) => {
    if (asked_) return asked_
    const filled = rows.map((row) => row[n]).filter((cell) => cell !== '')
    return filled.length > 0 && filled.every(isNumeric) ? 'right' : null
  })

  return {
    block: {
      type: 'table',
      // The rows as they were written, for the copy button: a table goes onto
      // the clipboard as markdown, because what it is pasted into is as likely
      // as not the composer, which sends markdown.
      source: lines.slice(at, i).map((row) => row.trim()).join('\n'),
      align,
      head: head.map(parseInline),
      rows: rows.map((row) => row.map(parseInline)),
    },
    end: i,
  }
}

// ---------------------------------------------------------------- the inlines

const ESCAPABLE = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/

function parseInline(src) {
  const out = []
  let text = ''
  let i = 0

  const flush = () => {
    if (text !== '') out.push({ type: 'text', text })
    text = ''
  }

  while (i < src.length) {
    const c = src[i]

    if (c === '\\' && i + 1 < src.length && ESCAPABLE.test(src[i + 1])) {
      text += src[i + 1]
      i += 2
      continue
    }

    if (c === '\n') {
      flush()
      out.push({ type: 'break' })
      i += 1
      continue
    }

    if (c === '`') {
      const run = runOf(src, i, '`')
      const close = findRun(src, i + run, '`', run)
      if (close >= 0) {
        flush()
        const code = codeSpan(src.slice(i + run, close))
        const ref = linking ? codePathRef(code) : null
        out.push(ref ? { type: 'filelink', ref, children: [{ type: 'code', text: code }] } : { type: 'code', text: code })
        i = close + run
        continue
      }
      text += src.slice(i, i + run)
      i += run
      continue
    }

    if (c === '<') {
      const auto = /^<([a-zA-Z][a-zA-Z0-9+.-]*:[^<>\s]*)>/.exec(src.slice(i))
      const href = auto ? safeHref(auto[1]) : null
      if (href) {
        flush()
        out.push({ type: 'link', href, children: [{ type: 'text', text: auto[1] }] })
        i += auto[0].length
        continue
      }
      // Everything else that looks like a tag is characters, and is drawn as
      // characters. This is the whole of the app's HTML handling.
      text += c
      i += 1
      continue
    }

    if (linking && c === '[' && src[i + 1] === '[') {
      const end = src.indexOf(']]', i + 2)
      const inner = end > i + 2 ? src.slice(i + 2, end) : ''
      if (inner && !inner.includes('\n') && !inner.includes('[')) {
        const [target, label] = inner.split('|')
        flush()
        out.push({ type: 'filelink', ref: `[[${target.trim()}]]`, children: [{ type: 'text', text: (label ?? target).trim() }] })
        i = end + 2
        continue
      }
    }

    if (linking && c === '!' && src[i + 1] === '[' && src[i + 2] === '[') {
      const end = src.indexOf(']]', i + 3)
      const inner = end > i + 3 ? src.slice(i + 3, end) : ''
      if (inner && !inner.includes('\n') && !inner.includes('[')) {
        const [target, label] = inner.split('|')
        flush()
        out.push({ type: 'embed', target: target.trim(), label: (label ?? '').trim() })
        i = end + 2
        continue
      }
    }

    // `==highlight==`, as Obsidian marks it.
    if (c === '=' && src[i + 1] === '=' && src[i + 2] !== undefined && !/[\s=]/.test(src[i + 2])) {
      const close = src.indexOf('==', i + 2)
      if (close > i + 2 && !/\s/.test(src[close - 1]) && !src.slice(i + 2, close).includes('\n')) {
        flush()
        out.push({ type: 'mark', children: parseInline(src.slice(i + 2, close)) })
        i = close + 2
        continue
      }
    }

    // A `#tag`: after a space or at the start, a letter first, as Obsidian
    // reads one. Drawn as a tag in a Files preview only.
    if (c === '#' && (i === 0 || /\s/.test(src[i - 1]))) {
      const tag = /^#([\p{L}_][\p{L}\p{N}_/-]*)/u.exec(src.slice(i))
      if (tag) {
        flush()
        out.push({ type: 'tag', text: tag[0] })
        i += tag[0].length
        continue
      }
    }

    if (c === '[' || (c === '!' && src[i + 1] === '[')) {
      const link = parseLink(src, i)
      if (link) {
        flush()
        out.push(link.node)
        i = link.end
        continue
      }
      text += c
      i += 1
      continue
    }

    if (c === '*' || c === '_' || c === '~') {
      const em = parseEmphasis(src, i)
      if (em) {
        flush()
        out.push(em.node)
        i = em.end
        continue
      }
      const run = runOf(src, i, c)
      text += src.slice(i, i + run)
      i += run
      continue
    }

    text += c
    i += 1
  }

  flush()
  return out
}

function runOf(src, at, char) {
  let n = 0
  while (src[at + n] === char) n += 1
  return n
}

// The next run of `char` at least `least` long, at or after `from`.
function findRun(src, from, char, least) {
  for (let j = from; j < src.length; j += 1) {
    if (src[j] !== char) continue
    if (runOf(src, j, char) >= least) return j
  }
  return -1
}

// A code span's content: newlines become spaces, and one space at each end is
// the writer making room for a backtick rather than part of the code.
function codeSpan(text) {
  const flat = text.replace(/\n/g, ' ')
  if (flat.length > 1 && flat.startsWith(' ') && flat.endsWith(' ') && flat.trim() !== '') return flat.slice(1, -1)
  return flat
}

// Emphasis, strong, and both — and strikethrough, which agents write as often
// as either. The opener may not be followed by a space and the closer may not
// be preceded by one, which is what keeps `a * b * c` as the characters it is;
// an underscore also needs a word boundary on each side, which is what keeps
// `some_file_name` whole.
function parseEmphasis(src, at) {
  const char = src[at]
  const run = runOf(src, at, char)
  if (char === '~' && run < 2) return null
  if (run === 0) return null

  const after = src[at + run]
  if (after === undefined || /\s/.test(after)) return null
  if (char === '_' && !boundary(src[at - 1])) return null

  const want = char === '~' ? 2 : Math.min(run, 3)
  const close = findCloser(src, at + run, char, want)
  if (close < 0) return null

  const inner = parseInline(src.slice(at + run, close))
  const node =
    char === '~'
      ? { type: 'del', children: inner }
      : want === 3
        ? { type: 'strong', children: [{ type: 'em', children: inner }] }
        : want === 2
          ? { type: 'strong', children: inner }
          : { type: 'em', children: inner }
  return { node, end: close + want }
}

function boundary(char) {
  return char === undefined || /[\s!"#$%&'()*+,\-./:;<=>?@[\\\]^`{|}~]/.test(char)
}

function findCloser(src, from, char, want) {
  for (let j = from; j < src.length; j += 1) {
    const ch = src[j]
    if (ch === '\\') {
      j += 1
      continue
    }
    // A delimiter inside a code span is code, not a delimiter.
    if (ch === '`') {
      const run = runOf(src, j, '`')
      const close = findRun(src, j + run, '`', run)
      j = close >= 0 ? close + run - 1 : j + run - 1
      continue
    }
    if (ch !== char) continue
    if (runOf(src, j, char) < want) continue
    if (/\s/.test(src[j - 1] ?? ' ')) continue
    if (char === '_' && !boundary(src[j + want])) continue
    return j
  }
  return -1
}

// `[label](destination)`, and `![alt](destination)` — an image is drawn as the
// link it is rather than fetched, because this page's own policy would refuse
// the fetch and a remote image in a reply is a beacon either way.
function parseLink(src, at) {
  const image = src[at] === '!'
  const open = image ? at + 1 : at
  if (src[open] !== '[') return null

  let depth = 0
  let close = -1
  for (let j = open; j < src.length; j += 1) {
    const ch = src[j]
    if (ch === '\\') {
      j += 1
      continue
    }
    if (ch === '[') depth += 1
    else if (ch === ']') {
      depth -= 1
      if (depth === 0) {
        close = j
        break
      }
    }
  }
  if (close < 0 || src[close + 1] !== '(') return null

  let depth2 = 1
  let end = -1
  for (let j = close + 2; j < src.length; j += 1) {
    const ch = src[j]
    if (ch === '\\') {
      j += 1
      continue
    }
    if (ch === '\n') return null
    if (ch === '(') depth2 += 1
    else if (ch === ')') {
      depth2 -= 1
      if (depth2 === 0) {
        end = j
        break
      }
    }
  }
  if (end < 0) return null

  const label = src.slice(open + 1, close)
  // `(url "title")` — the title is markdown's tooltip and this design has none.
  const dest = src.slice(close + 2, end).trim().split(/\s+/)[0] ?? ''
  const href = safeHref(dest)
  const ref = linking ? (href ? (isVaultUrl(href) ? href : null) : fileRef(dest, linking.from)) : null
  if (ref) {
    const children = label.trim() === '' ? [{ type: 'text', text: ref }] : parseInline(label)
    return { node: { type: 'filelink', ref, children }, end: end + 1 }
  }
  // A destination this app will not follow is not quietly dropped: the markdown
  // is left as written, so what the agent said is still on the screen.
  if (!href) return null

  const children = label.trim() === '' ? [{ type: 'text', text: href }] : parseInline(label)
  return { node: { type: 'link', href, children, image }, end: end + 1 }
}

// The one value from a reply that is not text. Three schemes become links and
// nothing else does — not `javascript:`, not `data:`, and not a destination
// with no scheme at all, which is every relative path an agent writes about
// its own files and which this page could not follow anyway.
export function safeHref(raw) {
  if (typeof raw !== 'string') return null
  let url = raw.trim()
  if (url.startsWith('<') && url.endsWith('>')) url = url.slice(1, -1).trim()
  // A control character inside the scheme is how `java&#9;script:` gets past a
  // check that only looks at the front of the string, so they come out first.
  const cleaned = url.replace(/[ - ]/g, '')
  const scheme = /^([a-zA-Z][a-zA-Z0-9+.-]*):/.exec(cleaned)
  if (!scheme) return null
  const name = scheme[1].toLowerCase()
  if (name !== 'http' && name !== 'https' && name !== 'mailto') return null
  return cleaned
}

// ---------------------------------------------------------------- the nodes

function renderBlocks(blocks, copy = null) {
  const out = []
  for (const block of blocks) {
    const node = renderBlock(block, copy)
    if (node) out.push(node)
  }
  return out
}

function renderBlock(block, copy = null) {
  switch (block.type) {
    case 'paragraph':
      return block.children.length === 0 ? null : h('p', {}, ...renderInline(block.children))
    case 'heading':
      return h(`h${Math.min(block.level, 6)}`, anchoring ? { class: 'anchored', 'data-anchor': anchorOf(stripInline(block.children)) } : {}, ...renderInline(block.children))
    case 'code':
      return copyable(h('pre', {}, h('code', { class: langClass(block.lang), text: block.text })), block.text, 'code', copy)
    case 'rule':
      return h('hr', {})
    case 'table':
      return copyable(renderTable(block), block.source, 'table', copy)
    case 'quote':
      return h('blockquote', {}, ...renderBlocks(block.blocks, copy))
    case 'callout':
      return h(
        'div',
        { class: `callout callout-${CALLOUTS[block.kind] ?? 'note'}`, 'data-callout': block.kind },
        h('div', { class: 'callout-title' }, ...renderInline(block.title)),
        block.blocks.length ? h('div', { class: 'callout-body' }, ...renderBlocks(block.blocks, copy)) : null,
      )
    case 'list':
      return h(
        block.ordered ? 'ol' : 'ul',
        block.ordered && block.start !== 1 ? { start: block.start } : {},
        ...block.items.map((item) => renderItem(item, block.loose, copy)),
      )
    default:
      return null
  }
}

// A tight item's words sit straight in the `li`, even where a nested list or a
// code block follows them — a paragraph there would put a line of air under
// every bullet. A loose list asked for that air and gets it.
function renderItem(blocks, loose, copy = null) {
  const out = []
  let task = null
  if (blocks.task !== undefined) {
    const n = vaulting ? vaulting.tasks++ : -1
    const tick = vaulting?.onTask
    task = h('input', {
      class: 'md-task',
      type: 'checkbox',
      checked: blocks.task,
      disabled: !tick,
      'aria-label': blocks.task ? 'Done' : 'To do',
      onclick: tick
        ? (event) => {
            event?.preventDefault?.()
            tick(n, !blocks.task)
          }
        : null,
    })
  }
  for (const block of blocks) {
    if (!loose && block.type === 'paragraph') out.push(...renderInline(block.children))
    else {
      const node = renderBlock(block, copy)
      if (node) out.push(node)
    }
  }
  if (!task) return h('li', {}, ...out)
  return h('li', { class: `task${blocks.task ? ' done' : ''}` }, task, h('span', { class: 'md-taskbody' }, ...out))
}

// Obsidian's callout kinds, by the colour family each is drawn in; its aliases
// read as the kind they stand for.
const CALLOUTS = {
  note: 'note', info: 'note', todo: 'note', abstract: 'tip', summary: 'tip', tldr: 'tip', tip: 'tip', hint: 'tip', important: 'tip',
  success: 'good', check: 'good', done: 'good', question: 'warn', help: 'warn', faq: 'warn', warning: 'warn', caution: 'warn', attention: 'warn',
  failure: 'bad', fail: 'bad', missing: 'bad', danger: 'bad', error: 'bad', bug: 'bad', example: 'quiet', quote: 'quiet', cite: 'quiet',
}

// A table, in a scroller of its own. The wrapper is the whole of how a wide
// table behaves on a phone: the table keeps its columns and the bubble keeps
// its width, and what does not fit slides sideways *inside* the bubble rather
// than pushing the thread out past the edge of the screen.
function renderTable(block) {
  const cell = (tag, cells, align) =>
    h(
      'tr',
      {},
      ...cells.map((children, n) => h(tag, align[n] ? { class: `ta-${align[n]}` } : {}, ...renderInline(children))),
    )
  return h(
    'div',
    { class: 'tablewrap' },
    h(
      'table',
      {},
      h('thead', {}, cell('th', block.head, block.align)),
      ...(block.rows.length === 0 ? [] : [h('tbody', {}, ...block.rows.map((row) => cell('td', row, block.align)))]),
    ),
  )
}

// A block with its copy button in the corner. The button sits in a frame of its
// own beside the scroller rather than inside it, so it stays in the corner while
// the code or the table slides sideways under it.
function copyable(node, source, what, copy) {
  if (typeof copy !== 'function') return node
  return h('div', { class: `block ${what}-frame` }, node, copy(source, what))
}

// The fence's info string, as a class and nothing else. It is an agent's word
// for a language and it is never trusted further than a name.
function langClass(lang) {
  const name = /^[A-Za-z0-9+#_.-]{1,20}$/.exec((lang ?? '').split(/\s+/)[0] ?? '')
  return name ? `lang-${name[0]}` : null
}

function renderInline(nodes) {
  const out = []
  for (const node of nodes) {
    switch (node.type) {
      case 'text':
        out.push(document.createTextNode(node.text))
        break
      case 'break':
        out.push(h('br', {}))
        break
      case 'code':
        out.push(h('code', { text: node.text }))
        break
      case 'strong':
        out.push(h('strong', {}, ...renderInline(node.children)))
        break
      case 'em':
        out.push(h('em', {}, ...renderInline(node.children)))
        break
      case 'del':
        out.push(h('del', {}, ...renderInline(node.children)))
        break
      case 'mark':
        out.push(h('mark', {}, ...renderInline(node.children)))
        break
      case 'tag':
        out.push(vaulting ? h('span', { class: 'md-tag', text: node.text }) : document.createTextNode(node.text))
        break
      case 'embed': {
        const drawn = vaulting?.embed?.(node.target, node.label)
        if (drawn) out.push(drawn)
        else out.push(...renderInline([{ type: 'filelink', ref: `[[${node.target}]]`, children: [{ type: 'text', text: node.label || node.target }] }]))
        break
      }
      case 'filelink': {
        const { handler, from } = linking ?? {}
        const ref = node.ref
        out.push(
          h(
            'a',
            {
              class: 'filelink',
              href: ref.startsWith('/files/') ? ref : '#',
              title: 'Open in Files',
              onclick: (event) => {
                event?.preventDefault?.()
                handler?.(ref, from, event)
              },
            },
            ...renderInline(node.children),
          ),
        )
        if (handler && layoutPixels(globalThis.innerWidth ?? 0) >= SIDE_MIN) out.push(h('button', { class: 'filelink-side', type: 'button', title: 'Open to the side', 'aria-label': 'Open to the side', onclick: event => {
          event.preventDefault(); event.stopPropagation(); handler(ref, from, { currentTarget: event.currentTarget, openToSide: true })
        } }, icon.panelRight(13)))
        break
      }
      case 'link':
        out.push(
          h(
            'a',
            { href: node.href, target: '_blank', rel: 'noopener noreferrer' },
            ...renderInline(node.children),
          ),
        )
        break
      default:
        break
    }
  }
  return out
}
