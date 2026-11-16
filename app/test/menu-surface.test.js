import test from 'node:test'
import assert from 'node:assert/strict'
import { menuPosition, menuSurface } from '../public/menu-surface.js'
import { installDom } from './fakedom.js'
import { h } from '../public/ui.js'
const viewport={width:1440,height:900}
const anchor=(left,top,width=40,height=40)=>({left,top,width,height,right:left+width,bottom:top+height})
test('menu opens at the nearest vertical edge, aligns start or end, and flips with room',()=>{
  assert.deepEqual(menuPosition(anchor(100,100),{width:300,height:200},viewport),{left:100,top:148,maxHeight:740,origin:'0px 0px'})
  assert.deepEqual(menuPosition(anchor(1350,800),{width:300,height:200},viewport),{left:1090,top:592,maxHeight:780,origin:'300px 200px'})
})
test('position shifts within visual viewport and constrains tall content without losing the anchor',()=>{
  const p=menuPosition(anchor(5,400),{width:300,height:900},viewport)
  assert.equal(p.left,12);assert.equal(p.top,448);assert.equal(p.maxHeight,440)
  const shifted=menuPosition(anchor(100,300),{width:300,height:200},{left:80,top:100,width:600,height:700})
  assert.equal(shifted.left,100);assert.equal(shifted.top,348)
})
test('shell supplies a title, explicit dismissal and top-layer behavior with existing contents',()=>{
  installDom();let closed=0
  const action=h('button',{text:'Choose'})
  const node=menuSurface(h('section',{},action),{title:'Options',close:()=>closed++})
  assert.equal(node.getAttribute('popover'),'manual');assert.equal(node.getAttribute('aria-label'),'Options')
  assert.equal(node.childNodes.at(-1),action)
  node.fire('menudismiss');assert.equal(closed,1)
})
test('an anchored listbox keeps the shared shell on phones without adding dialog controls',()=>{
  installDom();globalThis.innerWidth=390
  const node=menuSurface(h('div',{role:'listbox'},h('button',{role:'option',text:'Choose'})),{title:'Provider',close(){},anchored:true,heading:false,initialFocus:'selected',anchor:'#provider'})
  assert.equal(node.getAttribute('data-menu-layout'),'anchored')
  assert.equal(node.getAttribute('data-menu-anchor'),'#provider')
  assert.equal(node.getAttribute('data-menu-initial-focus'),'selected')
  assert.equal(node.childNodes.length,1)
  assert.equal(node.getAttribute('role'),'listbox')
})
