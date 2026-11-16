// Details and composer enter the same saved-settings sheet.
export async function chooseThreadOption(page, label, value) {
  await page.getByRole('button', { name: label, exact: true }).click()
  await page.getByRole('listbox', { name: label, exact: true }).locator(`[role="option"][data-value=${JSON.stringify(value)}]`).click()
  const sheet = page.getByRole('dialog', { name: 'Providers & models', exact: true })
  await page.waitForFunction((label) => !document.querySelector(`.tt-configuration button[aria-label=${JSON.stringify(label)}]`)?.disabled, label)
  await sheet.getByRole('button', { name: 'Done', exact: true }).click()
  await sheet.waitFor({ state: 'detached' })
}
