/** API 0: stable conveniences, not a sandbox. See docs/stable-parts.md. */
export type PlaceName = 'agent.profile' | 'rail.section' | 'rail.action' | 'panel.item' | 'app.overlay' | 'settings.section' | 'thread.list.decoration' | 'thread.list.action' | 'thread.header.action' | 'message.renderer' | 'message.footer.action' | 'composer.tool' | 'files.viewer' | 'files.action' | 'files.root' | 'files.share' | 'activity.kind' | 'apps.source' | 'apps.icon' | 'thread.list.line' | 'command'
export type ComponentId = 'core.thread-list' | 'core.message' | 'core.composer' | 'core.files.viewer' | 'core.files.editor' | 'core.rail' | 'core.activity' | 'core.apps' | 'core.settings'
export type View = Node | string | number | boolean | null | undefined | View[]
export interface Handle { remove(): void }
export interface Context {
  /** Place-specific fields are documented in docs/plugins/places.md. */
  [key: string]: any
  defaultView?: (...args: any[]) => any
}
export interface Registration {
  id: string
  order?: number
  slot?: string
  view?: string
  dirty?(context: Context): boolean
  component?: ComponentId | (string & {})
  when?: (context: Context) => boolean
  list?: (context: Context) => any
  load?: (context: Context) => any
  render?: (context: Context) => any
  title?: string
  label?: string
  icon?: string | ((size: number) => View)
  active?: () => boolean
  badge?: () => number | string | null
  onclick?: (event: any) => any
  /** settings.section: 'plugin' renders only inside the owner’s enabled detail. */
  group?: string
  /** A plugin settings section can operate with all optional keys absent. */
  worksWithoutKeys?: boolean
  /** Legacy opt-in: also render in this plugin’s enabled detail. */
  plugin?: string
  children?: string
  /** group:'integrations': row facts; null omits the row. */
  summary?: (context: Context) => { connected: boolean; summary: string } | null
  /** group:'integrations': non-null adds the shared Settings warning dot. */
  notice?: (context: Context) => { text: string } | null
  hidden?: boolean
  screens?: string[]
  frame?: 'full'
  open?: () => void
  selected?: () => boolean
  run?: (context: any) => any
}
export interface Place { add(registration: Registration): Handle }
export type Places = Record<PlaceName, Place> & {
  agent: { profile: Place }
  rail: { section: Place; action: Place }
  panel: { item: Place }
  app: { overlay: Place }
  settings: { section: Place }
  thread: { list: { decoration: Place; action: Place; line: Place }; header: { action: Place } }
  message: { renderer: Place; footer: { action: Place } }
  composer: { tool: Place }
  files: { viewer: Place; action: Place; root: Place; share: Place }
  activity: { kind: Place }
  apps: { source: Place }
}
export interface Settings {
  get<T = any>(key: string, fallback?: T): T
  set<T>(key: string, value: T): T
  /** Explicit device key, for retaining an existing preference on migration. */
  device<T = any>(key: string, normalize?: (value: any) => T): { value: T }
}
export interface Data {
  request(method: string, path: string, body?: any): Promise<any>
  agentSettings: { get(plugin: string, agent?: string): Promise<any>; patch(plugin: string, agent: string, values: Record<string, any>): Promise<any> }
  threads: { list(): Promise<any>; get(id: string): Promise<any>; create(body: any): Promise<any>; patch(id: string, body: any): Promise<any> }
  messages: { parts(message: any): any[]; list(id: string, query?: string): Promise<any>; send(id: string, body: any): Promise<any> }
  files: { read(root: string, path: string): Promise<any>; write(body: any): Promise<any>; list(root: string, path?: string): Promise<any> }
}
export interface FieldActivity { active: boolean; phase?: string; level?: number }
export interface Page {
  state: Record<string, any>
  root: HTMLElement
  schedule(): void
  fieldActivity(target: string): FieldActivity | null
  openSheet(sheet: Record<string, any>): void
  closeSheet(): void
  provide(name: string, service: object): Handle
  /** Open compatibility facade. Unlisted members may change within API 0. */
  [name: string]: any
}
export interface UI {
  h(tag: string, props?: Record<string, any> | null, ...children: any[]): HTMLElement
  icon: Record<string, (size?: number) => SVGElement>
  morph(parent: Node, children: any[]): void
  kept(node: Node): Node
  markdown(text: string, options?: any): any[]
  token(name: string): string | undefined
  menuSurface(body: HTMLElement, options?: Record<string, any>): HTMLElement
  segmentedControl(options: Record<string, any>): HTMLElement
  audioSessionType(type: string): void
  createSecretSettings(options: { id: string; request(method: string, path: string, body?: unknown): Promise<any>; redraw?(): void }): { load(): Promise<void>; view(): HTMLElement }
  host: Page
  /** Local keys stay local; other state keys and page operations delegate. */
  extension(options?: { state?: object; store?: object; helpers?: object; host?: Page }): { host: Page; attach<T extends object>(controller: T): T }
  styles: { add(relativeFile: string): Handle }
  /** Other exported native UI helpers are available without a stability promise. */
  [name: string]: any
}
export interface Loam {
  api: 0
  places: Places
  data: Data
  events: { on(name: string, callback: (detail: any) => any): Handle; emit(name: string, detail?: any): void }
  ui: UI
  settings: Settings
  commands: { register(command: Registration & { run(context: any): any }): Handle; run(id: string, context?: any): any }
  own(handle: Handle): Handle
  dispose(): void
}
export type Activate = (loam: Loam) => void | (() => void | Promise<void>) | { dispose(): void | Promise<void> } | Promise<void | (() => void | Promise<void>) | { dispose(): void | Promise<void> }>

/** Secret metadata only. Raw values are never returned through the page API. */
export interface PluginSecretStatus {
  name: string; label: string; set: boolean; source: 'plugin' | 'box' | null
  checked: { at?: string; ok: boolean; why: string; source?: string } | null
  refused: { at?: string; ok: boolean; why: string; source?: string } | null
}
