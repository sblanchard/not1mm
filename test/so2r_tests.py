"""Tests for the WinKeyer serial driver, OTRSP and SO2R state."""

import time

import pytest

from not1mm.lib.otrsp import OTRSP, mode_letter
from not1mm.lib.so2r import SO2RController, cw_duration, cw_units, other_radio
from not1mm.lib.winkeyer import (
    WinKeyer,
    WinKeyerError,
    clean_text,
    is_speed_pot_byte,
    is_status_byte,
    pin_config,
)


class FakeSerial:
    """Records writes and serves canned reads, like serial.Serial."""

    def __init__(self, replies=b"", **kwargs):
        self.kwargs = kwargs
        self.replies = bytearray(replies)
        self.written = bytearray()
        self.dtr = None
        self.rts = None
        self.closed = False

    def write(self, data):
        self.written += data
        return len(data)

    def read(self, size=1):
        if not self.replies:
            time.sleep(0.01)
            return b""
        data = bytes(self.replies[:size])
        del self.replies[:size]
        return data

    def reset_input_buffer(self):
        ...

    def close(self):
        self.closed = True


def factory(port):
    def make(**kwargs):
        port.kwargs = kwargs
        return port

    return make


# WinKeyer


def test_status_and_pot_byte_tags():
    assert is_status_byte(0xC0)
    assert is_status_byte(0xC4)
    assert not is_status_byte(0x84)
    assert is_speed_pot_byte(0x80)
    assert is_speed_pot_byte(0x9F)
    assert not is_speed_pot_byte(0xC0)
    assert not is_speed_pot_byte(ord("A"))


def test_clean_text():
    assert clean_text("cq test k6gte|") == b"CQ TEST K6GTE|"
    assert clean_text("5nn\n{}~") == b"5NN"


@pytest.mark.parametrize(
    "radio, ptt, sidetone, swap, expected",
    [
        (1, True, True, False, 0x07),
        (2, True, True, False, 0x0B),
        (1, False, False, False, 0x04),
        (2, False, False, False, 0x08),
        (1, False, False, True, 0x08),
        (2, False, False, True, 0x04),
    ],
)
def test_pin_config(radio, ptt, sidetone, swap, expected):
    assert pin_config(radio, ptt, sidetone, swap) == expected


def test_winkeyer_open_handshake_and_commands():
    port = FakeSerial(replies=bytes([31]))
    keyer = WinKeyer("/dev/fake", serial_factory=factory(port))
    assert keyer.open() == 31
    try:
        assert port.kwargs["baudrate"] == 1200
        assert port.kwargs["stopbits"] == 2
        assert port.dtr is True and port.rts is False
        # three NOPs then Admin:Host Open
        assert bytes(port.written) == bytes([0x13, 0x13, 0x13, 0x00, 0x02])

        port.written.clear()
        keyer.configure(speed=28, lead_ms=50, tail_ms=120, speed_pot_min=10)
        assert bytes(port.written) == bytes(
            [0x05, 10, 30, 0, 0x04, 5, 12, 0x09, 0x07, 0x02, 28]
        )

        port.written.clear()
        keyer.select_radio(2)
        keyer.set_speed(150)
        assert bytes(port.written) == bytes([0x09, 0x0B, 0x02, 99])

        port.written.clear()
        keyer.send("tu")
        deadline = time.monotonic() + 2
        while len(port.written) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert bytes(port.written) == b"TU"
    finally:
        keyer.close()
    assert port.written.endswith(bytes([0x0A, 0x00, 0x03]))
    assert port.closed


def test_winkeyer_no_answer_raises():
    port = FakeSerial()
    keyer = WinKeyer("/dev/fake", serial_factory=factory(port))
    with pytest.raises(WinKeyerError):
        keyer.open(timeout=0.1)
    assert port.closed


def test_winkeyer_status_and_pot_callbacks():
    statuses, pots = [], []
    keyer = WinKeyer(
        "/dev/fake", on_status=statuses.append, on_speed_pot=pots.append
    )
    keyer.speed_pot_min = 10
    keyer.handle_byte(0xC4)
    assert keyer.busy
    keyer.handle_byte(0x80 | 15)
    keyer.handle_byte(ord("A"))  # echo, ignored
    keyer.handle_byte(0xC0)
    assert not keyer.busy
    assert statuses == [0xC4, 0xC0]
    assert pots == [25]


def test_winkeyer_clear_buffer_drops_queued_text():
    keyer = WinKeyer("/dev/fake")
    keyer.send("CQ CQ")
    assert keyer.busy
    keyer.clear_buffer()
    assert not keyer.busy


# OTRSP


def test_otrsp_commands():
    port = FakeSerial(replies=b"NAMESO2RDuino\r")
    otrsp = OTRSP("/dev/fake", serial_factory=factory(port))
    assert otrsp.open() == "SO2RDuino"
    assert port.kwargs["baudrate"] == 9600
    assert port.kwargs["stopbits"] == 1
    port.written.clear()
    otrsp.tx(2)
    otrsp.rx(1)
    otrsp.rx(2, stereo=True)
    otrsp.rx(1, stereo=True, reverse=True)
    otrsp.band(1, "40")
    otrsp.band(2, "2")
    otrsp.mode(1, "CW-U")
    otrsp.mode(2, "USB")
    assert port.written.decode().split("\r")[:-1] == [
        "TX2",
        "RX1",
        "RX2S",
        "RX1R",
        "BAND17",
        "BAND2144",
        "MODE1C",
        "MODE2U",
    ]


def test_otrsp_device_without_name():
    port = FakeSerial()
    otrsp = OTRSP("/dev/fake", serial_factory=factory(port))
    assert otrsp.open() == ""


@pytest.mark.parametrize(
    "mode, letter",
    [("CW", "C"), ("CWR", "C"), ("LSB", "L"), ("FM", "F"), ("RTTY", "R"), ("FT8", "X")],
)
def test_mode_letter(mode, letter):
    assert mode_letter(mode) == letter


# SO2R state


class Recorder:
    def __init__(self):
        self.calls = []

    def tx(self, radio):
        self.calls.append(f"TX{radio}")

    def rx(self, radio, stereo=False, reverse=False):
        self.calls.append(f"RX{radio}{'S' if stereo else ''}")


def test_so2r_controller():
    otrsp = Recorder()
    keyed = []
    so2r = SO2RController(otrsp, keyed.append)
    so2r.sync()
    assert otrsp.calls == ["TX1", "RX1"]
    assert keyed == [1]

    so2r.set_focus(2)
    assert so2r.set_tx(2) is True
    assert so2r.set_tx(2) is False
    so2r.toggle_stereo()
    assert otrsp.calls[2:] == ["RX2", "TX2", "RX2S"]
    assert keyed == [1, 2]

    # Dueling CQ listens to the radio that is not transmitting.
    so2r.set_stereo(False)
    so2r.set_listen(1)
    assert otrsp.calls[-1] == "RX1"
    so2r.set_listen(None)
    assert otrsp.calls[-1] == "RX2"
    # Unchanged RX is not resent.
    count = len(otrsp.calls)
    so2r.set_focus(2)
    assert len(otrsp.calls) == count


def test_so2r_without_hardware():
    so2r = SO2RController()
    so2r.sync()
    so2r.set_focus(2)
    assert so2r.rx_command() == "RX2"


def test_other_radio():
    assert other_radio(1) == 2
    assert other_radio(2) == 1


def test_cw_timing():
    # PARIS plus its word space is the 50 unit reference word.
    assert cw_units("PARIS ") == 50
    assert cw_duration("PARIS ", 20) == pytest.approx(3.0)
    assert cw_units("E") == 1
    assert cw_units("EE") == 5
    assert cw_duration("", 25) == 0


# OTRSP over TCP


class FakeTcp(FakeSerial):
    """A TcpPort stand in; fail_writes makes the next write raise."""

    instances = []

    def __init__(self, host, port):
        super().__init__(replies=b"NAMESM-TEST\r")
        self.host, self.port = host, port
        self.fail_writes = False
        FakeTcp.instances.append(self)

    def write(self, data):
        if self.fail_writes:
            raise ConnectionResetError("gone")
        return super().write(data)


def test_otrsp_tcp():
    FakeTcp.instances = []
    otrsp = OTRSP(host="192.0.2.1", tcp_port=12060, tcp_factory=FakeTcp)
    assert otrsp.open() == "SM-TEST"
    conn = FakeTcp.instances[0]
    assert (conn.host, conn.port) == ("192.0.2.1", 12060)
    conn.written.clear()
    otrsp.tx(2)
    otrsp.rx(1, stereo=True)
    assert conn.written.decode().split("\r")[:-1] == ["TX2", "RX1S"]


def test_otrsp_tcp_reconnects_and_replays_state():
    FakeTcp.instances = []
    otrsp = OTRSP(host="192.0.2.1", tcp_factory=FakeTcp)
    otrsp._spawn = lambda target: target()
    otrsp.open()
    otrsp.tx(2)
    otrsp.rx(2)
    FakeTcp.instances[0].fail_writes = True
    otrsp._last_attempt = 0  # pretend the reconnect interval has passed
    otrsp.mode(1, "CW")
    assert otrsp.is_open
    fresh = FakeTcp.instances[1]
    assert fresh.written.decode().split("\r")[:-1] == [
        "?NAME",
        "TX2",
        "RX2",
        "MODE1C",
    ]


def test_otrsp_tcp_reconnect_is_rate_limited():
    FakeTcp.instances = []
    otrsp = OTRSP(host="192.0.2.1", tcp_factory=FakeTcp)
    otrsp._spawn = lambda target: target()
    otrsp.open()
    FakeTcp.instances[0].fail_writes = True
    otrsp.tx(1)  # just opened, so no immediate reconnect
    assert len(FakeTcp.instances) == 1
    assert not otrsp.is_open


def test_otrsp_tcp_starts_offline_and_connects_later():
    def unreachable(host, port):
        raise ConnectionRefusedError("down")

    otrsp = OTRSP(host="192.0.2.1", tcp_factory=unreachable)
    otrsp._spawn = lambda target: target()
    with pytest.raises(OSError):
        otrsp.open()
    otrsp.tx(2)  # remembered while offline
    FakeTcp.instances = []
    otrsp._tcp_factory = FakeTcp
    otrsp._last_attempt = 0
    otrsp.ensure_connected()
    assert otrsp.is_open
    assert FakeTcp.instances[0].written.decode().split("\r")[:-1] == ["?NAME", "TX2"]
