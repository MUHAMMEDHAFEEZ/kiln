"""Programs a Scripts widget starts and stops: process groups, logs, remembering across restarts (no GTK needed)."""
import os
import stat
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import svc as V  # noqa: E402


def wait_for(cond, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


class SvcTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            try:
                os.killpg(p.pid, 9)
            except OSError:
                pass
            p.wait()

    def spawn(self, command, **kw):
        p = V.spawn(command, logfile=f"{self.tmp.name}/log/x.log", **kw)
        self.procs.append(p)
        return p

    def test_it_starts_in_its_own_group_and_writes_a_log(self):
        p = self.spawn("echo hello; echo oops >&2; sleep 30")
        log = f"{self.tmp.name}/log/x.log"
        self.assertTrue(wait_for(lambda: "oops" in V.tail(log)))
        self.assertEqual(V.tail(log).split(), ["hello", "oops"])
        self.assertEqual(os.getpgid(p.pid), p.pid)                           # a group of its own
        self.assertEqual(stat.S_IMODE(os.stat(log).st_mode), 0o600)
        self.assertTrue(V.alive(p.pid, V.start_time(p.pid)))

    def test_stopping_takes_down_what_it_started_too(self):
        p = self.spawn("sleep 300 & sleep 300 & wait")                       # a server with children
        time.sleep(0.5)
        kids = [int(x) for x in os.popen(f"pgrep -g {p.pid}").read().split() if int(x) != p.pid]
        self.assertGreaterEqual(len(kids), 2)
        started = V.start_time(p.pid)
        self.assertTrue(V.terminate(p.pid, grace=3, started=started))
        p.wait()
        self.assertTrue(wait_for(lambda: not any(V.alive(k) for k in kids)))   # the children are gone too

    def test_a_stubborn_process_is_killed_after_the_grace(self):
        p = self.spawn("trap '' TERM; while true; do sleep 1; done")        # ignores SIGTERM
        time.sleep(0.4)
        t = time.time()
        self.assertTrue(V.terminate(p.pid, grace=0.6))
        p.wait()
        self.assertLess(time.time() - t, 6)
        self.assertFalse(V.alive(p.pid))

    def test_a_reused_pid_is_not_the_same_process(self):
        p = self.spawn("sleep 30")
        real = V.start_time(p.pid)
        self.assertTrue(V.alive(p.pid, real))
        self.assertFalse(V.alive(p.pid, real + 1))                           # same pid, other start time: a different process
        self.assertFalse(V.alive(999999999))
        self.assertTrue(V.terminate(999999999))                              # nothing there: already stopped

    def test_a_finished_process_is_not_alive_even_before_it_is_reaped(self):
        p = self.spawn("true")
        time.sleep(0.4)
        self.assertFalse(V.alive(p.pid))                                     # a zombie does not count

    def test_it_is_remembered_across_a_restart_of_kiln(self):
        p = self.spawn("sleep 30")
        pidfile = f"{self.tmp.name}/svc/w1-0.json"
        V.remember(pidfile, p.pid, V.start_time(p.pid), "sleep 30")
        self.assertEqual(stat.S_IMODE(os.stat(pidfile).st_mode), 0o600)
        self.assertEqual(V.recall(pidfile, "sleep 30"), (p.pid, V.start_time(p.pid)))
        self.assertIsNone(V.recall(pidfile, "another command"))             # the widget was edited: not the same program
        V.terminate(p.pid, grace=2)
        p.wait()
        self.assertIsNone(V.recall(pidfile, "sleep 30"))                    # it stopped meanwhile
        V.forget(pidfile)
        V.forget(pidfile)                                                    # twice is fine
        self.assertIsNone(V.recall(pidfile))
        with open(pidfile, "w") as f:
            f.write("{nope")
        self.assertIsNone(V.recall(pidfile))

    def test_checks(self):
        self.assertTrue(V.run_check("true"))
        self.assertFalse(V.run_check("false"))
        self.assertFalse(V.run_check(""))
        self.assertFalse(V.run_check("sleep 5", timeout=0.3))
        self.assertTrue(V.run_check("test -d /tmp"))

    def test_logs_are_cut_back(self):
        log = f"{self.tmp.name}/big.log"
        with open(log, "w") as f:
            f.write("line\n" * 100_000)
        V.trim(log)
        self.assertLessEqual(os.path.getsize(log), V.LOG_LIMIT)
        self.assertEqual(V.tail(f"{self.tmp.name}/missing.log"), "")
        with open(log, "w") as f:
            f.write("\n".join(f"l{i}" for i in range(100)))
        self.assertEqual(V.tail(log, lines=3), "l97\nl98\nl99")


if __name__ == "__main__":
    unittest.main()
