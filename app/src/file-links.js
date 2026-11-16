// Published file trees are deployment facts. A login redirect cannot tell us
// whether a file exists, so Copy link uses configured mappings without probing.
export function readFileWebUrls(raw = '') {
  if (!raw.trim()) return []
  const fail = () => { throw new Error('FILES_WEB_URLS must be a JSON array of {root, path, url} mappings with relative folder paths and HTTPS URLs.') }
  let rows
  try { rows = JSON.parse(raw) } catch { fail() }
  if (!Array.isArray(rows)) fail()
  return rows.map(row => {
    if (!row || !/^[a-z0-9_-]{1,40}$/i.test(row.root ?? '') || typeof row.path !== 'string' || typeof row.url !== 'string') fail()
    const path = row.path.replace(/\/+$/, '')
    if (row.path.startsWith('/') || /[\\\x00-\x1f\x7f]/.test(row.path) || path.split('/').some(s => ['.', '..'].includes(s) || (!s && path))) fail()
    let url
    try { url = new URL(row.url) } catch { fail() }
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) fail()
    return { root: row.root, path, url: url.href.replace(/\/+$/, '') }
  }).sort((a, b) => b.path.length - a.path.length)
}

export function fileLink(config, root, path) {
  const encode = value => value.split('/').map(encodeURIComponent).join('/')
  const mapping = config.filesWebUrls?.find(row => row.root === root && (!row.path || path === row.path || path.startsWith(`${row.path}/`)))
  if (mapping) {
    const relative = mapping.path ? path.slice(mapping.path.length).replace(/^\//, '') : path
    return { url: `${mapping.url}/${encode(relative)}`, kind: 'Web link' }
  }
  if (root === 'vault' && config.filesVaultUrls?.[0]) {
    return { url: `${config.filesVaultUrls[0]}/${encode(path)}`, kind: 'Vault web link' }
  }
  return { url: `/files/${encodeURIComponent(root)}/${encode(path)}`, kind: 'Loam link' }
}
