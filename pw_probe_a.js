async (page) => {
  const ctx = page.context();
  await ctx.addInitScript(() => {
    window.universe = {
      bridgeInfo: async () => ({ port: "8750", url: "http://127.0.0.1:8750", token: "dev", status: "up" })
    };
  });
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: "http://localhost:5199" }).catch(() => {});
  await page.goto("http://localhost:5199/chat?goal=task-uiprobe0001", { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".msg-user", { timeout: 30000 }).catch(() => null);
  await page.waitForTimeout(800);
  const bubbleTexts = await page.$$eval(".msg-user .m-bubble", els => els.map(e => (e.textContent || "").trim()));
  const storeProbe = await page.evaluate(async () => {
    try {
      const m = await import("/src/stores/chat.ts");
      const s = m.useChat.getState();
      return {
        messages: s.messages.map(x => ({ role: x.role, goal: x.goal_id, text: (x.text || x.reply || "").slice(0, 60) })),
        viewed: s.viewedGoalId
      };
    } catch (e) { return { err: String(e) }; }
  });
  return { url: page.url(), bubbleTexts, storeProbe };
}
