import { mount as mountFiles } from './files.js'
import { mount as mountComposer } from './composer.js'
import { mount as mountTranscript } from './transcript.js'
import { mount as mountSheets } from './sheets.js'
import { mount as mountConversation } from './conversation.js'
import { createApps, appAddress, canFrame, appUnavailable } from '../apps.js'
import { createAttachments, addFile, tooBig, markProgress, markStored, markFailed, carriesFiles, filesFrom } from '../attach.js'
import { createMessageState } from '../chat-ui.js'
import { fileView, folderView, nameOf, parentOf, splitPath, ROOT_LOOK } from '../files.js'
import { agentLook } from '../sessions.js'
import { h, icon, blob } from '../ui.js'
import { copyText } from '../clipboard.js'
import { panelDOM } from '../panels.js'
import { menuSurface } from '../menu-surface.js'
import { anchorOf } from '../markdown.js'

export function mountSidePanel(base) {
  const local = { screen: 'home', attach: createAttachments(), sheet: null, pop: null, editor: false,
    draftFocused: false, dragging: false, lastNotice: null, profileAgent: null }
  const state = new Proxy(local, {
    get: (target, name) => name === 'collapsed' ? false : Object.hasOwn(target, name) ? target[name] : base.state[name],
    set: (target, name, value) => {
      target[name] = value
      return true
    },
  })
  const modules = [], own = { panelId: 'side', state, root: panelDOM('side') }
  own.panelDOM = own.root
  const messageState = createMessageState()
  own.messageState = messageState; own.longMine = messageState.long
  const host = new Proxy(own, {
    get: (target, name) => {
      if (Object.hasOwn(target, name)) return target[name]
      const part = modules.findLast(part => name in part)
      if (part) return part[name]
      // Lazy calls let mounts refer to a service that is installed afterwards.
      if (['draft', 'activeThread', 'growDraft', 'composer', 'copyButton', 'foldCard', 'toolNode', 'itemNode', 'turnFacts', 'keyOf', 'openBlocks', 'fileFieldFor', 'applyTool', 'embedFor', 'fileDictation', 'filesHome', 'openSheet', 'closeSheet', 'closeMenus'].includes(name))
        return (...args) => host[name](...args)
      return base[name]
    },
    set: (target, name, value) => { target[name] = value; return true },
  })
  // Plugin renderers receive the mount that owns their field, rather than a
  // closure over the first composer on the page. The player is drawn once.
  own.registry = new Proxy(base.registry, { get: (target, name) => name === 'draw'
    ? (place, context) => place === 'app.overlay' && context.slot === 'player' ? [] : target.draw(place, { ...context, host })
    : typeof target[name] === 'function' ? target[name].bind(target) : target[name] })
  own.fieldActivity = target => own.registry.draw('composer.tool', { slot: 'state', target }).at(-1) ?? null
  own.activeThread = () => state.screen === 'threads' ? own.threads.st.open : null
  own.saveDraft = () => { if (own.activeThread()) own.threads.saveCompose() }
  own.goHome = own.goChats = () => base.panels.close()
  own.goProfile = agent => base.goProfile(agent)
  own.goFiles = (...args) => modules[0].goFiles(...args)
  own.goApps = () => base.panels.close()
  own.chooseFiles = input => { host.closeMenus(); input.value = ''; input.click() }
  own.picked = input => { const entries = [...(input.files ?? [])].map(file => ({ file, name: file.name })); input.value = ''; return own.intake(entries) }
  own.intake = entries => {
    const thread = own.activeThread()
    if (!thread || thread.creating) return []
    const attachmentState = state.attach
    const going = entries.map(async ({ file, name }) => {
      const complaint = tooBig(file, state.maxAttachmentBytes), item = addFile(attachmentState, file, { error: complaint, name })
      if (complaint) return
      const form = new FormData(); form.append('file', file, item.name)
      try {
        const answer = own.threads.st.fixture ? await own.threads.uploadPreview(file, item.name)
          : await base.put(`/api/threads/${encodeURIComponent(thread.thread.id)}/attachments`, form, (loaded, total) => { markProgress(attachmentState, item, loaded, total); base.schedule() })
        markStored(attachmentState, item, answer)
      } catch (error) { markFailed(attachmentState, item, error.message) }
      if (own.activeThread() === thread) own.saveDraft()
      base.schedule()
    })
    base.schedule(); return going
  }
  own.dragProps = () => ({
    ondragover: event => { if (!carriesFiles(event.dataTransfer)) return; event.preventDefault(); state.dragging = true; base.schedule() },
    ondragleave: event => { if (!event.currentTarget.contains(event.relatedTarget)) { state.dragging = false; base.schedule() } },
    ondrop: event => { if (!carriesFiles(event.dataTransfer)) return; event.preventDefault(); event.stopPropagation(); state.dragging = false; own.intake(filesFrom(event.dataTransfer)) },
  })
  own.provide = (group, service) => {
    const entry = service; modules.push(entry); base.schedule()
    return { remove: () => { const at = modules.indexOf(entry); if (at >= 0) modules.splice(at, 1); base.schedule() } }
  }
  modules.push(mountFiles(host, { navigation: false }))
  // goFiles above is a lazy reference to this mount.
  own.goFiles = modules[0].goFiles
  modules.push(mountComposer(host), mountTranscript(host), mountSheets(host))
  own.threads = mountConversation(host, {
    messageState, dom: own.root, sidePanel: true, side: () => base.panels,
    navigate: item => base.panels.openSide(item), address: () => {}, visible: () => base.panels.visible(),
    enter: () => { host.files.closeFile(); state.screen = 'threads'; state.sheet = null; base.schedule() },
    leave: () => base.panels.close(),
  })
  own.apps = createApps({ registry: own.registry, api: base.api, redraw: base.schedule, wide: () => true,
    avatar: id => blob(agentLook(id, state.agents), { size: 40, font: 18 }), agentName: id => state.agents.list.find(a => a.id === id)?.name ?? id,
    newThread: () => base.threads.create(), copy: copyText, navigate: item => base.panels.openSide(item), side: () => base.panels,
  })
  let request = 0
  function item() {
    if (state.screen === 'threads' && own.threads.st.open) return { kind: 'thread', id: own.threads.st.open.thread.id }
    const f = host.files.st.file
    if (state.screen === 'files' && f) return { kind: 'file', root: f.root, path: f.path, mode: f.mode }
    if (state.screen === 'apps' && own.apps.st.selected) return { kind: 'app', app: own.apps.st.selected }
    if (state.screen === 'apps' && state.requestedApp) return { kind: 'app', url: state.requestedApp }
    return null
  }
  function clear() {
    request++; state.requestedApp = null; state.appMenu = false
    host.registry.emit('field.reset', { host }); own.threads.close({ navigate: false }); host.files.closeFile(); own.apps.st.selected = null
    state.screen = 'home'; state.editor = false; state.pop = null; state.sheet = null
  }
  function open(item) {
    const ticket = ++request
    state.requestedApp = null; state.appMenu = false
    own.apps.st.selected = null
    if (item.kind === 'thread') {
      const pending = own.threads.open(item.id, { routed: true })
      if (item.message && own.threads.st.open) Object.assign(own.threads.st.open, { replyJump: item.message, jumpKind: 'quote' })
      return pending
    }
    own.threads.close({ navigate: false })
    host.files.closeFile()
    if (item.kind === 'file') return host.goFiles(item.root, item.path, { isFile: true, row: item.row, fragment: item.fragment, mode: item.mode, push: false, routed: true })
    state.screen = 'apps'
    if (item.app && item.app.kind !== 'plugin') return own.apps.open(item.app, null, { routed: true })
    const url = item.url ?? item.app?.url
    state.requestedApp = url
    own.apps.load().then(() => {
      if (request !== ticket) return
      if (own.apps.st.error) return
      const app = own.apps.st.apps.find(app => app.url === url)
      if (app && canFrame(app)) own.apps.open(app, null, { routed: true })
      else if (app) { own.apps.st.error = 'This app opens in a browser.'; base.schedule() }
      else { own.apps.st.error = 'That app is no longer available.'; base.schedule() }
    })
  }
  function guard(retry) {
    if (!host.files.dirty()) return true
    host.files.guard(null); host.files.st.sheet.proceed = host.proceedThen(retry); return false
  }
  function transfer(to, id) {
    own.threads.transferTo(to.threads, id)
  }
  function header() {
    const at = item(), f = host.files.st.file
    const label = at?.kind === 'app' ? at.app?.name ?? (own.apps.st.error ? 'App unavailable' : 'Opening app…') : f ? nameOf(f.path) : 'Files'
    const location = at?.kind === 'app' ? at.app ? appAddress(at.app.url) : at.url : f ? [ROOT_LOOK[f.root]?.name ?? f.root, ...splitPath(parentOf(f.path))].join(' / ') : ''
    const controls = base.panels.controls()
    return h('header', { class: 'side-head' },
      h('span', { class: 'side-face', 'aria-hidden': 'true' }, at?.kind === 'app' ? icon.apps(20) : icon.file(20)),
      h('div', { class: 'side-heading' }, h('strong', { text: label }), h('span', { text: location, title: location })),
      controls[0], controls[1], h('button', { class: 'iconbtn ghost', title: 'Item actions', 'aria-label': 'Item actions', onclick: () => {
        if (f) host.files.resourceMenu(f.row, host.filesHome())
        else { state.appMenu = !state.appMenu; base.schedule() }
      } }, icon.dots(18)), controls[2])
  }
  function view() {
    const f = host.files.st.file
    const body = state.screen === 'threads' ? own.threads.pane()
      : [header(), state.screen === 'apps' ? own.apps.view()
        : f ? fileView(host.files, { wide: true, field: host.fileFieldFor, home: host.filesHome(), go: host.goFiles, onLink: host.openFileRef,
          onTool: host.applyTool, embed: host.embedFor, ...host.fileDictation() })
          : folderView(host.files, { wide: true, go: host.goFiles, home: host.filesHome() })]
    const app = own.apps.st.selected
    const appMenu = state.appMenu && app ? menuSurface(h('div', { class: 'popover side-app-menu' },
      !appUnavailable(app) ? h('a', { class: 'setrow', href: app.url, target: '_blank', rel: 'noopener noreferrer', text: 'Open in browser' }) : null,
      h('button', { class: 'setrow', text: own.apps.st.copied ? 'Copied' : 'Copy link', onclick: own.apps.copyLink }),
      own.apps.st.copyError ? h('span', { role: 'status', text: own.apps.st.copyError }) : null),
    { title: 'App actions', anchor: '[data-panel="side"] [aria-label="Item actions"]', close: () => { state.appMenu = false; base.schedule() } }) : null
    return h('section', { class: 'pane side-pane', 'data-panel': 'side', 'aria-label': 'Side panel', key: 'side-pane' }, ...body, appMenu,
      host.editorNodes(), ...host.sheetNodes())
  }
  function afterRender() {
    if (state.screen === 'threads') own.threads.afterRender()
    host.fitToolOutputs(); host.fitMine(); host.files.focusRename()
    const f = host.files.st.file
    if (f?.fragment && !f.loading) {
      const heading = [...(own.root.querySelectorAll('.anchored') ?? [])].find(node => node.getAttribute('data-anchor') === anchorOf(f.fragment.replace(/^\^/, '')))
      heading?.scrollIntoView({ block: 'start' }); f.fragment = null
      f.landed = heading?.getAttribute('data-anchor') ?? null
    }
  }
  return { host, threads: own.threads, item, open, clear, guard, transfer, view, afterRender,
    pending: () => Boolean(own.threads.st.open?.creating || own.threads.st.open?.createFailed), empty: () => state.screen === 'threads' && !own.threads.st.open }
}
