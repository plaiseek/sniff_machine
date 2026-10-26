"""A fake vLLM server for the tests: the OpenAI-compatible API that helpers/vllm_client.py uses,
answering like a model that finds the sentences with a keyword.

    with FakeVllm(keyword="knafo") as fake:
        ...  # send requests to fake.address
        fake.max_active, fake.screens, fake.checks  # what it saw
"""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fake-qwen"
LINE = re.compile(r"^(>> )?(\d+)\| (.*)$", re.MULTILINE)


class FakeVllm:
    def __init__(
        self,
        keyword: str = "knafo",
        delay_s: float = 0.05,
        reasoning: bool = True,
        screen_length_first: int = 0,
        empty_lines: dict | None = None,
        fail_checks: tuple = (),
        kv_cache_tokens: int = 456_682,
    ):
        """screen_length_first: the first screening answers are cut (finish_reason "length").
        empty_lines: {sentence number: n}: the first n checks of a passage starting at that sentence
        get an empty answer (the model ended while thinking). fail_checks: the checks (counted from
        1, in order of arrival) that get an HTTP 500."""
        self.keyword = re.compile(keyword, re.IGNORECASE)
        self.delay_s, self.reasoning = delay_s, reasoning
        self.screen_length_first, self.fail_checks = screen_length_first, fail_checks
        self.empty_lines = dict(empty_lines or {})
        self.kv_cache_tokens = kv_cache_tokens
        self.lock = threading.Lock()
        self.active = self.max_active = self.screens = self.checks = 0
        self.requests = []  # (kind, request body)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/health":
                    return self._send(200, b"")
                if self.path == "/v1/models":
                    return self._json(200, {"object": "list", "data": [{"id": MODEL, "max_model_len": 262144}]})
                if self.path == "/metrics":
                    line = f'vllm:cache_config_info{{block_size="1648",kv_cache_size_tokens="{fake.kv_cache_tokens}"}} 1.0\n'
                    return self._send(200, line.encode())
                self._json(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                with fake.lock:
                    fake.active += 1
                    fake.max_active = max(fake.max_active, fake.active)
                try:
                    code, answer = fake.answer(body)
                    self._json(code, answer)
                finally:
                    with fake.lock:
                        fake.active -= 1

            def _json(self, code, body):
                self._send(code, json.dumps(body).encode())

            def _send(self, code, data):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def answer(self, body: dict) -> tuple[int, dict]:
        """(HTTP code, JSON body) for a chat completion request."""
        response_format = body.get("response_format") or {}
        thinking = (body.get("chat_template_kwargs") or {}).get("enable_thinking")
        if body.get("model") != MODEL or response_format.get("type") != "json_schema" or not isinstance(thinking, bool):
            return 400, {"error": "bad request"}
        schema = response_format["json_schema"]["schema"]["properties"]
        user = body["messages"][-1]["content"]
        time.sleep(self.delay_s)
        finish, content = "stop", None
        if "count" in schema:  # the probe of assert_vllm_servers()
            kind, answer = "probe", {"count": 3}
        elif "passages" in schema:
            kind = "screen"
            with self.lock:
                self.screens += 1
                cut = self.screens <= self.screen_length_first
            lines = [(int(n), text) for _, n, text in LINE.findall(user)]
            max_why = schema["passages"]["items"]["properties"]["why"].get("maxLength", 10**9)
            passages = [{"first": n, "last": n, "why": "mentions the keyword"[:max_why]} for n, text in lines if self.keyword.search(text)]
            if lines:  # numbers the code must drop or clip
                passages.append({"first": 999_999, "last": 1_000_000, "why": "out of range"})
                passages.append({"first": lines[-1][0] + 5, "last": lines[-1][0] - 1, "why": "reversed, past the end"})
            answer = {"passages": passages}
            if cut:
                finish, content = "length", '{"passages": [{"first": 1, "la'
        elif "verdict" in schema:
            kind = "check"
            marked = [(int(n), text) for m, n, text in LINE.findall(user) if m]
            with self.lock:
                self.checks += 1
                k = self.checks
                first_line = marked[0][0] if marked else None
                empty = self.empty_lines.get(first_line, 0) > 0
                if empty:
                    self.empty_lines[first_line] -= 1
            if k in self.fail_checks:
                return 500, {"error": f"fake failure of check {k}"}
            text = " ".join(t for _, t in marked)
            hit = bool(self.keyword.search(text))
            answer = {
                "first": marked[0][0] if marked else 1,
                "last": marked[-1][0] if marked else 1,
                "quote": " ".join(text.split()[:6]) if hit else "",
                "attribution": "own words",
                "meets": "the keyword" if hit else "",
                "against": "" if hit else "no keyword",
                "explanation": "The passage has the keyword." if hit else "No keyword.",
                "verdict": "yes" if hit else "no",
                "confidence": 1.7 if k % 5 == 0 else 0.9,  # out of [0, 1] now and then: clamped
            }
            if empty:
                content = ""
        else:
            return 400, {"error": "unknown schema"}
        with self.lock:
            self.requests.append((kind, body))
        if content is None:
            content = json.dumps(answer)
        message = {"role": "assistant", "content": content}
        if self.reasoning and thinking:
            message["reasoning"] = "Let me think."
        return 200, {
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": sum(len(m["content"]) for m in body["messages"]) // 4, "completion_tokens": len(content) // 4 + 1},
        }
