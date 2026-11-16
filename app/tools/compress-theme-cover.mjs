// Optional asset preparation, never an app/runtime dependency. Install sharp
// in scratch and point LOAM_IMAGE_DEPS at that directory, or install it locally.
import { createRequire } from 'node:module'
import path from 'node:path'
const [input, output, quality = '83', blur = '0'] = process.argv.slice(2)
if (!input || !output?.endsWith('.webp')) throw new Error('Usage: node tools/compress-theme-cover.mjs INPUT OUTPUT.webp')
const require = createRequire(process.env.LOAM_IMAGE_DEPS ? path.resolve(process.env.LOAM_IMAGE_DEPS, 'package.json') : import.meta.url)
const sharp = require('sharp')
if (!/^\d+$/.test(quality) || +quality < 1 || +quality > 100 || !Number.isFinite(+blur) || +blur < 0 || (+blur > 0 && +blur < .3)) throw new Error('Quality must be 1–100; optional blur must be zero or at least 0.3.')
let image = sharp(input).rotate().resize({ width: 640, withoutEnlargement: true })
if (+blur) image = image.blur(+blur)
const result = await image.webp({ quality: +quality, effort: 6 }).toFile(output)
if (result.size > 200_000) throw new Error('Cover exceeds 200 KB; reduce quality or dimensions before shipping it.')
console.log(JSON.stringify({ format: result.format, width: result.width, height: result.height, bytes: result.size }))
