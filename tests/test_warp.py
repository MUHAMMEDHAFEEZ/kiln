"""Cloudflare WARP (share/warp.py): reading warp-cli's answers and the switch, against a fake `warp-cli`."""
import os
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import warp as W  # noqa: E402

FAKE = r'''#!/usr/bin/python3
import os, sys
a = [x for x in sys.argv[1:] if x != "--accept-tos"]
state = open(os.environ["FAKE_STATE"]).read().strip() if os.path.exists(os.environ["FAKE_STATE"]) else "disconnected"
if a[:2] == ["-j", "status"]:
    print('{"status": "%s", "reason": "%s"}' % (state.capitalize(), "NetworkHealthy" if state == "connected" else "Manual"))
elif a == ["settings"]:
    print("(default)\tMode: Warp\n(network policy)\tWARP tunnel protocol: MASQUE")
elif a[:2] == ["registration", "show"]:
    print("Account type: Free\nLicense: SECRET-LICENSE-KEY")
elif a[:2] == ["tunnel", "stats"]:
    print("Tunnel Protocol: MASQUE (HTTPS via UDP)\nTime since last handshake: 343s\nSent: 2.1MB; Received: 1.3GB\n"
          "Estimated latency: 54ms\nEstimated loss: 0.29%")
elif a == ["connect"]:
    open(os.environ["FAKE_STATE"], "w").write("connected"); print("Success")
elif a == ["disconnect"]:
    open(os.environ["FAKE_STATE"], "w").write("disconnected"); print("Success")
'''


class ParseTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(W.parse_status('{"status": "Connected", "reason": "NetworkHealthy"}'), (W.CONNECTED, "NetworkHealthy"))
        self.assertEqual(W.parse_status('{"status": "Disconnected", "reason": "Manual"}')[0], W.DISCONNECTED)
        self.assertEqual(W.parse_status('{"status": "Connecting", "reason": "x"}')[0], W.CONNECTING)
        self.assertEqual(W.parse_status("Status update: Connected")[0], W.CONNECTED)
        self.assertEqual(W.parse_status("Status update: Disconnected. Reason: Manual Disconnection")[0], W.DISCONNECTED)
        self.assertEqual(W.parse_status("Error: Unable to connect to the CloudflareWARP daemon")[0], W.OFF)

    def test_stats(self):
        d = W.parse_stats("Tunnel Protocol: MASQUE (HTTPS via UDP)\nSent: 2.1MB; Received: 1.3GB\n"
                          "Estimated latency: 54ms\nEstimated loss: 0.29%\nTime since last handshake: 343s")
        self.assertEqual((d["latency"], d["loss"], d["protocol"], d["handshake"]), (54.0, 0.29, "MASQUE", 343))
        self.assertEqual(d["sent"], int(2.1 * 1024 ** 2))
        self.assertEqual(d["recv"], int(1.3 * 1024 ** 3))
        d = W.parse_stats("Sent: 1.6MB; Received: 531.4kB")                 # warp-cli writes a small k for kilobytes
        self.assertEqual((d["sent"], d["recv"]), (int(1.6 * 1024 ** 2), int(531.4 * 1024)))
        self.assertEqual(W.parse_stats("Sent: 12B; Received: 0B")["sent"], 12)
        self.assertEqual(W.parse_stats("nothing useful"), {})

    def test_mode_and_account_never_include_the_licence(self):
        self.assertEqual(W.parse_mode("(default)\tMode: WarpWithDnsOverHttps"), "WarpWithDnsOverHttps")
        self.assertEqual(W.parse_account("Account type: Free\nLicense: SECRET"), "Free")


class FakeCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        exe = f"{self.tmp.name}/warp-cli"
        with open(exe, "w") as f:
            f.write(FAKE)
        os.chmod(exe, os.stat(exe).st_mode | stat.S_IEXEC)
        os.environ["FAKE_STATE"] = f"{self.tmp.name}/state"
        self.old = W.BIN
        W.BIN = exe

    def tearDown(self):
        W.BIN = self.old
        self.tmp.cleanup()

    def test_snapshot_reads_everything_without_the_licence(self):
        W.connect()
        s = W.snapshot()
        self.assertEqual((s["state"], s["mode"], s["account"], s["latency"]), (W.CONNECTED, "Warp", "Free", 54.0))
        self.assertNotIn("SECRET", repr(s))
        W.disconnect()
        self.assertEqual(W.snapshot()["state"], W.DISCONNECTED)
        self.assertNotIn("latency", W.snapshot())                      # nothing to measure while it is off

    def test_missing_warp_is_reported(self):
        W.BIN = "/nonexistent/warp-cli"
        self.assertEqual(W.snapshot()["state"], W.MISSING)

    def test_the_poller_follows_a_click(self):
        w = W.Warp(interval=0.2).start()
        deadline = time.time() + 5
        while not w.now and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(w.now["state"], W.DISCONNECTED)
        w.toggle()
        self.assertEqual(w.busy, "connecting")
        deadline = time.time() + 8
        while w.now["state"] != W.CONNECTED and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(w.now["state"], W.CONNECTED)
        deadline = time.time() + 3
        while w.busy and time.time() < deadline:                     # the busy mark goes away once it is on
            time.sleep(0.05)
        self.assertEqual(w.busy, "")
        self.assertTrue(w.latency and w.latency[-1] == 54.0)


if __name__ == "__main__":
    unittest.main()
