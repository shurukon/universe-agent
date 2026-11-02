/**
 * components/desktop/AgentReply.tsx — the agent's reply, rendered as real
 * markdown (M1). Streams token-by-token with Streamdown (block-memoized,
 * incomplete-markdown-tolerant) and settles into static mode when done.
 * Streamdown sanitizes untrusted model output by default (rehype-sanitize
 * + rehype-harden); links open externally with rel=noreferrer.
 */
import { memo, useMemo, useRef, useState, type ComponentProps } from "react";
import { Streamdown } from "streamdown";
import { Check, Copy } from "lucide-react";

type PreProps = ComponentProps<"pre"> & { node?: unknown };

/** Copy button on every code block — matches the app's icon language. */
function CodePre({ node, children, ...rest }: PreProps) {
  void node; // react-markdown passes its hast node — never reaches the DOM
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);
  const copy = () => {
    const s = preRef.current?.innerText ?? "";
    if (!s) return;
    void navigator.clipboard.writeText(s);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="ar-pre">
      <button
        type="button"
        className="ar-copy"
        onClick={copy}
        aria-label="Copy code"
      >
        {copied ? <Check size={11} /> : <Copy size={11} />}
      </button>
      <pre ref={preRef} {...rest}>
        {children}
      </pre>
    </div>
  );
}

const AgentReply = memo(function AgentReply({
  text,
  streaming,
}: {
  text: string;
  streaming: boolean;
}) {
  // stable component map: a fresh one per token would re-render all blocks
  const components = useMemo(() => ({ pre: CodePre }), []);
  if (!text) return null;
  return (
    <div className="agent-reply">
      <Streamdown
        mode={streaming ? "streaming" : "static"}
        isAnimating={streaming}
        components={components}
        shikiTheme={["github-dark", "github-dark"]}
        className="ar-md"
      >
        {text}
      </Streamdown>
    </div>
  );
});

export default AgentReply;
