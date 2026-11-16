// Public, owner-tracked module providers. Removing a lower provider must not
// resurrect it when a newer provider is removed later.
export function serviceProviders(modules, changed = () => {}) {
  const stacks = new Map(), bases = new Map()
  return (group, value) => {
    if (!(group in modules) || !value || typeof value !== 'object') throw new Error('Unknown page service or invalid provider.')
    if (!bases.has(group)) bases.set(group, modules[group])
    const stack = stacks.get(group) ?? [], entry = { value }
    stacks.set(group, stack); stack.push(entry); modules[group] = value; changed()
    let active = true
    return { remove() {
      if (!active) return
      active = false; const index = stack.indexOf(entry); if (index >= 0) stack.splice(index, 1)
      modules[group] = stack.at(-1)?.value ?? bases.get(group); changed()
    } }
  }
}
