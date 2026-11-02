# Universe Chat Shell — Validation Notes

The current home surface is deliberately **frontend-only**. It does not call a Universe Core, Local API, provider, memory service, agent, skill, microphone, or screen-capture capability. The assistant response explicitly identifies this limitation rather than implying real execution.

## Completed validation

| Area | Result |
|---|---|
| Desktop layout (1440 × 900) | Passed. Header, left-biased welcome surface, Orbit Chips, and composer remain calm and readable. |
| Mobile layout (390 × 844) | Passed. No horizontal overflow; lockup, prompt chips, headline, and composer remain within the viewport. |
| Starter chips | Passed. A selected chip submits through the same local conversation flow. |
| Composer actions | Passed. Arrow-button and Enter submit non-empty text. Shift+Enter retains the draft and inserts a newline. |
| Conversation reset | Passed. Both **New chat** and the **Universe Agent** lockup restore the welcome state. |
| Response state | Passed. The local thinking state appears before the explicitly simulated response. |
| Type check and production build | Passed on 2026-08-27. |

## Scope held for later phases

Settings, Memory Lens, 3D Universe/Voice Mode, voice input, screen awareness, desktop-level floating icon, and live backend integration remain intentionally deferred.
