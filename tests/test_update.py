"""Tests for share/update.py against a local stand-in for GitHub (nothing is installed, nothing leaves the machine).

    python3 -m unittest discover -s tests
"""
import http.server
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import update as U  # noqa: E402


def release_tar(version, extra=None, top="kiln-abc123", drop=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        files = {f"{top}/install.sh": "#!/bin/sh\necho ok\n", f"{top}/share/VERSION": version + "\n"}
        files.update(extra or {})
        for name, body in files.items():
            if name.split("/", 1)[1] in drop:
                continue
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Fake(http.server.BaseHTTPRequestHandler):
    release = {}
    tar = b""

    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path.endswith("/releases/latest"):
            body = json.dumps(Fake.release).encode()
        elif "/tarball/" in self.path:
            body = Fake.tar
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        os.environ["KILN_UPDATE_API"] = f"http://127.0.0.1:{cls.srv.server_port}"
        U.API = os.environ["KILN_UPDATE_API"]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        os.environ.pop("KILN_UPDATE_API", None)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        U.SHARE, U.CACHE, U.CONF = f"{d}/share", f"{d}/cache", f"{d}/conf"
        U.STATE, U.EXT = f"{d}/cache/update.json", f"{d}/ext"
        for p in (U.SHARE, U.CACHE, U.CONF, U.EXT):
            os.makedirs(p)
        with open(f"{U.SHARE}/VERSION", "w") as f:
            f.write("1.1.0\n")
        with open(f"{U.EXT}/extension.js", "w") as f:
            f.write("old")
        Fake.release = {"tag_name": "v1.2.0", "html_url": "https://example.test/r", "body": "notes", "draft": False,
                        "prerelease": False}
        Fake.tar = release_tar("1.2.0")
        self.events = []
        U.restart = lambda: self.events.append("restart")
        U.notify = lambda t, b: self.events.append(("notify", t))
        U.healthy = lambda since: True

    def tearDown(self):
        self.tmp.cleanup()

    def test_versions(self):
        self.assertEqual(U.parse("v1.2.10"), (1, 2, 10))
        self.assertGreater(U.parse("1.10.0"), U.parse("1.9.9"))
        for bad in ("1.2", "v1.2.3-rc1", "latest", "", None):
            self.assertIsNone(U.parse(bad))

    def test_check_finds_a_newer_release_and_ignores_drafts(self):
        st = U.check()
        self.assertTrue(st["newer"])
        self.assertEqual((st["current"], st["latest"]), ("1.1.0", "1.2.0"))
        Fake.release["tag_name"] = "v1.1.0"
        self.assertFalse(U.check()["newer"])
        Fake.release.update(tag_name="v9.0.0", draft=True)
        self.assertIn("No release", U.check()["error"])

    def test_extract_refuses_bad_archives(self):
        for name, data in (("wrong", release_tar("9.9.9")),
                           ("escape", release_tar("1.2.0", {"../../evil.txt": "x"})),
                           ("nokiln", release_tar("1.2.0", drop=("install.sh",)))):
            path = f"{self.tmp.name}/{name}.tgz"
            with open(path, "wb") as f:
                f.write(data)
            with self.assertRaises(U.UpdateError, msg=name):
                U.extract(path, f"{self.tmp.name}/out-{name}", "1.2.0")
        self.assertFalse(os.path.exists(f"{self.tmp.name}/evil.txt"))

    def test_apply_installs_keeps_a_backup_and_reports_the_top_bar(self):
        ran = []

        def runner(*cmd, cwd=None, timeout=0):
            ran.append(cmd)
            if cmd[0] == "bash":                       # the "installer" replaces the extension code
                with open(f"{U.EXT}/extension.js", "w") as f:
                    f.write("new")
            return 0, ""
        self.assertEqual(U.apply("1.2.0", runner=runner), "updated 1.1.0 → 1.2.0")
        self.assertEqual(ran[0][:2][0], "bash")
        self.assertIn("--no-apply", ran[0])
        self.assertTrue(os.path.exists(f"{U.SHARE}.prev/VERSION"))          # the old version is kept
        with open(f"{U.EXT}.prev/extension.js") as f:
            self.assertEqual(f.read(), "old")
        st = U.load_state()
        self.assertTrue(st["needs_relogin"])
        self.assertIn(("notify", "Kiln 1.2.0 is installed"), self.events)
        self.assertEqual(self.events.count("restart"), 1)

    def test_a_version_that_does_not_start_is_rolled_back(self):
        U.healthy = lambda since: False
        calls = []
        U.rollback = lambda: calls.append("rollback")
        with self.assertRaises(U.UpdateError) as c:
            U.apply("1.2.0", runner=lambda *a, **k: (0, ""))
        self.assertIn("put back", str(c.exception))
        self.assertEqual(calls, ["rollback"])

    def test_a_failing_installer_stops_the_update(self):
        with self.assertRaises(U.UpdateError) as c:
            U.apply("1.2.0", runner=lambda *a, **k: (1, "Missing packages: xdotool\n"))
        self.assertIn("Missing packages", str(c.exception))
        self.assertEqual(self.events.count("restart"), 0)

    def test_modes_decide_what_auto_does(self):
        def set_mode(m):
            with open(f"{U.CONF}/config.json", "w") as f:
                json.dump({"updates": {"mode": m}}, f)
        set_mode("off")
        self.assertEqual(U.auto(), "updates are off")
        set_mode("notify")
        self.assertEqual(U.auto(), "1.2.0 is available")
        U.auto()
        self.assertEqual([e for e in self.events if isinstance(e, tuple)].__len__(), 1)    # told once, not every time
        set_mode("auto")
        with open(f"{U.CACHE}/mode", "w") as f:
            f.write("focus")
        self.assertIn("Focus", U.auto())                       # never in the middle of a focus session
        os.remove(f"{U.CACHE}/mode")
        U.apply = lambda v=None, runner=None: f"installed {v}"
        self.assertEqual(U.auto(), "installed 1.2.0")
        self.assertEqual(U.mode(), "auto")


if __name__ == "__main__":
    unittest.main()
