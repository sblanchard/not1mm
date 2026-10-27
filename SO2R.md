# SO2R and WinKeyer serial

Not1MM can run two radios from one entry window (SO2R) and key CW from a K1EL
WinKeyer plugged straight into a serial / USB port, without pywinkeyer.

Everything is set in **Settings → SO2R**.

## WinKeyer on a serial port

Select **Use WinKeyer serial for CW** and pick the port. This replaces the
cwdaemon / pywinkeyer / CAT choice on the CW tab.

- **Key PTT output**: the keyer asserts PTT around CW.
- **Sidetone**: the keyer's own sidetone.
- **PTT lead in / tail**: sent to the keyer in 10 ms steps (0-2500 ms).
- **Swap key outputs**: Not1MM keys radio 1 on KEY1 and radio 2 on KEY2. If
  your keyer is wired the other way, tick this.

Moving the speed pot changes the CW speed in Not1MM. Esc clears the keyer's
buffer.

## SO2R

Tick **Enable SO2R**. Radio 1 uses the CAT settings on the Rig Control tab.
Radio 2 has its own CAT interface (rigctld, flrig or TCI), address and port. For two radios on
rigctld, start a second rigctld on another port (for example `-t 4533`).

### Switching hardware

- **OTRSP SO2R controller**: any box that speaks OTRSP (SO2RDuino, SO2R Mini,
  the YCCC SO2R box, PIEXX SO2RXlate, 4O3A Station Manager and others). Not1MM
  sends `TX1`/`TX2` for transmit focus and `RX1`, `RX2`, `RX1S`, `RX2S` for
  the headphones. It also sends `BAND` and `MODE` for each radio; boxes that
  don't support them ignore them.
  - **TCP/IP** (OTRSP 0.9): enter the controller's IP address and TCP port
    (12060 by default). If the link drops, or the box is off when Not1MM
    starts, Not1MM reconnects in the background every few seconds. It then
    resends the current TX, RX, band and mode, and the status bar shows
    OTRSP OFFLINE until it is back.
  - **Serial port**: pick the COM port; it runs at 9600 8N1.
- **Key radio 1 / radio 2 from the WinKeyer's two key outputs**: with a serial
  WinKeyer, KEY1 goes to radio 1 and KEY2 to radio 2. You can use this with or
  without an OTRSP box.

### How it works

There is one entry window. It always belongs to the radio with keyboard focus,
marked ▶ in the status bar. When focus moves, the callsign, exchange and
Run/S&P state of the radio losing focus are kept aside. The other radio's
fields come back as you left them.

Function keys and the CW entry field transmit on the focused radio. If CW is
still going out on the other radio, it is stopped first.

Shift+F1 auto CQ stays on the radio it was started on. You can work the other
radio meanwhile; typing there does not stop the auto CQ.

### Keys

These keys apply only while SO2R is enabled. A key you have bound to something
else in Edit Keys keeps your binding. The SO2R actions are also listed in Edit
Keys if you want different keys.

| Key | Action |
| --- | --- |
| Ctrl+Left | Focus radio 1 |
| Ctrl+Right | Focus radio 2 |
| Pause or `\` | Swap focus to the other radio |
| `` ` `` | Toggle stereo (both radios) / mono (focused radio) |
| Ctrl+B | Start / stop dueling CQ |
| Esc | Stop CW and dueling CQ |

### Dueling CQ

Dueling CQ sends your F1 CQ on one radio and then the other, back and forth.
While a radio transmits, the headphones are on the other radio. The pause
between CQs is the **Dueling CQ gap**.

With a serial WinKeyer, Not1MM knows exactly when each CQ has finished. With
other keyers the end is estimated from the text length and speed.

Dueling CQ runs in CW only. Esc, Ctrl+B or any function key you press stops
it.

## Protocol references

- K1EL, *WinKeyer3 IC Interface & Operation Manual*, rev 1.3, and *Winkeyer2
  IC Interface & Operation Manual*, v23. <https://k1elsystems.com/DocsC.html>
- Paul Young K1XM, *Open Two Radio Switching Protocol*, version 0.9.
  <http://www.k1xm.org/OTRSP/>
