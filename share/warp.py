#!/usr/bin/python3
"""Cloudflare WARP (the VPN of 1.1.1.1) through its own `warp-cli`.

    warp.py                 print the state
    warp.py connect | disconnect | toggle

The desktop uses `Warp` (a background poller) for the work-bar chip and the VPN card. Nothing here reads or shows
the account licence key, and nothing is sent anywhere except by warp-cli itself."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time

BIN = shutil.which("warp-cli") or "/usr/bin/warp-cli"

# the states a chip can show
CONNECTED, CONNECTING, DISCONNECTED, OFF, MISSING = "connected", "connecting", "disconnected", "off", "missing"
LABEL = {CONNECTED: "Connected", CONNECTING: "Connecting…", DISCONNECTED: "Disconnected",
         OFF: "Service is not running", MISSING: "WARP is not installed"}


def available():
    return os.path.exists(BIN)


def _run(*args, timeout=6):
    """warp-cli's output (stdout + stderr) or None when it cannot be run."""
    try:
        r = subprocess.run([BIN, "--accept-tos", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return (r.stdout or "") + (r.stderr or "")


def parse_status(raw):
    """(state, reason) from `warp-cli -j status` (JSON), or from its plain text."""
    raw = raw or ""
    if "daemon" in raw.lower() and ("unable" in raw.lower() or "error" in raw.lower()):
        return OFF, ""
    try:
        d = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        word, reason = str(d.get("status", "")), str(d.get("reason", ""))
    except ValueError:
        m = re.search(r"(?:Status update:\s*)?(Connected|Connecting|Disconnected|Unable to connect|Registration[^.\n]*)", raw)
        word, reason = (m.group(1) if m else ""), ""
        r = re.search(r"Reason:\s*(.+)", raw)
        reason = r.group(1).strip() if r else reason
    w = word.lower()
    if w.startswith("connected"):
        return CONNECTED, reason
    if w.startswith("connecting") or w.startswith("pausing") or w.startswith("happy"):
        return CONNECTING, reason
    if w.startswith("disconnected") or w.startswith("unable") or w.startswith("registration"):
        return DISCONNECTED, reason or word
    return (OFF if not w else DISCONNECTED), reason


_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def _bytes(num, unit):
    return int(float(num) * _UNITS.get(unit.upper(), 1))


def parse_stats(raw):
    """latency (ms), loss (%), sent / received (bytes), protocol and seconds since the last handshake."""
    out = {}
    raw = raw or ""
    m = re.search(r"Estimated latency:\s*([\d.]+)\s*ms", raw)
    if m:
        out["latency"] = float(m.group(1))
    m = re.search(r"Estimated loss:\s*([\d.]+)\s*%", raw)
    if m:
        out["loss"] = float(m.group(1))
    m = re.search(r"Sent:\s*([\d.]+)\s*([KMGT]?B);\s*Received:\s*([\d.]+)\s*([KMGT]?B)", raw, re.I)
    if m:
        out["sent"], out["recv"] = _bytes(m.group(1), m.group(2)), _bytes(m.group(3), m.group(4))
    m = re.search(r"Tunnel Protocol:\s*(\w+)", raw)
    if m:
        out["protocol"] = m.group(1)
    m = re.search(r"Time since last handshake:\s*(\d+)\s*s", raw)
    if m:
        out["handshake"] = int(m.group(1))
    return out


def parse_mode(raw):
    """'Warp', 'DnsOverHttps', 'WarpWithDnsOverHttps', 'Proxy'... from `warp-cli settings`."""
    m = re.search(r"Mode:\s*(\S+)", raw or "")
    return m.group(1) if m else ""


def parse_account(raw):
    """'Free' / 'Plus' / a team name... never the licence key."""
    m = re.search(r"Account type:\s*(.+)", raw or "")
    return m.group(1).strip() if m else ""


MODE_NAMES = {"Warp": "WARP", "DnsOverHttps": "DNS only (1.1.1.1)", "WarpWithDnsOverHttps": "WARP + DoH",
              "DnsOverDot": "DNS over TLS", "WarpWithDnsOverDot": "WARP + DoT", "WarpProxy": "Proxy",
              "TunnelOnly": "Tunnel only", "Proxy": "Proxy"}


def snapshot(light=False):
    """One reading of everything. `light` reads the state only (cheap enough for a 3 s poll)."""
    if not available():
        return dict(state=MISSING, reason="", t=time.time())
    raw = _run("-j", "status")
    if raw is None:
        return dict(state=OFF, reason="", t=time.time())
    state, reason = parse_status(raw)
    snap = dict(state=state, reason=reason, t=time.time())
    if light or state == OFF:
        return snap
    snap["mode"] = parse_mode(_run("settings"))
    snap["account"] = parse_account(_run("registration", "show"))
    if state == CONNECTED:
        snap.update(parse_stats(_run("tunnel", "stats")))
    return snap


def connect():
    return _run("connect", timeout=15)


def disconnect():
    return _run("disconnect", timeout=15)


class Warp:
    """Polls warp-cli in a thread, so drawing never waits for it. `.now` is the latest reading,
    `.latency` the last readings of the round-trip time (for a graph)."""

    def __init__(self, interval=3.0, on_change=None):
        self.now = None
        self.latency = []
        self.busy = ""                          # "connecting" / "disconnecting" while a click is being carried out
        self.interval = interval
        self.on_change = on_change
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._started = False
        self._n = 0

    def start(self):
        if not self._started:
            self._started = True
            threading.Thread(target=self._loop, daemon=True).start()
        return self

    def poll_now(self):
        self._wake.set()

    def _loop(self):
        while True:
            self._n += 1
            full = self._n % 2 == 1 or (self.now or {}).get("state") != CONNECTED
            snap = snapshot(light=not full)
            with self._lock:
                old = self.now or {}
                if not full:                            # keep the details of the last full reading
                    for k in ("mode", "account", "latency", "loss", "sent", "recv", "protocol", "handshake"):
                        if k in old and snap["state"] == CONNECTED:
                            snap.setdefault(k, old[k])
                if "latency" in snap and snap["state"] == CONNECTED:
                    self.latency = (self.latency + [snap["latency"]])[-40:]
                elif snap["state"] != CONNECTED:
                    self.latency = []
                if self.busy and snap["state"] in ((CONNECTED,) if self.busy == "connecting" else (DISCONNECTED, OFF)):
                    self.busy = ""
                changed = (old.get("state"), old.get("reason")) != (snap["state"], snap["reason"])
                self.now = snap
            if self.on_change and (changed or full):
                try:
                    self.on_change()
                except Exception:                      # a draw callback must never kill the poller
                    pass
            self._wake.wait(self.interval if not self.busy else 1.0)
            self._wake.clear()

    def toggle(self):
        """Connect when it is off, disconnect when it is on (what a click on the chip does)."""
        state = (self.now or {}).get("state")
        if self.busy:
            return
        want = DISCONNECTED if state in (CONNECTED, CONNECTING) else CONNECTED
        self.busy = "disconnecting" if want == DISCONNECTED else "connecting"

        def work():
            (disconnect if want == DISCONNECTED else connect)()
            time.sleep(0.5)
            self.poll_now()
            time.sleep(12)                              # give up waiting for the state to follow
            self.busy = ""
        threading.Thread(target=work, daemon=True).start()
        self.poll_now()


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd in ("connect", "disconnect"):
        print(((connect() if cmd == "connect" else disconnect()) or "").strip())
        time.sleep(1)
    elif cmd == "toggle":
        s = snapshot(light=True)["state"]
        (disconnect if s in (CONNECTED, CONNECTING) else connect)()
        time.sleep(1)
    s = snapshot()
    print(LABEL.get(s["state"], s["state"]) + (f" ({s['reason']})" if s.get("reason") else ""))
    for k in ("mode", "account", "protocol", "latency", "loss", "sent", "recv"):
        if k in s:
            print(f"  {k}: {s[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
