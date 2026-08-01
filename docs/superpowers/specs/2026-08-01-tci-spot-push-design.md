# TCI Spot Push — Bandmap Spots on the SDR Panadapter (Phase 2)

**Date:** 2026-08-01
**Status:** Approved design, ready for implementation planning
**Depends on:** the TCI backend from `2026-08-01-tci-support-design.md` (branch
`add-tci-support`, PR mbridak/not1mm#638)

## Goal

Push not1mm's bandmap spots to AetherSDR via TCI `spot:` commands, so DX
callsigns appear directly on the panadapter waterfall.

This is phase 2 of TCI support. It was deliberately excluded from phase 1
because it hooks the bandmap rather than the CAT interface and has no
flrig/rigctld equivalent.

## Protocol facts, verified against the live radio

Phase 1's lesson was that TCI documentation and TCI reality differ. `spot:` is a
**client-to-server** command, so it never appeared in the phase 1 handshake
capture — the radio only receives it. It was therefore probed directly on
2026-08-01 against the live AetherSDR.

**Confirmed working:**

```
spot:<callsign>,<modulation>,<frequency_hz>,<color>,<text>;
```

Two spots sent 2 kHz and 4 kHz above a 14.270 MHz USB dial both rendered on the
panadapter. **Both colour encodings were accepted** — signed integer
(`-65536`) and hex (`0xFF0000`).

**Confirmed NOT working — spots cannot be retracted.** Three forms were tried,
all accepted without an error frame and all ignored:

```
spot_delete:TE1ST;                      -> ignored
spot_delete:TE1ST,usb,14272000;         -> ignored
spot_delete:0,TE2ST;                    -> ignored
```

AetherSDR expires spots on its own schedule. That is the only cleanup
available.

## The constraint this imposes

not1mm's bandmap actively removes spots: `spot_aging()`
(`not1mm/bandmap.py:889`) drops them by age, `delete_spot()` (`:303`) removes
one, and `clear_spots()` (`:1002`) empties the lot. **None of these removals can
be mirrored to the radio.**

The panadapter's spot list will therefore drift from the bandmap's. This is
accepted as a documented limitation, not worked around:

- It is bounded — AetherSDR expires spots itself, so nothing accumulates
  without limit.
- The alternative designs are worse. Pushing only on an explicit user action
  would make the feature manual and easy to forget; not pushing at all forfeits
  the feature.

## Architecture

### Routing

`BandMapWindow` owns its own `QTcpSocket` to the DX cluster
(`not1mm/bandmap.py:460`) and ingests spots entirely internally via `receive()`
(`:910`) calling `self.spots.addspot(spot)` (`:951`). **`__main__` never sees a
spot.** It also imports no CAT class — it is fully decoupled from rig control.

`MainWindow`, however, owns both `self.bandmap_window` (`__main__.py:752`) and
`self.rig_control`. It is the natural junction, and bandmap already talks to it
by signal: `message`, `cluster_expire`, and `bandmapwindow_closed` are all
connected at `__main__.py:758-761`.

The design follows that established pattern exactly:

1. `BandMapWindow` gains `spot_added = pyqtSignal(dict)`.
2. `__main__` connects it to a handler alongside the existing three.
3. The handler forwards to the rig, guarded by
   `getattr(self.rig_control.cat, "send_spot", None)` — the same guard style the
   phase 1 shutdown hook uses at `not1mm/radio.py:115`. flrig, rigctld, and
   fake have no `send_spot`, so they no-op with no backend check.

Bandmap remains ignorant of CAT. Nothing in `bandmap.py` imports rig control.

### Approaches considered

- **A. Signal from BandMapWindow — CHOSEN.** Matches the three existing
  bandmap-to-main signals. One signal, one connect line, one handler.
- **B. Inject a `rig_control` reference into `BandMapWindow`.** Fewer hops, but
  breaks the decoupling, and `setup_rig_control` rebuilds `self.rig_control` on
  every settings save — the injected reference would go stale and fail
  silently. Rejected.
- **C. Push from inside `Database.addspot()`.** The lowest funnel, catching
  every path. But `Database` is a pure in-memory store with no Qt and no
  outside knowledge; threading a callback through it muddies a clean, testable
  class. Rejected.

### Emit points

Two, deliberately:

1. **On cluster ingest** — in `receive()` at `bandmap.py:951`, immediately after
   `self.spots.addspot(spot)` succeeds, when the spot falls within the current
   band.
2. **On band change** — in `set_band()` (`bandmap.py:883-887`), inside the
   existing `if band != self.currentBand.name:` guard and after
   `self.currentBand` is reassigned, pushing that band's spots once via
   `self.spots.getspotsinband(self.currentBand.start, self.currentBand.end)`.
   The guard matters: `set_band` is called with the same band repeatedly, and
   emitting outside it would re-push on every call.

Ingest alone is insufficient: switching to 20m would show nothing until fresh
spots happened to arrive, even though the bandmap already holds plenty.

Hooking `update_stations()` (`:796`) was considered instead, since it already
computes in-band spots via `getspotsinband()`. Rejected: it runs on every
redraw, so it would need an "already pushed" set to avoid re-sending constantly,
and would add per-redraw cost to a hot path. Two bounded hooks are simpler.

## Scope: current band only

Only spots inside the current band are pushed. The panadapter can only display
the slice it is tuned to, so off-band spots are invisible — sending them is
wasted traffic on a websocket shared with VFO polling and CW keying.

## Data mapping

New method `TciCAT.send_spot(callsign, mode, freq_hz, color, text) -> bool`,
gated on `self.online` exactly like every other setter, building the command
via the existing `build_command()` from `not1mm/lib/tci_protocol.py`.

| TCI field | Source | Note |
|---|---|---|
| callsign | `spot["callsign"]` | |
| modulation | current radio mode, via `not1mm_mode_to_tci()` | See below |
| frequency | `int(round(spot["freq"] * 1000))` | **Unit change** — see below |
| color | a module constant, `-65536` | Not user-configurable |
| text | `spot["comment"]`, truncated to 30 chars | Where contest exchange info lives |

**Frequency is a unit conversion, not a copy.** The bandmap stores kHz as a
float (`spot["freq"] = float(freq)` at `bandmap.py:951`, e.g. `14030.0`); TCI
requires integer Hz. Getting this wrong puts spots 1000× off frequency.

**Cluster spots carry no mode.** `receive()` builds the spot dict with only
`ts`, `callsign`, `spotter`, `comment`, and `freq` — it never sets `mode`. The
spot's own mode is therefore unavailable. The current radio mode is used
instead, which is reasonable given only current-band spots are pushed and the
operator is by definition on that band. If `spot` does carry a `mode` key from
another path, prefer it.

**Colour is a constant, not a setting.** Both encodings work, so there is no
compatibility reason to expose a choice, and a new preference key would need UI,
load/save wiring, and a migration for no clear benefit.

### Delimiter sanitizing is mandatory

TCI frames are **comma-delimited and semicolon-terminated**, and the spot text
comes from a DX cluster comment — arbitrary free text written by strangers.
Passing it through unsanitized is a frame-injection bug, not a cosmetic one:

- A comment containing `,` (very common — `CQ TEST, 599`) injects a spurious
  field, shifting every later argument.
- A comment containing `;` terminates the frame early, so the remainder is
  parsed by the radio as a new command.

`send_spot` must strip or replace `,` and `;` in **both** the callsign and the
text before building the command. Replacing each with a space is sufficient;
the text is a display label with no structural meaning. This is a required
behavior with its own test, not a nicety.

## Error handling

- **Non-TCI backends no-op.** The `getattr` guard means no code path exists for
  flrig, rigctld, or fake. They are not modified.
- **Offline is a no-op.** `send_spot` returns `False` without sending when the
  client is not `online`, matching every other `TciCAT` setter.
- **Failures are silent by design.** Spot push is cosmetic; a dropped spot must
  never interrupt logging or rig control. Log at debug, never raise.
- **Malformed spots are skipped.** A spot lacking `callsign` or `freq`, or whose
  `freq` will not convert to a number, is dropped with a debug log rather than
  sent as a malformed frame.

## Testing

**Unit, no socket required:**

- `TciCAT.send_spot` builds the exact expected command string
- `send_spot` sends nothing while offline
- kHz-to-Hz conversion, including rounding of fractional kHz
- Spots missing `callsign`, missing `freq`, or with a non-numeric `freq` are
  dropped rather than sent
- Comment truncation at 30 chars
- **Delimiter sanitizing**: a comment containing `,` or `;`, and a callsign
  containing either, produce a well-formed single frame with the correct field
  count — this is the frame-injection guard and needs an explicit test

**Bandmap signal, no socket required:**

- `spot_added` fires for a spot inside the current band
- `spot_added` does NOT fire for a spot outside it
- Changing band emits for the new band's existing spots

**Integration:** `not1mm/testing/faketci.py` already echoes received commands,
so it can assert the `spot:` frame arrives correctly formed.

**Manual:** confirm spots appear on the live AetherSDR panadapter at the right
frequencies, and that changing band populates the new band.

## Out of scope

- Retracting or updating spots — the radio does not support it
- Pushing spots for bands other than the current one
- Dupe-aware filtering (pushing only unworked stations); it depends on
  contest-specific logic and is a materially bigger job
- A user-configurable spot colour
- Any change to flrig, rigctld, or the fake backend
