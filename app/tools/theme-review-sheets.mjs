// Optional evidence tool. Sharp is installed in job scratch, never at runtime.
// Preserve screenshots at native resolution, with their cover beside each one.
import { createRequire } from 'node:module'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
const [source,out]=process.argv.slice(2)
if(!source||!out) throw Error('Usage: node tools/theme-review-sheets.mjs CAPTURE_DIRECTORY OUTPUT_DIRECTORY')
const sharp=createRequire(path.join(process.env.LOAM_IMAGE_DEPS || path.join(process.env.TMPDIR,'deps'),'package.json'))('sharp')
await mkdir(out,{recursive:true})
for(const id of ['deep-forest','autumn','yosemite','mist-trail','foundry']) for(const width of [390,1440]) for(const mode of ['light','dark']) {
  const height=width===390?844:1000, cellWidth=width+320, cellHeight=height+40
  const layers=[]
  const photo=await sharp(`${id==="latte"||id==="plain"?"public/themes":"../plugins/_listed"}/${id}/cover${id==='autumn'&&mode==='dark'?'-dark':''}.webp`).resize(300,200,{fit:'cover'}).toBuffer()
  for(const [i,surface]of ['list','thread','appearance','controls'].entries()) {
    const x=i%2*cellWidth, y=Math.floor(i/2)*cellHeight
    const caption=Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${cellWidth}" height="40"><text x="12" y="26" fill="#eee" font-size="16" font-family="sans-serif">${id} · ${mode} · ${width}px · ${surface}</text></svg>`)
    layers.push({input:caption,left:x,top:y},{input:photo,left:x+10,top:y+52}, {input:await readFile(path.join(source,`${id}-${width}-${mode}-${surface}.png`)),left:x+320,top:y+40})
  }
  await sharp({create:{width:cellWidth*2,height:cellHeight*2,channels:3,background:'#252525'}}).composite(layers).webp({quality:94,effort:5}).toFile(path.join(out,`${id}-${width}-${mode}.webp`))
}
console.log('20 sheets: each native-resolution home, thread, Appearance and controls capture beside its cover.')
