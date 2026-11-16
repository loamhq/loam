import { createRegistry } from './plugin-registry.js'
export function createCoreRegistry(options) {
  const registry = createRegistry(options), core = registry.scope({ id: '@core' })
  const components = {
    'core.thread-list': 'thread.list.decoration', 'core.message': 'message.renderer',
    'core.composer': 'composer.tool', 'core.files.viewer': 'files.viewer',
    'core.files.editor': 'files.viewer', 'core.rail': 'rail.section',
    'core.activity': 'activity.kind', 'core.apps': 'apps.source', 'core.settings': 'settings.section',
  }
  for (const [id, place] of Object.entries(components)) core.places[place].add({ id, component: id, render: context => context.defaultView() })
  for (const slot of ['reply', 'branch', 'copy', 'row']) core.places.message.footer.action.add({ id: `core.footer.${slot}`, slot, when: context => context.slot === slot, render: context => context.defaultView() })
  for (const slot of ['attach', 'model', 'mention', 'send', 'expand']) core.places.composer.tool.add({ id: `core.composer.${slot}`, slot, when: context => context.slot === slot, render: context => context.defaultView() })
  core.places.files.action.add({ id: 'core.files.toolbar', slot: 'toolbar', when: context => context.slot === 'toolbar', render: context => context.defaultView() })
  for (const kind of ['change', 'refused', 'fire', 'run', 'job']) core.places.activity.kind.add({ id: kind, when: context => context.item?.kind === kind, render: context => context.defaultView() })
  for (const [id, title] of [['yours', 'Yours'], ['installed', 'Installed'], ['loam', 'Loam']]) core.places.apps.source.add({ id, title, render: context => context.defaultView(id, title) })
  return registry
}
