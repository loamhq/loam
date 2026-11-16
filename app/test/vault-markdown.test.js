// A vault note in a Files preview, drawn as Obsidian draws what the vault's
// notes use: frontmatter as properties, tasks that tick, callouts, highlights,
// tags and embeds. And the two pure halves of the editor: a tick written back
// to the right line, and the toolbar's new marks.

import test from 'node:test'
import assert from 'node:assert/strict'

import { installDom } from './fakedom.js'

installDom()
const { markdown, frontmatterOf, toggleTask } = await import('../public/markdown.js')
const { formatText, saveState } = await import('../public/files.js')

const find = (nodes, cls, out = []) => {
  for (const n of nodes) {
    if (n.nodeType !== 1) continue
    if ((n.getAttribute('class') ?? '').split(' ').includes(cls)) out.push(n)
    find(n.childNodes, cls, out)
  }
  return out
}
const vault = (src, opts = {}) => markdown(src, { vault: true, from: { root: 'vault', path: 'Notes/a.md' }, onLink: () => {}, ...opts })

const NOTE = `---
title: "Planning"
created_by: "[[Ada]]"
agents: ["[[Ada]]", "[[Loam]]"]
tags:
  - list
  - plan
empty:
---
# Body

- [ ] one
- [x] two ==bright== #tag/sub

> [!warning] Mind this
> careful

![[shot.png]]
`

test('frontmatter is a properties block, never raw YAML; lists are chips and wiki-links in values are live', () => {
  const nodes = vault(NOTE)
  const props = find(nodes, 'md-props')
  assert.equal(props.length, 1)
  assert.equal(nodes[0], props[0], 'the properties come first')
  assert.deepEqual(find(nodes, 'md-propkey').map((n) => n.textContent), ['title', 'created_by', 'agents', 'tags', 'empty'])
  assert.deepEqual(find(props, 'md-chip').map((n) => n.textContent), ['Ada', 'Loam', 'list', 'plan'])
  assert.equal(find(props, 'filelink').length, 3, 'created_by and both agents link')
  assert.equal(find(props, 'md-propempty').length, 1)
  // Not `chips`, which is the home screen's filter strip and its 20px inset.
  assert.ok(!find(props, 'md-propval').some((n) => /\bchips\b/.test(n.getAttribute?.('class') ?? n.className ?? '')))
  assert.doesNotMatch(nodes.map((n) => n.textContent).join('\n'), /^---|created_by:/m)
  assert.equal(frontmatterOf('---\nnot closed\n# x'), null, 'an unclosed fence is text')
  assert.equal(frontmatterOf('# a\n---\nb: c\n---'), null, 'only at the very top')
})

test('outside a Files preview the frontmatter is left alone', () => {
  assert.equal(find(markdown(NOTE), 'md-props').length, 0)
})

test('tasks are checkboxes; ticking one asks for the nth task in the other state', () => {
  const asked = []
  const nodes = vault(NOTE, { onTask: (n, checked) => asked.push([n, checked]) })
  const boxes = find(nodes, 'md-task')
  assert.equal(boxes.length, 2)
  assert.equal(boxes[0].hasAttribute('checked'), false)
  assert.equal(boxes[1].hasAttribute('checked'), true)
  boxes[1].fire('click', { preventDefault() {} })
  boxes[0].fire('click', { preventDefault() {} })
  assert.deepEqual(asked, [[1, false], [0, true]])
  assert.equal(find(markdown('- [ ] a'), 'md-task')[0].hasAttribute('disabled'), true, 'a chat’s box cannot be ticked')
})

test('a tick is written to its own line, past frontmatter and code; a stale tick writes nothing', () => {
  const src = '---\na: "- [ ] not a task"\n---\n```\n- [ ] code\n```\n- [ ] one\n> - [x] quoted\n1. [ ] numbered'
  assert.match(toggleTask(src, 0, true), /\n- \[x\] one\n/)
  assert.match(toggleTask(src, 1, false), /\n> - \[ \] quoted\n/)
  assert.match(toggleTask(src, 2, true), /\n1\. \[x\] numbered$/)
  assert.match(toggleTask(src, 0, true), /- \[ \] code/, 'the fence is untouched')
  assert.equal(toggleTask(src, 1, true), null, 'already in that state: the note moved')
  assert.equal(toggleTask(src, 9, true), null)
})

test('callouts, highlights and tags are drawn; an embed the page has no picture for is a link to it', () => {
  const nodes = vault(NOTE)
  const callout = find(nodes, 'callout')[0]
  assert.match(callout.getAttribute('class'), /callout-warn/)
  assert.equal(find([callout], 'callout-title')[0].textContent, 'Mind this')
  assert.equal(nodes.flatMap(function marks(n) { return n.nodeType !== 1 ? [] : [...(n.tag === 'mark' ? [n] : []), ...n.childNodes.flatMap(marks)] })[0].textContent, 'bright')
  assert.deepEqual(find(nodes, 'md-tag').map((n) => n.textContent), ['#tag/sub'])
  assert.ok(find(nodes, 'filelink').some((n) => n.textContent === 'shot.png'))
  const drawn = vault(NOTE, { embed: (t) => (t === 'shot.png' ? document.createElement('img') : null) })
  assert.ok(JSON.stringify(drawn.map((n) => n.textContent)).length > 0)
  assert.equal(find(markdown('a #tag here'), 'md-tag').length, 0, 'a chat draws no tags')
})

test('the toolbar’s task, wiki-link and code marks', () => {
  assert.equal(formatText('buy milk', 0, 0, 'task').value, '- [ ] buy milk')
  assert.equal(formatText('- buy milk', 0, 0, 'task').value, '- [ ] buy milk', 'a list item becomes a task, not a list of one')
  assert.equal(formatText('- [ ] buy milk', 0, 0, 'task').value, 'buy milk')
  assert.equal(formatText('- [ ] buy milk', 0, 0, 'list').value, '- buy milk')
  assert.equal(formatText('see note', 4, 8, 'wikilink').value, 'see [[note]]')
  assert.equal(formatText('run ls', 4, 6, 'code').value, 'run `ls`')
  assert.equal(formatText('a\nb', 0, 3, 'code').value, '```\na\nb\n```')
})

test('the header’s save word', () => {
  const f = { conflict: null, saving: false, failed: false, flash: '' }
  assert.equal(saveState(f, false), null)
  assert.equal(saveState(f, true).word, 'Edited')
  assert.equal(saveState({ ...f, saving: true }, true).word, 'Saving…')
  assert.equal(saveState({ ...f, failed: true }, true).word, 'Couldn’t save')
  assert.equal(saveState({ ...f, flash: 'Saved' }, false).word, 'Saved')
  assert.match(saveState({ ...f, conflict: {} }, true).word, /changed on the host/)
})
