"""Tests for implement.py: reply parsing, allow-list, atomic edits, task parsing, and a full run against a fake Ollama.
Run: python3 -m unittest -v test_implement"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import implement

HERE = Path(__file__).parent


def git_repo(files):
    d = Path(tempfile.mkdtemp())
    for name, body in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body)
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=d, check=True, capture_output=True)
    return d


class ApplyReply(unittest.TestCase):
    def test_file_block_creates_and_marks_scripts_executable(self):
        d = Path(tempfile.mkdtemp())
        changed, errors = implement.apply_reply("=== FILE: run.sh ===\n#!/bin/bash\necho hi\n=== END ===", d, ["run.sh"])
        self.assertEqual((changed, errors), (["run.sh"], []))
        self.assertTrue(os.access(d / "run.sh", os.X_OK))

    def test_edit_block_needs_exactly_one_match(self):
        d = git_repo({"a.py": "x = 1\nx = 1\n"})
        reply = "=== EDIT: a.py ===\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n=== END ==="
        changed, errors = implement.apply_reply(reply, d, ["a.py"])
        self.assertEqual(changed, [])
        self.assertIn("matched 2 times", errors[0])
        self.assertEqual((d / "a.py").read_text(), "x = 1\nx = 1\n")          # untouched

    def test_edit_block_applies_all_or_nothing_per_file(self):
        d = git_repo({"a.py": "one\ntwo\n"})
        reply = ("=== EDIT: a.py ===\n<<<<<<< SEARCH\none\n=======\n1\n>>>>>>> REPLACE\n"
                 "<<<<<<< SEARCH\nmissing\n=======\n3\n>>>>>>> REPLACE\n=== END ===")
        changed, errors = implement.apply_reply(reply, d, ["a.py"])
        self.assertEqual(changed, [])
        self.assertEqual((d / "a.py").read_text(), "one\ntwo\n")

    def test_path_outside_allow_list_rejects_the_whole_reply(self):
        d = Path(tempfile.mkdtemp())
        reply = "=== FILE: ok.py ===\nx\n=== END ===\n=== FILE: evil.py ===\ny\n=== END ==="
        changed, errors = implement.apply_reply(reply, d, ["ok.py"])
        self.assertEqual(changed, [])
        self.assertIn("path not allowed: evil.py", errors)
        self.assertFalse((d / "ok.py").exists())

    def test_path_traversal_is_rejected(self):
        d = Path(tempfile.mkdtemp())
        changed, errors = implement.apply_reply("=== FILE: ../x.py ===\ny\n=== END ===", d, ["../x.py"])
        self.assertEqual(changed, [])
        self.assertTrue(errors)

    def test_reply_without_blocks_is_an_error(self):
        _, errors = implement.apply_reply("I think you should...", Path(tempfile.mkdtemp()), ["a.py"])
        self.assertIn("no === FILE/EDIT === blocks", errors[0])


class ParseTask(unittest.TestCase):
    def _task(self, text):
        p = Path(tempfile.mkdtemp()) / "My Task.md"
        p.write_text(text)
        return implement.parse_task(p)

    def test_sections_and_shape_line(self):
        t = self._task("# Task\n## Goal\nDo it.\n## Read\na.py\n## May change\nb.py\n## Constraints\n- small\n"
                       "## Shape\nuse a list\n## Done when\ntrue\n")
        self.assertEqual((t["name"], t["read"], t["may"], t["done"]), ("my-task", ["a.py"], ["b.py"], "true"))
        self.assertIn("- small", t["constraints"])
        self.assertIn("- Shape: use a list", t["constraints"])

    def test_unfilled_template_shape_is_ignored(self):
        t = self._task("## Goal\ng\n## May change\nb.py\n## Shape\n<one line>\n## Done when\ntrue\n")
        self.assertNotIn("Shape", t["constraints"])

    def test_missing_required_section_exits(self):
        with self.assertRaises(SystemExit):
            self._task("## Goal\ng\n## Done when\ntrue\n")


class FakeOllama(http.server.BaseHTTPRequestHandler):
    replies, calls = [], []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.calls.append(body)
        out = json.dumps({"message": {"content": FakeOllama.replies.pop(0)}, "prompt_eval_count": 500,
                          "eval_count": 50}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        FakeOllama.calls.clear()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        self.srv.shutdown()

    def run_cli(self, repo, replies):
        FakeOllama.replies[:] = replies
        task = self.tmp / "t.md"
        task.write_text("## Goal\nMake the answer 42.\n## Read\nans.py\n## May change\nans.py\n"
                        "## Done when\ngrep -q 42 ans.py\n")
        env = {**os.environ, "LOCAL_BASE": f"http://127.0.0.1:{self.srv.server_port}/v1", "LOCAL_MODEL": "m",
               "IMPLEMENT_LOG": str(self.tmp / "log.jsonl")}
        return subprocess.run([sys.executable, str(HERE / "implement.py"), str(task), "--repo", str(repo), "--in-place"],
                              capture_output=True, text=True, env=env)

    def test_fails_then_succeeds_on_the_retry_round(self):
        repo = git_repo({"ans.py": "ANSWER = 0\n"})
        bad = "=== FILE: ans.py ===\nANSWER = 41\n=== END ==="
        good = "=== FILE: ans.py ===\nANSWER = 42\n=== END ==="
        r = self.run_cli(repo, [bad, good])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rounds=2", r.stdout)
        self.assertEqual((repo / "ans.py").read_text(), "ANSWER = 42\n")
        self.assertIn("The check failed", FakeOllama.calls[1]["messages"][-1]["content"])
        self.assertEqual(FakeOllama.calls[0]["options"]["num_ctx"], implement.NUM_CTX)
        row = json.loads((self.tmp / "log.jsonl").read_text().splitlines()[-1])
        self.assertTrue(row["passed"])
        self.assertEqual((row["rounds"], row["prompt_tokens"], row["completion_tokens"]), (2, 1000, 100))

    def test_exits_nonzero_after_all_rounds_fail(self):
        repo = git_repo({"ans.py": "ANSWER = 0\n"})
        bad = "=== FILE: ans.py ===\nANSWER = 41\n=== END ==="
        r = self.run_cli(repo, [bad, bad, bad])
        self.assertEqual(r.returncode, 1)
        self.assertIn("FAIL", r.stdout)

    def test_requires_local_model_settings(self):
        env = {k: v for k, v in os.environ.items() if k not in ("LOCAL_BASE", "LOCAL_MODEL")}
        r = subprocess.run([sys.executable, str(HERE / "implement.py"), "x.md"], capture_output=True, text=True, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LOCAL_BASE", r.stderr)


if __name__ == "__main__":
    unittest.main()
