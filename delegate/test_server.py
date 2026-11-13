"""Protocol + guard tests for server.py against a fake Ollama. Run: python3 -m unittest -v test_server"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HERE = Path(__file__).parent


class FakeOllama(http.server.BaseHTTPRequestHandler):
    calls, paths, reply, done = [], [], "FAKE ANSWER", "stop"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.calls.append(body)
        FakeOllama.paths.append(self.path)
        out = json.dumps({"message": {"content": FakeOllama.reply}, "done_reason": FakeOllama.done,
                          "prompt_eval_count": 321, "eval_count": 12}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


class Client:
    def __init__(self, url, log, model="test-model"):
        env = {k: v for k, v in os.environ.items() if k not in ("LOCAL_BASE", "LOCAL_MODEL")}
        env.update(LOCAL_BASE=url, DELEGATE_LOG=str(log))
        if model:
            env["LOCAL_MODEL"] = model
        self.p = subprocess.Popen([sys.executable, str(HERE / "server.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, env=env)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        return None if notify else json.loads(self.p.stdout.readline())

    def call(self, **args):
        return self.send("tools/call", {"name": "ask_local", "arguments": args})["result"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=5)
        self.p.stdout.close()


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.srv.server_port}/v1"      # gateway-style base; server must strip /v1

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "calls.jsonl"
        self.c = Client(self.url, self.log)
        FakeOllama.calls.clear()
        FakeOllama.paths.clear()
        FakeOllama.reply, FakeOllama.done = "FAKE ANSWER", "stop"

    def tearDown(self):
        self.c.close()
        self.tmp.cleanup()

    def test_handshake_and_tools(self):
        r = self.c.send("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.c.send("notifications/initialized", notify=True))
        tools = self.c.send("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["ask_local"])

    def test_reads_file_and_logs(self):
        f = Path(self.tmp.name) / "a.txt"
        f.write_text("SECRET-FREE CONTENT " * 50)
        r = self.c.call(task="summarize", paths=[str(f)])
        self.assertFalse(r["isError"])
        self.assertEqual(r["content"][0]["text"], "FAKE ANSWER")
        self.assertIn("SECRET-FREE CONTENT", FakeOllama.calls[0]["messages"][1]["content"])
        self.assertFalse(FakeOllama.calls[0]["think"])
        self.assertEqual(FakeOllama.paths, ["/api/chat"])            # /v1 stripped from LOCAL_BASE
        self.assertEqual(FakeOllama.calls[0]["model"], "test-model")
        self.assertIn("   1| SECRET-FREE", FakeOllama.calls[0]["messages"][1]["content"])   # numbered lines
        rec = json.loads(self.log.read_text().splitlines()[0])
        self.assertTrue(rec["ok"])
        self.assertGreater(rec["material_tokens"], 100)
        self.assertEqual((rec["qwen_in"], rec["qwen_out"]), (321, 12))

    def test_sensitive_and_missing_paths_refused_without_calling_model(self):
        env = Path(self.tmp.name) / ".env"
        env.write_text("TOKEN=abc")
        for bad in (str(env), str(Path(self.tmp.name) / "nope.txt"), "~/.ssh/id_ed25519"):
            r = self.c.call(task="x", paths=[bad])
            self.assertTrue(r["isError"], bad)
        self.assertEqual(FakeOllama.calls, [])

    def test_truncation_flagged(self):
        FakeOllama.done = "length"
        r = self.c.call(task="x", text="hello")
        self.assertIn("[TRUNCATED", r["content"][0]["text"])
        self.assertTrue(json.loads(self.log.read_text().splitlines()[0])["truncated"])

    def test_oversize_material_refused(self):
        f = Path(self.tmp.name) / "big.txt"
        f.write_text("x" * (300 * 1024))
        self.assertTrue(self.c.call(task="x", paths=[str(f)])["isError"])

    def test_model_down_is_a_tool_error_not_a_crash(self):
        c = Client("http://127.0.0.1:9", self.log)
        try:
            r = c.call(task="x", text="hi")
            self.assertTrue(r["isError"])
            self.assertIn("unavailable", r["content"][0]["text"])
            self.assertEqual(c.send("ping")["result"], {})     # transport still alive
        finally:
            c.close()

    def test_missing_model_is_a_clear_tool_error(self):
        c = Client(self.url, self.log, model=None)
        try:
            r = c.call(task="x", text="hi")
            self.assertTrue(r["isError"])
            self.assertIn("LOCAL_MODEL", r["content"][0]["text"])
            self.assertEqual(FakeOllama.calls, [])
        finally:
            c.close()

    def test_unknown_method_and_empty_task(self):
        self.assertEqual(self.c.send("nope")["error"]["code"], -32601)
        self.assertTrue(self.c.call(task="  ")["isError"])


if __name__ == "__main__":
    unittest.main()
