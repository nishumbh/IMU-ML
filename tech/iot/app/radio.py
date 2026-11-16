"""Official MicroPython ESP32 network/espnow adapter, no third-party shim.

Requires firmware providing WLAN(STA_IF).active/disconnect/config(channel=),
ESPNow.active, set_pmk, add_peer(..., lmk=, channel=, encrypt=),
irecv(0), and send(mac, data, True). Unsupported APIs fail closed; there is
no plaintext fallback in iteration 2. Verify these on the installed C3 build.
Synchronous send waits for the firmware's bounded MAC ACK/retry operation;
Python cannot cancel that call. Its latency can exceed a 20 ms slot, which the
scheduler records as missed slots, never catch-up bursts. ACK != paired row.
"""
from .config import validate


class Radio:
    def __init__(self, config):
        import network
        import espnow
        self._esp = None
        self._wlan = None
        self.peer, pmk, lmk = validate(config, config.get("role"))
        try:
            self._wlan = network.WLAN(network.STA_IF)
            self._wlan.active(True)
            self._wlan.disconnect()
            self._wlan.config(channel=config["channel"])
            self._esp = espnow.ESPNow()
            self._esp.active(True)
            if config["iteration"] == 2:
                self._esp.set_pmk(pmk)
                self._esp.add_peer(self.peer, lmk=lmk,
                                   channel=config["channel"], encrypt=True)
            else:
                self._esp.add_peer(self.peer, channel=config["channel"],
                                   encrypt=False)
        except BaseException:
            self.close()
            raise

    def send(self, packet):
        return self._esp.send(self.peer, packet, True)

    def receive(self):
        # irecv reuses internal buffers. Callers decode before the next receive.
        sender, packet = self._esp.irecv(0)
        if sender is None:
            return None
        return bytes(sender), packet

    def close(self):
        try:
            if self._esp is not None:
                self._esp.active(False)
        finally:
            if self._wlan is not None:
                self._wlan.active(False)
