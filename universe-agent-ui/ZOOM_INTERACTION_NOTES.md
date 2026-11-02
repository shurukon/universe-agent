# Zoom and Camera Interaction — Verification

| Adjustment | Result |
|---|---|
| Desktop home distance | Reduced from `80.5` to `66`, making the approved sphere approximately 22% larger than the prior imported version. |
| Zoom range | Expanded from `44–104` to `32–148`, allowing a much closer detailed inspection and a more generous pull-back. |
| Wheel / trackpad | Increased dolly sensitivity from `0.03` to `0.09`, with per-event clamping to keep strong movement controllable. |
| Keyboard | Increased `+` / `−` increments from `7` to `14`, matching the stronger direct-manipulation behavior. |
| Selection | Increased focus advance from `8` to `13` units so selecting an element produces a visibly stronger approach. |
| Live scene | Direct browser preview rendered the enlarged sphere. Selecting the bright center caused the expected closer framing while preserving the original GLM visual composition. |

The renderer’s device-pixel-ratio ceiling was reduced to `1.45` and high-quality multisampling to `2` to reduce WebGL capture pressure without visibly changing the approved scene.

The `+` key produced the new larger forward step inside the live scene. A wheel event dispatched to the canvas was intercepted and cancelled by the controls, confirming that the canvas owns the scroll-to-dolly interaction even though the full-screen page itself has no document scroll container.
