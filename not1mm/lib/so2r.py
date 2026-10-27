"""
Single Operator Two Radio (SO2R) state.

Tracks which radio has keyboard focus, which has transmit focus and what the
headphones hear, and pushes changes out to the switching hardware: an OTRSP
device for TX/RX switching and, optionally, a WinKeyer whose two key outputs
are wired to the two radios.

GPL V3
"""

import logging

logger = logging.getLogger("so2r")

# Morse elements per character: "." is one unit on, "-" three units on.
MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..", "0": "-----", "1": ".----", "2": "..---",
    "3": "...--", "4": "....-", "5": ".....", "6": "-....", "7": "--...",
    "8": "---..", "9": "----.", "/": "-..-.", "?": "..--..", "=": "-...-",
    ".": ".-.-.-", ",": "--..--", "+": ".-.-.", "-": "-....-",
}  # fmt: skip


def other_radio(radio: int) -> int:
    return 2 if radio == 1 else 1


def cw_units(text: str) -> int:
    """
    Length of text in Morse units (PARIS is 50).
    Characters without a Morse code are skipped.
    """
    units = 0
    previous_was_char = False
    for char in text.upper():
        if char == " ":
            # Letter gaps are only added between letters, so a word space
            # is the full 7 units.
            units += 7
            previous_was_char = False
            continue
        elements = MORSE.get(char)
        if elements is None:
            continue
        if previous_was_char:
            units += 3
        units += sum(1 if e == "." else 3 for e in elements) + len(elements) - 1
        previous_was_char = True
    return units


def cw_duration(text: str, wpm: int) -> float:
    """Seconds needed to send text at wpm. One unit is 1.2 / wpm seconds."""
    if wpm <= 0:
        return 0.0
    return cw_units(text) * 1.2 / wpm


class SO2RController:
    """
    focus   radio the entry fields belong to (keyboard focus)
    tx      radio with transmit focus
    stereo  both receivers in the headphones, focus radio in the left ear
    listen  radio forced into the headphones (dueling CQ listens to the
            radio that is not transmitting), or None to follow focus
    """

    def __init__(self, otrsp=None, select_key_output=None) -> None:
        self.otrsp = otrsp
        self.select_key_output = select_key_output
        self.focus = 1
        self.tx = 1
        self.stereo = False
        self.listen = None
        self._sent_rx = None

    def sync(self) -> None:
        """Push the complete state to the hardware, e.g. after (re)connecting."""
        self._sent_rx = None
        self._send_tx()
        self.update_rx()

    def set_focus(self, radio: int) -> None:
        self.focus = radio
        self.update_rx()

    def set_tx(self, radio: int) -> bool:
        """Give transmit focus to radio. Returns True if it changed."""
        if radio == self.tx:
            return False
        self.tx = radio
        self._send_tx()
        return True

    def set_stereo(self, stereo: bool) -> None:
        self.stereo = stereo
        self.update_rx()

    def toggle_stereo(self) -> None:
        self.set_stereo(not self.stereo)

    def set_listen(self, radio) -> None:
        self.listen = radio
        self.update_rx()

    @property
    def rx_radio(self) -> int:
        return self.listen if self.listen else self.focus

    def rx_command(self) -> str:
        return f"RX{self.rx_radio}{'S' if self.stereo else ''}"

    def update_rx(self) -> None:
        command = self.rx_command()
        if command == self._sent_rx:
            return
        self._sent_rx = command
        if self.otrsp:
            self.otrsp.rx(self.rx_radio, stereo=self.stereo)

    def _send_tx(self) -> None:
        if self.otrsp:
            self.otrsp.tx(self.tx)
        if self.select_key_output:
            self.select_key_output(self.tx)
