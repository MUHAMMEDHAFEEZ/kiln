"""The desktop terminal with a real bash and VTE: grey suggestions, the keys that take them, search, zoom; and the
work bar's "+N more" and VPN chips. Skipped without a display."""
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..", "share")
os.environ.setdefault("CANBERRA_DRIVER", "null")


def spit(path, text):
    with open(path, "w") as f:
        f.write(text)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class TerminalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        t = cls.tmp.name
        os.makedirs(f"{t}/conf")
        os.makedirs(f"{t}/cache")
        os.environ.update(KILN_CONFIG=f"{t}/conf", KILN_CACHE=f"{t}/cache", HISTFILE=f"{t}/hist")
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("Gdk", "3.0")
            from gi.repository import Gdk, Gtk
            sys.path.insert(0, ROOT)
            import app
            import suggest
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.Gdk, cls.Gtk, cls.app, cls.S = Gdk, Gtk, app, suggest
        cls.saved_dirs = (app.APP, app.CACHE)             # app may have been imported by an earlier test
        app.APP, app.CACHE = f"{t}/conf", f"{t}/cache"
        # the prompt, plus what VTE's shell integration does: it writes its own window title at every prompt, after our hook
        spit(f"{app.CACHE}/term.bashrc", "PS1='\\u@\\h \\w\\n\\$ '\nPROMPT_COMMAND='printf \"\\033]0;user@host:~\\007\"'\n")
        spit(f"{t}/hist", "git status\ngit commit -m 'first'\ngit commit -m 'second'\nexport API_KEY=abc\n")
        spit(f"{t}/log", "")
        cls.saved = (app.SG_HISTORY, dict(app.SG_CFG))
        app.SG_HISTORY = suggest.History(f"{t}/log", f"{t}/hist")
        app.SG_CFG.update(enabled=True, ai="auto", auto_ai=False)
        cls.card = app.TerminalCard()
        cls.win = Gtk.Window()
        cls.win.set_default_size(900, 420)
        cls.win.add(cls.card)
        cls.win.show_all()
        cls.pump(1.5)

    @classmethod
    def tearDownClass(cls):
        app = cls.app
        app.APP, app.CACHE = cls.saved_dirs
        app.SG_HISTORY, saved_cfg = cls.saved
        app.SG_CFG.clear()
        app.SG_CFG.update(saved_cfg)
        cls.win.destroy()
        cls.tmp.cleanup()

    @classmethod
    def pump(cls, sec):
        end = time.time() + sec
        while time.time() < end:
            while cls.Gtk.events_pending():
                cls.Gtk.main_iteration()
            time.sleep(0.01)

    def setUp(self):
        self.term = self.card.term
        self.st = self.card._sg_state(self.term)
        self.clear_line()

    def clear_line(self):
        self.term.feed_child(b"\x15")
        self.pump(0.4)
        self.st["hidden_for"] = None

    def type_(self, s):
        self.term.feed_child(s.encode())
        self.pump(0.5)

    def key(self, keyval, state=0):
        ev = self.Gdk.Event.new(self.Gdk.EventType.KEY_PRESS)
        ev.keyval, ev.state = keyval, state
        return self.card._sg_key(self.term, ev)

    def test_a_prompt_is_detected_and_history_is_suggested(self):
        self.assertTrue(self.st["prompt"])
        self.type_("git com")
        self.assertEqual((self.st["full"], self.st["kind"]), ("git commit -m 'second'", "history"))

    def test_tab_and_right_arrow_take_it_and_escape_hides_it(self):
        self.type_("git com")
        self.assertTrue(self.key(self.Gdk.KEY_Tab))
        self.pump(0.5)
        self.assertEqual(self.st["typed"], "git commit -m 'second'")
        self.clear_line()
        self.type_("git sta")
        self.assertTrue(self.key(self.Gdk.KEY_Escape))
        self.assertEqual(self.st["full"], "")
        self.clear_line()
        self.type_("git ")
        self.assertTrue(self.key(self.Gdk.KEY_Right, self.Gdk.ModifierType.CONTROL_MASK))     # one word
        self.pump(0.5)
        self.assertEqual(self.st["typed"], "git commit")

    def test_tab_without_a_suggestion_is_left_to_bash(self):
        self.type_("zzzz-nothing")
        self.assertFalse(self.key(self.Gdk.KEY_Tab))

    def test_typing_along_keeps_the_suggestion(self):
        self.type_("git commit -m 'f")
        self.assertEqual(self.st["full"], "git commit -m 'first'")
        self.assertFalse(self.key(self.Gdk.KEY_i))              # the key still goes to the shell
        self.assertEqual(self.st["typed"], "git commit -m 'fi")
        self.assertEqual(self.st["full"], "git commit -m 'first'")

    def test_secrets_in_history_are_not_suggested(self):
        self.type_("export API")
        self.assertEqual(self.st["full"], "")

    def test_no_suggestion_while_a_program_runs(self):
        self.term.feed_child(b"sleep 1.5\n")
        self.pump(0.6)
        self.assertFalse(self.st["prompt"])
        self.assertEqual(self.st["full"], "")
        self.assertFalse(self.key(self.Gdk.KEY_Tab))
        self.pump(1.8)
        self.assertTrue(self.st["prompt"])

    def test_ctrl_v_pastes_arabic_text_on_any_layout(self):
        cb = self.Gtk.Clipboard.get(self.Gdk.SELECTION_CLIPBOARD)
        cb.set_text("\u0627\u0638\u0628\u0637 \u0627\u0644\u0633\u064a\u0631\u0641\u0631 echo", -1)
        self.pump(0.2)
        for keyval, hw in ((self.Gdk.KEY_v, 55), (self.Gdk.KEY_Arabic_ra, 55)):          # Latin V, and the same key on an Arabic layout
            self.clear_line()
            ev = self.Gdk.Event.new(self.Gdk.EventType.KEY_PRESS)
            ev.keyval, ev.state, ev.hardware_keycode = keyval, self.Gdk.ModifierType.CONTROL_MASK, hw
            self.assertTrue(self.card.on_key(self.term, ev))
            self.pump(0.6)
            self.assertEqual(self.card._sg_line(self.term), "\u0627\u0638\u0628\u0637 \u0627\u0644\u0633\u064a\u0631\u0641\u0631 echo")

    def test_pasting_copied_files_pastes_their_quoted_paths(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "my file.txt")
        spit(path, "x")
        uri = "file://" + path.replace(" ", "%20")

        class Clip:                                              # what the clipboard holds after "Copy" on a file in Files
            wait_is_text_available = staticmethod(lambda: False)
            wait_is_uris_available = staticmethod(lambda: True)
            wait_for_uris = staticmethod(lambda: [uri])
        real = self.Gtk.Clipboard.get
        self.Gtk.Clipboard.get = staticmethod(lambda *_a: Clip)
        self.addCleanup(lambda: setattr(self.Gtk.Clipboard, "get", real))
        self.clear_line()
        self.card.paste(self.term)
        self.pump(0.6)
        self.assertEqual(self.card._sg_line(self.term).strip(), "'" + path + "'")

    def test_commands_kiln_types_itself_are_not_remembered(self):
        self.card.run_command("echo injected-by-kiln")
        self.pump(1.2)
        self.type_("echo typed-by-me")
        self.term.feed_child(b"\n")
        self.pump(1.2)
        hist = open(os.environ["HISTFILE"]).read()
        self.assertIn("typed-by-me", hist)
        self.assertNotIn("injected-by-kiln", hist)
        self.assertNotIn("injected-by-kiln", open(f"{self.app.APP}/commands.log").read())

    def test_the_suggestion_shows_in_the_right_place_after_clear(self):
        """`clear` erases the scrollback and leaves VTE's scroll position out of step with the cursor's row: the cursor
        is found from the text on the screen instead."""
        for _ in range(2):
            self.term.feed_child(b"seq 1 80\nclear\n")
            self.pump(1.5)
            self.type_("cl")
            col, row = self.term.get_cursor_position()
            self.assertEqual(self.st["full"], "clear")
            vrow = self.card._visible_row(self.term, col, row)
            self.assertIsNotNone(vrow)
            text = (self.term.get_text_format(self.app.Vte.Format.TEXT) or "").split("\n")
            self.assertEqual(text[vrow].rstrip().split()[-1], "cl")          # the row it draws on is the one with the prompt
            self.clear_line()

    def test_the_debug_report_says_why_and_never_shows_what_you_typed(self):
        self.type_("echo secret-words")
        report = "\n".join(self.card.debug_report())
        self.assertIn("shell in front: True", report)
        self.assertIn("line read from the screen: 17 characters", report)
        self.assertNotIn("secret-words", report)
        self.assertTrue(os.path.exists(f"{self.app.CACHE}/suggest-debug.txt"))

    def test_a_program_cannot_fake_the_prompt_title(self):
        """A running program that prints the "at the prompt" title must not turn the suggestions on."""
        self.term.feed_child(b"bash -c \"printf '\\033]0;kiln:prompt\\007'; sleep 2\"\n")
        self.pump(1.0)
        self.assertEqual(self.term.get_window_title(), "kiln:prompt")      # the forged title did arrive...
        self.assertFalse(self.card._sg_at_prompt(self.term))               # ...but the shell is not in front
        self.assertFalse(self.key(self.Gdk.KEY_Tab))
        self.pump(2.2)
        self.assertTrue(self.card._sg_at_prompt(self.term))

    def test_a_hash_request_goes_to_the_ai_and_replaces_the_line(self):
        asked = []
        old = self.S.ask_ai
        self.S.ask_ai = lambda typed, cwd, hist, cfg, timeout=40: asked.append(typed) or "find ~ -size +100M"
        old_backend = self.S.backend
        self.S.backend = lambda cfg: ("ollama", "m")
        try:
            self.type_("# big files")
            self.assertEqual(self.st["full"], "")                 # nothing is sent until you ask
            self.assertEqual(asked, [])
            self.assertTrue(self.key(self.Gdk.KEY_space, self.Gdk.ModifierType.CONTROL_MASK))
            deadline = time.time() + 5
            while not self.st["full"] and time.time() < deadline:
                self.pump(0.1)
            self.assertEqual(asked, ["# big files"])
            self.assertEqual((self.st["full"], self.st["kind"], self.st["replace"]), ("find ~ -size +100M", "ai", True))
            self.assertTrue(self.key(self.Gdk.KEY_Tab))
            self.pump(0.5)
            self.assertEqual(self.card._sg_line(self.term), "find ~ -size +100M")     # the request is gone, the command typed
        finally:
            self.S.ask_ai, self.S.backend = old, old_backend

    def test_ai_off_says_so_and_sends_nothing(self):
        old = self.S.backend
        self.S.backend = lambda cfg: None
        try:
            self.type_("# big files")
            self.key(self.Gdk.KEY_space, self.Gdk.ModifierType.CONTROL_MASK)
            self.assertIn("AI is off", self.st["note"])
        finally:
            self.S.backend = old

    def test_search_finds_text_and_zoom_changes_the_font(self):
        self.type_("echo needle-77\n")
        self.card.show_search()
        self.card.search_entry.set_text("needle")
        self.pump(0.4)
        self.assertTrue(self.term.get_has_selection())
        self.assertEqual(self.card.search_info.get_text(), "")
        self.card.search_entry.set_text("no-such-text-anywhere")
        self.pump(0.4)
        self.assertEqual(self.card.search_info.get_text(), "no match")
        self.card.hide_search()
        base = self.card.eff_pt()
        self.card.zoom_by(2)
        self.assertEqual(self.card.eff_pt(), base + 2)
        self.card.zoom_by(0)
        self.assertEqual(self.card.eff_pt(), base)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display")
class WorkBarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.makedirs(f"{cls.tmp.name}/conf")
        os.makedirs(f"{cls.tmp.name}/cache")
        os.environ.setdefault("KILN_CONFIG", f"{cls.tmp.name}/conf")
        os.environ.setdefault("KILN_CACHE", f"{cls.tmp.name}/cache")
        try:
            import cairo
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gdk
            sys.path.insert(0, ROOT)
            import app
            import warp
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"no GTK here ({e})")
        if Gdk.Screen.get_default() is None:
            raise unittest.SkipTest("no screen")
        cls.cairo, cls.app, cls.warp = cairo, app, warp
        cls.saved_refresh = app.WorkBar.refresh
        app.WorkBar.refresh = lambda self: True                 # no scanning of this computer
        cls.bar = app.WorkBar(type("T", (), {"tabs": [], "active": 0})())
        cls.bar.W = 1792

    @classmethod
    def tearDownClass(cls):
        cls.app.WorkBar.refresh = cls.saved_refresh
        cls.app.WARP[0] = None
        cls.tmp.cleanup()

    def draw(self, servers, vpn=None, busy=""):
        w = self.warp.Warp()
        w.now, w.busy = vpn, busy
        self.app.WARP[0] = w if vpn else None
        self.bar.servers, self.bar.containers, self.bar.proj = servers, [], None
        s = self.cairo.ImageSurface(self.cairo.FORMAT_ARGB32, 1792, 50)
        self.bar.on_draw(None, self.cairo.Context(s))
        return [k for _r, k in self.bar.hits]

    @staticmethod
    def server(i, kind="server"):
        return dict(port=3000 + i, label=f"next dev {i}", kind=kind, cwd=None, pid=1, proc="node")

    def test_every_server_fits_no_more_chip(self):
        keys = self.draw([self.server(0), self.server(1)])
        self.assertEqual([k[0] for k in keys].count("srv"), 2)
        self.assertNotIn("more", [k[0] for k in keys])

    def test_the_rest_go_behind_a_clickable_chip_that_lists_them(self):
        servers = [self.server(i) for i in range(8)] + [self.server(99, "service")]
        keys = self.draw(servers)
        more = [k for k in keys if k[0] == "more"]
        shown = [k for k in keys if k[0] == "srv"]
        self.assertEqual(len(more), 1)
        self.assertEqual(len(shown) + len(more[0][1]), len(servers))       # nothing is lost
        self.assertIn(servers[-1], more[0][1])
        menu = []
        old = self.app.popup_menu
        self.app.popup_menu = lambda ev, items: menu.extend(items)
        try:
            self.bar.more_menu(None, more[0][1])
        finally:
            self.app.popup_menu = old
        self.assertEqual(len(menu), len(more[0][1]))
        self.assertTrue(any(":3099" in label and "copy address" in label for label, _cb in menu))   # a service is copied, not opened

    def test_the_vpn_chip_shows_the_state(self):
        keys = self.draw([self.server(0)], {"state": "connected", "latency": 54.0})
        self.assertIn(("vpn", None), keys)
        self.assertEqual(self.bar.vpn_parts()[2:], ("WARP", "54 ms"))
        self.draw([self.server(0)], {"state": "disconnected"})
        self.assertEqual(self.bar.vpn_parts()[3], "off")
        self.draw([self.server(0)], {"state": "disconnected"}, busy="connecting")
        self.assertEqual(self.bar.vpn_parts()[3], "connecting…")
        self.draw([self.server(0)], {"state": self.warp.MISSING})
        self.assertIsNone(self.bar.vpn_parts())
        self.assertNotIn(("vpn", None), self.draw([self.server(0)], None))


if __name__ == "__main__":
    unittest.main()
