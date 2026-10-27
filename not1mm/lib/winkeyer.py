"""
K1EL WinKeyer (WK2/WK3) driver for a directly attached serial / USB port.

Protocol reference: K1EL "WinKeyer3 IC Interface & Operation Manual" rev 1.3
and "Winkeyer2 IC Interface & Operation Manual" v23.

Serial framing is 1200 baud, 8 data bits, no parity, 2 stop bits.
Bytes coming back from the keyer are tagged:
    0b110xxxxx  status byte
    0b10xxxxxx  speed pot byte (value is offset from the pot minimum)
    otherwise   echo back of a sent character

GPL V3
"""

import logging
import queue
import threading
import time

import serial

logger = logging.getLogger("winkeyer")

# Immediate commands
CMD_ADMIN = 0x00
CMD_SPEED = 0x02
CMD_PTT_LEAD_TAIL = 0x04
CMD_SETUP_SPEED_POT = 0x05
CMD_GET_SPEED_POT = 0x07
CMD_PINCONFIG = 0x09
CMD_CLEAR_BUFFER = 0x0A
CMD_NOP = 0x13
CMD_GET_STATUS = 0x15

# Admin sub commands
ADMIN_HOST_OPEN = 0x02
ADMIN_HOST_CLOSE = 0x03

# Status byte bits
STATUS_XOFF = 0x01
STATUS_BREAKIN = 0x02
STATUS_BUSY = 0x04

# PINCFG low nibble. The datasheet's PINCFG table labels bit 3 as KeyOut 1,
# but working SO2R loggers (so2sdr) key radio 1 with bit 2 and radio 2 with
# bit 3, which matches the WK1 layout where bit 2 was the primary key pin.
# The swap_outputs option flips this for keyers wired the other way.
PIN_PTT = 0x01
PIN_SIDETONE = 0x02
PIN_KEY_RADIO_1 = 0x04
PIN_KEY_RADIO_2 = 0x08

# Characters the keyer will send. Lower case is folded to upper case before
# filtering; '|' is a half dit gap, the rest of 0x20-0x5F are letters,
# figures, punctuation and the prosign mappings in the datasheet.
_SENDABLE = {chr(c) for c in range(0x20, 0x60)} | {"|"}


class WinKeyerError(Exception):
    """Raised when the keyer can not be opened."""


def is_status_byte(value: int) -> bool:
    """Status bytes carry the tag 0b110 in the three most significant bits."""
    return value & 0xE0 == 0xC0


def is_speed_pot_byte(value: int) -> bool:
    """Speed pot bytes carry the tag 0b10 in the two most significant bits."""
    return value & 0xC0 == 0x80


def clean_text(text: str) -> bytes:
    """Upper case the text and drop anything the keyer can not send."""
    return "".join(c for c in text.upper() if c in _SENDABLE).encode("ascii")


def set_modem_lines(port, dtr: bool, rts: bool) -> None:
    """
    Set DTR / RTS where the port has them. Virtual and network bridged
    ports often do not, which is fine for a keyer powered over USB.
    """
    for name, value in (("dtr", dtr), ("rts", rts)):
        try:
            setattr(port, name, value)
        except (serial.SerialException, OSError) as err:
            logger.debug("Can not set %s on %s: %s", name, port, err)


def pin_config(radio: int, ptt: bool, sidetone: bool, swap_outputs: bool) -> int:
    """Build the PINCFG byte that keys the given radio (1 or 2)."""
    use_second = (radio == 2) != swap_outputs
    value = PIN_KEY_RADIO_2 if use_second else PIN_KEY_RADIO_1
    if ptt:
        value |= PIN_PTT
    if sidetone:
        value |= PIN_SIDETONE
    return value


class WinKeyer:
    """
    Talks to a WinKeyer over a serial port.

    Text is queued and written by a background thread which pauses while the
    keyer reports XOFF (input buffer more than 2/3 full). Immediate commands
    are written straight away. Status and speed pot bytes are reported through
    the on_status / on_speed_pot callbacks, which run on the reader thread.
    """

    def __init__(
        self,
        device: str,
        on_status=None,
        on_speed_pot=None,
        serial_factory=serial.Serial,
    ) -> None:
        self.device = device
        self.on_status = on_status
        self.on_speed_pot = on_speed_pot
        self._serial_factory = serial_factory
        self._port = None
        self._write_lock = threading.Lock()
        self._outbound = queue.Queue()
        self._stop = threading.Event()
        self._threads = []
        self.version = None
        self.status = 0
        self.speed_pot_min = 10
        self.radio = 1
        self.ptt = True
        self.sidetone = True
        self.swap_outputs = False

    @property
    def is_open(self) -> bool:
        return self._port is not None

    @property
    def busy(self) -> bool:
        """True while Morse is being sent or text is still waiting to go out."""
        return bool(self.status & STATUS_BUSY) or not self._outbound.empty()

    @property
    def xoff(self) -> bool:
        return bool(self.status & STATUS_XOFF)

    def open(self, timeout: float = 2.0) -> int:
        """Open the port, put the keyer in host mode and return its version."""
        try:
            port = self._serial_factory(
                port=self.device,
                baudrate=1200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_TWO,
                timeout=0.1,
                write_timeout=1,
            )
        except (serial.SerialException, OSError) as err:
            raise WinKeyerError(f"Can not open {self.device}: {err}") from err
        # Same line states as so2sdr and other loggers: DTR on, RTS off.
        set_modem_lines(port, dtr=True, rts=False)
        version = None
        try:
            # NOPs resync a keyer left mid command by a previous program.
            port.write(bytes([CMD_NOP, CMD_NOP, CMD_NOP]))
            port.reset_input_buffer()
            port.write(bytes([CMD_ADMIN, ADMIN_HOST_OPEN]))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                data = port.read(1)
                if data:
                    version = data[0]
                    break
        except (serial.SerialException, OSError) as err:
            port.close()
            raise WinKeyerError(f"WinKeyer on {self.device}: {err}") from err
        if version is None:
            port.close()
            raise WinKeyerError(f"No answer from WinKeyer on {self.device}")
        self.version = version
        self._port = port
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._reader, name="wk-reader", daemon=True),
            threading.Thread(target=self._writer, name="wk-writer", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        logger.info("WinKeyer on %s version %s", self.device, version)
        return version

    def close(self) -> None:
        """Clear the buffer, return the keyer to standalone mode and close."""
        if self._port is None:
            return
        self._drain_outbound()
        try:
            self._write(bytes([CMD_CLEAR_BUFFER, CMD_ADMIN, ADMIN_HOST_CLOSE]))
        except (serial.SerialException, OSError) as err:
            logger.warning("WinKeyer close: %s", err)
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1)
        self._threads = []
        try:
            self._port.close()
        except (serial.SerialException, OSError):
            ...
        self._port = None

    def configure(
        self,
        speed: int,
        ptt: bool = True,
        sidetone: bool = True,
        swap_outputs: bool = False,
        lead_ms: int = 0,
        tail_ms: int = 0,
        speed_pot_min: int = 10,
        speed_pot_range: int = 30,
    ) -> None:
        """Send the session settings after open()."""
        self.ptt = ptt
        self.sidetone = sidetone
        self.swap_outputs = swap_outputs
        self.speed_pot_min = max(5, min(99, speed_pot_min))
        # The pot is windowed into at most 32 steps.
        pot_range = max(0, min(31, speed_pot_range))
        self._write(
            bytes([CMD_SETUP_SPEED_POT, self.speed_pot_min, pot_range, 0])
        )
        self.set_ptt_timing(lead_ms, tail_ms)
        self.select_radio(self.radio)
        self.set_speed(speed)

    def set_speed(self, wpm: int) -> None:
        """Immediate speed change. 0 would hand control to the pot, so clamp."""
        self._write(bytes([CMD_SPEED, max(5, min(99, int(wpm)))]))

    def set_ptt_timing(self, lead_ms: int, tail_ms: int) -> None:
        """Lead in and tail are sent in 10 ms units, 0-250."""

        def units(ms):
            return max(0, min(250, int(ms) // 10))

        self._write(bytes([CMD_PTT_LEAD_TAIL, units(lead_ms), units(tail_ms)]))

    def select_radio(self, radio: int) -> None:
        """Route KEY (and PTT) to radio 1 or 2 through the PINCFG register."""
        self.radio = radio
        self._write(
            bytes(
                [
                    CMD_PINCONFIG,
                    pin_config(radio, self.ptt, self.sidetone, self.swap_outputs),
                ]
            )
        )

    def send(self, text: str) -> None:
        """Queue text for sending."""
        for byte in clean_text(text):
            self._outbound.put(byte)

    def clear_buffer(self) -> None:
        """Abort sending, including anything not yet passed to the keyer."""
        self._drain_outbound()
        self._write(bytes([CMD_CLEAR_BUFFER]))

    def request_status(self) -> None:
        self._write(bytes([CMD_GET_STATUS]))

    def _drain_outbound(self) -> None:
        while True:
            try:
                self._outbound.get_nowait()
            except queue.Empty:
                return

    def _write(self, data: bytes) -> None:
        if self._port is None:
            return
        with self._write_lock:
            self._port.write(data)

    def _writer(self) -> None:
        while not self._stop.is_set():
            if self.xoff:
                time.sleep(0.05)
                continue
            try:
                byte = self._outbound.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._write(bytes([byte]))
            except (serial.SerialException, OSError) as err:
                logger.warning("WinKeyer write: %s", err)
                time.sleep(0.5)

    def _reader(self) -> None:
        while not self._stop.is_set():
            try:
                data = self._port.read(1)
            except (serial.SerialException, OSError, TypeError, AttributeError) as err:
                logger.warning("WinKeyer read: %s", err)
                time.sleep(0.5)
                continue
            if data:
                self.handle_byte(data[0])

    def handle_byte(self, value: int) -> None:
        """Process one byte returned by the keyer."""
        if is_status_byte(value):
            self.status = value
            if self.on_status:
                self.on_status(value)
        elif is_speed_pot_byte(value):
            if self.on_speed_pot:
                self.on_speed_pot(self.speed_pot_min + (value & 0x3F))
        # Echo back bytes are not used.
