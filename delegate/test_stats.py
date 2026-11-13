"""Tests for stats.py, report.py and console.py. Run: python3 -m unittest -v test_stats"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

import console
import stats

HERE = Path(__file__).parent
NOW = time.time()
ROWS = [
    {"ts": NOW, "ok": True, "secs": 4.5, "material_tokens": 2811, "qwen_in": 3862, "qwen_out": 220,
     "task_tokens": 18, "result_tokens": 90, "truncated": False, "note": False, "n_paths": 1},
    {"ts": NOW, "ok": True, "secs": 9.0, "material_tokens": 24000, "qwen_in": 25100, "qwen_out": 400,
     "task_tokens": 30, "result_tokens": 160, "truncated": True, "note": True, "n_paths": 3},
    {"ts": NOW, "ok": False, "err": "connection refused"},
    {"ts": NOW - 40 * 86400, "ok": True, "secs": 1, "material_tokens": 999, "qwen_in": 1, "qwen_out": 1,
     "task_tokens": 1, "result_tokens": 1},
]


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.jsonl"
        self.log.write_text("\n".join(json.dumps(r) for r in ROWS) + "\nnot json\n\n")
        for k in ("CLAUDE_IN_PER_MTOK", "CLAUDE_OUT_PER_MTOK"):
            os.environ.pop(k, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_skips_bad_lines_and_filters_by_days(self):
        self.assertEqual(len(stats.load(self.log)), 4)
        self.assertEqual(len(stats.load(self.log, days=7)), 3)
        self.assertEqual(stats.load(Path(self.tmp.name) / "missing.jsonl"), [])

    def test_summarize(self):
        t = stats.summarize(stats.load(self.log, days=7))
        self.assertEqual((t["calls"], t["ok"], t["failed"], t["truncated"], t["notes"]), (3, 2, 1, 1, 1))
        self.assertEqual(t["not_read"], 26811)
        self.assertEqual(t["net_input_avoided"], 26811 - 250)
        self.assertIsNone(t["dollars_saved"])

    def test_dollars_only_with_prices(self):
        os.environ.update(CLAUDE_IN_PER_MTOK="3", CLAUDE_OUT_PER_MTOK="15")
        t = stats.summarize(stats.load(self.log, days=7))
        self.assertAlmostEqual(t["dollars_saved"], (26561 / 1e6 * 3) - (48 / 1e6 * 15), places=4)

    def test_report_cli(self):
        out = subprocess.run([sys.executable, str(HERE / "report.py"), "--log", str(self.log), "--days", "7"],
                             capture_output=True, text=True, cwd=self.tmp.name)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("calls: 3  ok: 2  failed: 1", out.stdout)
        empty = subprocess.run([sys.executable, str(HERE / "report.py"), "--log", str(Path(self.tmp.name) / "x")],
                               capture_output=True, text=True, cwd=self.tmp.name)
        self.assertNotEqual(empty.returncode, 0)

    def test_console_serves_page_and_stats(self):
        srv = console.make_server(self.log, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            page = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("savings console", page)
            d = json.load(urllib.request.urlopen(base + "/stats"))
            self.assertEqual(d["summary"]["calls"], 4)
            self.assertEqual(d["feed"][0]["ok"] in (True, False), True)
            self.assertTrue(any(not r["ok"] and r["err"] for r in d["feed"]))
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/nope")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
