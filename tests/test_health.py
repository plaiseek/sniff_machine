"""helpers/health.py: which listed model servers a step uses, against fake /health servers.

    python3.11 -m unittest discover tests
"""

import contextlib
import io
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from helpers.pyannote_client import DEFAULT_MODEL, assert_pyannote_servers
from helpers.speechbrain_client import assert_speechbrain_servers
from helpers.whisper_client import assert_whisper_servers
from helpers.wtpsplit_client import assert_wtpsplit_servers


class FakeHealth:
    """A server whose GET /health answers `status` with the JSON `answer`; `then`: the
    (status, answer) of the next requests, in order, the last one repeated."""

    def __init__(self, answer, status: int = 200, then: list | None = None):
        answers = [(status, answer)] + (then or [])

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                status, answer = answers.pop(0) if len(answers) > 1 else answers[0]
                body = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def closed_address() -> str:
    """An address where nothing listens: a server that is down."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


def quietly(function, *args):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        result = function(*args)
    return result, out.getvalue()


class ReadyServersTest(unittest.TestCase):
    def test_a_server_that_is_down_is_left_out(self):
        down = closed_address()
        with FakeHealth({"status": "ok"}) as whisper:
            ready, out = quietly(assert_whisper_servers, [down, whisper.address])
        self.assertEqual(ready, [whisper.address])
        self.assertIn(f"Whisper server {down} is not ready, it is left out", out)

    def test_exits_when_no_server_answers(self):
        with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(io.StringIO()):
            assert_whisper_servers([closed_address(), closed_address()])
        self.assertIn("No Whisper server", str(stop.exception.code))
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            assert_whisper_servers([])

    def test_a_loading_server_is_kept(self):
        with FakeHealth({"status": "ok"}) as ok, FakeHealth({"error": "loading"}, status=503) as loading:
            ready, _ = quietly(assert_whisper_servers, [ok.address, loading.address])
        self.assertEqual(ready, [ok.address, loading.address])

    def test_returns_the_model_of_the_servers_that_answer(self):
        with FakeHealth({"model": "spkrec"}) as a, FakeHealth({"model": "spkrec"}) as b:
            (model, ready), _ = quietly(assert_speechbrain_servers, [a.address, closed_address(), b.address])
        self.assertEqual((model, ready), ("spkrec", [a.address, b.address]))

    def test_old_pyannote_images_run_the_default_model(self):
        with FakeHealth({"status": "ok"}) as old:
            (model, _), _ = quietly(assert_pyannote_servers, [old.address])
        self.assertEqual(model, DEFAULT_MODEL)

    def test_an_answer_that_is_not_an_object_is_left_out(self):
        with FakeHealth([]) as bad, FakeHealth({"status": "ok"}) as whisper, FakeHealth(None) as worse, FakeHealth({"model": "p"}) as pyannote:
            ready, _ = quietly(assert_whisper_servers, [bad.address, whisper.address])
            (model, ready_pyannote), _ = quietly(assert_pyannote_servers, [worse.address, pyannote.address])
        self.assertEqual((ready, model, ready_pyannote), ([whisper.address], "p", [pyannote.address]))

    def test_a_server_that_names_no_model_is_left_out(self):
        with FakeHealth({"model": ""}) as bad, FakeHealth({"model": "sat"}) as good:
            (model, ready), _ = quietly(assert_wtpsplit_servers, [bad.address, good.address])
        self.assertEqual((model, ready), ("sat", [good.address]))

    def test_exits_when_the_servers_run_different_models(self):
        with FakeHealth({"model": "a"}) as a, FakeHealth({"model": "b"}) as b:
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(io.StringIO()):
                assert_speechbrain_servers([a.address, b.address])
        self.assertIn("different models", str(stop.exception.code))



@mock.patch("helpers.health.time.sleep")  # the waits for a busy server, every 10 s
class BusyServersTest(unittest.TestCase):
    def test_a_loading_server_is_waited_for_until_it_names_its_model(self, sleep):
        with FakeHealth({}, status=503, then=[(503, {}), (200, {"model": "spkrec"})]) as loading:
            (model, ready), _ = quietly(assert_speechbrain_servers, [loading.address])
        self.assertEqual((model, ready), ("spkrec", [loading.address]))

    def test_a_waited_for_server_that_fails_is_left_out(self, sleep):
        with FakeHealth({}, status=503, then=[(500, {})]) as failing, FakeHealth({}, status=503, then=[(503, {}), (200, {"model": "m"})]) as loading:
            (model, ready), out = quietly(assert_speechbrain_servers, [failing.address, loading.address])
        self.assertEqual((model, ready), ("m", [loading.address]))
        self.assertIn(f"{failing.address} is not ready, it is left out: HTTP 500", out)

    def test_exits_when_no_busy_server_answers_in_time(self, sleep):
        with FakeHealth({}, status=503) as loading:
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(io.StringIO()):
                assert_speechbrain_servers([loading.address], 0)
        self.assertIn("within 0 s", str(stop.exception.code))

    def test_exits_when_the_only_busy_server_fails(self, sleep):
        with FakeHealth({}, status=503, then=[(500, {})]) as failing:
            with self.assertRaises(SystemExit) as stop, contextlib.redirect_stdout(io.StringIO()):
                assert_wtpsplit_servers([failing.address])
        self.assertIn("No wtpsplit server", str(stop.exception.code))


if __name__ == "__main__":
    unittest.main()
