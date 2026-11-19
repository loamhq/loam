import path from 'node:path'
import zlib from 'node:zlib'
import { Transform } from 'node:stream'

const SECRET_NAME = /(?:^\.env(?:\.|$)|\.env$|\.pem$|\.key$|\.p12$|\.pfx$|\.keystore$|^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.|$)|^(?:auth|credentials|\.credentials)\.json$|^token$|\.token$|^\.netrc$|^\.git-credentials$|^secrets?\.(?:json|ya?ml|toml|env|txt)$)/i
const SPACES_PRIVATE = new Set(['logs', 'cache', 'scratch', '__pycache__', 'venv', 'node_modules'])
const SPACES_PRIVATE_SUFFIX = ['.pyc', '.log', '.swp', '.swo']
const STORE_EXT = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.heic', '.heif',
  '.mp4', '.mov', '.webm', '.m4v', '.avi',
  '.mp3', '.m4a', '.aac', '.ogg', '.flac', '.opus',
  '.zip', '.gz', '.bz2', '.xz', '.zst', '.7z', '.rar',
  '.pdf', '.woff', '.woff2', '.docx', '.xlsx', '.pptx', '.epub', '.odt',
])

export function shouldExclude(root, parts, kind = 'file') {
  if (kind === 'symlink') return true
  if (!Array.isArray(parts) || parts.length === 0) return true
  for (const part of parts) {
    if (!part || part === '.' || part === '..') return true
    if (part === '.git' || part === '.stversions') return true
    if (SECRET_NAME.test(part)) return true
    if (root === 'spaces' && SPACES_PRIVATE.has(part)) return true
  }
  const leaf = parts[parts.length - 1]
  if (root === 'spaces' && SPACES_PRIVATE_SUFFIX.some((suffix) => leaf.endsWith(suffix))) return true
  return false
}

export function archiveFilename(root, relPath = '') {
  const clean = String(relPath ?? '').replace(/^\/+|\/+$/g, '')
  const base = clean ? clean.split('/').pop() : (root === 'spaces' ? 'ai-spaces' : 'vault')
  const safe = (base || 'archive').replace(/["\\/\r\n\x00-\x1f]/g, '_').trim() || 'archive'
  return safe.endsWith('.zip') ? safe : `${safe}.zip`
}

export function shouldStore(name, size = null) {
  if (size === 0) return true
  return STORE_EXT.has(path.extname(String(name ?? '')).toLowerCase())
}

function dosDateTime(iso) {
  const parsed = iso ? new Date(iso) : new Date()
  const d = Number.isFinite(parsed.getTime()) && parsed.getUTCFullYear() >= 1980 ? parsed : new Date(Date.UTC(1980, 0, 1, 0, 0, 0))
  const time = (d.getUTCHours() << 11) | (d.getUTCMinutes() << 5) | (d.getUTCSeconds() >> 1)
  const date = ((d.getUTCFullYear() - 1980) << 9) | ((d.getUTCMonth() + 1) << 5) | d.getUTCDate()
  return { time, date }
}

function writeChunk(res, chunk) {
  if (res.destroyed || res.writableEnded) return Promise.reject(new Error('Archive response closed.'))
  if (res.write(chunk)) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const onDrain = () => { cleanup(); resolve() }
    const onClose = () => { cleanup(); reject(new Error('Archive response closed.')) }
    const cleanup = () => {
      res.off('drain', onDrain)
      res.off('close', onClose)
      res.off('error', onClose)
    }
    res.once('drain', onDrain)
    res.once('close', onClose)
    res.once('error', onClose)
  })
}

async function listFolder(broker, root, relPath, signal) {
  const entries = []
  let cursor = null
  for (let page = 0; page < 1000; page++) {
    const qs = new URLSearchParams({ root, path: relPath, limit: '1000' })
    if (cursor) qs.set('cursor', cursor)
    const out = await broker.json('GET', `/v1/files/list?${qs}`, undefined, { signal })
    if (out.status !== 200) {
      let message = `Could not list ${root}/${relPath || ''} (${out.status}).`
      try { const body = JSON.parse(out.body); if (body?.error) message = body.error } catch {}
      const err = new Error(message)
      err.status = out.status
      throw err
    }
    const body = JSON.parse(out.body)
    if (Array.isArray(body.entries)) entries.push(...body.entries)
    if (!body.next_cursor) break
    cursor = body.next_cursor
  }
  return entries
}

// Streams a ZIP / ZIP64 archive over the broker's paginated list and file
// download routes without buffering files or the archive in memory or on disk.
export async function streamZipArchive({ root, path: basePath = '', broker, req, res }) {
  const cleanBase = String(basePath ?? '').replace(/^\/+|\/+$/g, '')
  const basePrefix = cleanBase ? `${cleanBase}/` : ''
  const ac = new AbortController()
  const onAbort = () => { if (!res.writableFinished) ac.abort() }
  res.on('close', onAbort)

  let offset = 0n
  const cd = []
  const emit = async (buf) => {
    await writeChunk(res, buf)
    offset += BigInt(buf.length)
  }

  const relativeName = (entryPath) => {
    const p = String(entryPath ?? '').replace(/^\/+|\/+$/g, '')
    if (basePrefix && p.startsWith(basePrefix)) return p.slice(basePrefix.length)
    return p
  }

  const writeDirectory = async (relName, modified) => {
    const dirName = relName.endsWith('/') ? relName : `${relName}/`
    const nameBuf = Buffer.from(dirName, 'utf8')
    const { time, date } = dosDateTime(modified)
    const localOffset = offset
    const hdr = Buffer.allocUnsafe(30 + nameBuf.length)
    hdr.writeUInt32LE(0x04034b50, 0)
    hdr.writeUInt16LE(20, 4)
    hdr.writeUInt16LE(0x0800, 6)
    hdr.writeUInt16LE(0, 8)
    hdr.writeUInt16LE(time, 10)
    hdr.writeUInt16LE(date, 12)
    hdr.writeUInt32LE(0, 14)
    hdr.writeUInt32LE(0, 18)
    hdr.writeUInt32LE(0, 22)
    hdr.writeUInt16LE(nameBuf.length, 26)
    hdr.writeUInt16LE(0, 28)
    nameBuf.copy(hdr, 30)
    await emit(hdr)
    cd.push({ nameBuf, flags: 0x0800, method: 0, time, date, crc: 0, compSize: 0n, uncompSize: 0n, offset: localOffset, externalAttrs: 0x10 })
  }

  const writeFile = async (entry, relName) => {
    const qs = new URLSearchParams({ root, path: entry.path })
    const out = await broker.download(`/v1/files/download?${qs}`)
    if (!out.ok) {
      if (out.status === 404) return // removed or hidden between list and read
      let message = `Could not download ${entry.path} (${out.status}).`
      try { const parsed = JSON.parse(out.body); if (parsed?.error) message = parsed.error } catch {}
      const err = new Error(message)
      err.status = out.status
      throw err
    }

    const nameBuf = Buffer.from(relName, 'utf8')
    const { time, date } = dosDateTime(entry.modified)
    const method = shouldStore(entry.name, entry.size) ? 0 : 8
    const zip64Local = !Number.isFinite(entry.size) || entry.size >= 0xF0000000
    const flags = 0x0808
    const localOffset = offset

    const extraLen = zip64Local ? 20 : 0
    const hdr = Buffer.allocUnsafe(30 + nameBuf.length + extraLen)
    hdr.writeUInt32LE(0x04034b50, 0)
    hdr.writeUInt16LE(zip64Local ? 45 : 20, 4)
    hdr.writeUInt16LE(flags, 6)
    hdr.writeUInt16LE(method, 8)
    hdr.writeUInt16LE(time, 10)
    hdr.writeUInt16LE(date, 12)
    hdr.writeUInt32LE(0, 14)
    hdr.writeUInt32LE(zip64Local ? 0xFFFFFFFF : 0, 18)
    hdr.writeUInt32LE(zip64Local ? 0xFFFFFFFF : 0, 22)
    hdr.writeUInt16LE(nameBuf.length, 26)
    hdr.writeUInt16LE(extraLen, 28)
    nameBuf.copy(hdr, 30)
    if (zip64Local) {
      hdr.writeUInt16LE(0x0001, 30 + nameBuf.length)
      hdr.writeUInt16LE(16, 32 + nameBuf.length)
      hdr.writeBigUInt64LE(0n, 34 + nameBuf.length)
      hdr.writeBigUInt64LE(0n, 42 + nameBuf.length)
    }
    await emit(hdr)

    let crc = 0
    let uncompSize = 0n
    let compSize = 0n

    if (out.stream) {
      if (method === 0) {
        for await (const chunk of out.stream) {
          if (ac.signal.aborted) { out.stream.destroy(); throw new Error('Archive download aborted.') }
          const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
          crc = zlib.crc32(buf, crc)
          uncompSize += BigInt(buf.length)
          compSize += BigInt(buf.length)
          await emit(buf)
        }
      } else {
        const meter = new Transform({
          transform(chunk, _enc, cb) {
            const buf = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk)
            crc = zlib.crc32(buf, crc)
            uncompSize += BigInt(buf.length)
            cb(null, buf)
          },
        })
        const deflate = zlib.createDeflateRaw({ level: 6 })
        const onStop = () => { out.stream.destroy(); meter.destroy(); deflate.destroy() }
        ac.signal.addEventListener('abort', onStop, { once: true })
        out.stream.on('error', (err) => deflate.destroy(err))
        meter.on('error', (err) => deflate.destroy(err))
        out.stream.pipe(meter).pipe(deflate)
        try {
          for await (const chunk of deflate) {
            if (ac.signal.aborted) throw new Error('Archive download aborted.')
            compSize += BigInt(chunk.length)
            await emit(chunk)
          }
        } finally {
          ac.signal.removeEventListener('abort', onStop)
        }
      }
    }

    const crcU32 = crc >>> 0
    const dd = Buffer.allocUnsafe(zip64Local ? 24 : 16)
    dd.writeUInt32LE(0x08074b50, 0)
    dd.writeUInt32LE(crcU32, 4)
    if (zip64Local) {
      dd.writeBigUInt64LE(compSize, 8)
      dd.writeBigUInt64LE(uncompSize, 16)
    } else {
      dd.writeUInt32LE(Number(compSize), 8)
      dd.writeUInt32LE(Number(uncompSize), 12)
    }
    await emit(dd)

    cd.push({ nameBuf, flags, method, time, date, crc: crcU32, compSize, uncompSize, offset: localOffset, externalAttrs: 0 })
  }

  try {
    const visited = new Set()
    const stack = [cleanBase]
    while (stack.length > 0) {
      if (ac.signal.aborted) throw new Error('Archive download aborted.')
      const dirPath = stack.pop()
      if (visited.has(dirPath)) continue
      visited.add(dirPath)
      const entries = await listFolder(broker, root, dirPath, ac.signal)
      const relDir = relativeName(dirPath)
      if (relDir && entries.length === 0) {
        await writeDirectory(relDir, null)
        continue
      }
      const subdirs = []
      for (const entry of entries) {
        if (!entry || typeof entry.path !== 'string') continue
        const parts = entry.path.replace(/^\/+|\/+$/g, '').split('/').filter(Boolean)
        if (shouldExclude(root, parts, entry.kind)) continue
        const rel = relativeName(entry.path)
        if (!rel) continue
        if (entry.kind === 'folder') {
          subdirs.push(entry.path.replace(/^\/+|\/+$/g, ''))
        } else if (entry.kind === 'file') {
          await writeFile(entry, rel)
        }
      }
      // Preserve listing order when popping from stack.
      for (let i = subdirs.length - 1; i >= 0; i--) stack.push(subdirs[i])
    }

    const cdOffset = offset
    for (const item of cd) {
      const needUncomp = item.uncompSize >= 0xFFFFFFFFn
      const needComp = item.compSize >= 0xFFFFFFFFn
      const needOff = item.offset >= 0xFFFFFFFFn
      const extraDataLen = (needUncomp ? 8 : 0) + (needComp ? 8 : 0) + (needOff ? 8 : 0)
      const extraLen = extraDataLen ? 4 + extraDataLen : 0
      const versionNeed = extraLen ? 45 : (item.flags & 0x0008 ? 20 : 20)
      const rec = Buffer.allocUnsafe(46 + item.nameBuf.length + extraLen)
      rec.writeUInt32LE(0x02014b50, 0)
      rec.writeUInt16LE(versionNeed, 4)
      rec.writeUInt16LE(versionNeed, 6)
      rec.writeUInt16LE(item.flags, 8)
      rec.writeUInt16LE(item.method, 10)
      rec.writeUInt16LE(item.time, 12)
      rec.writeUInt16LE(item.date, 14)
      rec.writeUInt32LE(item.crc, 16)
      rec.writeUInt32LE(needComp ? 0xFFFFFFFF : Number(item.compSize), 20)
      rec.writeUInt32LE(needUncomp ? 0xFFFFFFFF : Number(item.uncompSize), 24)
      rec.writeUInt16LE(item.nameBuf.length, 28)
      rec.writeUInt16LE(extraLen, 30)
      rec.writeUInt16LE(0, 32)
      rec.writeUInt16LE(0, 34)
      rec.writeUInt16LE(0, 36)
      rec.writeUInt32LE(item.externalAttrs, 38)
      rec.writeUInt32LE(needOff ? 0xFFFFFFFF : Number(item.offset), 42)
      item.nameBuf.copy(rec, 46)
      if (extraLen) {
        let p = 46 + item.nameBuf.length
        rec.writeUInt16LE(0x0001, p); p += 2
        rec.writeUInt16LE(extraDataLen, p); p += 2
        if (needUncomp) { rec.writeBigUInt64LE(item.uncompSize, p); p += 8 }
        if (needComp) { rec.writeBigUInt64LE(item.compSize, p); p += 8 }
        if (needOff) { rec.writeBigUInt64LE(item.offset, p); p += 8 }
      }
      await emit(rec)
    }

    const cdSize = offset - cdOffset
    const count = BigInt(cd.length)
    const needZip64Eocd = count >= 0xFFFFn || cdSize >= 0xFFFFFFFFn || cdOffset >= 0xFFFFFFFFn
    if (needZip64Eocd) {
      const z64Offset = offset
      const z64 = Buffer.allocUnsafe(56)
      z64.writeUInt32LE(0x06064b50, 0)
      z64.writeBigUInt64LE(44n, 4)
      z64.writeUInt16LE(45, 12)
      z64.writeUInt16LE(45, 14)
      z64.writeUInt32LE(0, 16)
      z64.writeUInt32LE(0, 20)
      z64.writeBigUInt64LE(count, 24)
      z64.writeBigUInt64LE(count, 32)
      z64.writeBigUInt64LE(cdSize, 40)
      z64.writeBigUInt64LE(cdOffset, 48)
      await emit(z64)

      const loc = Buffer.allocUnsafe(20)
      loc.writeUInt32LE(0x07064b50, 0)
      loc.writeUInt32LE(0, 4)
      loc.writeBigUInt64LE(z64Offset, 8)
      loc.writeUInt32LE(1, 16)
      await emit(loc)
    }

    const eocd = Buffer.allocUnsafe(22)
    eocd.writeUInt32LE(0x06054b50, 0)
    eocd.writeUInt16LE(0, 4)
    eocd.writeUInt16LE(0, 6)
    eocd.writeUInt16LE(count >= 0xFFFFn ? 0xFFFF : Number(count), 8)
    eocd.writeUInt16LE(count >= 0xFFFFn ? 0xFFFF : Number(count), 10)
    eocd.writeUInt32LE(cdSize >= 0xFFFFFFFFn ? 0xFFFFFFFF : Number(cdSize), 12)
    eocd.writeUInt32LE(cdOffset >= 0xFFFFFFFFn ? 0xFFFFFFFF : Number(cdOffset), 16)
    eocd.writeUInt16LE(0, 20)
    await emit(eocd)
    res.end()
  } finally {
    res.off('close', onAbort)
  }
}
