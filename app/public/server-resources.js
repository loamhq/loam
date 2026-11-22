import { h, icon } from './ui.js'
import { formatDate, formatTime } from './time.js'

const GiB = 2 ** 30
const valid = value => Number.isFinite(value) && value >= 0
const capacityNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 })
const loadNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })
const size = bytes => bytes > 0 && bytes < GiB / 10
  ? `${Math.max(1, Math.round(bytes / 2 ** 20))} MiB` : `${capacityNumber.format(bytes / GiB)} GiB`
const labels = { vault: 'Your files', workspaces: 'Agent files', records: 'Conversations and records',
  containers: 'Containers and images', other: 'System and other files' }

function meter(label, { value, capacity, fraction, note, warning, detail } = {}) {
  return h('div', { class: `resource-meter${warning ? ' resource-warning' : ''}` },
    h('h3', { text: label }),
    h('div', { class: 'resource-value' }, h('strong', { text: value ?? 'Unavailable' }),
      capacity ? h('span', { text: ` of ${capacity}` }) : null),
    h('div', { class: 'resource-track', 'aria-hidden': 'true' },
      valid(fraction) ? h('i', { style: { width: `${Math.min(1, fraction) * 100}%` } }) : null),
    note ? h('div', { class: 'resource-note' }, note) : null, detail)
}

function storageDetails(storage) {
  const sample = storage.breakdown
  const reserved = storage.total_bytes - storage.used_bytes - storage.free_bytes
  const rows = Object.entries(labels).sort(([a], [b]) => (sample?.groups?.[b] ?? -1) - (sample?.groups?.[a] ?? -1))
  return h('div', { class: 'resource-breakdown', id: 'storage-breakdown' },
    sample?.groups ? [
      h('ul', {}, rows.map(([key, name]) => {
        const bytes = sample.groups[key]
        return h('li', { key }, h('span', { text: name }), h('span', { class: 'breakdown-track', 'aria-hidden': 'true' },
          valid(bytes) ? h('i', { style: { width: `${Math.min(1, bytes / storage.total_bytes) * 100}%` } }) : null),
        h('span', { text: valid(bytes) ? size(bytes) : 'Unavailable' }))
      })),
      h('p', { text: `Measured ${formatDate(sample.sampled_at)} at ${formatTime(sample.sampled_at)} on the vault disk.` }),
    ] : h('p', { text: 'The storage breakdown is waiting for a disk sample.' }),
    reserved > 0 ? h('p', { text: `The filesystem keeps ${size(reserved)} in reserve.` }) : null)
}

function memoryMeter(box, expanded, toggle) {
  const { usedMb, totalMb, room } = box
  if (!valid(usedMb) || !(totalMb > 0)) return meter('Memory')
  const extras = [['engineer job', box.jobs], ['wake', box.wakes]].filter(([, unit]) => unit?.count > 0)
    .map(([name, unit]) => `${unit.count} ${name}${unit.count === 1 ? '' : 's'}${valid(unit.memoryMb) ? ` · ${unit.memoryMb} MiB` : ''}`)
  let note = `${size((totalMb - usedMb) * 2 ** 20)} available`
  if (room === 0) note = 'No room for a new session'
  else if (valid(room)) note = `Room for about ${room} more session${room === 1 ? '' : 's'}`
  return meter('Memory', {
    value: size(usedMb * 2 ** 20), capacity: `${size(totalMb * 2 ** 20)} used`, fraction: usedMb / totalMb,
    note: extras.length ? disclosure('memory-details', note, expanded, toggle, 'resource-info') : note,
    warning: room === 0,
    detail: expanded['memory-details'] && extras.length ? h('div', { class: 'resource-note', id: 'memory-details', text: extras.join(' · ') }) : null,
  })
}

function cpuMeter(cpu) {
  if (!valid(cpu?.load_1m) || !(cpu.vcpus > 0)) return meter('CPU load')
  const fraction = cpu.load_1m / cpu.vcpus
  const state = fraction >= 1 ? 'Heavy' : fraction >= .65 ? 'Moderate' : 'Light'
  return meter('CPU load', {
    value: loadNumber.format(cpu.load_1m), capacity: `${cpu.vcpus} vCPUs`,
    fraction, note: `${state} load · 1-minute average`, warning: fraction >= 1,
  })
}

function storageMeter(disk) {
  if (!disk) return meter('Storage')
  const { used_bytes: used, free_bytes: free, total_bytes: total } = disk
  // df's pressure threshold excludes the filesystem reserve from writable space.
  const low = free < 10 * GiB || used >= .9 * (used + free)
  return meter('Storage', {
    value: size(used), capacity: `${size(total)} used`, fraction: used / total,
    note: `${size(free)} free${low ? ' · Low space' : ''}`, warning: low,
    detail: low ? h('p', { class: 'storage-warning', text: 'Free up space so Loam can keep saving work.' }) : null,
  })
}

function disclosure(id, label, expanded, toggle, className) {
  return h('button', { type: 'button', class: className, 'aria-expanded': String(expanded[id]),
    'aria-controls': expanded[id] ? id : null, onclick: () => toggle(id) }, label, icon.chevronDown(12))
}

export function serverResources(box, expanded, toggle) {
  if (!box) return null
  const storage = box.storage
  const disk = valid(storage?.used_bytes) && valid(storage.free_bytes) && storage.total_bytes > 0 ? storage : null
  return h('section', { class: 'server-resources', 'aria-labelledby': 'server-resources-title' },
    h('h2', { id: 'server-resources-title', class: 'display', text: 'Server resources' }),
    h('div', { class: 'resource-panel' },
      h('div', { class: 'resource-meters' }, memoryMeter(box, expanded, toggle), cpuMeter(box.cpu), storageMeter(disk)),
      disk ? disclosure('storage-breakdown', 'Storage breakdown', expanded, toggle, 'resource-toggle') : null,
      disk && expanded['storage-breakdown'] ? storageDetails(disk) : null))
}
