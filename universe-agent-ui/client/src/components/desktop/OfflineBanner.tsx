/**
 * components/desktop/OfflineBanner.tsx — the no-silent-failure promise.
 * Shown whenever the live socket is reconnecting/offline or the backend
 * is down; states exactly what is wrong and what the app is doing about it.
 * When the bridge supervisor reports `failed`, a Retry button invokes the
 * manual `universe:bridge-restart` IPC (resets attempts, spawns immediately).
 */
import { useState } from 'react'
import { useChat } from '@/stores/chat'
import { RotateCcw, WifiOff } from 'lucide-react'

export default function OfflineBanner() {
  const connection = useChat((s) => s.connection)
  const backend = useChat((s) => !s.backendReady)
  const detail = useChat((s) => s.backendDetail)
  const [retrying, setRetrying] = useState(false)

  const failed = backend && /failed|exited|spawn error|not found/i.test(detail)

  if (connection === 'live' && !backend) return null

  const offline = connection === 'offline' || backend
  const message = backend
    ? `Universe backend is ${detail || 'unavailable'} — trying to reconnect`
    : connection === 'reconnecting'
      ? 'Lost the live connection — reconnecting…'
      : 'Connection lost — retrying in the background'

  const onRetry = async () => {
    const api = (window as unknown as { universe?: { bridgeRestart?: () => Promise<boolean> } }).universe
    if (!api?.bridgeRestart || retrying) return
    setRetrying(true)
    try {
      await api.bridgeRestart()
    } finally {
      setRetrying(false)
    }
  }

  return (
    <div className={`offbanner${offline ? ' off' : ''}`} role="status" aria-live="polite">
      <WifiOff size={13} strokeWidth={1.8} aria-hidden="true" />
      <span>{message}</span>
      {failed && (
        <button
          type="button"
          className="offbanner-retry"
          onClick={onRetry}
          disabled={retrying}
          aria-label="Retry starting the Universe backend"
        >
          <RotateCcw size={12} strokeWidth={2} aria-hidden="true" />
          {retrying ? 'Retrying…' : 'Retry'}
        </button>
      )}
    </div>
  )
}
