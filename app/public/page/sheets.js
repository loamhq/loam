import { settingsHome, CONNECTIONS_GROUP } from "../app-shape.js"
import { sheetHandle } from "../chat-ui.js"
import { layoutPixels } from "../scale.js"
import { pickerSheet } from "../filepicker.js"
import { agentLook } from "../sessions.js"
import { h, icon, on as onNode, kept } from "../ui.js"
import { menuSurface } from "../menu-surface.js"
import { addSheet } from "../files.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {


// ------------------------------------------------------------------ sheets

function closeActivityDialogs(sheet) {
  const controller = sheet?.kind === 'activity' ? host.activity : sheet?.kind === 'settings' ? host.engineerActivity : null
  if (controller?.st.confirm) controller.dismiss()
  if (controller?.st.detail) controller.closeDetail()
}

function openSheet(sheet, { restoring = false } = {}) {
  if (sheet.kind === 'running' || (sheet.kind === 'settings' && sheet.page === 'running')) { host.goActivity(); host.running.load(); return }
  if (sheet.kind === 'settings') { if (CONNECTIONS_GROUP[sheet.page]) sheet.group = CONNECTIONS_GROUP[sheet.page]; sheet.page = settingsHome(sheet.page, sheet.provider) }
  if (!restoring && host.state.wide && sheet.kind === 'settings' && sheet.page === 'root' && host.state.sheet?.kind === 'settings') { closeSheet({ historyBack: true }); return }
  if (host.state.sheet?.kind === 'settings' && sheet.kind !== 'settings') host.providerSignIn.cancel()
  if (host.state.sheet?.kind !== sheet.kind) closeActivityDialogs(host.state.sheet)
  host.registry.emit('sheet.changing', { previous: host.state.sheet, sheet })
  // One menu at a time: the desktop's popover is not left open behind a sheet.
  host.state.pop = null
  // The quota reading on the providers page is the most recent one the host has
  // seen, and it moves while a session runs. Ask again on the way in.
  if (sheet.kind === 'settings') {
    host.loadProviders()
    // What the card said about the last thing tapped is not news the next
    // time Settings opens.
    host.state.github = { ...host.state.github, error: '', done: '', at: 'github', confirm: false, links: null, connect: false }
    host.loadGithub()
  }
  host.state.sheet = sheet
  if (sheet.kind === 'settings') host.registry.emit('settings.opened', { page: sheet.page, opening: true })
  host.schedule()
}

let closing = null
function closeSheet({ historyBack = false, settled = false } = {}) {
  host.providerSignIn.cancel()
  const sheet = host.state.sheet
  if (historyBack && !sheet) return
  if (historyBack && !settled && host.state.wide && !globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches) {
    if (closing === sheet) return
    const surface = host.root.querySelector?.('[data-menu-centered]')
    if (surface?.animate) {
      closing = sheet
      const motion = getComputedStyle(surface).getPropertyValue('--motion-fast').trim()
      const duration = (parseFloat(motion) * (motion.endsWith('ms') ? 1 : 1000)) || 140
      const animation = surface.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(.98)' }], { duration, easing: 'ease-out', fill: 'forwards' })
      animation.finished.catch(() => {}).then(() => {
        animation.cancel()
        closing = null
        if (host.state.sheet === sheet) closeSheet({ historyBack, settled: true })
      })
      return
    }
  }
  if (sheet?.dismiss) return sheet.dismiss()
  closeActivityDialogs(sheet)
  host.registry.emit('sheet.changing', { previous: sheet, sheet: null })
  host.state.sheet = null
  if (historyBack) host.appModalHistory.dismiss()
  host.schedule()
}



// **The top of every bottom sheet: the grip, and a visible way out.** The owner,
// 2026-09-29: "The only way to close is to click outside; there isn't any X." So
// a quiet ✕ in the design's ghost style sits at the right of the grip's row —
// 32px, round, the header's own ink — and the grip does what it looks like it
// does: the shared sheet gesture closes it when dragged down. The row is its own line rather than a button laid over
// the header, so a long title never runs under the ✕. The desktop's dialogs
// hide the row and keep their own Close.
function sheetTop() { return sheetHandle(() => closeSheet({ historyBack: host.state.sheet?.kind === 'settings' })) }



// Phone gestures are owned by the shared menu-surface controller.
const SHEET_SETTLE_MS = 220

function pickerNodes() {
  const body = pickerSheet(host.picker, { agentName: host.picker.st.agent ? agentLook(host.picker.st.agent, host.state.agents).name || host.picker.st.agent : null })
  const close = () => host.picker.close()
  const card = h('div', { class: 'sheet fs-sheet fs-picker' }, sheetHandle(close), ...body)
  const surface = menuSurface(card, { title: 'Choose files', close, heading: false, width: 520 })
  const node = host.state.wide ? h('div', { class: 'dialog fs-dialog', onclick: (e) => { if (e.target === e.currentTarget) close() } }, surface) : surface
  node.__key = 'sheet:picker'
  return [h('button', { class: 'scrim', key: 'scrim:picker', title: 'Close', onclick: close }), node]
}

function filesSheetNodes() {
  const close = () => {
    host.files.st.sheet = null
    host.schedule()
  }
  close.redraw = host.schedule
  if (host.files.st.sheet?.kind === 'name' && host.fileNameField.__for !== host.files.st.sheet) {
    host.fileNameField.value = ''
    host.fileNameField.__for = host.files.st.sheet
  }
  const body = addSheet(host.files, { close, nameField: kept(host.fileNameField), nameValue: () => host.fileNameField.value, chooseUpload: host.chooseUpload, uploadChooser: kept(host.filesUploadChooser) })
  if (!body) return []
  const card = h('div', { class: 'sheet fs-sheet' }, sheetHandle(close), ...body)
  const surface = menuSurface(card, { title: host.files.st.sheet.name ?? 'File actions', close, heading: false, width: 520,
    fallbackPlacement: host.files.st.sheet.kind === 'leave' ? 'center' : null })
  const node = host.state.wide ? h('div', { class: 'dialog fs-dialog', onclick: (e) => { if (e.target === e.currentTarget) close() } }, surface) : surface
  node.__key = `sheet:files:${host.files.st.sheet.kind}`
  return [h('button', { class: 'scrim', key: 'scrim:files', title: 'Close', onclick: close }), node]
}

function sheetNodes() {
  const sheet = host.state.sheet
  if (!sheet) return host.picker.st.open ? pickerNodes() : filesSheetNodes()
  if (sheet.kind === 'market') return host.marketNodes(sheet)
  if (!['settings', 'activity', 'menu'].includes(sheet.kind)) return host.registry.draw('app.overlay', { slot: 'sheet', sheet })
  const centered = host.state.wide && ['settings', 'activity'].includes(sheet.kind)
  const close = () => closeSheet({ historyBack: centered || sheet.kind === 'settings' })
  const scrim = h('button', { class: 'scrim', key: 'scrim', title: 'Close', 'aria-label': 'Close', onclick: close })
  const body = sheet.kind === 'settings' ? host.state.wide ? host.settingsDialog(sheet) : host.settingsSheet(sheet)
    : sheet.kind === 'activity' ? h('div', { class: 'sheet app-modal activity-modal' },
      h('header', { class: 'app-modal-head' }, h('h2', { class: 'display sheet-title', tabindex: '-1', text: 'Activity' }),
        h('button', { class: 'iconbtn ghost', 'aria-label': 'Close Activity', onclick: close }, icon.close(14))), host.activity.view())
    : host.menuSheet(sheet)
  const titles = { menu: host.composerMenu(sheet.page)?.title ?? 'Attach', settings: 'Settings', activity: 'Activity' }
  const surface = menuSurface(body, { centered, anchor: sheet.kind === 'settings' && !centered ? '.settings-utility' : null, fallbackPlacement: sheet.kind === 'settings' ? 'center' : null, title: titles[sheet.kind] ?? 'Options', close, initialFocus: centered || sheet.kind === 'settings' ? 'heading' : null, width: centered ? 960 : sheet.kind === 'settings' ? 760 : body.classList.contains('actions-sheet') ? 340 : 480,
    heading: !['settings', 'activity'].includes(sheet.kind) && !body.querySelector('.display') })
  const node = host.state.wide ? h('div', { class: `dialog ${sheet.kind}`, onclick: (e) => { if (e.target === e.currentTarget) close() } }, surface) : surface
  node.__key = `sheet:${sheet.kind}`
  return [scrim, node, ...host.registry.draw('app.overlay', { slot: 'sheet', sheet })]
}
return Object.defineProperties({}, {
    openSheet: { enumerable: true, get: () => openSheet },
    closeSheet: { enumerable: true, get: () => closeSheet },
    sheetTop: { enumerable: true, get: () => sheetTop },
    SHEET_SETTLE_MS: { enumerable: true, get: () => SHEET_SETTLE_MS },
    pickerNodes: { enumerable: true, get: () => pickerNodes },
    filesSheetNodes: { enumerable: true, get: () => filesSheetNodes },
    sheetNodes: { enumerable: true, get: () => sheetNodes }
})
}
