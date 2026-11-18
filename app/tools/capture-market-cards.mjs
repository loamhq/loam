// Captures of a listing card in an agent's reply (2026-11-18), at 390 and
// 1440, light and dark, in Latte and in Autumn, against the fixture app
// (test/market-fixture.mjs: an invented list and thread, no person's data):
// a reply with one card, with two, the install sheet opened from a card, the
// card after install, and the listing's page opened from a card with its way
// back. The walk and its assertions are test/market-card-browser.mjs; this
// runs it with somewhere to put the pictures.
//
//   MARKET_CARD_EVIDENCE=<folder> LOAM_PLAYWRIGHT=<playwright/index.mjs> LOAM_CHROME=<chromium> node tools/capture-market-cards.mjs
if (!process.env.MARKET_CARD_EVIDENCE) throw new Error('Set MARKET_CARD_EVIDENCE')
await import('../test/market-card-browser.mjs')
