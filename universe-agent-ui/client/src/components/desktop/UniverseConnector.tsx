/**
 * components/desktop/UniverseConnector.tsx — mounts once inside the shell.
 * Owns the WebSocket lifecycle + the store bootstrap, and renders nothing
 * itself. The bridge status (Electron preload) also lands in the store so
 * the banner can distinguish "backend down" from "network down".
 */
import { useEffect } from 'react'
import { useChat } from '@/stores/chat'
import { useSetup } from '@/stores/setup'
import { UniverseWs } from '@/lib/ws'
import { BRIDGE_URL, api } from '@/lib/api'

interface DesktopBridge {
  universe?: {
    onBridgeStatus: (cb: (s: { status: string; detail?: string }) => void) => () => void
  }
}

interface UniverseTestSeam {
  __universeTest?: unknown
}

export default function UniverseConnector() {
  useEffect(() => {
    const store = useChat.getState()

    const ws = new UniverseWs({
      onEvent: (ev) => {
        useChat.getState().ingestEvent(ev)
        // rebroadcast for surfaces outside the chat store (the big universe
        // listens for comets/star flares; §6.3 of the desktop plan)
        window.dispatchEvent(new CustomEvent('universe:event', { detail: ev }))
      },
      onState: (s) => {
        useChat.getState().setConnection(s)
        if (s === 'live') {
          // back online: re-sync everything we might have missed
          const state = useChat.getState()
          ws.resync(state._wsGoalIds(), state.lastSeqByGoal).then(() => {
            useChat.getState().refreshTasks()
          })
          // the first-run gate checked once on mount — a bridge that came up
          // after (or restarted) needs the gate re-checked too
          void useSetup.getState().check()
        }
      },
      onSettingsUpdated: () => {
        // settings changed elsewhere (another surface / the bridge itself):
        // stuck consents + stale settings re-fetch from the source of truth
        const state = useChat.getState()
        void state.refreshConsents()
        void state.loadSettings()
      },
    })
    ws.connect()

    // Electron pushes bridge status through the preload bridge
    const desktop = (window as unknown as DesktopBridge).universe
    const offBridgeStatus = desktop?.onBridgeStatus?.((s) => {
      useChat.getState().setBackend(s.status === 'up' || s.status === 'starting', s.detail ?? s.status)
    })

    void store.bootstrap()

    // slow health re-check: keeps the banner truthful when the WS is fine
    // but the goal endpoints died, and when there is no Electron at all (dev)
    const healthTimer = setInterval(() => {
      api
        .health()
        .then((h) => useChat.getState().setBackend(h.ready, h.error ?? ''))
        .catch(() => useChat.getState().setBackend(false, 'bridge unreachable'))
    }, 30_000)

    return () => {
      clearInterval(healthTimer)
      offBridgeStatus?.()
      ws.close()
    }
  }, [BRIDGE_URL])

  // dev-only test seam: lets e2e harnesses (Playwright, ui_states_check)
  // exercise the REAL store ingestion path — the same ingestEvent the WS
  // uses — without needing a live LLM provider. Never present in prod builds.
  if (import.meta.env.DEV) {
    ;(window as unknown as UniverseTestSeam).__universeTest = {
      getState: useChat.getState,
      setState: useChat.setState,
    }
  }

  return null
}
