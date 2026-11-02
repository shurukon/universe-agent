/**
 * components/desktop/ConfirmDialog.tsx — the in-app confirm dialog.
 *
 * Replaces every window.confirm (chat delete, project delete, provider
 * remove, MCP remove): a small centered card on the same scrim language
 * as ConsentModal, Esc/backdrop to cancel, Enter to confirm, focus on
 * the safe action first so Delete is never the accident.
 */
import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { TriangleAlert } from "lucide-react";

/**
 * components/desktop/ConfirmDialog.tsx — the in-app confirm dialog.
 * Rendered through a PORTAL into document.body: callers that mount it
 * inside narrow stacking contexts (the sidebar <aside> used to clip the
 * modal into the 264px column, behind the main surface) get a
 * guaranteed top-level overlay — position:fixed against the document
 * root, above every panel.
 */

export interface ConfirmSpec {
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
}

export default function ConfirmDialog({
  spec,
  onClose,
}: {
  spec: ConfirmSpec;
  onClose: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
      if (e.key === "Enter") {
        e.preventDefault();
        spec.onConfirm();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return createPortal(
    <div
      className="modal-root"
      role="alertdialog"
      aria-modal="true"
      aria-label={spec.title}
      onMouseDown={e => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="cm-card cf-card">
        <div className="cm-head">
          <span className="cm-ic cf-ic" aria-hidden="true">
            <TriangleAlert size={15} strokeWidth={1.8} />
          </span>
          <span className="cm-title">{spec.title}</span>
        </div>
        <p className="cm-body">{spec.body}</p>
        <div className="cm-actions">
          <button ref={cancelRef} className="cm-btn" onClick={onClose}>
            Cancel
          </button>
          <button
            className={`cm-btn ${spec.danger ? "deny" : "ok"}`}
            onClick={() => {
              spec.onConfirm();
              onClose();
            }}
          >
            {spec.confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body
  );
}
