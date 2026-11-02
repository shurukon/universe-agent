async (page) => {
  const ta = page.locator("textarea").first();
  await ta.scrollIntoViewIfNeeded().catch(() => null);
  await ta.click();
  // real-single-press behavior: one press must delete one character
  const before = await ta.inputValue();
  await page.keyboard.press("Backspace");
  const afterReal = await ta.inputValue();

  // synthetic OS-style auto-repeat events (what holding a key produces):
  // the bug swallowed ALL repeats; the fix only blocks action keys
  const synth = await page.evaluate(() => {
    const ta = document.querySelector("textarea");
    const run = key => {
      const e = new KeyboardEvent("keydown", { key, repeat: true, bubbles: true, cancelable: true });
      ta.dispatchEvent(e);
      return e.defaultPrevented;
    };
    return {
      backspaceRepeatPrevented: run("Backspace"),
      enterRepeatPrevented: run("Enter"),
      tabRepeatPrevented: run("Tab"),
      escapeRepeatPrevented: run("Escape"),
      letterRepeatPrevented: run("e"),
      spaceRepeatPrevented: run(" ")
    };
  });

  // restore the user's original clipboard content
  await page.evaluate(() => navigator.clipboard.writeText("4938755414469054")).catch(() => null);
  const clipNow = await page.evaluate(() => navigator.clipboard.readText().catch(() => "__UNREADABLE__"));

  return {
    realBackspaceWorks: before.length === afterReal.length + 1,
    beforeLen: before.length,
    afterRealLen: afterReal.length,
    synth,
    clipboardRestored: clipNow === "4938755414469054"
  };
}
