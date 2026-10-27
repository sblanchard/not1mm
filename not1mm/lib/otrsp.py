"""
OTRSP (Open Two Radio Switching Protocol) SO2R controller.

Protocol reference: Paul Young K1XM, "Open Two Radio Switching Protocol"
version 0.9, http://www.k1xm.org/OTRSP/

Commands are ASCII terminated by a carriage return, sent at 9600 8N1 on a
serial port or over a TCP connection.
A control program needs only TX1, TX2, RX1, RX2, RX1S, RX2S, RX1R and RX2R;
BAND and MODE are optional and ignored by devices that do not implement them.

GPL V3
"""

import logging
import socket
import threading
import time

import serial

from not1mm.lib.winkeyer import set_modem_lines

logger = logging.getLogger("otrsp")

# Bottom of each band in MHz, as the BAND command expects.
BAND_BOTTOM_MHZ = {
    "160": "1.8",
    "80": "3.5",
    "60": "5.3",
    "40": "7",
    "30": "10.1",
    "20": "14",
    "17": "18.068",
    "15": "21",
    "12": "24.89",
    "10": "28",
    "6": "50",
    "4": "70",
    "2": "144",
    "1.25": "222",
    "70cm": "420",
    "33cm": "902",
    "23cm": "1240",
}


def mode_letter(mode: str) -> str:
    """Map a rig mode to the single letter used by the MODE command."""
    mode = mode.upper()
    if mode.startswith("CW"):
        return "C"
    if mode in ("USB", "LSB", "FM", "AM"):
        return mode[0]
    if mode.startswith("RTTY") or mode in ("FSK", "PKTLSB", "PKTUSB", "LSB-D", "USB-D"):
        return "R"
    return "X"


def rx_value(radio: int, stereo: bool, reverse: bool = False) -> str:
    """RX command value: radio number plus S for stereo or R for reverse stereo."""
    if not stereo:
        return str(radio)
    return f"{radio}{'R' if reverse else 'S'}"


class TcpPort:
    """
    The serial port calls OTRSP uses, over a TCP connection. OTRSP 0.9 allows
    TCP/IP as a transport; some SO2R controllers serve it on port 12060.
    """

    def __init__(self, host: str, port: int, timeout: float = 0.05) -> None:
        self._socket = socket.create_connection((host, port), timeout=1.0)
        self._socket.settimeout(timeout)

    def write(self, data: bytes) -> int:
        self._socket.sendall(data)
        return len(data)

    def read(self, size: int = 1) -> bytes:
        try:
            data = self._socket.recv(size)
        except TimeoutError:
            return b""
        if not data:
            raise ConnectionResetError("OTRSP connection closed by the device")
        return data

    def reset_input_buffer(self) -> None:
        while self.read(256):
            ...

    def close(self) -> None:
        self._socket.close()


class OTRSP:
    """
    Sends OTRSP commands to an SO2R device on a serial port, or over TCP when
    host is given.

    The latest TX, RX, BAND and MODE commands are remembered. If a TCP
    connection is down, commands are not sent; a background thread
    reconnects (at most every RECONNECT_INTERVAL seconds) and replays them,
    so the device catches up without the GUI ever waiting on the network.
    """

    RECONNECT_INTERVAL = 5.0

    def __init__(
        self,
        device: str = "",
        baudrate: int = 9600,
        serial_factory=serial.Serial,
        host: str = "",
        tcp_port: int = 12060,
        tcp_factory=TcpPort,
    ) -> None:
        self.device = device
        self.baudrate = baudrate
        self.host = host
        self.tcp_port = tcp_port
        self._serial_factory = serial_factory
        self._tcp_factory = tcp_factory
        self._port = None
        self._lock = threading.Lock()
        self._reconnecting = False
        self._last_attempt = 0.0
        self._state = {}
        self.name = ""
        # Replaced in tests to reconnect synchronously.
        self._spawn = lambda target: threading.Thread(
            target=target, name="otrsp-reconnect", daemon=True
        ).start()

    @property
    def is_open(self) -> bool:
        return self._port is not None

    @property
    def address(self) -> str:
        return f"{self.host}:{self.tcp_port}" if self.host else self.device

    def _connect(self):
        if self.host:
            return self._tcp_factory(self.host, self.tcp_port)
        port = self._serial_factory(
            port=self.device,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=1,
        )
        # RTS is PTT and DTR is CW in the spec; keep both released.
        set_modem_lines(port, dtr=False, rts=False)
        return port

    def open(self) -> str:
        """Connect, replay remembered state and return the ?NAME answer."""
        self._last_attempt = time.monotonic()
        port = self._connect()
        reply = b""
        try:
            port.reset_input_buffer()
            port.write(b"?NAME\r")
            # Devices must answer a query within 250 ms.
            deadline = time.monotonic() + 0.3
            while time.monotonic() < deadline and not reply.endswith(b"\r"):
                reply += port.read(40)
            for command in self._state.values():
                port.write(f"{command}\r".encode("utf-8"))
        except (serial.SerialException, OSError):
            try:
                port.close()
            except (serial.SerialException, OSError):
                ...
            raise
        with self._lock:
            self._port = port
        reply = reply.decode("utf-8", "replace").strip()
        self.name = reply[4:] if reply.startswith("NAME") else ""
        logger.info("OTRSP on %s name %r", self.address, self.name)
        return self.name

    def close(self) -> None:
        with self._lock:
            port, self._port = self._port, None
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError):
                ...

    def ensure_connected(self) -> None:
        """Start a background reconnect if a TCP link is down and one is due."""
        if self._port is not None or not self.host or self._reconnecting:
            return
        if time.monotonic() - self._last_attempt < self.RECONNECT_INTERVAL:
            return
        self._reconnecting = True
        self._last_attempt = time.monotonic()
        self._spawn(self._reconnect)

    def _reconnect(self) -> None:
        try:
            self.open()
        except (serial.SerialException, OSError) as err:
            logger.info("OTRSP reconnect to %s failed: %s", self.address, err)
        finally:
            self._reconnecting = False

    def send(self, command: str, remember: str = "") -> None:
        """Send a command. remember names state to replay after a reconnect."""
        if remember:
            self._state[remember] = command
        with self._lock:
            port = self._port
            if port is not None:
                logger.debug("OTRSP > %s", command)
                try:
                    port.write(f"{command}\r".encode("utf-8"))
                    return
                except (serial.SerialException, OSError) as err:
                    logger.warning("OTRSP write to %s: %s", self.address, err)
                    self._port = None
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError):
                ...
        self.ensure_connected()

    def tx(self, radio: int) -> None:
        self.send(f"TX{radio}", remember="TX")

    def rx(self, radio: int, stereo: bool = False, reverse: bool = False) -> None:
        self.send(f"RX{rx_value(radio, stereo, reverse)}", remember="RX")

    def band(self, radio: int, band: str) -> None:
        bottom = BAND_BOTTOM_MHZ.get(band)
        if bottom:
            self.send(f"BAND{radio}{bottom}", remember=f"BAND{radio}")

    def mode(self, radio: int, mode: str) -> None:
        self.send(f"MODE{radio}{mode_letter(mode)}", remember=f"MODE{radio}")
