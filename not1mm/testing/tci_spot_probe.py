"""
Send a single TCI spot: command to a live radio and report what comes back.

Read-mostly probe: the ONLY command it ever transmits is `spot:`. It never
touches vfo, modulation, trx, or anything that could key the transmitter.

Usage: python spot_probe.py [host] [port]
"""

import sys

from PyQt6.QtCore import QCoreApplication, QTimer, QUrl
from PyQt6.QtWebSockets import QWebSocket

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 50001

app = QCoreApplication([])
socket = QWebSocket()
state = {"vfo": None, "mode": "cw", "ready": False, "sent": False}


def send(cmd: str) -> None:
    print(f">>> {cmd}", flush=True)
    socket.sendTextMessage(cmd)


def fire_spot() -> None:
    """Place spots 2 and 4 kHz above the current dial, so they land on screen."""
    if state["sent"] or state["vfo"] is None:
        return
    state["sent"] = True
    base = state["vfo"]
    # Two spots, two colour encodings -- if only one renders, the colour format
    # is the discriminator. Signed-int and hex are the two plausible encodings.
    send(f"spot:TE1ST,{state['mode']},{base + 2000},-65536,PROBE RED;")
    send(f"spot:TE2ST,{state['mode']},{base + 4000},0xFF0000,PROBE HEX;")
    print("\n--- spots sent; look at the AetherSDR panadapter now ---\n", flush=True)
    QTimer.singleShot(12000, app.quit)


def on_text(payload: str) -> None:
    for frame in [f + ";" for f in payload.split(";") if f.strip()]:
        name = frame.split(":")[0].split(";")[0].lower()
        # Suppress the s-meter firehose; show everything else.
        if name in ("rx_smeter", "tx_smeter"):
            continue
        if name in ("spot", "spot_delete", "error") or state["ready"]:
            print(f"<<< {frame}", flush=True)
        if name == "vfo":
            parts = frame[:-1].split(":")[1].split(",")
            if len(parts) >= 3 and parts[0] == "0" and parts[1] == "0":
                state["vfo"] = int(parts[2])
        elif name == "modulation":
            parts = frame[:-1].split(":")[1].split(",")
            if len(parts) >= 2 and parts[0] == "0":
                state["mode"] = parts[1]
        elif name == "ready":
            state["ready"] = True
            print(f"--- handshake done, dial at {state['vfo']} Hz ---", flush=True)
            QTimer.singleShot(500, fire_spot)


socket.textMessageReceived.connect(on_text)
socket.errorOccurred.connect(lambda e: (print(f"!!! {e}", flush=True), app.quit()))
socket.open(QUrl(f"ws://{HOST}:{PORT}"))
QTimer.singleShot(25000, app.quit)
app.exec()
