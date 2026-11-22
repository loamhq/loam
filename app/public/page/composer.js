import { listeningRecipients } from "../thread-identity.js"
import { isMac } from "../keys.js"
import { richField } from "../compose.js"
import { h, icon, kept } from "../ui.js"
import { removeAttachment, bytes, uploading, stored, heldFiles, isVideoName } from "../attach.js"
import { loginView } from "../providers.js"
import { menuSurface } from "../menu-surface.js"

// Mounted once. Cross-module state and operations are explicit host services.
export function mount(host) {


// ------------------------------------------------------------------ drawing

// The fields a person can have a cursor in. They are made once and *placed* on
// every redraw rather than rebuilt — see `kept()` in ui.js — so what is typed,
// where the caret is and what is selected all survive a screen being drawn
// again around them, without anything being put back by hand.
//
// Row one of the composer. It grows from one line to six and then scrolls
// inside itself, because a draft that grows without end eats the transcript it
// is about and, on a phone with the keyboard up, eats itself. The number is
// here and in the stylesheet's `max-height`, and `test/style.test.js` holds the
// two together — a box that grew past what CSS would draw would simply clip.
const DRAFT_MAX_PX = 132



// Which chord the hint line names. Read once: a machine does not become a Mac
// while the page is open.
const MAC = isMac(globalThis.navigator)



// Row one is a rich field and not a `<textarea>`, since 2026-09-22. The owner:
// "please make sure bullet points and numbered lists render as MD in the text
// input" — a bullet is a bullet as it is typed, `**bold**` is bold the moment
// it closes, and what is sent is the markdown underneath, which the bubble on
// the other side renders with the same `markdown.js`.
//
// Everything outside `compose.js` still talks to it as the textarea it was:
// `draft.value` is the markdown it holds, `draft.placeholder` is what it says
// when empty. The draft store, dictation and `submitDraft()` below are
// unchanged because of that, and are the reason the accessors exist.
const draft = richField(
  h('div', {
    id: host.panelId === 'side' ? 'side-draft' : 'draft',
    class: 'draft',
    // Row one carries a key and the textarea did not, because it is now a
    // `div` in a box whose other children are `div`s: the reconciler matches an
    // unkeyed node by position and tag, so a chip strip appearing above row one
    // matched *this* node and patched the field into an attachment strip — the
    // draft, its listeners' node identity and the caret in it, gone on the
    // first file chosen. Caught by `test/app.test.js`, 2026-09-22.
    key: 'draft',
    contenteditable: 'true',
    role: 'textbox',
    'aria-multiline': 'true',
    enterkeyhint: 'enter',
    // The keyboard animates in, and a phone reports the viewport it is going to
    // leave part way through rather than at the moment of focus. The browser's
    // own `resize` is what this app acts on; these are the two nudges that catch
    // a browser which sends the last one early, and each is free when the box
    // has not moved.
    onfocus: () => {
      host.state.draftFocused = true
      host.schedule()
      host.fitViewport()
      setTimeout(host.fitViewport, 150)
      setTimeout(host.fitViewport, 450)
    },
    onblur: () => {
      host.state.draftFocused = false
      host.schedule()
      setTimeout(host.fitViewport, 150)
    },
  }),
  {
    sends: event => host.sendKeys.sends(event),
    key: (event) => activeThread() && host.threads.mentionKey(event),
    send: () => submitDraft(),
    // A screenshot pasted into the field. The field hands over the files it
    // found and knows nothing about where they go; this is the same intake the
    // + menu and a drop use.
    files: (entries) => { if (!activeThread()?.creating) host.intake(entries) },
    changed: () => {
      // Typing is "any other action" as far as a failed take's notice goes.
      host.registry.emit('field.input', { host })
      growDraft()
      // Written down as it is typed, not only when the chat is left: a phone
      // that sleeps, a tab that is closed, a reload — none of those is a
      // leaving the thread.
      host.saveDraft()
      if (activeThread()) host.threads.mention()
      host.schedule()
    },
  },
)



// The same draft, with a screen to write it on.
//
// The owner, 2026-09-25: "Have an option in the text box to open a bigger full
// screen view to work on the message that makes it easier to work on longer
// prompts." Row one is six lines and then it scrolls, which is right for a
// question and wrong for a brief.
//
// **There is one draft and this is a second view of it, not a second value.**
// Every keystroke here writes straight through to `draft.value` and parks it, so
// `submitDraft()` — which reads the composer's field and knows nothing about
// this screen — sends the same text whichever of the two it was typed in, and a
// phone that dies with the editor open reopens with the words in the composer.
// The alternative, a string held here and handed back on Done, is two copies of
// one thing and a lost draft on every way out that is not Done.
//
// It is the same `richField` for the same reason: the markdown-as-you-type is
// the composer's behaviour and a big box that drew asterisks would be a
// different editor.
const editorDraft = richField(
  h('div', {
    id: host.panelId === 'side' ? 'side-editor-draft' : 'editor-draft',
    class: 'draft editor-draft',
    key: 'editor-draft',
    contenteditable: 'true',
    role: 'textbox',
    'aria-multiline': 'true',
    enterkeyhint: 'enter',
  }),
  {
    sends: event => host.sendKeys.sends(event),
    send: () => sendFromEditor(),
    // Attachments and the model belong to the chat and not to this screen, so
    // nothing is taken in here: a paste of an image is the composer's business
    // and this is text only.
    files: () => {},
    changed: () => {
      host.registry.emit('field.input', { host })
      draft.value = editorDraft.value
      host.saveDraft()
      host.schedule()
    },
  },
)

function openEditor() {
  closeMenus()
  editorDraft.value = draft.value
  host.state.editor = true
  host.schedule()
  setTimeout(() => editorDraft.focus?.(), 0)
}



// Done, Escape, and the back chevron are all this: the screen goes and the
// composer has the words, with the caret at the end of them — which is where the
// person was typing.
function closeEditor() {
  if (!host.state.editor) return
  // A take in the editor's field goes with the screen it was drawn on; what was
  // typed is unaffected, as it is by any ✕.
  host.registry.emit('field.closed', { target: 'editor', host })
  host.state.editor = false
  const text = editorDraft.value
  draft.value = text
  growDraft()
  host.saveDraft()
  host.schedule()
  setTimeout(() => draft.focus?.(), 0)
}



// Sending from the editor sends the chat's message, attachments and all: the
// draft is one value and the send is the one send. The screen goes first so the
// person lands back in the conversation their message has just joined.
function sendFromEditor() {
  if (host.fieldActivity('editor')) return
  draft.value = editorDraft.value
  host.state.editor = false
  submitDraft()
}



// The two choosers behind the + sheet. They are made once and *placed* like the
// fields above, because a chooser has to be in the page for a browser to open
// it — Safari refuses `click()` on a detached input.
//
// The image one takes `accept="image/*"` and no `capture`: that is what lets a
// phone offer the camera *and* the gallery. `capture` would force the camera and
// take the gallery away, which is the opposite of what the design asks for.
const fileChooser = h('input', {
  id: host.panelId === 'side' ? 'side-pick-file' : 'pick-file',
  class: 'picker',
  type: 'file',
  multiple: true,
  onchange: () => host.picked(fileChooser),
})

const imageChooser = h('input', {
  id: host.panelId === 'side' ? 'side-pick-image' : 'pick-image',
  class: 'picker',
  type: 'file',
  accept: 'image/*,video/mp4,video/quicktime,video/webm,.mov',
  multiple: true,
  onchange: () => host.picked(imageChooser),
})




// The box is the height of what is in it, up to six lines. `auto` first,
// because `scrollHeight` on an element already stretched to hold its content
// answers the height it has rather than the height it needs, so a draft that
// shrank would never shrink back.
function growDraft() {
  if (!draft.style) return
  draft.style.height = 'auto'
  const wanted = draft.scrollHeight ?? 0
  draft.style.height = wanted > 0 ? `${Math.min(wanted, DRAFT_MAX_PX)}px` : ''
}

function submitDraft() {
  if (host.fieldActivity('draft')) return
  host.registry.emit('field.input', { host })
  if (activeThread()) host.threads.send()
}




// What this message is carrying, above the composer: one chip per file, with the
// state it is in and a × that takes it off. A chip that is still going up says
// how far; one the host refused says why, in the host's words or in this page's
// about the cap, and is taken off the same way.
function attachStrip() {
  const items = host.state.attach.items
  if (items.length === 0) return null
  return h('div', { class: 'attachstrip' },
    ...items.map((item) => {
      const active = !item.path && !item.error && !item.held
      const action = active ? `Cancel upload of ${item.name}` : `Take ${item.name} off this message`
      return h(
        'div',
        { class: `attachment-chip${isVideoName(item.name) ? ' video-chip' : ''}${item.error ? ' bad' : ''}${active ? ' going' : ''}`, key: item.id },
        item.kind === 'image' ? icon.image(13) : icon.file(13),
        h(
          'span',
          { class: 'attachment-chip-who' },
          h('span', { class: 'attachment-chip-name', text: item.name }),
          h('span', { class: 'attachment-chip-sub', role: item.error ? 'status' : null, text: chipSub(item) }),
        ),
        h(
          'button',
          {
            class: 'attachment-chip-x',
            type: 'button',
            title: action,
            'aria-label': action,
            onclick: () => {
              removeAttachment(host.state.attach, item.id)
              host.saveDraft()
              host.schedule()
            },
          },
          icon.close(10),
        ),
      )
    }),
  )
}



// Under the name: why it will not go, how far it has got, or how big it is.
function chipSub(item) {
  if (item.error) return item.error
  if (item.path && isVideoName(item.name)) {
    const thread = activeThread()?.thread
    const agents = host.threads.catalog()
    const engine = id => thread.members.find(m => m.persona === id)?.engine ?? agents.find(a => a.id === id)?.engine
    const recipients = thread ? listeningRecipients(thread, draft.value || ' ', true, agents) : []
    const unable = recipients.filter(id => ['codex', 'claude-code'].includes(engine(id)))
    if (unable.length) return `${unable.map(id => agents.find(a => a.id === id)?.name || id).join(', ')} cannot watch this video. The file stays in Files.`
    return recipients.length && recipients.every(id => engine(id) === 'antigravity')
      ? 'Ready to watch with Antigravity' : 'Antigravity can watch. Other agents receive the file.'
  }
  if (item.path) return bytes(item.size)
  if (item.progress >= 1) return 'Saving…'
  if (item.progress >= .01) return `${Math.floor(item.progress * 100)}% uploaded`
  return 'Uploading…'
}




// The sign-in warning as a bar of its own, which is what the **Providers list**
// draws: a row per provider whose login is about to run out, at the top of the
// page it is fixed on.
//
// Above a composer this is no longer a bar — it is the top rung of the one quiet
// line, in `noticeRow()` above, because a chat can only ever have one thing to
// say there. The threshold, the arithmetic and the words are providers.js's in
// both places; what differs is the chrome and the fact that a list can carry
// several of these at once and a chat cannot.
function loginNotice(providerId) {
  const view = loginView(host.state.providers.find((p) => p.id === providerId))
  if (!view || view.level === 'none') return null
  return h(
    'div',
    { class: `loginwarn ${view.level}`, key: `loginwarn:${view.id}` },
    h('span', { text: `${view.notice} · ` }),
    h('button', {
      class: 'loginwarn-act',
      type: 'button',
      text: 'Sign in again',
      onclick: () => host.goSettings('provider', view.id),
    }),
  )
}




// The composer: one rounded box with two rows in it, on both layouts.
//
// The owner, 2026-09-22: "the text box should have two rows. Row one is the text
// input. Row 2 is all the actions. The box design should be a rectangle with
// rounded edges, not half circles." So the box is the design's card radius and
// not a pill, the text has the full width of it to grow into, and the buttons
// are underneath rather than crowding the line being typed. What the chips are
// carrying goes inside the box above row one, because they belong to the
// message being written and not to the screen behind it — and above them the
// one notice this chat has, when it has one (`noticeRow()`), with the context
// as a hairline along the box's top edge (`contextEdge()`).
function activeThread() { return host.state.screen === 'threads' ? host.threads.st.open : null }

function composer(...args) { return host.registry.component('core.composer', { variant: 'form', args, defaultView: () => builtinComposer(...args) }) }

function builtinComposer(ctx) {
  const going = uploading(host.state.attach)
  // What this message would carry: the files the host has already written down,
  // and — in a draft, where there is nowhere to write them down yet — the ones
  // the page is holding. A message that is nothing but a file is an ordinary
  // message, so either kind is something to send.
  const carrying = stored(host.state.attach).length > 0 || heldFiles(host.state.attach).length > 0
  const has = (draft.value.trim() !== '' || (carrying && !ctx.requiresText)) && !going && !ctx.disabled
  // A take in the field: row one is the take, and row two goes quiet — every
  // control in it disabled and the send gone — so the only two things that can
  // be pressed are the ✕ and the ✓ inside the field.
  const take = host.fieldActivity('draft')
  draft.placeholder = ctx.changePrompt ?? ctx.placeholder ?? `Message ${ctx.agentName}`
  const editable = String(!ctx.readOnly), label = ctx.changePrompt ?? 'Message thread'
  if (draft.getAttribute('contenteditable') !== editable) draft.setAttribute('contenteditable', editable)
  if (draft.getAttribute('aria-readonly') !== String(Boolean(ctx.readOnly))) draft.setAttribute('aria-readonly', String(Boolean(ctx.readOnly)))
  if (draft.getAttribute('aria-label') !== label) draft.setAttribute('aria-label', label)
  return h(
    'form',
    {
      class: 'composer',
      onsubmit: (e) => {
        e.preventDefault()
        submitDraft()
      },
    },
    // The two choosers are in the page because a browser will not open one that
    // is not. They are never drawn; the sheet is what opens them.
    kept(fileChooser),
    kept(imageChooser),
    // The menu, where the desktop draws it: anchored to this box and inside the
    // composer it belongs to, with a scrim behind it so a click anywhere else
    // closes it. On the phone the same content is a bottom sheet, so there is
    // nothing here to draw.
    // `state.wide` and not `state.pop` alone: a window dragged below the
    // threshold with the menu open gets the phone's layout, and the phone's
    // menu is a sheet.
    host.state.wide && host.state.pop
      ? h('button', { class: 'popscrim', key: 'popscrim', type: 'button', title: 'Close', onclick: closeMenus })
      : null,
    host.state.wide && host.state.pop ? menuPopover() : null,
    !take ? host.registry.draw('composer.tool', { host, slot: 'advice', target: 'draft' }) : null,
    h(
      'div',
      { class: 'composer-box' },
      ctx.mentionPicker,
      host.registry.draw('composer.tool', { host, slot: 'notice', target: 'draft', context: ctx })
        .filter(node => !node?.classList?.contains('speech-key-link')),
      attachStrip(),
      // Row one: the field, and the glyph that opens it full screen in its
      // top-right corner. The owner, 2026-09-25: "I like the design, but I feel
      // like it can be put in a more intelligent and UX strategic place." It
      // sat in row two among the buttons that act on a message; it is an
      // affordance of the *box*, so it sits on the box it expands. Keyed, as
      // the field is, so a chip strip arriving above cannot be matched to it.
      //
      // While a take is going the field stays in the row, hidden rather than
      // emptied, and the take is drawn beside it in the same footprint.
      h(
        'div',
        { class: `draft-row${take ? ' taking' : ''}`, key: 'draft-row' },
        kept(draft),
        take ? host.registry.draw('composer.tool', { host, slot: 'take', target: host.state.editor ? 'editor' : 'draft' }) : host.registry.draw('composer.tool', { host, slot: 'expand', target: 'draft', defaultView: () => expandGlyph() }),
      ),
      // Row two, one horizontal row at every width. Its class is
      // `composer-actions` and not `actions`: `actions` is the action sheet's
      // card, which is a column, and sharing the name drew this row as one.
      h(
        'div',
        { class: `composer-actions${take ? ' quiet' : ''}` },
        host.registry.draw('composer.tool', { host, slot: 'attach', target: 'draft', context: ctx, defaultView: () => h(
          'button',
          {
            class: `plusbtn${host.state.wide && menuOpen('root') ? ' on' : ''}`,
            type: 'button',
            title: 'Attach a file',
            disabled: Boolean(take) || ctx.disabled,
            onclick: togglePlus,
          },
          icon.plus(18),
        ) }),
        // What this chat runs on, beside the +, because it is a setting a
        // person changes mid-conversation and not something to go looking for
        // inside a menu about attachments.
        //
        // The owner, 2026-09-22: "instead have a model button next to the plus
        // that opens the model and effort options." It names the record's own
        // model — the agent's own default when the record names none, which is
        // a default this app does not know the name of and does not invent —
        // and it opens the same THIS CHAT menu the + used to hold, in this
        // layout's own chrome.
        host.registry.draw('composer.tool', { host, slot: 'model', target: 'draft', context: ctx, defaultView: () => ctx.modelControl }),
        host.registry.draw('composer.tool', { host, slot: 'mention', target: 'draft', context: ctx, defaultView: () => ctx.mentionButton ?? null }),
        h('div', { class: 'composer-gap' }),
        host.registry.draw('composer.tool', { host, slot: 'extra', target: 'draft', context: ctx }),
        host.registry.draw('composer.tool', { host, slot: 'microphone', target: 'draft' }),
        // The send button is always on the right of row two — it is the only
        // way to send on a phone now that Enter makes a line, so a button that
        // came and went with the draft would be a button a person had to
        // discover. It is disabled until there is something to send, which says
        // the same thing without moving.
        take
          ? null
          : host.registry.draw('composer.tool', { host, slot: 'send', target: 'draft', context: ctx, defaultView: () => h(
              'button',
              {
                class: 'roundsend',
                type: 'submit',
                disabled: !has,
                title: going ? 'Waiting for the files to finish going up' : 'Send',
              },
              icon.send(16),
            ) }),
      ),
      // Last in the box and drawn over its top edge, so the rows read in the
      // order they are seen: the notice, the field, the actions.

    ),
  )
}



// The draft, on a screen of its own: the same quiet glyph it always was, now in
// the field's top-right corner.
//
// **It shows while there is something to expand or someone writing** — the
// field has the caret, or holds text — and is faded out and untappable on an
// empty box nobody is in, so a composer at rest is the field and row two and
// nothing else. Faded rather than removed, so the corner never reflows the
// text under it and the node a finger reaches for is the same node.
//
// A press does not take the caret out of the field: the `mousedown` that would
// blur it is cancelled. Otherwise the blur would redraw the glyph away between
// the press and the click, and an empty field someone had just tapped into
// could never be expanded.
function expandGlyph() {
  const shown = host.state.draftFocused || draft.value.trim() !== ''
  return h(
    'button',
    {
      class: `expandbtn content-mark${shown ? ' on' : ''}`,
      key: 'expand',
      type: 'button',
      title: 'Write this in a bigger window',
      'aria-label': 'Write this in a bigger window',
      'aria-hidden': shown ? null : 'true',
      tabindex: shown ? null : '-1',
      onmousedown: (e) => e.preventDefault?.(),
      onclick: openEditor,
    },
    icon.expand(14),
  )
}



// The draft, full screen. One layout at both widths: a header with the way out,
// the field, and the send — there is nothing here whose shape a desktop would
// want differently, because the whole of it is "the text, with room".
//
// It is drawn beside the screen rather than inside it so that nothing about a
// chat's own layout — the dock, the transcript's padding, the loading card —
// has to know it exists.
function editorNodes() {
  if (!host.state.editor) return []
  const name = host.threads.defaultName()
  const has = draft.value.trim() !== ''
  // Dictating here is the composer's take in this screen's field: the same
  // surface, standing in the big field, and the foot quiet under it.
  const take = host.fieldActivity('editor')
  return [
    h(
      'div',
      { class: 'editor', key: 'editor' },
      host.panelId === 'main' && host.state.sheet?.kind !== 'settings' ? host.runNotice() : null,
      h(
        'div',
        { class: 'editor-head' },
        h('button', { class: 'iconbtn ghost', type: 'button', title: 'Back to the chat', onclick: closeEditor }, icon.chevronLeft(20)),
        h('div', { class: 'display editor-title', text: name ? `Message ${name}` : 'Your message' }),
        h('button', { class: 'editor-done', type: 'button', text: 'Done', onclick: closeEditor }),
      ),
      !take ? host.registry.draw('composer.tool', { host, slot: 'advice', target: 'editor' }) : null,
      h('div', { class: `editor-body${take ? ' taking' : ''}` }, kept(editorDraft), take ? host.registry.draw('composer.tool', { host, slot: 'take', target: host.state.editor ? 'editor' : 'draft' }) : null),
      h(
        'div',
        { class: `editor-foot${take ? ' quiet' : ''}` },
        h('span', { class: 'editor-hint', text: host.sendKeys.hint }),
        host.registry.draw('composer.tool', { host, slot: 'microphone', target: 'editor' }),
        take
          ? null
          : host.registry.draw('composer.tool', { host, slot: 'send', target: 'editor', defaultView: () => h(
              'button',
              { class: 'roundsend', type: 'button', disabled: !has, title: 'Send', onclick: sendFromEditor },
              icon.send(16),
            ) }),
      ),
    ),
  ]
}



// ------------------------------------------------- the composer's two menus
//
// **Two menus, two chromes, one builder.** The design draws a menu as a popover
// anchored above the button it came from on the desktop and as the bottom sheet
// on the phone, so the content is one function and the chrome is two, rather
// than menus that have to be kept saying the same thing.
//
// The two are the **+**, which is attachments and nothing else — a file, an
// image, and the vault that is not here yet — and the **model button**, which
// is THIS CHAT: the model and the effort this chat runs at. They were one menu
// until 2026-09-22, the + holding both under two kickers. The owner, that day:
// "instead have a model button next to the plus that opens the model and effort
// options." A setting a person changes mid-conversation is not something to go
// looking for under a plus sign, so it has a button of its own that says what
// the chat is set to without being opened.
//
// A page belongs to one of those two roots — `root` is the +, and `chat`,
// `model` and `effort` are the model button's — which is how a button knows
// whether the menu that is open is its own.
//
// "Pick from Files" opens the picker over the chat, a draft included; what is
// picked in a draft goes with its first message.
//
// Model and Effort drill into a sub-*page of this same menu* rather than into a
// second popover: a back button, a kicker, and one row per value the providers
// route offers, with a check on the one the record is set to.

// Which page is open, wherever it is drawn, and `null` when no menu is.
function menuPage() {
  const page = host.state.wide ? host.state.pop?.page : host.state.sheet?.kind === 'menu' ? host.state.sheet.page : null
  return page ?? null
}



// Which of the two menus a page belongs to.
function menuRootOf(page) {
  return page === 'root' ? 'root' : 'chat'
}



// Whether the menu that is open is this button's own — which is what a button
// reads to draw itself as on, and what a second click on it reads to close.
function menuOpen(root) {
  const page = menuPage()
  return page !== null && menuRootOf(page) === root
}



// The page to draw, which is the + menu's root while nothing is open.
function openPage() {
  return menuPage() ?? 'root'
}




// A button opens its menu where the layout draws it, and closes it when its own
// menu is already open — the design's "popovers close on selection or when + is
// clicked again". Clicking the other button swaps the menu rather than closing
// it, because a person who wanted the other one asked for it.
function openMenu(page) {
  if (!host.state.wide) return host.openSheet({ kind: 'menu', page })
  const mine = menuOpen(menuRootOf(page))
  host.state.sheet = null
  host.state.pop = mine ? null : { page }
  host.schedule()
}

function togglePlus() {
  openMenu('root')
}




// Both chromes, because a choice made in either is a choice made.
function closeMenus() {
  host.state.pop = null
  host.state.sheet = null
  host.schedule()
}




// What the menu holds on the page it is on. A row says what to draw and what to
// do, and nothing about where it is drawn — or which chat it is about, which is
// the target's.
function composerMenu() {
  const cap = host.state.maxAttachmentBytes
  const videoCap = host.state.maxVideoBytes
  return {
    title: 'Add to this message',
    groups: [
      {
        kicker: 'Attach',
        rows: [
          {
            key: 'file',
            title: 'Upload a file',
            sub: 'Documents, images, code and video',
            glyph: icon.file(18),
            run: () => host.chooseFiles(fileChooser),
          },
          {
            key: 'image',
            title: 'Photo or video',
            sub: 'Photos, MP4, MOV and WebM',
            glyph: icon.image(18),
            run: () => host.chooseFiles(imageChooser),
          },
          {
            key: 'vault',
            title: 'Pick from Files',
            sub: 'A file from Vault or AI Spaces',
            glyph: icon.folder(18),
            run: () => host.pickFromFiles(),
          },
        ],
      },
    ],
    // The host's number, said before anything is chosen. It is the broker's cap
    // and this app keeps no copy of it — see `/api/limits`.
    note: videoCap ? `Videos up to ${bytes(videoCap)} each. Other files up to ${bytes(cap)} each.`
      : cap ? `Up to ${bytes(cap)} per file.` : null,
  }
}

function menuRow(row) {
  return h(
    'button',
    {
      key: row.key,
      class: `setrow${row.check || row.on ? ' on' : ''}`,
      type: 'button',
      role: row.role,
      disabled: row.disabled,
      onclick: row.run ?? (() => {}),
    },
    row.glyph ? h('div', { class: 'icon-tile' }, row.glyph) : null,
    h(
      'div',
      { class: 'setrow-who' },
      h('div', { class: 'setrow-title', text: row.title }),
      row.sub ? h('div', { class: 'setrow-sub', text: row.sub }) : null,
    ),
    row.value ? h('div', { class: 'setrow-value', text: row.value }) : null,
    row.into ? icon.chevronRight(10) : null,
    row.check ? icon.check(14) : null,
  )
}



// The phone's chrome: the bottom sheet, with the sub-pages under a back button.
function menuSheet(sheet) {
  const menu = composerMenu(sheet.page ?? 'root')
  return h(
    'div',
    { class: 'sheet' },
    host.sheetTop(),
    menu.back
      ? h('button', { class: 'backbtn', onclick: menu.back.run }, icon.chevronLeft(20), menu.back.label)
      : h('div', { class: 'sheet-head' }, h('div', { class: 'display sheet-title', text: menu.title })),
    ...menuGroups(menu),
    menu.note ? h('div', { class: 'note', text: menu.note }) : null,
  )
}



// A menu's rows, grouped and kickered — the part of a sheet's body that is the
// same wherever the sheet was opened from. The New-chat card draws these too.
function menuGroups(menu) {
  return menu.groups
    .filter((group) => group.rows.length > 0)
    .map((group) =>
      h(
        'div',
        { key: `group:${group.kicker}`, class: 'menugroup' },
        h('div', { class: 'kicker menukicker', text: group.kicker }),
        h('div', { class: 'rows' }, ...group.rows.map(menuRow)),
      ),
    )
}



// The desktop's content stays in the composer tree. The shared shell anchors
// its top-layer card to the button, with a divider between groups — a menu of
// one group has none, which is both of them today.
function menuPopover() {
  const menu = composerMenu(openPage())
  const inside = []
  for (const group of menu.groups) {
    if (group.rows.length === 0) continue
    if (inside.length > 0) inside.push(h('div', { class: 'popdiv', key: `div:${group.kicker}` }))
    inside.push(h('div', { class: 'kicker menukicker', key: `k:${group.kicker}`, text: group.kicker }))
    for (const row of group.rows) inside.push(menuRow(row))
  }
  return menuSurface(h(
    'div',
    { class: 'popover', key: 'popover' },
    menu.back
      ? h('button', { class: 'backbtn', type: 'button', onclick: menu.back.run }, icon.chevronLeft(14), menu.back.label)
      : null,
    ...inside,
    menu.note ? h('div', { class: 'note popnote', text: menu.note }) : null,
  ), { title: menu.title, close: closeMenus, back: menu.back?.run })
}
return Object.defineProperties({}, {
    DRAFT_MAX_PX: { enumerable: true, get: () => DRAFT_MAX_PX },
    MAC: { enumerable: true, get: () => MAC },
    draft: { enumerable: true, get: () => draft },
    editorDraft: { enumerable: true, get: () => editorDraft },
    openEditor: { enumerable: true, get: () => openEditor },
    closeEditor: { enumerable: true, get: () => closeEditor },
    sendFromEditor: { enumerable: true, get: () => sendFromEditor },
    fileChooser: { enumerable: true, get: () => fileChooser },
    imageChooser: { enumerable: true, get: () => imageChooser },
    growDraft: { enumerable: true, get: () => growDraft },
    submitDraft: { enumerable: true, get: () => submitDraft },
    attachStrip: { enumerable: true, get: () => attachStrip },
    chipSub: { enumerable: true, get: () => chipSub },
    loginNotice: { enumerable: true, get: () => loginNotice },
    activeThread: { enumerable: true, get: () => activeThread },
    composer: { enumerable: true, get: () => composer },
    expandGlyph: { enumerable: true, get: () => expandGlyph },
    editorNodes: { enumerable: true, get: () => editorNodes },
    menuPage: { enumerable: true, get: () => menuPage },
    menuRootOf: { enumerable: true, get: () => menuRootOf },
    menuOpen: { enumerable: true, get: () => menuOpen },
    openPage: { enumerable: true, get: () => openPage },
    openMenu: { enumerable: true, get: () => openMenu },
    togglePlus: { enumerable: true, get: () => togglePlus },
    closeMenus: { enumerable: true, get: () => closeMenus },
    composerMenu: { enumerable: true, get: () => composerMenu },
    menuRow: { enumerable: true, get: () => menuRow },
    menuSheet: { enumerable: true, get: () => menuSheet },
    menuGroups: { enumerable: true, get: () => menuGroups },
    menuPopover: { enumerable: true, get: () => menuPopover }
})
}
