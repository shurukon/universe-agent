/**
 * lib/clipboard.ts — copy that actually works in every surface this
 * renderer runs in (user bug 2026-10-30: navigator.clipboard alone
 * silently rejects in restricted webviews, and the button appeared dead).
 *
 *   1. the Electron preload bridge (window.universe.copyText) — the main
 *      process owns the OS clipboard, no permission dance
 *   2. navigator.clipboard.writeText — modern browsers, gesture-permitted
 *   3. the legacy execCommand fallback — last resort webviews
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  const g = (
    window as unknown as {
      universe?: { copyText?: (t: string) => Promise<boolean> };
    }
  ).universe;
  if (g?.copyText) {
    try {
      if (await g.copyText(text)) return true;
    } catch {
      /* fall through to the web paths */
    }
  }
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    /* restricted context — try the legacy path */
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}
