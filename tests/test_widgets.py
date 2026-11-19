"""Widgets you build yourself: storage, checking, links, the command runner (no GTK needed)."""
import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import widgets as W  # noqa: E402


class WidgetsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = f"{self.tmp.name}/widgets.json"

    def make(self):
        return W.Widgets(self.path)

    def test_add_update_delete_and_reload(self):
        ws = self.make()
        a = ws.add("note", "Ideas", text="buy milk")
        b = ws.add("command", "Disk", command="df -h /", interval=10)
        c = ws.add("links", "Docs", items=W.parse_links("Kiln = https://example.com\nLogs = ~/logs"))
        d = ws.add("image", "", path="~/pic.png")
        self.assertEqual([a, b, c, d], ["w1", "w2", "w3", "w4"])
        ws.update(a, text="buy milk and eggs")
        ws.delete(b)
        self.assertEqual(ws.add("note"), "w2")                       # a free number is used again
        ws.save()
        again = self.make()
        self.assertEqual([w["id"] for w in again.items], ["w1", "w3", "w4", "w2"])
        self.assertEqual(again.get("w1")["text"], "buy milk and eggs")
        self.assertEqual(again.get("w4")["title"], "Picture")        # no title: the kind's name
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_values_are_checked(self):
        ws = self.make()
        w = ws.add("command", "x" * 99, command="echo a\nrm -rf x", interval=1)
        got = ws.get(w)
        self.assertEqual(len(got["title"]), W.MAX_TITLE)
        self.assertEqual(got["command"], "echo a rm -rf x")              # one line
        self.assertEqual(got["interval"], W.MIN_INTERVAL)
        self.assertEqual(ws.update(w, interval=999999)["interval"], W.MAX_INTERVAL)
        self.assertEqual(ws.update(w, interval="oops")["interval"], W.DEFAULT_INTERVAL)
        with self.assertRaises(ValueError):
            ws.add("virus")
        with self.assertRaises(ValueError):
            ws.update("w99", title="x")

    def test_a_hand_edited_file_is_checked(self):
        with open(self.path, "w") as f:
            json.dump({"version": 1, "widgets": [
                {"id": "w1", "type": "note", "title": "ok", "text": "t" * 99999},
                {"id": "w1", "type": "note", "title": "same id"},
                {"id": "../x", "type": "note"},
                {"id": "w2", "type": "shell", "title": "no such kind"},
                {"id": "w3", "type": "links", "items": [{"label": "a", "target": "b"}, {"label": "", "target": "c"}, 5]},
                "junk"]}, f)
        ws = self.make()
        self.assertEqual([w["id"] for w in ws.items], ["w1", "w3"])
        self.assertEqual(len(ws.get("w1")["text"]), W.MAX_TEXT)
        self.assertEqual(ws.get("w3")["items"], [{"label": "a", "target": "b"}])
        with open(self.path, "w") as f:
            f.write("{nope")
        self.assertEqual(self.make().items, [])

    def test_the_most_widgets(self):
        ws = self.make()
        for _ in range(W.MAX_WIDGETS):
            ws.add("note")
        with self.assertRaises(ValueError):
            ws.add("note")

    def test_links(self):
        items = W.parse_links("Docs = https://example.com/a?b=c\nbad line\n = nothing\nLogs = ~/logs\nBuild = make -C ~/app = x")
        self.assertEqual([i["label"] for i in items], ["Docs", "Logs", "Build"])
        self.assertEqual(items[2]["target"], "make -C ~/app = x")          # only the first = splits
        self.assertEqual(W.parse_links(W.links_text(items)), items)
        self.assertEqual(len(W.parse_links("\n".join(f"a{i} = b" for i in range(50)))), W.MAX_LINKS)

    def test_what_a_shortcut_does(self):
        self.assertEqual(W.classify_target("https://example.com/x"), ("url", "https://example.com/x"))
        self.assertEqual(W.classify_target("HTTP://Example.com"), ("url", "HTTP://Example.com"))
        self.assertEqual(W.classify_target("~/logs"), ("path", os.path.expanduser("~/logs")))
        self.assertEqual(W.classify_target("/var/log"), ("path", "/var/log"))
        self.assertEqual(W.classify_target("/tmp; rm -rf ~")[0], "command")      # not mistaken for a path
        self.assertEqual(W.classify_target("https://a b")[0], "command")
        self.assertEqual(W.classify_target("javascript:alert(1)")[0], "command")  # never opened as an address
        self.assertEqual(W.classify_target("make test")[0], "command")

    def test_web_addresses(self):
        good = {"example.com": "https://example.com", "https://a.b/c?d=1": "https://a.b/c?d=1",
                "localhost:3000": "http://localhost:3000", "192.168.1.5:8080/x": "http://192.168.1.5:8080/x",
                "HTTP://Example.com": "HTTP://Example.com", "https://[::1]:80/": "https://[::1]:80/"}
        for given, want in good.items():
            self.assertEqual(W.normalize_url(given), want, given)
        for bad in ("", "   ", "javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "ftp://x.y", "https://",
                    "https://evil.com@x.test", "a b.com", "example", "https://a.b/ c", "x" * 600):
            self.assertEqual(W.normalize_url(bad), "", bad)

    def test_web_and_scripts_widgets(self):
        ws = self.make()
        w = ws.add("web", "Board", url="localhost:3000", zoom=5, refresh=99999)
        got = ws.get(w)
        self.assertEqual((got["url"], got["zoom"], got["refresh"]), ("http://localhost:3000", W.MIN_ZOOM, W.MAX_REFRESH))
        self.assertEqual(ws.update(w, url="javascript:alert(1)")["url"], "")           # never kept
        self.assertEqual(ws.update(w, zoom="x", refresh="y")["zoom"], 100)
        s = ws.add("scripts", "Ops", items=[
            {"label": "Deploy", "command": "cd app\nmake deploy\n", "where": "terminal", "cwd": "~/app"},
            {"label": "Tests", "command": "pytest -q", "where": "nowhere"},
            {"label": "", "command": "x"}, {"label": "no command", "command": "  "}, "junk"] + [
            {"label": f"s{i}", "command": "true"} for i in range(30)])
        items = ws.get(s)["items"]
        self.assertEqual(len(items), W.MAX_SCRIPTS)
        self.assertEqual(items[0], {"label": "Deploy", "command": "cd app\nmake deploy", "mode": "run", "where": "terminal",
                                    "cwd": "~/app"})
        self.assertEqual(items[1]["where"], "background")                              # an unknown place is the background
        self.assertEqual(items[1]["cwd"], "")
        ws.save()
        self.assertEqual(self.make().get(s)["items"][0]["command"], "cd app\nmake deploy")
        self.assertTrue(all(t in W.ICONS and t in W.DESCRIPTIONS for t in W.TYPES))

    def test_a_script_can_be_a_service_you_start_and_stop(self):
        ws = self.make()
        w = ws.add("scripts", "Dev", items=[
            {"label": "Server", "command": "npm run dev", "mode": "service", "stop": "pkill -f vite\n", "check": "ss -ltn | grep -q :3000",
             "where": "terminal", "cwd": "~/app"},
            {"label": "Plain", "command": "make", "mode": "nonsense", "stop": "ignored", "check": "ignored"},
            {"label": "No stop", "command": "node server.js", "mode": "service"}])
        a, b, c = ws.get(w)["items"]
        self.assertEqual((a["mode"], a["stop"], a["check"], a["where"]), ("service", "pkill -f vite", "ss -ltn | grep -q :3000",
                                                                         "background"))      # a service always runs in the background
        self.assertEqual(b, {"label": "Plain", "command": "make", "mode": "run", "where": "background", "cwd": ""})
        self.assertEqual((c["mode"], c["stop"], c["check"]), ("service", "", ""))             # SIGTERM stops it; Kiln follows the process
        ws.save()
        self.assertEqual(self.make().get(w)["items"][0]["check"], "ss -ltn | grep -q :3000")

    def test_every_template_is_a_valid_entry(self):
        for name, entry in W.SCRIPT_TEMPLATES:
            clean = W.clean_script(entry)
            self.assertIsNotNone(clean, name)
            self.assertEqual(clean["label"], entry["label"], name)
            self.assertEqual(clean["mode"], entry.get("mode", "run"), name)
            if clean["mode"] == "service":
                self.assertTrue(clean["stop"], name)                        # a service can be stopped

    def test_running_a_script_in_a_folder(self):
        ok, out = W.run_command("pwd\necho second", cwd="/tmp", script=True)
        self.assertEqual((ok, out), (True, "/tmp\nsecond"))
        self.assertEqual(W.run_command("pwd", cwd="/no/such/dir")[1], os.path.expanduser("~"))   # a missing folder: home
        self.assertEqual(W.run_command("echo a\necho b")[1], "a echo b")                      # a one-line command collapses newlines
        self.assertEqual(W.run_command("echo a\necho b", script=True)[1], "a\nb")

    def test_running_a_command(self):
        ok, out = W.run_command("echo hello; echo oops >&2")
        self.assertTrue(ok)
        self.assertEqual(out, "hello\noops")
        self.assertEqual(W.run_command("printf '\\033[31mred\\033[0m'")[1], "red")          # colours removed
        ok, out = W.run_command("exit 3")
        self.assertFalse(ok)
        self.assertEqual(out, "exit status 3")
        ok, out = W.run_command("yes | head -c 100000", limit=50)
        self.assertTrue(out.endswith("…") and len(out) < 60)
        ok, out = W.run_command("sleep 5", timeout=0.3)
        self.assertFalse(ok)
        self.assertIn("timed out", out)
        self.assertEqual(W.run_command("   ")[1], "no command")
        self.assertEqual(W.run_command("cat")[1], "(no output)")                    # stdin is closed: it does not wait


if __name__ == "__main__":
    unittest.main()
