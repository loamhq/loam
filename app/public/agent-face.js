import { latteArt } from './latte-avatar.js'
import { signatureArt } from './signature-avatar.js'

// Suppliers share one component and remain in retained mentions/prose. CSS
// switches materials immediately without replacing text or selection offsets.
export function agentFace(id) { return [latteArt(id), signatureArt(id)] }
