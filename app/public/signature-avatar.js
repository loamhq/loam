// Local material suppliers for the shared agent-face component. The original
// eight slots in each set are permanent identity; append, never reorder them.
// Keep the original modulo fixed if adding slots in a later identity version.
// No IDs, masks, filters or paint servers: repeated faces are independent.
import { variantFor } from './face-identity.js'
export { variantFor } from './face-identity.js'
const NS = 'http://www.w3.org/2000/svg'
const node = (tag, attrs = {}, children = []) => {
  const el = document.createElementNS(NS, tag)
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value)
  for (const child of children) el.appendChild(child)
  return el
}
const path = (d, attrs = {}) => node('path', { d, ...attrs })
const line = (d, attrs = {}) => path(d, { fill: 'none', stroke: 'var(--face-line)', 'stroke-width': 2, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', ...attrs })
const detail = children => node('g', { class: 'face-detail' }, children)
const mark = children => node('g', { class: 'face-mark' }, children)
// Creek rocks and cut timber each have their own silhouette.
const stones = [
  'M8 62Q8 46 27 40L66 35Q85 35 93 53Q98 69 76 75L30 78Q10 77 8 62Z',
  'M16 53Q16 22 45 16Q75 13 86 39Q98 70 72 84Q39 98 21 76Q14 65 16 53Z',
  'M29 82Q13 68 24 38Q35 9 55 11Q76 14 80 42Q88 72 69 85Q47 98 29 82Z',
  'M12 70Q9 58 29 41L66 19Q83 13 88 32Q96 56 73 73Q45 91 24 83Q13 80 12 70Z',
  'M30 87Q16 77 20 48Q22 18 44 12Q62 8 76 28Q87 49 77 72Q63 96 43 92Z',
  'M9 61Q14 44 33 39Q54 28 76 40Q98 53 89 69Q81 84 51 84Q23 84 12 73Z',
  'M17 65L23 35Q26 23 42 20L69 26Q81 29 88 49L82 74Q70 86 48 84L24 77Z',
  'M19 74Q10 57 23 37Q39 15 63 20Q86 25 88 48Q93 74 71 83Q37 96 19 74Z',
]
const glints = [[46,44,21,3,0],[41,31,14,5,-8],[47,24,10,5,-12],[62,31,13,4,-28],[43,29,9,6,-18],[46,44,18,3,0],[43,33,12,4,9],[48,31,13,5,-13]]
const cuts = [
  {d:'M9 59Q8 36 29 28Q56 18 81 32Q96 43 89 66Q83 83 54 84Q23 88 12 72Z',x:42,y:54,rx:30,ry:20,rings:[1,.74,.4],bark:.86},
  {d:'M24 82Q12 65 23 36Q34 10 57 12Q81 19 82 51Q87 78 65 89Q43 99 24 82Z',x:53,y:45,rx:20,ry:30,rings:[1,.84,.66,.48,.27],bark:.9},
  {d:'M16 72L12 49Q11 27 37 20Q61 12 82 29L89 57Q85 82 59 87L30 85Z',x:50,y:53,rx:27,ry:27,rings:[1,.5],bark:.8},
  {d:'M9 56Q14 36 37 27Q68 13 87 34Q103 58 79 74Q50 95 21 79Q10 75 9 56Z',x:57,y:52,rx:25,ry:17,rings:[1,.8,.59,.35],bark:.93},
  {d:'M20 75Q11 59 21 37Q25 21 43 18L68 23Q91 37 85 61L76 80Q58 93 39 86Z',x:42,y:44,rx:19,ry:22,rings:[1,.64,.3],bark:.78},
  {d:'M14 47Q16 22 40 24Q62 11 81 30Q93 47 77 61Q87 82 59 88Q31 92 19 74Z',x:49,y:54,rx:23,ry:24,rings:[1,.83,.65,.47,.28],bark:.87},
  {d:'M13 48Q12 25 34 18Q64 5 83 33Q94 51 83 75Q63 93 33 83Q13 73 13 48Z',x:54,y:47,rx:25,ry:25,rings:[1,.56],bark:.9},
  {d:'M30 84Q12 73 21 47Q31 16 51 13Q72 12 81 34Q91 59 71 81Q50 99 30 84Z',x:45,y:61,rx:16,ry:23,rings:[1,.77,.55,.3],bark:.82},
]
const leaves = [
  'M49 13L58 33L70 27L68 42L88 38L77 55L84 63L59 70L53 87L48 73L25 80L30 64L13 54L32 48L26 30L42 36Z',
  'M49 14C60 7 65 24 60 29C78 22 81 38 70 43C89 43 88 60 73 61C81 72 64 83 54 73L48 89L44 75C24 84 19 70 28 64C9 58 15 44 30 46C17 34 30 22 40 30C32 18 42 10 49 14Z',
  'M50 12L65 25L67 32L79 39L77 48L84 56L74 63L72 72L59 78L50 88L41 77L28 72L27 61L17 55L24 45L22 38L35 30L37 23Z',
  'M51 13C78 29 86 49 72 69C65 79 55 80 49 88C42 78 27 79 22 62C13 38 33 24 51 13Z',
  'M48 86L44 70Q23 66 12 44L15 26L32 32L43 47L45 20L55 17L60 45L74 26L87 26L88 44Q76 66 57 71L52 87Z',
  'M50 12L61 30L67 29L66 40L75 40L71 50L80 56L70 64L70 72L59 76L50 88L40 77L29 73L30 64L20 57L28 49L24 40L35 39L33 29L40 30Z',
  'M49 87L43 70L25 69L18 52L26 27L40 33L47 21L54 34L70 26L80 52L73 69L56 71L52 87Z',
  'M49 88L45 76Q21 71 18 50Q14 28 38 21L50 13L61 21Q85 28 82 50Q79 71 56 77L52 88Z',

]
// Rock face, facet, name. The shared base leaves room for carved initials.
const landmarks = [
  ['M8 83L20 60Q30 27 57 20L57 60L73 70L92 83Z','M57 20L57 60L73 70L92 83H65L48 67Z','half-dome'],
  ['M8 83L23 25L54 18L65 27L67 65L91 83Z','M54 18L51 70L65 83H91L67 65L65 27Z','el-capitan'],
  ['M8 83L30 69L38 20L51 12L61 47L66 70L92 83Z','M51 12L46 70L62 83H92L66 70L61 47Z','sentinel'],
  ['M8 83L24 63L32 24L43 43L54 13L68 58L77 38L92 83Z','M54 13L51 66L65 83H92L77 38L69 66Z','cathedral'],
  ['M8 83L20 32L37 26L43 51L54 27L78 20L84 64L92 83Z','M78 20L70 69L84 83H92L84 64Z','waterfall'],
  ['M8 83L20 66Q40 21 63 24L74 51L92 83Z','M63 24L56 63L72 83H92L74 51Z','pine-ridge'],
  ['M8 83L21 52Q32 22 57 28L66 51L76 59L92 83Z','M57 28L51 65L66 83H92L76 59L66 51Z','glacier-point'],
  ['M8 83L20 63L27 43L41 28L59 20L68 40L76 45L92 83Z','M59 20L54 67L69 83H92L76 45L68 40Z','three-brothers'],
]
const stamps = ['M38 58L50 35L62 58Z', 'M37 41H63M37 51H63M37 61H63', 'M50 34L66 50L50 66L34 50Z', 'M37 61V42Q50 28 63 42V61', 'M35 59L46 37L56 59L66 37', 'M34 50H66M50 34V66', 'M38 34V66H64', 'M34 57L50 36L66 57M41 63H59']

const supplier = (count, draw) => Object.assign(draw, { count })
export const faceSuppliers = {
  'mist-trail': supplier(stones.length, n => [
    path(stones[n], {fill:'var(--face-edge)',opacity:.16,transform:'translate(0 3)'}),
    path(stones[n], {fill:'var(--face-body)','data-silhouette':'stone'}),
    // Soft flat layers catch wet light without an outline or paint server.
    ...[1.4,1].map((scale,i)=>node('ellipse',{class:'face-mark',cx:glints[n][0],cy:glints[n][1],rx:glints[n][2]*scale,ry:glints[n][3]*scale,transform:`rotate(${glints[n][4]} ${glints[n][0]} ${glints[n][1]})`,fill:'var(--face-light)',opacity:i?.3:.1})),
    ...([2,7].includes(n) ? [mark([path(n===2?'M29 28Q43 44 76 47L79 56Q46 56 24 41Z':'M22 41Q40 62 85 58L85 67Q45 73 18 52Z',{fill:'var(--face-quartz)',opacity:.9})])] : []),
    ...([0,5,7].includes(n) ? [detail([path(['M11 63Q18 55 25 62L29 73L20 76Z','M14 66Q24 58 33 65L31 78L22 79Z','M22 69Q28 63 35 69L38 82L28 82Z'][[0,5,7].indexOf(n)],{fill:'var(--face-moss)'})])] : []),
    ...(n===1 ? [detail([node('circle',{cx:35,cy:43,r:1.3,fill:'var(--face-light)'}),node('circle',{cx:62,cy:68,r:1,fill:'var(--face-light)'})])] : []),
  ]),
  foundry: supplier(stamps.length, n => [
    node('rect', { x: 12, y: 12, width: 76, height: 76, rx: n % 2 ? 10 : 6, fill: 'var(--face-edge)' }),
    node('rect', { x: 15, y: 15, width: 70, height: 70, rx: n % 2 ? 8 : 4, fill: 'var(--face-body)' }),
    line('M19 78V19H79', { stroke: 'var(--face-light)', opacity: .55, 'stroke-width': 1.5 }),
    ...[[22,22],[78,22],[22,78],[78,78]].map(([cx,cy]) => node('circle', { cx, cy, r: 2.4, fill: 'var(--face-light)' })),
    detail([line('M22 80H78M80 22V78', { opacity: .4, 'stroke-width': 1 })]),
    mark([line(stamps[n], { 'stroke-width': 4, stroke: 'var(--face-ink)' })]),
  ]),
  'deep-forest': supplier(cuts.length, n => {
    const c=cuts[n], scale=c.bark, shift=50*(1-scale)
    return [path(c.d,{fill:'var(--face-edge)'}),path(c.d,{fill:'var(--face-body)','data-silhouette':'wood',transform:`translate(${shift} ${shift}) scale(${scale})`}),
      ...c.rings.map((r,i)=>node('ellipse',{cx:c.x,cy:c.y,rx:c.rx*r,ry:c.ry*r,transform:`rotate(${[4,-9,12,-18,7,13,-5,22][n]} ${c.x} ${c.y})`,fill:'none',stroke:'var(--face-line)','stroke-width':i?1.8:2.2,class:'face-mark'})),
      ...([0,3,6].includes(n)?[mark([line([`M15 67L${c.x-8} ${c.y+2}`,`M83 41L${c.x+5} ${c.y-4}`,`M72 80L${c.x+2} ${c.y+8}`][[0,3,6].indexOf(n)],{'stroke-width':2.2})])]:[]),
      ...([1,4].includes(n)?[detail([path('M24 31Q12 46 18 64L25 63Q21 47 30 34Z',{fill:'var(--face-moss)'})])]:[]),
      mark([node('ellipse',{cx:c.x,cy:c.y,rx:2.8,ry:3.5,fill:'var(--face-line)'})])]
  }),
  autumn: supplier(leaves.length, n => [
    path(leaves[n], { fill: `var(--face-leaf-${n})`, stroke:'var(--face-edge)', 'stroke-width':1.2, 'stroke-linejoin':'round','data-silhouette':'leaf' }),
    mark([line('M49 87L51 26', { stroke:'var(--face-line)', 'stroke-width':2.2 })]),
    detail([line(n===7?'M50 43L36 33M50 57L29 46M51 43L64 33M50 57L71 46':'M50 42L39 34M50 54L30 45M50 67L34 65M51 41L61 31M50 54L69 45M50 67L68 61', { 'stroke-width':1.4, opacity:.7 })]),
  ]),
  yosemite: supplier(landmarks.length, n => [
    path('M9 69V26Q9 12 24 12H76Q91 12 91 27V69Q91 87 75 87H25Q9 87 9 69Z',{fill:'var(--face-sky)'}),
    ...([0,3,7].includes(n)?[node('circle',{cx:n===3?73:26,cy:29,r:n===7?4:6,fill:'var(--face-cloud)'})]:[]),
    ...([1,2,6].includes(n)?[detail([path('M15 30Q27 24 41 30Q49 25 61 29L61 32H15Z',{fill:'var(--face-cloud)'})])]:[]),
    path(landmarks[n][0],{fill:'var(--face-body)','data-landmark':landmarks[n][2],'data-silhouette':'granite'}),
    path(landmarks[n][1],{fill:'var(--face-edge)'}),
    ...(n===4?[mark([line('M46 34Q45 41 47 47L46 57Q44 65 44 72',{stroke:'var(--face-cloud)','stroke-width':4})])]:[]),
    ...(n===5?[mark([path('M30 40L20 55H25L16 66H23L13 77H28V85H33V77H46L37 66H43L34 55H40Z',{fill:'var(--face-moss)'})])]:[]),
    detail([line(n===1?'M33 36L27 68M41 32L36 70':n===3?'M34 42L30 67M55 33L57 64':'M36 48L31 67M43 40L39 65',{'stroke-width':1.3,opacity:.4})]),
  ]),
}

export function signatureArt(id, slot = null) {
  const n = variantFor(id, { slot })
  return node('svg', { class:'signature-art', viewBox:'0 0 100 100', 'aria-hidden':'true', focusable:'false', 'data-variant':n },
    Object.entries(faceSuppliers).map(([material, supply]) => node('g', { 'data-material':material }, supply(variantFor(id, { count: supply.count ?? 8, slot })))))
}
