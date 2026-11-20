// The Files plugin exports its standalone editor. Mount it only when its
// Markdown viewer is active here (including the plugin's staging restriction).
// The adapter gives it visible Markdown and routes every save to this board.
import { kept } from './ui.js'
import { owner } from './owner.js'
import { docText, applyDocText } from './board-document.js'

export function boardFilesEditor({ registry, redraw, write, draft, conflict, openRef }) {
  let current = null
  const asDoc = board => ({
    id: board.path, text: docText(board.markdown), revision: board.revision,
    me: { id: owner().id, name: owner().name, kind: 'owner' }, can: { edit: true },
  })
  function leave() {
    const entry = current
    current = null
    if (entry?.editor) {
      if (entry.editor.dirty() && !entry.conflicted) entry.editor.flush().finally(() => entry.editor.destroy())
      else entry.editor.destroy()
    }
  }
  function mount(o) {
    const file = { root: 'vault', path: o.board.path, view: 'markdown' }
    if (!registry.entries('files.viewer', { slot: 'viewer', file }).some(e => e.id === 'doc-comments.editor' && e.owner === 'doc-comments')) {
      leave(); return undefined
    }
    if (current?.o !== o) {
      leave()
      const entry = current = { o, raw: o.board.markdown, base: o.board }
      import('/api/plugins/doc-comments/files/page/editor/editor.js').then(({ createDocEditor }) => {
        if (current !== entry) return
        entry.base = o.board; entry.raw = o.board.markdown
        entry.editor = createDocEditor({
          doc: asDoc(o.board), phone: true, history: false, crumbs: [{ label: o.thread.title }],
          openLink: (ref, event) => {
            if (/^(https?:|mailto:)/i.test(ref)) window.open(ref, '_blank', 'noopener,noreferrer')
            else openRef?.(ref, { root: 'vault', path: file.path }, event)
          },
          onText: value => {
            entry.raw = applyDocText(entry.raw, value, file.path)
            draft(o, entry.base.markdown, entry.raw)
          },
          api: {
            get: async () => asDoc(entry.base),
            save: async body => {
              const text = applyDocText(entry.raw, body.text, file.path)
              const base = entry.base
              try {
                const answer = await write(o, text, body.base_revision)
                entry.base = answer
                o.board = answer
                draft(o, answer.markdown, entry.raw)
                redraw()
                return asDoc(answer)
              } catch (err) {
                entry.conflicted = true
                await conflict(o, base.markdown, entry.raw, err)
                if (current === entry) leave()
                // The host now owns the retained draft and its retry button.
                return asDoc(o.board)
              }
            },
          },
        })
        redraw()
      }).catch(() => { if (current === entry) { entry.failed = true; redraw() } })
    }
    if (current.failed) return undefined
    const ed = current.editor
    if (ed && !ed.dirty() && current.base.revision !== o.board.revision) {
      current.base = o.board; current.raw = o.board.markdown
      ed.reload()
    }
    return ed ? kept(ed.dom) : null
  }
  return { mount, leave }
}
