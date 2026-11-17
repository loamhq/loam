# Page plugin API 0

A folder is plain files: `PLUGIN.md`, optional `theme.css`, `page/main.js`
exporting `activate(loam)`, and `assets/`. No build step. The page uses host
rows, never parses a manifest. Shipped themes supply tokens; extensions register code;
an override can replace a core component. The public API is a compatibility
promise, not a restriction: see [the stable parts](../stable-parts.md). Code runs as the page,
not in a permission sandbox. Declarations explain behavior; they do not grant
or restrict access. Same-origin relay/auth and the host's file guard remain.

`activate` may return a cleanup function or `{dispose()}`. The loader removes
all registrations, event listeners and commands on disable, replacement or
failure and calls that cleanup. A failed import/activation/render is reported
on that plugin's Settings row and cannot stop another plugin. Async work must
check its own disposed/generation flag. Rendering is synchronous DOM (or arrays
of DOM/text/null), never HTML strings to be interpreted.

Core calls the same `loam.places`/`commands`/`events` interfaces. `add` returns
an idempotent `{remove()}`. Registration ids are unique per plugin/place;
`order` sorts ascending, then registration order. `when(context)` optionally
filters. Dotted and nested access are equivalent, e.g.
`loam.places['message.footer.action']` and
`loam.places.message.footer.action`. A place's optional `slot` is matched
before `when`. A render gets the same context regardless of its author.

## Places

| Place | Register; context; return; example |
| --- | --- |
| `rail.section` | `{id,title,icon,list?,load?,render?,open?,selected?,hidden?,screens?,frame?}`. On desktop, selecting changes only the navigator and calls optional `load({host})`; `list({host,wide:true,collapse})` supplies list-column contents. Existing `render` is used as its fallback. On phone, `render({wide:false})`, `open`, `selected`, `screens` and `frame:'full'` keep their existing screen behavior. A desktop section never owns a panel or the workspace frame. `hidden` omits the rail/nav entry. |
| `panel.item` | `{id,render}`. `id` is an item kind, preferably namespaced. `render({host,item,panel,wide:true})` returns panel contents in either slot. `host` is that slot's mount, `panel` is `main` or `side`, and `item` is `{kind,id}` for a plugin kind. Keep any view state scoped by panel and item. Open with `loam.commands.run('panel.open',{item:{kind:'notes.note',id:'one'},panel:'main'})`. Identities get addresses and obey the shared uniqueness/move/save rules; no section change occurs. Disposing the registration leaves an unavailable-item explanation until it returns. |
| `app.overlay` | `{id,render,when?}`; `{screen,wide}`; overlay DOM appended after the normal dialogs. Manage positioning/focus with shared components. Example: `add({id:'pet',render:()=>loam.ui.h('aside',{class:'my-pet'},'Hello')})`. |
| `agent.profile` | `{id,render,when?}`; `{agent,plugin,busy,error,save,defaultView}`. For an enabled plugin with declared fields, `defaultView(fields?)` supplies host-backed choice/text rows (all fields by default). `save(values)` uses the profile's ordinary guarded PATCH and returns whether it succeeded; `busy` disables controls during writes and `error` carries the host's refusal. A plugin can decorate these with the same public place as any extension. With no registration the declared fields render automatically. |
| `settings.section` | `{id,title,icon?,render,group?,plugin?}`; `{back,sheet}`; an array of Settings DOM. No `group` means a navigation section; `group:'plugin'` renders only inside the owning plugin’s detail while on, with `{plugin:true,back:false}`; `group:'integrations'` contributes a service row and detail (below). The optional legacy `plugin:'<id>'` also renders inside that plugin’s details while on. Example: `add({id:'preferences',title:'Reading preferences',group:'plugin',render:()=>[loam.ui.h('p',{},'Choose how to read')]})`. |
| `thread.list.decoration` | `{id,render}`; `{thread,open}`; noninteractive decoration inside a thread row. `component:'core.thread-list'` instead replaces the complete list with `{args,state,defaultView}`. Example: `add({id:'tag',render:({thread})=>loam.ui.h('span',{},thread.pinned?'Pinned':'')})`. |
| `thread.header.action` | `{id,render}`; `{thread,state,open,patch}`; actions immediately below the shared header, above the conversation. Example: `add({id:'rename',render:({patch})=>loam.ui.h('button',{onclick:()=>patch({title:'Notes'})},'Name Notes')})`. |
| `message.renderer` | `{id,when,render}`; `{message,thread,state}`; a specialized complete message DOM. `component:'core.message'` replaces all messages and receives `{message,thread,args,state,defaultView}`. Whole-component overrides take precedence over specialized renderers. Example: `add({id:'notice',when:c=>c.message.kind==='notice',render:c=>loam.ui.h('aside',{},c.message.text)})`. |
| `message.footer.action` | `{id,slot?,render}`. Default `slot:'leading'` adds DOM beside Reply/Branch/Copy; context has `{message,thread}`. Core slots `reply`, `branch`, `copy` carry `defaultView()`. `slot:'row'` is a transform, ascending order: `{message,thread,agent,agentName,mine,leading,trailing,view,defaultView}`; return rows, incorporating or replacing `view`. Example: `add({id:'save',render:({message})=>loam.ui.h('button',{onclick:()=>save(message)},'Save')})`. |
| `composer.tool` | `{id,slot?,render}`. Default `extra` adds a tool before the microphone; `{target:'draft'|'editor'|'file',context?}`. Core `attach`, `model`, `mention`, `send`, `expand` have `defaultView()`. Field contributors also use `microphone`, `take`, `notice` and `state`. The state slot returns `{active,phase?,level?}` rather than DOM. Return DOM, or null when irrelevant. `component:'core.composer'` replaces the form/editor with `{args,variant,defaultView}` (`variant` is `form` or `editor`). Example: `add({id:'date',render:()=>loam.ui.h('button',{type:'button',onclick:()=>insertDate()},'Date')})`. |
| `files.viewer` | `{id,slot?,when,render}`. Default `viewer` specializes a file: `{files,file,options,defaultView}`; last matching specialized renderer wins. `slot:'action'` contributes a reader/action adapter. `component:'core.files.viewer'` replaces the whole viewer; `core.files.editor` replaces its field (`args` includes file/read-only options). Example: `add({id:'csv',when:c=>c.file?.path.endsWith('.csv'),render:c=>loam.ui.h('pre',{},c.file.text)})`. |
| `files.action` | `{id,slot?,render}`. Default `action`: `{files,file}` returns extra header controls. `toolbar` transforms the complete header with `{files,file,view,defaultView}`. Example: `add({id:'export',render:({file})=>loam.ui.h('button',{onclick:()=>exportFile(file)},'Export')})`. |
| `activity.kind` | `{id,when,render}`; `{item,defaultView}` returns an Activity row. Existing change/refused/fire/run/job kinds register here. `component:'core.activity'` replaces Activity with `{args,state,variant,defaultView}` (`view`, `dialogs`, `profile-dialogs` or `notices`). Example: `add({id:'reminder',when:c=>c.item.kind==='reminder',render:c=>loam.ui.h('li',{},c.item.title)})`. |
| `apps.source` | `{id,title,render}`; `{apps,row,open,defaultView(source,title)}`; an app group DOM. Yours/Installed use this contract. `component:'core.apps'` replaces the whole Apps view with `{args,state,defaultView}`. Example: `add({id:'team',title:'Team',render:c=>c.defaultView('team','Team')})`. |
| `command` | `{id,title?,run}` through `loam.commands.register()` (or `.places.command.add()`). `run(context)` returns a value or promise; callers await async commands. Example: `register({id:'my.open',run:()=>loam.commands.run('section.open',{id:'notes'})})`. |

`thread.kind` is reserved and unavailable in API 0. Registering an unknown
place is an error. Render errors release the responsible plugin's handles;
core is restored in the same component dispatch. Core renderer errors remain errors, so product
regressions cannot silently disappear.

## Component overrides

List the id in `overrides` to explain intent, and add a registration in its owning place with
`component` set to that id. The normal core registration steps aside while
the plugin is active; removing it restores core. `defaultView()` is the same
core implementation supplied to every matching registration, allowing a
replacement to decorate it deliberately. It is never drawn as a fallback
outside the registry.

| Id | Owning place |
| --- | --- |
| `core.thread-list` | `thread.list.decoration` |
| `core.message` | `message.renderer` |
| `core.composer` | `composer.tool` |
| `core.files.viewer`, `core.files.editor` | `files.viewer` |
| `core.rail` | `rail.section` |
| `core.activity` | `activity.kind` |
| `core.apps` | `apps.source` |
| `core.settings` | `settings.section` (complete phone sheet or desktop dialog, `variant: settingsSheet/settingsDialog`) |

For example:

```js
export function activate(loam) {
  loam.places.message.renderer.add({
    id: 'example.message', component: 'core.message',
    render: ({ message }) => loam.ui.h('article', {}, message.text),
  })
}
```

## Shared services and lifecycle

`loam.data` exposes authenticated relay operations for threads, messages,
boards and files, including `request(method,path,body)` for the documented
relay. It holds no broker credential. `loam.events.emit(name,detail)` broadcasts a custom event.
`loam.events.on(name,callback)` returns
a removable handle. Events include `thread.event`, `message.arrived`,
`turn.started`, `turn.ended`, `file.changed`, `section.changed` and `rendered`; thread
notifications include the thread and original event. `file.changed` follows a
successful save. A plugin's editor can **hold the open file** (since
2026-11-16, for doc-comments): `files.own(file, {dirty, save, edit, field})`
(each optional; `save` returns a promise) returns a removable handle; while
held, Files' `edit(text)`, `save()` and `dirty()` go to the plugin, the way
out of an unsaved edit waits on its save, and `host.fileField` is the
plugin's `field` (a textarea-like object: `value`, `selectionStart`,
`selectionEnd`, `setSelectionRange`, `focus`, `isConnected`), so dictation
lands in the plugin's editor. Opening another file releases it. `file.renamed` follows an accepted rename in the originating
page, with the host's new row and `from` address; see [Files renaming](../files-renaming.md).
External file change streaming is not available on the host.

`loam.ui` supplies the native UI/markdown components, icons and token lookup.
Its public `host` facade exposes the mounted page services described in
[modules.md](modules.md), including draft, Files, sheet and navigation adapters. Every plugin receives that same facade; there is no
first-party access branch. It is broad and mutable in API 0, reflecting the
existing singleton draft integration. Consumers should prefer data,
events, places and commands. The explicit stable subset is typed; other members stay open without a
compatibility promise.

`loam.settings.get(key,fallback)` and `.set(key,value)` are scoped to the
plugin id, local to this browser with an in-memory fallback. Host settings
files are not built yet. Titles, pins and read marks remain host data and do
not belong in these settings. `loam.commands.run(id,context)` resolves the last command in layer/order
precedence (Yours after defaults). Core registers `core.chats`, `core.files`,
`core.apps`, `core.settings` and `section.open`.

The public host facade also supplies `provide(moduleName, service)` for
replacing an existing core module. Its handle is automatically owned. Audio
plugins instead use `ui.extension({state,store,helpers})` for local controllers
and public places/events for integration; core never calls those controllers.
`ui.styles.add(relativeFile)` loads ordinary package CSS with automatic
cleanup and Yours precedence. `settings.device(key,normalize)` retains an
existing device preference address. See [stable parts](../stable-parts.md)
for field state, sheet and lifecycle contracts and the exact stable facade.

Startup import and activation have separate 15-second bounds; cleanup is also
bounded. Register promptly and own asynchronous resources with cancellation.
Closed scopes reject late registrations/listeners; a late `own(handle)` removes
the resource immediately. Removing providers out of order never resurrects a
disabled predecessor. A render failure removes the entire responsible scope,
skips its remaining callbacks and restores the core in that same dispatch.
`rendered` fires after the root redraw; core and plugins use the same listener
API for DOM synchronization. Commands may be asynchronous; renderers may not.

The data methods return the relay's JSON promise unchanged and encode each ID:
`threads.list/get/create/patch`, `messages.parts(message)/list(threadId,query?)/send`,
`boards.get(threadId)/put(threadId,body)`, and
`files.read(root,path)/list(root,path?)/write(body)`. Arguments are the same
request bodies used by core. For example,
`await loam.data.messages.send(threadId,{text:'Hello'})` posts to that thread.
Use `request(method,path,body)` for other documented JSON routes; binary or
multipart operations use the browser APIs/shared host adapters. `ui.token(name)`
reads the current CSS token rather than caching a theme value.

## Per-agent settings

A manifest may declare `agent_settings`, a list of up to 16 fields. Each has
an `id`, `label`, optional `choices: [{id, label}]` and `default`. Choices
render as native pickers; other fields render as text inputs (200 characters).
The host validates and stores values. Declare `agent.profile` in `places`.

Use `loam.data.agentSettings.get(pluginId, agentId)` (omit the agent to list
stored values) and `.patch(pluginId, agentId, {field: value})`. These call the
ordinary `/api/plugins/<plugin>/agents[/<agent>]` relay. Null removes a stored
key. This is distinct from the device-local `loam.settings` adapter. Read aloud
uses this store for agent voices; the Files reading voice remains device-local.

## Plugin-owned integrations

Use `settings.section` with `group: 'integrations'`. It contributes inside
Integrations, without another Settings navigation item. The registration has
`id`, `title`, `icon(size)` (or a shared icon name), `summary()` returning
`{connected: boolean, summary: string}` and `render({back, sheet})` returning
Settings DOM. A null summary omits the row. Core groups it under Connected or
Available and opens `goSettings('integration', id)` on selection. `sheet.account`
retains the optional account key; plugins own their account lists and controls.
`notice()` returns `{text}` when a person is needed, or null; core uses it for
the same Integrations warning dot as its own service. The plugin owns any
notice wording inside its detail view. These callbacks use the registry's
ordinary failure isolation and disappear with their scope.

For example:

```js
loam.places.settings.section.add({
  id: 'example', title: 'Example', group: 'integrations', icon: 'link',
  summary: () => ({ connected: false, summary: 'Not connected' }),
  notice: () => null,
  render: () => [loam.ui.host.settingsBack('Integrations', 'integrations'),
    loam.ui.host.settingsHead('Example'), loam.ui.h('p', {}, 'Connect here')],
})
```

Plugin servers are called with ordinary same-origin `fetch('/x/<id>/…')`;
`data.request` addresses the core `/api` relay instead. Core adds no integration
relay or discovery request for a plugin. The effective enabled host catalog row
and its `has.page_main` determine whether its page code runs.

`sheet.changing` fires before shared `openSheet`, `closeSheet` or `goSettings`
replaces a sheet, with `{previous, sheet}`; `sheet` is null on close. Use it to
stop polls and clear transient form values. `plugin.settings.opened` sends `{id}` when an enabled plugin detail opens or its
switch is turned back on in that detail. Load device choices or auxiliary
resources there. Settings sections are removed with their owner’s scope;
preferences are retained. The host renders manifest-declared secret fields once,
below plugin preferences, only while on; the plugin must not duplicate them.
`settings.opened` retains `{page}`;
`opening: true` additionally identifies a shared `openSheet` Settings entry,
when a plugin may refresh its state/reset last-visit notices. Ordinary Settings
section navigation omits that flag. A custom `dismiss()` still owns its own
closing transition. Cleanup must also abort pending work and release timers;
a late network answer must not restore a disabled plugin's UI.

### Plugin key fields

A `contributes["settings.secret"]` declaration names a host-stored credential;
for example `{name: GEMINI_API_KEY, label: Gemini API key, check: builtin:gemini}`.
The Plugins detail displays its status and paste/check/forget controls. A plugin
with its own Settings section can render the same component:

```js
const keys = loam.ui.createSecretSettings({
  id: 'my-plugin', request: loam.data.request,
  redraw: () => loam.ui.host.schedule(),
})
// For a custom surface only: add keys.view() there. Plugin details render
// manifest-declared secret fields automatically; do not add a second copy.
```

`load()` refreshes metadata. Values typed into the form are cleared on submit;
the component stores only status and the host's check/refusal sentences. The
browser API never supplies the stored value. A successful key action emits
`plugin.secrets.changed` with `{id}` so an enabled plugin can refresh health.
The server reads credentials separately through the broker's server-only route.
