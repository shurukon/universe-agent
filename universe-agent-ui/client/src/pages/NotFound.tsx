/**
 * pages/NotFound.tsx — universe identity 404. The template's light-blue
 * "Page Not Found" card was the first thing users saw when the Electron
 * load path was wrong; this surface now matches the rest of the app.
 */
import { AlertCircle, Home } from "lucide-react";
import { useLocation } from "wouter";
import { useChat } from "@/stores/chat";

export default function NotFound() {
  const [, setLocation] = useLocation();
  const connection = useChat(s => s.connection);
  const offline = connection === "offline";

  return (
    <div
      className="fixed inset-0 flex items-center justify-center"
      style={{ background: "#161616" }}
    >
      <div className="text-center" style={{ maxWidth: 420, padding: "0 24px" }}>
        <div className="flex justify-center" style={{ marginBottom: 18 }}>
          <AlertCircle
            size={34}
            strokeWidth={1.6}
            style={{ color: "var(--accent-cyan)" }}
            aria-hidden="true"
          />
        </div>
        <div
          className="mono"
          style={{ fontSize: 30, color: "var(--ink)", letterSpacing: ".08em" }}
        >
          404
        </div>
        <p
          style={{
            color: "var(--ink2)",
            fontSize: 13,
            marginTop: 8,
            lineHeight: 1.6,
          }}
        >
          This route doesn't exist in the universe.
          {offline && (
            <span style={{ color: "var(--ink3)" }}>
              <br />
              (The bridge is offline, too — reconnecting.)
            </span>
          )}
        </p>
        <button
          className="cm-btn ok"
          style={{ marginTop: 22 }}
          onClick={() => setLocation("/chat")}
        >
          <Home size={13} strokeWidth={1.8} aria-hidden="true" />
          Go to chat
        </button>
      </div>
    </div>
  );
}
