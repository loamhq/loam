// The host consumes this declaration; generating it does not install a registry.
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { loadThemes } from '../src/themes.js'
export function appearanceRegistry(themes) {
  const settings = [
    { key: 'appearance.location', type: ['object', 'null'], default: null, additionalProperties: false,
      required: ['latitude', 'longitude', 'source', 'timeZone'], properties: {
        latitude: { type: 'number', minimum: -90, maximum: 90, multipleOf: 0.1 },
        longitude: { type: 'number', minimum: -180, maximum: 180, multipleOf: 0.1 },
        source: { type: 'string', enum: ['geolocation', 'timezone'] }, timeZone: { type: 'string', description: 'IANA time zone for dates and switch-time labels' },
      } },
  ]
  for (const kind of ['phone', 'desktop']) {
    const key = field => `appearance.${kind}.${field}`
    settings.push(
      { key: key('mode'), type: 'string', enum: ['light', 'dark', 'sun', 'system'], default: 'system' },
      { key: key('transition'), type: 'string', enum: ['fast', 'normal', 'long'], default: 'normal' },
      { key: key('size'), type: 'integer', minimum: 75, maximum: 150, multipleOf: 5, default: 100 },
      { key: key('theme'), type: 'string', enum: themes.map(t => t.id), default: 'latte' },
    )
    for (const theme of themes) for (const option of theme.options || []) settings.push({
      key: key(`options.${theme.id}.${option.id}`), type: 'string', enum: option.choices.map(c => c.id), default: option.default,
    })
  }
  return { status: 'broker integration required', route: '/v1/settings/appearance', settings }
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const file = new URL('../docs/appearance-settings.registry.json', import.meta.url)
  const registry = appearanceRegistry((await loadThemes()).themes)
  const source = JSON.stringify(registry, null, 2) + '\n'
  const sectionsFile = new URL('../docs/settings-sections.registry.json', import.meta.url)
  const sections = JSON.parse(await readFile(sectionsFile, 'utf8'))
  const fields = registry.settings.map(s => s.key.slice('appearance.'.length))
  if (process.argv.includes('--write')) {
    await writeFile(file, source)
    sections.sections.find(s => s.id === 'appearance').fields = fields
    await writeFile(sectionsFile, JSON.stringify(sections, null, 2) + '\n')
  }
  else if (process.argv.includes('--check')) {
    if (await readFile(file, 'utf8') !== source) throw new Error('Appearance registry drift: run node tools/appearance-registry.js --write')
    if (JSON.stringify(sections.sections.find(s => s.id === 'appearance').fields) !== JSON.stringify(fields)) throw new Error('Settings section registry drift: run node tools/appearance-registry.js --write')
    console.log('PASS: appearance registry matches every installed manifest option.')
  } else process.stdout.write(source)
}
