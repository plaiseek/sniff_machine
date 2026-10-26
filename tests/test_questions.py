"""ask_transcripts.py's question step against the fake vLLM server, on a made-up content in a
database of its own (no GPU, no network).

    python3.11 -m unittest discover tests
"""

import contextlib
import io
import pathlib
import tempfile
import threading
import time
import unittest

import helpers.database as db
from fake_vllm import MODEL, FakeVllm
from helpers.pipeline import run_steps
from helpers.questions import _merge, _passages, question_step, quote_found, settings, windows
from helpers.transcripts import normalize
from helpers.vllm_client import ServerHints, Slots, VllmServers, assert_vllm_servers, server_kv_tokens

QUESTION = "Où parle-t-on de Knafo ?"
SENTENCES = 2000  # 6 words each: 12,000 words, 3 windows
KEYWORD_EVERY = 97  # sentences 1, 98, 195, ... (numbered from 1) name the keyword: 21 of them


def sentence(i: int) -> str:
    return f"Phrase {i} parle de Knafo aujourd'hui." if i % KEYWORD_EVERY == 0 else f"Phrase {i} parle de la météo."


class QuestionStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path = db.db_path
        db.db_path = pathlib.Path(self.tmp.name) / "test.db"
        words = [w for i in range(SENTENCES) for w in sentence(i).split()]
        self.words = len(words)
        with contextlib.redirect_stdout(io.StringIO()), db.connect() as db_conn:
            self.content = db.add_content(db_conn, "Test", "x1", "https://example.com/x1", "Test", 4800.0, "Tester", "2026-01-01")
            rows = [(w, normalize(w), 400 * n, 400 * n + 300, 0.9) for n, w in enumerate(words)]
            db.add_transcription(db_conn, self.content.id, pathlib.Path("cache/whisper_result/x1.json"), "fr", rows)

    def tearDown(self):
        db.db_path = self.saved_path
        self.tmp.cleanup()

    def ask(self, fake: FakeVllm, clients: int = 8, kv_tokens: int = 440_000):
        """(report, findings by verdict, question_contents row)."""
        vllm = VllmServers([fake.address], MODEL, 262144, True)
        with db.connect() as db_conn:
            question_id = db.add_question(db_conn, QUESTION, None, MODEL, settings(True))
        step = question_step(vllm, ServerHints(clients, kv_tokens, 300), question_id, QUESTION, "", None, self.words)
        with contextlib.redirect_stdout(io.StringIO()):
            report = run_steps([self.content], [step])
        with db.connect() as db_conn:
            findings = {}
            for row in db.get_findings(db_conn, question_id, verdicts=("yes", "unsure", "no")):
                findings.setdefault(row[7], []).append(row)
            done = db_conn.execute("SELECT windows, candidates FROM question_contents WHERE question_id = ?", (question_id,)).fetchone()
        return report, findings, done

    def test_finds_every_passage_with_clients_requests_at_once(self):
        with FakeVllm() as fake:
            report, findings, done = self.ask(fake, clients=3)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(len(findings["yes"]), 21)
        self.assertEqual(done[0], 3)  # windows
        self.assertEqual(fake.max_active, 3)
        self.assertTrue(all(0 <= row[8] <= 1 for row in findings["yes"]))  # confidences clamped

    def test_never_more_than_the_kv_cache_holds(self):
        with FakeVllm() as fake:
            report, findings, _ = self.ask(fake, kv_tokens=1)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(fake.max_active, 1)
        self.assertEqual(len(findings["yes"]), 21)

    def test_a_cut_window_is_read_again_in_halves(self):
        with FakeVllm(screen_length_first=1) as fake:
            report, findings, done = self.ask(fake)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(done[0], 5)  # 3 windows, one of them read again as 2 halves
        self.assertEqual(len(findings["yes"]), 21)

    def test_an_empty_answer_is_asked_again_once_then_unsure(self):
        with FakeVllm(empty_lines={98: 1, 195: 2}) as fake:
            report, findings, _ = self.ask(fake)
        self.assertEqual(report.exit_code(), 0)
        self.assertEqual(len(findings["yes"]), 20)  # sentence 98 answered the second time
        [unsure] = findings["unsure"]
        self.assertEqual(unsure[2], 194 * 6)  # first word of sentence 195
        self.assertTrue(unsure[12].startswith("No answer"))

    def test_an_error_fails_the_content_and_cancels_its_other_requests(self):
        with FakeVllm(fail_checks=(1,)) as fake:
            report, findings, done = self.ask(fake, clients=1)
        self.assertEqual(report.exit_code(), 1)
        self.assertEqual(len(report.failed["question"]), 1)
        self.assertEqual(fake.checks, 1)  # the other passages were never sent
        self.assertIsNone(done)

    def test_asked_again_it_reads_nothing_new(self):
        with FakeVllm() as fake:
            self.ask(fake)
            sent = len(fake.requests)
            report, findings, _ = self.ask(fake)
        self.assertEqual(report.already_done, 1)
        self.assertEqual(len(fake.requests), sent)
        self.assertEqual(len(findings["yes"]), 21)


class VllmServersTest(unittest.TestCase):
    def test_finds_whether_the_model_thinks(self):
        for reasoning in (True, False):
            with FakeVllm(reasoning=reasoning) as fake, contextlib.redirect_stdout(io.StringIO()):
                vllm = assert_vllm_servers([fake.address])
            self.assertEqual((vllm.model, vllm.reasoning), (MODEL, reasoning))

    def test_reads_the_kv_cache_size(self):
        with FakeVllm(kv_cache_tokens=123_456) as fake:
            self.assertEqual(server_kv_tokens(fake.address), 123_456)


class SlotsTest(unittest.TestCase):
    def test_waiting_requests_go_by_priority(self):
        slots = Slots(ServerHints(clients=1, kv_tokens=100, tokens_per_s=1))
        order, release = [], threading.Event()

        def take(priority):
            with slots.slot(priority, 1):
                order.append(priority)
                if priority == -1:
                    release.wait()

        threads = [threading.Thread(target=take, args=(-1,))]
        threads[0].start()
        while slots.running < 1:  # the others must wait behind it
            time.sleep(0.01)
        for n, priority in enumerate((2, 0, 1), 1):
            threads.append(threading.Thread(target=take, args=(priority,)))
            threads[-1].start()
            while len(slots.waiting) < n:
                time.sleep(0.01)
        release.set()
        for thread in threads:
            thread.join()
        self.assertEqual(order, [-1, 0, 1, 2])

    def test_requests_share_the_kv_cache(self):
        slots = Slots(ServerHints(clients=8, kv_tokens=10, tokens_per_s=1))
        active, most, lock = [0], [0], threading.Lock()

        def take(tokens):
            with slots.slot(0, tokens):
                with lock:
                    active[0] += 1
                    most[0] = max(most[0], active[0])
                time.sleep(0.05)
                with lock:
                    active[0] -= 1

        threads = [threading.Thread(target=take, args=(tokens,)) for tokens in (6, 6, 50)]  # 50: alone
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(most[0], 1)


class PassagesTest(unittest.TestCase):
    sentences = [(6 * i, 6 * i + 5, 2400 * i, 2400 * i + 2000, None, f"s{i}") for i in range(3000)]

    def test_windows_cover_every_sentence_and_overlap(self):
        found = windows(self.sentences)
        self.assertEqual(found[0][0], 0)
        self.assertEqual(found[-1][1], len(self.sentences))
        for (_, end), (start, _) in zip(found, found[1:]):
            self.assertLess(start, end)

    def test_numbers_out_of_the_window_are_dropped_or_clipped(self):
        answer = {"passages": [{"first": 12, "last": 10, "why": ""}, {"first": 900, "last": 950, "why": ""}, {"first": "x"}]}
        self.assertEqual(_passages(answer, 0, 100, None), [(9, 11, "")])
        self.assertEqual(_passages(answer, 0, 100, {10}), [(10, 10, "")])

    def test_overlapping_passages_are_merged_then_cut(self):
        merged = _merge([(0, 3, "a"), (4, 5, "b"), (2, 20, "a"), (40, 41, "c")])
        self.assertEqual(merged, [(0, 11, "a; b"), (12, 20, "a; b"), (40, 41, "c")])

    def test_a_quote_may_fix_a_misheard_word(self):
        self.assertTrue(quote_found("on parle de Knafo aujourd'hui", "Phrase 3 on parle de Knapho aujourd'hui."))
        self.assertFalse(quote_found("rien à voir avec ça", "Phrase 3 on parle de Knafo."))


if __name__ == "__main__":
    unittest.main()
