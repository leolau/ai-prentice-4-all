# WhatsApp bridge: reconnect backoff + persisted QR payload

Status: implemented, deployed 2026-09-23.

## Problem

`scripts/whatsapp-bridge/bridge.js` reconnected a fixed 3s after every
`connection === 'close'` event. While the bridge is unpaired, every
close→reconnect cycle emits a fresh QR code, so an unpaired bridge ran a
full WhatsApp websocket handshake + QR emit every ~4–5s indefinitely
(observed: ~92k QR emissions in the personal-bridge log, ~127 connects in
9 minutes). WhatsApp responded with repeated `503 stream:error` drops —
server-side throttling that also rotated the QR faster than its ~60s
lifetime, making the code effectively unscannable.

## Fix

- `reconnectAttempts` counter, reset on `connection === 'open'`.
- Non-515 closes back off exponentially: `min(3s * 2^n, 60s)`. 515
  restarts keep the 1s fast path (normal pairing handshake, not a
  failure) and do not increment the counter. `loggedOut` still exits(1)
  for systemd restart handling.
- QR payloads are persisted to `<session-dir>/qr.txt` on each emit
  (terminal ASCII QR art is unreliable for camera scanning; external
  tooling renders the payload to PNG). The file is removed on a
  successful `open` so a stale pairing payload is never left on disk.

## Behaviour after fix

- Fresh bridge start: first attempts are fast (3s, 6s, 12s, 24s, 48s),
  then settle at one connect/QR refresh per 60s — matching the QR's
  natural lifetime, so the on-screen/file QR is almost always valid.
- Logged-in transient drops still reconnect quickly at first, backing
  off only if the outage persists.

## Verification

- `node --check` clean.
- Deployed; personal bridge restarted and observed emitting QR at the
  backed-off cadence, `qr.txt` written to the session dir.
