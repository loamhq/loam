import test from 'node:test'
import assert from 'node:assert/strict'
import {readFile,mkdir,mkdtemp,writeFile,rm} from 'node:fs/promises'
import {tmpdir} from 'node:os'
import path from 'node:path'
import {execFileSync} from 'node:child_process'
import {contrast} from './theme-contrast.mjs'

test('material paint generator reproduces every committed variant without touching the working tree',async t=>{
  const dir=await mkdtemp(path.join(tmpdir(),'face-paints-'));t.after(()=>rm(dir,{recursive:true,force:true}))
  await mkdir(path.join(dir,'tools'));await mkdir(path.join(dir,'public/themes'),{recursive:true})
  for(const file of ['build-face-paints.mjs','face-paints.json'])await writeFile(path.join(dir,'tools',file),await readFile(new URL(file,import.meta.url)))
  await writeFile(path.join(dir,'tools/theme-contrast.mjs'),`export ${contrast.toString()}\n`)
  execFileSync(process.execPath,[path.join(dir,'tools/build-face-paints.mjs')])
  assert.equal(await readFile(path.join(dir,'public/themes/face-paints.css'),'utf8'),await readFile('public/themes/face-paints.css','utf8'))
})
