"""
K6GTE, CW interface abstraction
Email: michael.bridak@gmail.com
GPL V3
"""

import logging
import socket
from xmlrpc.client import Error, ServerProxy

from not1mm.lib.winkeyer import WinKeyer, WinKeyerError

if __name__ == "__main__":
    print("I'm not the program you are looking for.")

logger = logging.getLogger("cwinterface")


class CW:
    """

    An interface to cwdaemon, PyWinkeyerSerial and a WinKeyer on a serial port

    servertype: int 1=cwdaemon, 2=pywinkeyer, 3=rigctld, 4=WinKeyer serial

    For servertype 4 host is the serial device and port is ignored.
    """

    def __init__(
        self,
        servertype: int,
        host: str,
        port: int,
        on_status=None,
        on_speed_pot=None,
    ) -> None:
        self.servertype = servertype
        self.cat = None
        self.host = host
        self.port = port
        self.speed = 20
        self.winkeyer_functions = []
        self.winkeyer = None
        self.error = ""
        if self.servertype == 4:
            keyer = WinKeyer(host, on_status=on_status, on_speed_pot=on_speed_pot)
            try:
                keyer.open()
                self.winkeyer = keyer
            except WinKeyerError as err:
                self.error = str(err)
                logger.critical(self.error)
            return
        if self.servertype == 2:
            if not self.__check_sane_ip(self.host):
                logger.critical(f"Bad IP: {self.host}")
                return
            with ServerProxy(f"http://{self.host}:{self.port}") as proxy:
                try:
                    self.winkeyer_functions = proxy.system.listMethods()
                    logger.debug("%s", f"{self.winkeyer_functions}")
                except Error as exception:
                    logger.info(
                        "http://%s:%s, xmlrpc error: %s",
                        self.host,
                        self.port,
                        exception,
                    )
                except ConnectionRefusedError:
                    logger.info(
                        "http://%s:%s, xmlrpc Connection Refused", self.host, self.port
                    )

    def __check_sane_ip(self, ip: str) -> bool:
        """check if IP address look normal"""
        x = ip.split(".")
        if len(x) != 4:
            return False
        for y in x:
            if not y.isnumeric():
                return False
        return True

    def sendcw(self, texttosend):
        """sends cw to k1el"""
        logger.debug(f"{texttosend=} {self.servertype=}")
        if texttosend:
            if self.servertype == 4:
                if self.winkeyer:
                    self.winkeyer.send(texttosend)
            elif self.servertype == 2:
                self._sendcw_xmlrpc(texttosend)
            elif self.servertype == 1:
                self._sendcw_udp(texttosend)
            elif self.servertype == 3:
                self._sendcwcat(texttosend)

    def _sendcw_xmlrpc(self, texttosend):
        """sends cw to xmlrpc"""
        logger.debug("xmlrpc: %s", texttosend)
        if texttosend and self.__check_sane_ip(self.host):
            with ServerProxy(f"http://{self.host}:{self.port}") as proxy:
                try:
                    proxy.k1elsendstring(texttosend)
                except Error as exception:
                    logger.debug(
                        "http://%s:%s, xmlrpc error: %s",
                        self.host,
                        self.port,
                        exception,
                    )
                except ConnectionRefusedError:
                    logger.debug(
                        "http://%s:%s, xmlrpc Connection Refused", self.host, self.port
                    )
        else:
            logger.critical(f"Bad IP: {self.host}")

    def _sendcw_udp(self, texttosend):
        """send cw to udp port"""
        logger.debug("UDP: %s", texttosend)
        if texttosend and self.__check_sane_ip(self.host):
            server_address_port = (self.host, self.port)
            # bufferSize          = 1024
            udp_client_socket = socket.socket(
                family=socket.AF_INET, type=socket.SOCK_DGRAM
            )
            try:
                udp_client_socket.sendto(
                    bytes(texttosend, "utf-8"), server_address_port
                )
            except socket.gaierror:
                ...
        else:
            logger.critical(f"Bad IP: {self.host.encode()}")

    def _sendcwcat(self, texttosend):
        """..."""

    def configure_winkeyer(self, **settings) -> None:
        """Send session settings to a serial WinKeyer (see WinKeyer.configure)."""
        if self.winkeyer:
            self.winkeyer.configure(speed=self.speed, **settings)

    def select_key_output(self, radio: int) -> None:
        """Key radio 1 or 2 from a serial WinKeyer's two key outputs."""
        if self.winkeyer:
            self.winkeyer.select_radio(radio)

    @property
    def busy(self):
        """True/False when the keyer reports its state, None when unknown."""
        if self.winkeyer:
            return self.winkeyer.busy
        return None

    def close(self) -> None:
        """Release a serial WinKeyer. Network keyers hold nothing open."""
        if self.winkeyer:
            self.winkeyer.close()
            self.winkeyer = None

    def set_winkeyer_speed(self, speed):
        """doc"""
        if self.servertype == 4:
            if self.winkeyer:
                self.winkeyer.set_speed(speed)
            return
        if not self.__check_sane_ip(self.host):
            logger.critical(f"Bad IP: {self.host}")
            return
        with ServerProxy(f"http://{self.host}:{self.port}") as proxy:
            try:
                if "setspeed" in self.winkeyer_functions:
                    proxy.setspeed(speed)
            except Error as exception:
                logger.info(
                    "http://%s:%s, xmlrpc error: %s", self.host, self.port, exception
                )
            except ConnectionRefusedError:
                logger.info(
                    "http://%s:%s, xmlrpc Connection Refused", self.host, self.port
                )

    def winkeyer_stop(self):
        """doc"""
        if self.servertype == 4:
            if self.winkeyer:
                self.winkeyer.clear_buffer()
            return
        if not self.__check_sane_ip(self.host):
            logger.critical(f"Bad IP: {self.host}")
            return
        with ServerProxy(f"http://{self.host}:{self.port}") as proxy:
            try:
                if "clearbuffer" in self.winkeyer_functions:
                    proxy.clearbuffer()
            except Error as exception:
                logger.info(
                    "http://%s:%s, xmlrpc error: %s", self.host, self.port, exception
                )
            except ConnectionRefusedError:
                logger.info(
                    "http://%s:%s, xmlrpc Connection Refused", self.host, self.port
                )
