import { createThreads } from '../threads.js'
import { markdown } from '../markdown.js'
import { clearAttachments, stored, uploading, restoreAttachments } from '../attach.js'
import { createMessageState } from '../chat-ui.js'

// The same live conversation mount is used in either slot. Fields and steps
// are services of this mount; audio and host residency remain app services.
export function mount(host, options = {}) {
  return createThreads({ registry: host.registry, api: host.api, redraw: host.schedule,
    changedAgent: agent => host.changedAgent?.(agent), agents: () => host.state.agents.list, agentsReady: () => host.state.agentsReady, providers: () => host.state.providers,
    running: host.running, rail: host.rail, nav: host.nav, openProfile: host.goProfile, openSettings: (page, provider) => host.goSettings?.(page, provider),
    listIsMain: () => ['home', 'threads'].includes(host.state.screen),
    visitId: () => host.panelId === 'side' ? host.panels.sideVisit() : globalThis.history?.state?.panelVisit ?? 'initial',
    historyButton: label => host.backButton(host.panelId, label),
    messageState: options.messageState ?? createMessageState(),
    renderText: (text, extra = {}) => markdown(text, { ...extra, copy: host.copyButton, onLink: (ref, from, event) => host.openFileRef(ref, from, event, { chat: extra.chatLinks }) }),
    openBoardFile: (path, id) => {
      const item = { kind: 'file', root: 'vault', path, mode: 'edit' }
      if (host.panels.available()) {
        host.panels.openSide(item)
      } else {
        globalThis.history?.replaceState?.({ ...globalThis.history.state, board: id }, '')
        host.panels.openMain({ ...item, boardThread: id })
      }
    },
    openRef: (ref, from, event) => host.openFileRef(ref, from, event),
    // A listing card in an agent's reply (page/market.js).
    marketCards: (refs, context) => host.marketCards?.(refs, context) ?? null,
    stepUI: { fold: host.foldCard, tool: host.toolNode, item: item => host.itemNode({ what: item.kind, item }),
      facts: (item, engine) => host.turnFacts(item, host.state.providers.find(p => p.id === engine) ?? null),
      reveal: steps => host.openBlocks.add(`fold:${host.keyOf(steps[0])}`) },
    sidebar: { collapsed: () => host.state.collapsed, set: host.setCollapsed, view: options.sidePanel ? null : () => host.listColumn() },
    compose: { field: () => host.draft, render: ctx => host.composer(ctx),
      leave: () => { host.registry.emit('field.reset', { host }); host.state.editor = false; host.closeMenus(); clearAttachments(host.state.attach); host.growDraft() },
      dictating: () => Boolean(host.fieldActivity('draft')?.active),
      audioLevel: () => host.fieldActivity('draft')?.level ?? 0,
      files: () => stored(host.state.attach), busy: () => uploading(host.state.attach) || Boolean(host.fieldActivity('draft')?.active),
      clear: () => { clearAttachments(host.state.attach); host.growDraft() },
      restore: items => restoreAttachments(host.state.attach, items), changed: () => { host.growDraft(); host.schedule() }, dragProps: host.dragProps },
    ...options,
  })
}
