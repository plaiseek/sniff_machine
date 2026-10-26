"""Ask an LLM where something is said in a content's transcription (ask_transcripts.py), in two passes:

1. Screening: the transcription in windows of WINDOW_WORDS words overlapping by OVERLAP_WORDS, one
   numbered sentence per line, with times and speaker labels. Without thinking, the LLM lists every
   passage that may match: missing one is worse than listing one too many.
2. Checking: each passage, among CONTEXT_WORDS words before and after it. The LLM thinks (when the
   vLLM server has a reasoning parser), then gives the sentences that match, a quote, what the
   speaker does with these words (own words, approving or criticizing a quote...), what of the
   question they meet and what speaks against it, why, its verdict and its confidence.

The LLM cites sentence numbers, never times: they are mapped back to the words, and a number out of
the text it was shown is dropped. Its answers are leads to listen to, not verdicts.

The windows of a content are read at once, then its passages are checked at once: each server keeps
ServerHints.clients requests in flight (Slots), the contents that started first going first.

    with db.connect() as db_conn:
        vllm = assert_vllm_servers(servers.VLLM_SERVERS)
        question_id = db.add_question(db_conn, question_text(question, criteria), speaker, vllm.model, settings(vllm.reasoning))
    report = run_steps(contents, [question_step(vllm, hints, question_id, question, criteria, speaker, words)])
"""

import itertools
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import dataclass

import helpers.database as db
from helpers.pipeline import Step
from helpers.transcripts import content_sentences, hms, normalize
from helpers.vllm_client import ServerHints, Slots, VllmServers, chat_json

# Change it when the prompts change: the same question is then asked again
PROMPT_VERSION = 3
WINDOW_WORDS = 5000  # about 10k tokens, 30 min of speech
OVERLAP_WORDS = 300  # about 2 min: a passage cut by the end of a window is whole in the next one
CONTEXT_WORDS = 600  # before and after a passage, in the second pass
MAX_PASSAGE_SENTENCES = 12  # longer passages are checked in parts
TIME_EVERY_MS = 60_000  # a line gives the time at each new speaker, and at least this often
SCREEN_MAX_TOKENS = 4096  # a window whose passages do not fit is read again in two halves
SCREEN_WHY_CHARS = 200  # without thinking, the LLM would reason at length in why it lists a passage
CHECK_MAX_TOKENS = 16384  # with the thinking
# Sampling recommended for Qwen3, without and with thinking (greedy decoding makes thinking loop)
SCREEN_SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20}
CHECK_SAMPLING = {"temperature": 0.6, "top_p": 0.95, "top_k": 20}
CHARS_PER_TOKEN = 3  # a bit less than in French with Qwen3: a prompt is never thought smaller than it is
QUOTE_MIN_SHARE = 0.8  # a quote is found when this share of its words are in the passage

VERDICTS = ("yes", "unsure", "no")
ATTRIBUTIONS = ("own words", "approves what they quote or read", "quoting, reading or reporting", "quotes to criticize", "unclear")
# The second pass's other answers, stored as lines of the explanation (after it)
EXPLANATION_LINES = (("meets", "Meets"), ("against", "Against"))

_TRANSCRIPTION = """How the transcription is written:
- It comes from speech recognition: words can be misheard, and punctuation is often missing.
- Each line is one sentence, after its number: "812| ...".
- A line "[H:MM:SS] LABEL:" gives the time, and who speaks from there on. The labels come from
  automatic speaker separation, which can be wrong. A name after a label says who it is: set by a
  person, or guessed from the voice. Never decide who someone is from what is said."""

# The system messages only depend on the question, so vLLM's prefix caching could read them once for
# every request: but it caches whole blocks (1,648 tokens for Qwen3.8-27B), and a shorter prompt (this
# one is about 1.2k tokens with criteria/haine_loi_1881.txt) is read every time
_SCREEN_SYSTEM = """You read the automatic transcription of a video or audio, and find the passages that match a question.

{transcription}

{question}

List every passage that may match, even when you are not sure, including what the speakers read or
quote: each one will be checked again with more context, and missing a passage is worse than listing
one too many. Do not leave a passage out for a reason the context could change (who is meant, irony,
a quote, an exception of the criteria): the check decides. A passage is one sentence or a few
consecutive sentences: give the numbers of its first and last sentences, and why it may match in at
most 15 words. Write in the language of the question. If nothing may match, give an empty list."""

_CHECK_SYSTEM = """You check whether a passage of the automatic transcription of a video or audio matches a question.

{transcription}

{question}

The lines of the passage start with ">>"; the lines around them are context. Read them before
deciding: the speaker may be quoting someone, reading a text or messages, reacting to a video being
played, joking or being ironic. Then give:
- first, last: the numbers of the first and last sentences that match (of the passage, if it does
  not match). They can go a little beyond the marked lines.
- quote: the words that match, copied exactly from the transcription ("" if none).
- attribution: what the speaker does with these words: "own words" (says them in their own name),
  "approves what they quote or read" (repeats, reads or reports someone else's words and agrees
  with them or takes them up), "quoting, reading or reporting" (without taking a stance), "quotes to
  criticize" (to reject, condemn or mock them), or "unclear".
- meets: in a few words, what of the question (and of its criteria) the passage meets ("" if nothing).
- against: in a few words, what speaks against it matching: the context, irony, someone else's
  words, an unclear transcription ("" if nothing).
- explanation: in two or three sentences, your reasoning, weighing both.
- verdict: "yes" if the passage matches the question, "no" if it does not, "unsure" if it could
  match but the context or the transcription leaves a real doubt.
- confidence: from 0 to 1, how sure you are of the verdict.
Write in the language of the question."""

_SCREEN_SCHEMA = {
    "type": "object",
    "properties": {
        "passages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                    "why": {"type": "string", "maxLength": SCREEN_WHY_CHARS},
                },
                "required": ["first", "last", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["passages"],
    "additionalProperties": False,
}

# The answer is written in this order: what speaks against comes before the verdict
_CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "first": {"type": "integer"},
        "last": {"type": "integer"},
        "quote": {"type": "string"},
        "attribution": {"type": "string", "enum": list(ATTRIBUTIONS)},
        "meets": {"type": "string"},
        "against": {"type": "string"},
        "explanation": {"type": "string"},
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "confidence": {"type": "number"},
    },
    "required": ["first", "last", "quote", "attribution", "meets", "against", "explanation", "verdict", "confidence"],
    "additionalProperties": False,
}


def settings(reasoning: bool) -> dict:
    """What, besides the question and the model, makes the answers (stored with the question)."""
    return {
        "prompt": PROMPT_VERSION,
        "window_words": WINDOW_WORDS,
        "overlap_words": OVERLAP_WORDS,
        "context_words": CONTEXT_WORDS,
        "max_passage_sentences": MAX_PASSAGE_SENTENCES,
        "thinking": reasoning,
    }


def question_text(question: str, criteria: str) -> str:
    """A question with its criteria, as stored (db.add_question())."""
    return question + (f"\n\nCriteria:\n{criteria}" if criteria else "")


def speaker_labels_named(labels: dict, name: str) -> set[str]:
    """The labels of a diarization (db.get_speaker_labels()) that are this speaker, by a person or by voice."""
    return {label for label, (_, label_name, _, _) in labels.items() if label_name == name}


def _words(sentence: tuple) -> int:
    return sentence[1] - sentence[0] + 1


def windows(sentences: list[tuple]) -> list[tuple[int, int]]:
    """The windows of the first pass, as (start, end) sentence indexes (end excluded): about
    WINDOW_WORDS words each, the next starting about OVERLAP_WORDS words before the end."""
    result, start = [], 0
    while start < len(sentences):
        end, words = start, 0
        while end < len(sentences) and (end == start or words + _words(sentences[end]) <= WINDOW_WORDS):
            words += _words(sentences[end])
            end += 1
        result.append((start, end))
        if end == len(sentences):
            break
        next_start, overlap = end, 0
        while next_start - 1 > start and overlap + _words(sentences[next_start - 1]) <= OVERLAP_WORDS:
            next_start -= 1
            overlap += _words(sentences[next_start])
        start = next_start
    return result


def _describe(labels: dict, label: str | None, diarized: bool) -> str:
    """A label as the LLM sees it: with who it is when known."""
    if label is None:
        return "speaker unknown" if diarized else ""
    if label not in labels:
        return label
    _, name, source, similarity = labels[label]
    if source == "human":
        return f"{label} ({name}, named by a person)" if name else f"{label} (nobody known, said a person)"
    if name and similarity is not None:
        return f"{label} (maybe {name}: voice similarity {similarity:.2f})"
    return label


def _header(content: db.Content, sentences: list[tuple], labels: dict, diarized: bool) -> str:
    lines = [f"Title: {content.title or '?'}", f"Channel: {content.uploader or '?'}, uploaded {(content.upload_date or '?')[:10]}"]
    if diarized:
        found = sorted({s[4] for s in sentences if s[4]})
        lines.append("Speakers (from automatic speaker separation): " + ", ".join(_describe(labels, label, True) for label in found))
    else:
        lines.append("Speakers: not separated, who speaks is unknown")
    return "\n".join(lines)


def _lines(sentences: list[tuple], start: int, end: int, labels: dict, diarized: bool, marked: range = range(0)) -> str:
    """Sentences start..end-1, numbered from 1 in the whole content."""
    out, speaker, last_time = [], object(), None
    for i in range(start, end):
        _, _, start_ms, _, label, text = sentences[i]
        if label != speaker or last_time is None or start_ms - last_time >= TIME_EVERY_MS:
            who = _describe(labels, label, diarized)
            out.append(f"[{hms(start_ms)}]" + (f" {who}:" if who else ""))
            speaker, last_time = label, start_ms
        out.append(f"{'>> ' if i in marked else ''}{i + 1}| {text}")
    return "\n".join(out)


def _eligible_note(wanted: set[str] | None, speaker: str | None) -> str:
    if not wanted:
        return ""
    return f"Only the sentences of {', '.join(sorted(wanted))} ({speaker}) can match: read the others as context.\n"


def _passages(answer: dict, start: int, end: int, eligible: set[int] | None) -> list[tuple[int, int, str]]:
    """The passages of a first-pass answer, as (first, last, why) sentence indexes in start..end-1
    (and narrowed to the eligible sentences): the others are dropped."""
    result = []
    for passage in answer.get("passages") or []:
        try:
            first, last, why = int(passage["first"]) - 1, int(passage["last"]) - 1, str(passage.get("why", ""))
        except (KeyError, TypeError, ValueError):
            continue
        first, last = max(min(first, last), start), min(max(first, last), end - 1)
        if eligible is not None:
            inside = [i for i in range(first, last + 1) if i in eligible]
            first, last = (inside[0], inside[-1]) if inside else (1, 0)
        if first <= last:
            result.append((first, last, why))
    return result


def _merge(passages: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """Overlapping or adjacent passages (e.g. found by two windows) as one, then cut in parts of at
    most MAX_PASSAGE_SENTENCES sentences."""
    merged = []
    for first, last, why in sorted(passages):
        if merged and first <= merged[-1][1] + 1:
            m_first, m_last, m_whys = merged[-1]
            merged[-1] = (m_first, max(m_last, last), m_whys if why in m_whys else m_whys + [why])
        else:
            merged.append((first, last, [why]))
    parts = []
    for first, last, whys in merged:
        for part in range(first, last + 1, MAX_PASSAGE_SENTENCES):
            parts.append((part, min(part + MAX_PASSAGE_SENTENCES - 1, last), "; ".join(w for w in whys if w)))
    return parts


def _halves(sentences: list[tuple], start: int, end: int) -> list[tuple[int, int]]:
    """A window in two, the second half starting about OVERLAP_WORDS words before the middle."""
    middle = (start + end) // 2
    second, overlap = middle, 0
    while second - 1 > start and overlap + _words(sentences[second - 1]) <= OVERLAP_WORDS:
        second -= 1
        overlap += _words(sentences[second])
    return [(start, middle), (second, end)]


def _context(sentences: list[tuple], first: int, last: int) -> tuple[int, int]:
    """(start, end) of the excerpt around a passage: CONTEXT_WORDS words before and after it."""
    start, words = first, 0
    while start > 0 and words < CONTEXT_WORDS:
        start -= 1
        words += _words(sentences[start])
    end, words = last + 1, 0
    while end < len(sentences) and words < CONTEXT_WORDS:
        words += _words(sentences[end])
        end += 1
    return start, end


def quote_found(quote: str, text: str) -> bool:
    """Whether most words of a quote are in a text (the LLM may fix a misheard word)."""
    quote_words = [w for w in map(normalize, quote.split()) if w]
    if not quote_words:
        return False
    text_words = Counter(w for w in map(normalize, text.split()) if w)
    found = sum(min(n, text_words[w]) for w, n in Counter(quote_words).items())
    return found / len(quote_words) >= QUOTE_MIN_SHARE


def _explanation(answer: dict | None, finish_reason: str | None) -> str:
    """The explanation of a second-pass answer, then its EXPLANATION_LINES."""
    if answer is None:
        return f"No answer within {CHECK_MAX_TOKENS} tokens" if finish_reason == "length" else "No answer: the LLM ended while thinking, twice"
    lines = [str(answer.get("explanation") or "")]
    for key, label in EXPLANATION_LINES:
        value = " ".join(str(answer.get(key) or "").split())
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def _finding(sentences, first, last, reply, screening) -> tuple:
    """A row of db.replace_findings()."""
    answer = reply.answer
    in_passage = sentences[first:last + 1]
    text = " ".join(s[5] for s in in_passage)
    quote = str(answer.get("quote") or "") if answer else ""
    confidence = answer.get("confidence") if answer else None
    confidence = min(max(float(confidence), 0.0), 1.0) if isinstance(confidence, (int, float)) else None
    attribution = answer.get("attribution") if answer else None
    return (
        in_passage[0][0],
        in_passage[-1][1],
        min(s[2] for s in in_passage),
        max(s[3] for s in in_passage),
        ",".join(dict.fromkeys(s[4] for s in in_passage if s[4])) or None,
        answer["verdict"] if answer and answer.get("verdict") in VERDICTS else "unsure",
        confidence,
        attribution if attribution in ATTRIBUTIONS else None,
        quote,
        int(quote_found(quote, text)),
        _explanation(answer, reply.finish_reason),
        screening,
    )


class _Cancelled(Exception):
    """A request not sent because another one of the same content failed."""


def _all_at_once(calls: list) -> list:
    """The results of the calls, each run in a daemon thread (Ctrl+C stops the script at once).
    A call gets a threading.Event: once it is set, it must not send its request. The first error
    sets it, and is raised once the other calls are over."""
    stop = threading.Event()
    results, errors = [None] * len(calls), []

    def run(n, call):
        try:
            results[n] = call(stop)
        except BaseException as e:
            errors.append(e)
            stop.set()

    threads = [threading.Thread(target=run, args=(n, call), daemon=True) for n, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for e in errors:
        if not isinstance(e, _Cancelled):
            raise e
    return results


class _Progress:
    """How fast the servers write, and the time left at that speed."""

    def __init__(self, words: int, tokens_per_s: float):
        self.lock = threading.Lock()
        self.started = time.monotonic()
        self.words_left, self.tokens_per_s = words, tokens_per_s
        self.words_done = self.written_done = self.written = 0

    def wrote(self, tokens: int) -> None:
        with self.lock:
            self.written += tokens

    def content_done(self, words: int, written: int) -> str:
        with self.lock:
            self.words_left = max(self.words_left - words, 0)
            self.words_done += words
            self.written_done += written
            speed = self.written / max(time.monotonic() - self.started, 1)
            left_s = self.words_left * self.written_done / max(self.words_done, 1) / max(speed, 1)
        return f"{speed:.0f} tokens/s written so far (expected {self.tokens_per_s:.0f}), about {hms(int(left_s * 1000))} left"


@dataclass
class _Run:
    """What the contents of one question_step() share."""

    vllm: VllmServers
    question_id: int
    question: str
    criteria: str
    speaker: str | None
    slots: dict  # server -> Slots
    progress: _Progress
    started: itertools.count  # contents in the order they start: the first ones go first on the servers


def ask_content(db_conn: sqlite3.Connection, run: _Run, server: str, content: db.Content) -> str:
    """Ask the question on the whole transcription of a content and store what was found in place
    of what was found before. Returns a description of it."""
    state = db.get_transcription_state(db_conn, content.id)
    if state is None:
        raise ValueError(f"The words of content {content.id} are not stored (see store_words.py)")
    diarization_id = state[1]
    labels = db.get_speaker_labels(db_conn, diarization_id) if diarization_id else {}
    sentences = content_sentences(db_conn, content.id)
    wanted = speaker_labels_named(labels, run.speaker) if run.speaker else None
    eligible = {i for i, s in enumerate(sentences) if s[4] in wanted} if wanted is not None else None
    diarized = diarization_id is not None
    header = _header(content, sentences, labels, diarized) + "\n" + _eligible_note(wanted, run.speaker)
    question = f"The question:\n{question_text(run.question, run.criteria)}"
    priority, slots = next(run.started), run.slots[server]
    tokens, asked_again, lock = [0, 0], [0], threading.Lock()

    def chat(system, user, schema, thinking, max_tokens, sampling):
        """A request, as a call of _all_at_once() returning its Reply: it waits for a slot of the
        server, and is sent again once if the LLM ended while thinking (it samples: it may not again)."""

        def call(stop):
            for attempt in range(2):
                with slots.slot(priority, (len(system) + len(user)) // CHARS_PER_TOKEN + max_tokens):
                    if stop.is_set():
                        raise _Cancelled()
                    reply = chat_json(server, run.vllm.model, system, user, schema, thinking, max_tokens, sampling)
                with lock:
                    tokens[0] += reply.prompt_tokens
                    tokens[1] += reply.completion_tokens
                    asked_again[0] += attempt
                run.progress.wrote(reply.completion_tokens)
                if reply.answer is not None or reply.finish_reason == "length":
                    break
            return reply

        return call

    screen_system = _SCREEN_SYSTEM.format(transcription=_TRANSCRIPTION, question=question)
    read = [(start, end) for start, end in windows(sentences) if eligible is None or eligible & set(range(start, end))]
    found, todo, n_read = [], read, len(read)
    while todo:
        answers = _all_at_once([
            chat(
                screen_system,
                f"{header}Part of the transcription, from {hms(sentences[start][2])} to {hms(sentences[end - 1][3])}:\n\n"
                f"{_lines(sentences, start, end, labels, diarized)}\n\nThe question, again:\n{run.question}",
                _SCREEN_SCHEMA, False, SCREEN_MAX_TOKENS, SCREEN_SAMPLING,
            )
            for start, end in todo
        ])
        again = []
        for (start, end), reply in zip(todo, answers):
            if reply.answer is not None:
                found += _passages(reply.answer, start, end, eligible)
            elif end - start < 2:
                raise ValueError(f"The first pass gave no answer for sentence {start + 1} ({reply.finish_reason})")
            else:  # too many passages for one answer, or it ended while thinking twice: in smaller parts
                again += [(a, b) for a, b in _halves(sentences, start, end) if eligible is None or eligible & set(range(a, b))]
        todo = again
        n_read += len(again)
    candidates = [(first, last, screening, *_context(sentences, first, last)) for first, last, screening in _merge(found)]

    check_system = _CHECK_SYSTEM.format(transcription=_TRANSCRIPTION, question=question)
    answers = _all_at_once([
        chat(
            check_system,
            f"{header}Excerpt from {hms(sentences[start][2])} to {hms(sentences[end - 1][3])}:\n\n"
            f"{_lines(sentences, start, end, labels, diarized, range(first, last + 1))}\n\n"
            f"Does the passage of sentences {first + 1} to {last + 1} (marked >>) match the question?\n"
            f"The question, again:\n{run.question}",
            _CHECK_SCHEMA, run.vllm.reasoning, CHECK_MAX_TOKENS, CHECK_SAMPLING,
        )
        for first, last, _, start, end in candidates
    ])
    findings = {}
    for (first, last, screening, start, end), reply in zip(candidates, answers):
        answer = reply.answer
        f_first, f_last = first, last
        if answer is not None:
            try:
                a_first, a_last = sorted((int(answer["first"]) - 1, int(answer["last"]) - 1))
                if start <= a_first and a_last < end and a_first <= last and first <= a_last:
                    f_first, f_last = a_first, a_last  # within the excerpt, and about the passage
            except (KeyError, TypeError, ValueError):
                pass
        if eligible is not None:
            inside = [i for i in range(f_first, f_last + 1) if i in eligible]
            f_first, f_last = (inside[0], inside[-1]) if inside else (first, last)
        row = _finding(sentences, f_first, f_last, reply, screening)
        # Two passages may end up on the same sentences: keep the surest verdict
        rank = (VERDICTS.index(row[5]), -(row[6] or 0))
        if row[0] not in findings or rank < findings[row[0]][0]:
            findings[row[0]] = (rank, row)

    rows = [row for _, row in sorted(findings.values(), key=lambda item: item[1][0])]
    db.replace_findings(db_conn, run.question_id, content.id, state, n_read, len(candidates), tuple(tokens), rows)
    counts = Counter(row[5] for row in rows)
    return (
        f"{n_read} windows, {len(candidates)} passages checked: {counts['yes']} yes, {counts['unsure']} unsure,"
        f" {counts['no']} no ({tokens[0]:,} tokens read, {tokens[1]:,} written"
        + (f", {asked_again[0]} asked again" if asked_again[0] else "")
        + f"; {run.progress.content_done(state[2], tokens[1])})"
    )


def question_step(
    vllm: VllmServers, hints: ServerHints, question_id: int, question: str, criteria: str, speaker: str | None, words: int
) -> Step:
    """Ask the question on the stored words of contents (see helpers.transcripts.update_transcripts()),
    `words` in all (for the time left). Needs no audio."""
    run = _Run(
        vllm, question_id, question, criteria, speaker,
        {address: Slots(hints) for address in vllm.addresses},
        _Progress(words, hints.tokens_per_s * len(vllm.addresses)),
        itertools.count(),
    )

    def is_done(db_conn, content):
        return db.question_done(db_conn, question_id, content.id)

    def run_content(db_conn, server, content, wav_path):
        return ask_content(db_conn, run, server, content)

    # As many contents at once as requests: a content between its two passes leaves room to another
    return Step("question", "Asking about", vllm.addresses, is_done, run_content, needs_audio=False, workers=hints.clients)
