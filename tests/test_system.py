"""Tests for the project scanner and the theme engine's GTK accent block (no display needed).

    python3 -m unittest discover -s tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "share"))
import theme as T  # noqa: E402

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


GIT = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@example.com")


def repo(path, marker=None):
    os.makedirs(path)
    if marker:
        write(os.path.join(path, marker), "")
    subprocess.run(["git", "-C", path, "init", "-q", "-b", "main"], check=True, env=GIT)
    write(os.path.join(path, "a.txt"), "x")
    subprocess.run(["git", "-C", path, "add", "-A"], check=True, env=GIT)
    subprocess.run(["git", "-C", path, "commit", "-q", "-m", "first"], check=True, env=GIT)


class ScannerTests(unittest.TestCase):
    def test_finds_projects_and_skips_the_noise(self):
        with tempfile.TemporaryDirectory() as d:
            root, conf, cache = f"{d}/code", f"{d}/conf", f"{d}/cache"
            repo(f"{root}/shop", "package.json")
            repo(f"{root}/group/api", "go.mod")
            repo(f"{root}/node_modules/dep")             # never a project
            repo(f"{root}/a/b/c/d/too-deep")             # deeper than three folders
            repo(f"{root}/skipme/x")
            os.makedirs(f"{root}/empty-repo")
            subprocess.run(["git", "-C", f"{root}/empty-repo", "init", "-q"], check=True)   # no commits yet
            os.makedirs(conf)
            write(f"{conf}/config.json", json.dumps({"project_dirs": [root], "project_exclude": ["skipme"]}))
            env = dict(os.environ, KILN_CONFIG=conf, KILN_CACHE=cache)
            subprocess.run([sys.executable, os.path.join(ROOT, "share", "scan-projects.py")], check=True, env=env)
            rows = [l.split("|") for l in read(f"{cache}/projects.txt").splitlines()[1:]]
            self.assertEqual(sorted(r[1] for r in rows), ["api", "shop"])
            self.assertEqual({r[1]: r[5] for r in rows}, {"shop": "JavaScript", "api": "Go"})
            st = json.loads(read(f"{cache}/projects.status"))
            self.assertEqual((st["state"], st["found"]), ("done", 2))

    def test_nothing_to_scan_still_finishes(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/conf")
            write(f"{d}/conf/config.json", json.dumps({"project_dirs": [f"{d}/missing"]}))
            env = dict(os.environ, KILN_CONFIG=f"{d}/conf", KILN_CACHE=f"{d}/cache")
            subprocess.run([sys.executable, os.path.join(ROOT, "share", "scan-projects.py")], check=True, env=env)
            self.assertEqual(json.loads(read(f"{d}/cache/projects.status"))["state"], "done")


class GtkAccentTests(unittest.TestCase):
    COLORS = {"sel": "3f7f86", "on_sel": "ffffff", "acc": "7cc6c2"}

    def test_block_is_added_replaced_and_removed_without_touching_the_rest(self):
        with tempfile.TemporaryDirectory() as home:
            for d in ("gtk-3.0", "gtk-4.0"):
                os.makedirs(f"{home}/.config/{d}")
            mine = "button { border-radius: 2px; }\n"
            write(f"{home}/.config/gtk-4.0/gtk.css", mine)
            T.write_gtk_accent(self.COLORS, home)
            css = read(f"{home}/.config/gtk-4.0/gtk.css")
            self.assertIn(mine.strip(), css)
            self.assertIn("@define-color accent_bg_color #3f7f86;", css)
            T.write_gtk_accent(dict(self.COLORS, sel="123456"), home)             # a second theme: one block only
            css = read(f"{home}/.config/gtk-4.0/gtk.css")
            self.assertEqual(css.count("kiln:begin"), 1)
            self.assertIn("#123456", css)
            T.write_gtk_accent(None, home)
            self.assertEqual(read(f"{home}/.config/gtk-4.0/gtk.css").strip(), mine.strip())
            self.assertFalse(os.path.exists(f"{home}/.config/gtk-3.0/gtk.css"))   # only held our block: removed


if __name__ == "__main__":
    unittest.main()
