// Synthetic prototype, not a copy of a private vault file. Four real embedded
// PNGs account for most bytes; a short comment brings it to the reported size.
import { deflateSync } from 'node:zlib'

export const LARGE_HTML_BYTES = 3232179
export const TEXT_MAX = 2097152
export const LARGE_HTML_PATH = 'Prototypes/Shelf studies.html'

function crc32(bytes) {
  let crc = 0xffffffff
  for (const b of bytes) {
    crc ^= b
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0)
  }
  return (crc ^ 0xffffffff) >>> 0
}
function chunk(type, bytes) {
  const name = Buffer.from(type), length = Buffer.alloc(4), crc = Buffer.alloc(4)
  length.writeUInt32BE(bytes.length)
  crc.writeUInt32BE(crc32(Buffer.concat([name, bytes])))
  return Buffer.concat([length, name, bytes, crc])
}
function shelf(seed) {
  const width = 512, height = 384, pixels = Buffer.alloc((width * 3 + 1) * height)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0
    const grain = seed % 12, shelf = y % 112 > 98, book = x % 78 < 54 && y % 112 > 24
    const rgb = shelf ? [105, 75, 49] : book ? [[113, 132, 103], [176, 121, 91], [184, 163, 118]][Math.floor(x / 78) % 3] : [234, 226, 210]
    const at = y * (width * 3 + 1) + 1 + x * 3
    for (let c = 0; c < 3; c++) pixels[at + c] = rgb[c] - grain
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width); header.writeUInt32BE(height, 4); header[8] = 8; header[9] = 2
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', header), chunk('IDAT', deflateSync(pixels, { level: 0 })), chunk('IEND', Buffer.alloc(0))]).toString('base64')
}

const cards = [1, 2, 3, 4].map(n => `<article><img alt="Shelf concept ${n}" src="data:image/png;base64,${shelf(n)}"><h2>Study ${String(n).padStart(2, '0')}</h2><p>Warm materials, room for the unexpected.</p></article>`).join('')
const page = `<!doctype html><html><meta name="viewport" content="width=device-width,initial-scale=1"><style>
:root{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;padding:clamp(20px,4vw,48px);font:15px/1.5 system-ui;background:#f6f1e9;color:#3c3b32}header{max-width:800px;margin:auto auto 28px}small{letter-spacing:.14em}h1{font:normal clamp(30px,4vw,48px)/1.1 Georgia;margin:18px 0}button{padding:10px 16px;border:1px solid currentColor;border-radius:24px;background:transparent;color:inherit;cursor:pointer}section{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px;max-width:800px;margin:auto}article{border:1px solid #8884;border-radius:16px;overflow:hidden;background:#fff8}img{width:100%;display:block}h2,article p{margin:12px 16px}h2{font:normal 24px Georgia}article p{font-size:13px;opacity:.8}body[data-layout=gallery] section{grid-template-columns:1fr}@media(max-width:500px){section{grid-template-columns:1fr}}@media(prefers-color-scheme:dark){body{background:#242921;color:#e7e8de}article{background:#ffffff08}}
</style><body data-layout="grid"><header><small>STUDIO / PROTOTYPES</small><h1>A shelf for everyday things.</h1><p>Four studies in warmth, rhythm and a little breathing room.</p><button id="options" onclick="document.body.dataset.layout=document.body.dataset.layout==='grid'?'gallery':'grid';this.textContent=document.body.dataset.layout==='gallery'?'Show grid':'Show gallery'">Show gallery</button></header><section>${cards}</section></body></html>`
export const LARGE_HTML = page + '<!--' + ' '.repeat(LARGE_HTML_BYTES - Buffer.byteLength(page) - 7) + '-->'
