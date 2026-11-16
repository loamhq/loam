// Optional Sharp evidence tool; screenshots retain their native resolution.
import {createRequire} from 'node:module'
import {mkdir,readFile} from 'node:fs/promises'
import path from 'node:path'
const [source,out]=process.argv.slice(2)
if(!source||!out)throw Error('Usage: node tools/face-grid-review.mjs CAPTURES OUTPUT')
const sharp=createRequire(path.join(process.env.LOAM_IMAGE_DEPS||path.join(process.env.TMPDIR,'deps'),'package.json'))('sharp')
await mkdir(out,{recursive:true})
for(const id of ['mist-trail','deep-forest','yosemite','autumn','foundry'])for(const width of[390,1440])for(const mode of['light','dark']){
 const height=width===390?844:1000,layers=[]
 layers.push({input:await sharp(`public/themes/${id}/cover${id==='autumn'&&mode==='dark'?'-dark':''}.webp`).resize(280,200,{fit:'cover'}).toBuffer(),left:10,top:48})
 for(const [i,surface]of['grid','thread','board'].entries()){
  layers.push({input:await readFile(path.join(source,`${id}-${width}-${mode}-${surface}.png`)),left:300+i*width,top:40})
  layers.push({input:Buffer.from(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="40"><text x="12" y="26" fill="#eee" font-size="16" font-family="sans-serif">${id} · ${mode} · ${width}px · ${surface}</text></svg>`),left:300+i*width,top:0})
 }
 await sharp({create:{width:300+width*3,height:height+40,channels:3,background:'#242424'}}).composite(layers).webp({quality:95,effort:5}).toFile(path.join(out,`${id}-${width}-${mode}.webp`))
}
console.log('20 native-resolution review sheets: cover, seven-agent Chats grid, thread and board.')
