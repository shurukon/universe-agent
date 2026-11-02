async (page) => {
  const row = page.locator(".msg-user").first();
  await row.scrollIntoViewIfNeeded().catch(() => null);
  await row.hover();
  const copyBtn = row.locator('button[aria-label="Copy message"]');
  const before = await page.evaluate(() =>
    navigator.clipboard.readText().catch(() => "__UNREADABLE__")
  );
  await copyBtn.click();
  await page.waitForTimeout(400);
  const after = await page.evaluate(() =>
    navigator.clipboard.readText().catch(() => "__UNREADABLE__")
  );
  const copiedShown = await row.locator('button[aria-label="Copy message"]').count();
  return {
    before,
    after,
    copiedText: after === "probe user message one",
    copyBtns: copiedShown,
    clipboardChanged: before !== after
  };
}
