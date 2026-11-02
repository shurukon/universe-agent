import { createRoot } from "react-dom/client";
import App from "./App";
// Local fonts (offline-first: no CDN, CSP-clean). ZCode-literal identity
// (2026-10): the UI face is the Windows system stack (Segoe UI) matching
// ZCode's own rendering — no bundled UI font. JetBrains Mono stays for
// code/mono readouts; Source Serif 4 for the rare serif touch.
import "@fontsource/source-serif-4/500.css";
import "@fontsource/source-serif-4/600.css";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/500.css";
import "./index.css";
// the ZCode-literal chrome sheet (tokens + chrome) — global because the
// TopBar and the command palette live above every page, not inside any
// one of them
import "./components/desktop/shell.css";
// the ZCode-parity surface sheet — imported LAST so its measured tokens
// win the cascade over the earlier design-system / desktop / redesign
// passes. Behaviour is untouched; only the surface is re-pinned.
import "./components/desktop/zcode-parity.css";

createRoot(document.getElementById("root")!).render(<App />);
