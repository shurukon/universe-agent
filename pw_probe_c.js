async (page) => {
  const row = page.locator(".msg-user").nth(1);
  await row.scrollIntoViewIfNeeded().catch(() => null);
  await row.hover();
  const editBtn = row.locator('button[aria-label="Edit message"]');
  const btnCount = await editBtn.count();
  if (btnCount === 0) return { fail: "no edit button found" };
  await editBtn.click();
  await page.waitForTimeout(900);
  const textareas = await page.$$eval("textarea", els => els.map(e => e.value));
  const bubbles = await page.$$eval(".msg-user .m-bubble", els => els.map(e => (e.textContent || "").trim()));
  const bodyText = await page.evaluate(() => (document.body.innerText || "").slice(0, 500));
  return {
    editBtn: btnCount,
    composerValues: textareas,
    bubblesLeft: bubbles,
    composerSeeded: textareas.some(v => v === "probe user message two"),
    toastHint: bodyText.includes("removed") || bodyText.includes("composer")
  };
}
