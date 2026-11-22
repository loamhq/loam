// Public installation settings, supplied before modules by appearance-state.js.
// Keep defaults shared with the server; never put credentials in this object.
export const INSTALLATION_DEFAULTS = Object.freeze({
  appsDomain: 'example.com',
  timeZone: 'America/Los_Angeles',
  inbox: '00_INBOX',
  boards: 'Boards',
  attachments: '98_ATTACHMENTS',
})
export const installation = () => ({ ...INSTALLATION_DEFAULTS, ...globalThis.LOAM_INSTALLATION })
