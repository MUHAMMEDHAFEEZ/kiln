"""Scripts widgets that start and stop programs: state, Start / Stop, the log, check commands, a restart of Kiln.
Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class ServiceCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        os.makedirs(f"{t}/conf")
        os.makedirs(f"{t}/cache")
        os.environ.update(KILN_CONFIG=f"{t}/conf", KILN_CACHE=f"{t}/cache")
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, Gtk
            sys.path.insert(0, ROOT)
            import app
            import svc
            import widgets as WG
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.Gtk, cls.app, cls.WG, cls.SV = Gtk, app, WG, svc
        cls.saved = (app.CACHE,)
        app.CACHE = f"{t}/cache"

    @classmethod
    def tearDownClass(cls):
        cls.app.CACHE = cls.saved[0]
        cls.tmp.cleanup()

    def setUp(self):
        self.store = self.WG.Widgets(f"{self.tmp.name}/w{id(self)}.json")
        self.cards = []

    def tearDown(self):
        for card in self.cards:                                  # never leave a program running after a test
            for i, st in list(card.svc.items()):
                if st.get("pid"):
                    self.SV.terminate(st["pid"], 1.0, st.get("started"))

    def pump(self, until=None, seconds=6):
        end = time.time() + seconds
        while time.time() < end:
            while self.Gtk.events_pending():
                self.Gtk.main_iteration()
            if until and until():
                return True
            time.sleep(0.02)
        return bool(until and until())

    def card(self, **item):
        item.setdefault("label", "Server")
        item.setdefault("mode", "service")
        wid = self.store.add("scripts", "Ops", items=[item])
        c = self.app.CustomCard(wid, self.store)
        self.cards.append(c)
        return c

    def draw(self, card):
        import cairo
        card._on_draw(None, cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, card.W, card.H)))

    def test_start_shows_it_running_with_a_log_and_stop_ends_it(self):
        c = self.card(command="echo booted; sleep 60")
        self.draw(c)
        self.assertFalse(c.svc_state(0)["up"])
        c.run_script(0)                                           # a click on a stopped program: Start
        st = c.svc_state(0)
        self.assertTrue(st["up"])
        pid = st["pid"]
        self.assertTrue(self.pump(lambda: "booted" in c.svc_log(0)))
        self.draw(c)
        c.run_script(0)                                           # a click on a running program: Stop
        self.assertEqual(st["busy"], "stopping")
        self.assertTrue(self.pump(lambda: not st["busy"]))
        self.assertFalse(st["up"])
        self.assertFalse(self.SV.alive(pid))
        self.draw(c)

    def test_a_stop_command_runs_before_the_signal(self):
        flag = f"{self.tmp.name}/stopped-{id(self)}"
        c = self.card(command="sleep 60", stop=f"touch {flag}")
        c.run_script(0)
        c.run_script(0)
        self.assertTrue(self.pump(lambda: not c.svc_state(0)["busy"]))
        self.assertTrue(os.path.exists(flag))

    def test_a_program_that_stops_on_its_own_is_noticed(self):
        c = self.card(command="echo bye; exit 3")
        c.run_script(0)
        self.assertTrue(self.pump(lambda: (c.svc_poll() or True) and not c.svc_state(0)["up"]))
        st = c.svc_state(0)
        self.assertEqual(st["exit"], 3)
        self.assertIn("bye", c.svc_log(0))
        self.draw(c)                                              # "stopped on its own (exit 3)", in red

    def test_a_program_started_before_a_restart_of_kiln_is_picked_up_again(self):
        first = self.card(command="sleep 60")
        first.run_script(0)
        pid = first.svc_state(0)["pid"]
        again = self.app.CustomCard(first.wid, self.store)       # what a restarted Kiln builds
        self.cards.append(again)
        st = again.svc_state(0)
        self.assertEqual((st["up"], st["pid"]), (True, pid))
        again.run_script(0)                                       # and it can stop it
        self.assertTrue(self.pump(lambda: not st["busy"]))
        self.assertFalse(self.SV.alive(pid))
        self.assertFalse(self.app.CustomCard(first.wid, self.store).svc_state(0)["up"])

    def test_a_service_with_a_check_follows_the_check(self):
        flag = f"{self.tmp.name}/up-{id(self)}"
        c = self.card(command=f"touch {flag}", stop=f"rm -f {flag}", check=f"test -f {flag}")
        c.svc_poll()
        self.assertTrue(self.pump(lambda: not c.svc_state(0)["checking"]))
        self.assertFalse(c.svc_state(0)["up"])
        c.run_script(0)                                           # the start command returns at once; the check says it is up
        self.assertTrue(self.pump(lambda: (c.svc_poll() or True) and c.svc_state(0)["up"]))
        self.assertTrue(os.path.exists(flag))
        c.run_script(0)
        self.assertTrue(self.pump(lambda: not c.svc_state(0)["busy"]))
        self.assertTrue(self.pump(lambda: (c.svc_poll() or True) and not c.svc_state(0)["up"]))
        self.assertFalse(os.path.exists(flag))

    def test_a_plain_entry_still_runs_once(self):
        c = self.card(mode="run", command="echo once")
        c.run_script(0)
        self.assertTrue(self.pump(lambda: (c.runs.get(0) or {}).get("state") == "ok"))
        self.assertEqual(c.runs[0]["out"], "once")
        self.assertNotIn(0, c.svc)


if __name__ == "__main__":
    unittest.main()
