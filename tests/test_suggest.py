"""Terminal suggestions (share/suggest.py): what is remembered, ranking, project completions, the AI answer cleaner, the
shell hooks. No GUI, no network, no model."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "share"))
import suggest as S  # noqa: E402


def slurp(path):
    with open(path) as f:
        return f.read()


def spit(path, text):
    with open(path, "w") as f:
        f.write(text)


class PrivacyTests(unittest.TestCase):
    def test_secrets_are_recognised(self):
        for cmd in ("export API_KEY=abc123", "curl -H 'Authorization: Bearer abc.def' https://x", "mysql -u root -phunter2",
                    "git clone https://me:pw123@github.com/x/y", "aws s3 ls --token abc", "export GITHUB_TOKEN=ghp_" + "a" * 24,
                    "login --password hunter2", " ls", "", "psql -h db -U me -pSecret", "621060", "  123456 "):
            self.assertTrue(S.is_private(cmd), cmd)
        for cmd in ("git status", "npm run dev -- -p 3000", "ls -la", "docker run -p 8080:80 nginx", "echo token"):
            self.assertFalse(S.is_private(cmd), cmd)

    def test_history_never_offers_a_secret(self):
        with tempfile.TemporaryDirectory() as d:
            hist = S.History(f"{d}/log", f"{d}/h")
            with open(f"{d}/h", "w") as f:
                f.write("export API_KEY=abc\nexport PATH=$PATH:/x\n#1700000000\n")
            self.assertEqual([c for c, _f in hist.entries()], ["export PATH=$PATH:/x"])      # comment lines skipped too
            self.assertIsNone(S.local("export API", "/", hist))


class SafetyTests(unittest.TestCase):
    """A suggestion is typed for you, so it must never carry a newline, an escape or any control character."""

    def test_is_safe(self):
        for ok in ("git status", "echo 'مرحبا'", "ls -la ~/Desktop"):
            self.assertTrue(S.is_safe(ok), ok)
        for bad in ("", "ls\nrm -rf x", "ls\r", "x\x1b[2J", "\x15ls", "a\x7fb", "a\x85b", "a\tb", "a\u202eb", "a\u200bb"):
            self.assertFalse(S.is_safe(bad), repr(bad))

    def test_a_folder_named_with_a_newline_is_not_offered_to_cd(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/proj\nrm -rf ~")
            os.makedirs(f"{d}/proj2")
            self.assertEqual(S.from_context("cd pro", d), "cd proj2/")        # the odd one is skipped, not chosen
            os.rmdir(f"{d}/proj2")
            self.assertIsNone(S.from_context("cd pro", d))

    def test_a_makefile_target_or_script_with_control_characters_is_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            with open(f"{d}/package.json", "w") as f:
                json.dump({"scripts": {"build\x1b[2J": "x", "bu\nild": "y"}}, f)
            self.assertIsNone(S.from_context("npm run bu", d))

    def test_history_with_control_characters_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            with open(f"{d}/h", "w") as f:
                f.write("echo hi\x1b[2Jthere\necho \x15safe\necho plain\n")
            hist = S.History(f"{d}/log", f"{d}/h")
            self.assertEqual([c for c, _f in hist.entries()], ["echo plain"])

    def test_an_ai_answer_with_control_characters_is_dropped(self):
        self.assertIsNone(S.clean_reply("ls\x1b[2J -la", "ls"))
        self.assertIsNone(S.clean_reply("find . \u202e-name x", "# find"))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name
        self.hist = S.History(f"{self.d}/log", f"{self.d}/h")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, shell=(), log=()):
        with open(f"{self.d}/h", "w") as f:
            f.write("\n".join(shell) + "\n")
        with open(f"{self.d}/log", "w") as f:
            f.write("".join(f"{folder}\t{cmd}\n" for folder, cmd in log))

    def test_the_most_used_and_recent_command_wins(self):
        self.write(["git commit -m a", "git commit -m b", "git commit -m b", "git commit -m c"])
        self.assertEqual(S.local("git commit", "/x", self.hist), ("git commit -m b", "history"))

    def test_this_folder_beats_the_rest(self):
        self.write(["npm run build", "npm run build"], [("/app", "npm run dev"), ("/other", "npm run build")])
        self.assertEqual(S.local("npm run ", "/app", self.hist)[0], "npm run dev")

    def test_nothing_longer_nothing_suggested(self):
        self.write(["frobnicate"])
        self.assertIsNone(S.local("frobnicate", "/", self.hist))        # the same text is no suggestion
        self.assertIsNone(S.local("   ", "/", self.hist))
        self.assertIsNone(S.local("# note", "/", self.hist))            # '#' is for the AI

    def test_a_changed_file_is_read_again(self):
        self.write(["make test"])
        self.assertEqual(S.local("make t", "/", self.hist)[0], "make test")
        with open(f"{self.d}/h", "a") as f:
            f.write("make tidy-up-everything\n")
        self.assertEqual(S.local("make t", "/", self.hist)[0], "make tidy-up-everything")

    def test_recent_commands_of_a_folder(self):
        self.write([], [("/a", "one"), ("/b", "two"), ("/a", "three"), ("/a", "one")])
        self.assertEqual(self.hist.recent_in("/a"), ["three", "one"])

    def test_forget_removes_the_log(self):
        self.write([], [("/a", "one")])
        self.hist.entries()
        self.hist.forget()
        self.assertFalse(os.path.exists(f"{self.d}/log"))
        self.assertEqual(self.hist.entries(), [])


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_package_scripts_and_make_targets(self):
        with open(f"{self.d}/package.json", "w") as f:
            json.dump({"scripts": {"build": "x", "dev": "y", "test": "z"}}, f)
        with open(f"{self.d}/Makefile", "w") as f:
            f.write("CC := gcc\nall: build\n\nbuild:\n\tcc x\n.PHONY: all\ndeploy-prod:\n\techo\n")
        self.assertEqual(S.from_context("npm run de", self.d), "npm run dev")
        self.assertEqual(S.from_context("pnpm run t", self.d), "pnpm run test")
        self.assertEqual(S.from_context("make dep", self.d), "make deploy-prod")
        self.assertIsNone(S.from_context("npm run ", self.d))           # nothing of the word typed: no guess
        self.assertIsNone(S.from_context("npm run zzz", self.d))

    def test_cd_offers_folders_only(self):
        os.makedirs(f"{self.d}/projects/api")
        os.makedirs(f"{self.d}/.hidden")
        spit(f"{self.d}/projects.txt", "")
        self.assertEqual(S.from_context("cd pro", self.d), "cd projects/")
        self.assertEqual(S.from_context(f"cd {self.d}/projects/a", "/"), f"cd {self.d}/projects/api/")
        self.assertIsNone(S.from_context("cd .h", "/x"))               # a hidden folder only when you type the dot... of an existing parent
        self.assertEqual(S.from_context("cd .hi", self.d), "cd .hidden/")

    def test_tool_subcommands(self):
        self.assertEqual(S.from_context("git sta", self.d), "git status")
        self.assertEqual(S.from_context("docker compose do", self.d), "docker compose down")
        self.assertEqual(S.from_context("python3 manage.py mig", self.d), "python3 manage.py migrate")
        self.assertIsNone(S.from_context("kubectl ge", self.d))

    def test_git_branches(self):
        run = lambda *a: subprocess.run(["git", "-C", self.d, *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q", "-b", "main")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
        run("branch", "feature/login")
        self.assertEqual(S.from_context("git checkout fe", self.d), "git checkout feature/login")
        self.assertEqual(S.git_branch(self.d), "main")


class WordTests(unittest.TestCase):
    def test_accept_word(self):
        self.assertEqual(S.accept_word("git ", "git commit -m 'x'"), "git commit")
        self.assertEqual(S.accept_word("git com", "git commit -m 'x'"), "git commit")
        self.assertEqual(S.accept_word("git commit", "git commit -m 'x'"), "git commit -m")
        self.assertEqual(S.accept_word("cd pro", "cd projects/api/"), "cd projects/api/")

    def test_cells_and_clip(self):
        self.assertEqual(S.cells("abc"), 3)
        self.assertEqual(S.cells("日本"), 4)
        self.assertEqual(S.cells("é"), 1)
        self.assertEqual(S.clip("日本語", 5), "日本")

    def test_hull_outlines_two_boxes(self):
        pts = [(0, 0), (2, 0), (0, 4), (2, 4), (10, 0), (12, 0), (10, 4), (12, 4), (1, 2)]
        self.assertEqual(sorted(S.convex_hull(pts)), [(0, 0), (0, 4), (12, 0), (12, 4)])


class CatalogTests(unittest.TestCase):
    """A machine with an empty history still gets useful suggestions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hist = S.History.__new__(S.History)
        self.hist.entries = lambda: []

    def test_everyday_command(self):
        self.assertEqual(S.local("ls", self.tmp, self.hist), ("ls -la", "context"))
        self.assertEqual(S.local("git s", self.tmp, self.hist), ("git status", "context"))

    def test_finishes_a_program_name(self):
        self.assertEqual(S.local("doc", self.tmp, self.hist)[0], "docker")

    def test_go_project(self):
        open(os.path.join(self.tmp, "go.mod"), "w").close()
        self.assertEqual(S.local("go", self.tmp, self.hist)[0], "go run .")
        self.assertEqual(S.local("go t", self.tmp, self.hist)[0], "go test ./...")

    def test_history_wins(self):
        h = S.History.__new__(S.History)
        h.entries = lambda: [("ls -R", self.tmp)]
        self.assertEqual(S.local("ls", self.tmp, h), ("ls -R", "history"))

    def test_one_letter_and_unknown_are_quiet(self):
        self.assertIsNone(S.local("l", self.tmp, self.hist))
        self.assertIsNone(S.local("zzzq", self.tmp, self.hist))

    def test_everything_offered_is_safe(self):
        for c in list(S.CATALOG) + S.project_commands(self.tmp):
            self.assertTrue(S.is_safe(c), c)
            self.assertFalse(S.is_private(c), c)


class AiTests(unittest.TestCase):
    def test_a_completion_must_keep_what_was_typed(self):
        self.assertEqual(S.clean_reply("git commit -m 'fix'", "git com"), "git commit -m 'fix'")
        self.assertEqual(S.clean_reply("```bash\n$ git commit -v\n```", "git com"), "git commit -v")
        self.assertEqual(S.clean_reply("<think>hmm</think>\ngit commit", "git com"), "git commit")
        self.assertIsNone(S.clean_reply("ls -la", "git com"))            # not a completion of the line
        self.assertIsNone(S.clean_reply("git com", "git com"))           # nothing added
        self.assertIsNone(S.clean_reply("", "git com"))

    def test_a_request_becomes_a_command(self):
        self.assertEqual(S.clean_reply("find ~ -size +100M", "# big files"), "find ~ -size +100M")
        self.assertIsNone(S.clean_reply("# just a comment", "# big files"))
        self.assertIsNone(S.clean_reply("export TOKEN=abc", "# set it"))   # a secret in the answer is dropped

    def test_the_prompt_has_context_but_no_secrets_and_no_file_contents(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(f"{d}/proj")
            spit(f"{d}/proj/app.py", "PASSWORD = 'hunter2'\n")
            hist = S.History(f"{d}/log", f"{d}/h")
            spit(f"{d}/log", f"{d}/proj\tpytest -q\n{d}/proj\texport KEY=zzz\n")
            p = S.build_prompt("pytest -", f"{d}/proj", hist)
            self.assertIn("app.py", p)
            self.assertIn("$ pytest -q", p)
            self.assertNotIn("hunter2", p)
            self.assertNotIn("zzz", p)
            self.assertIn("Request: big files", S.build_prompt("# big files", f"{d}/proj", hist))

    def test_a_secret_in_the_line_is_never_sent(self):
        cfg = S.settings({"ai": "ollama"})
        S._up.update(t=9e18, models=["m"])                               # pretend Ollama answers
        called = []
        old = S.urllib.request.urlopen
        S.urllib.request.urlopen = lambda *a, **k: called.append(1)
        try:
            self.assertIsNone(S.ask_ai("curl -H 'Authorization: Bearer abc' x", "/", S.History("/nope", "/nope"), cfg))
            self.assertEqual(called, [])
        finally:
            S.urllib.request.urlopen = old
            S._up.update(t=0.0, models=[])

    def test_backend_choice(self):
        S._up.update(t=9e18, models=["small", "big"])
        try:
            self.assertEqual(S.backend(S.settings({})), ("ollama", "small"))
            self.assertEqual(S.backend(S.settings({"model": "big"})), ("ollama", "big"))
            self.assertIsNone(S.backend(S.settings({"ai": "off"})))
        finally:
            S._up.update(t=0.0, models=[])


class ShellTests(unittest.TestCase):
    def test_the_rc_is_valid_bash_and_logs_commands_with_their_folder(self):
        with tempfile.TemporaryDirectory() as d:
            spit(f"{d}/base.rc", "PS1='$ '\n")
            S.write_shell_rc(f"{d}/shell.rc", f"{d}/base.rc")
            self.assertEqual(subprocess.run(["bash", "-n", f"{d}/shell.rc"]).returncode, 0)
            S.prepare_log(f"{d}/log")
            env = dict(os.environ, KILN_CMDLOG=f"{d}/log", HISTFILE=f"{d}/h", HOME=d)
            script = (f"cd {d}; . {d}/shell.rc; set -o history; HISTFILE={d}/h\n"
                      "eval \"$PROMPT_COMMAND\"\n"                                 # the first prompt logs nothing, old history is not new
                      "history -s 'echo one'; eval \"$PROMPT_COMMAND\"; eval \"$PROMPT_COMMAND\"\n"
                      "history -s 'echo two'; eval \"$PROMPT_COMMAND\"\n")
            subprocess.run(["bash", "-i", "-c", script], env=env, capture_output=True, timeout=20)
            rows = [l.split("\t") for l in slurp(f"{d}/log").splitlines()]
            self.assertEqual([r[1] for r in rows], ["echo one", "echo two"])   # twice at the same prompt logs once
            self.assertTrue(all(r[0] == d for r in rows))

    def test_the_log_is_private_and_trimmed(self):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/sub/commands.log"
            S.prepare_log(path)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path, "w") as f:
                f.write("".join(f"/x\tcommand number {i}\n" for i in range(300)))
            S.prepare_log(path, keep=100, max_bytes=1000)
            lines = slurp(path).splitlines()
            self.assertEqual(len(lines), 100)
            self.assertEqual(lines[-1], "/x\tcommand number 299")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
