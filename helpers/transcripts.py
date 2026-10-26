"""The words of the transcriptions in the database (table words), each with the speaker turn it
falls in: the exclusive turn of the content's latest diarization that contains its middle. And
their sentences (table sentences), never across two speakers.

    with db.connect() as db_conn:
        failures = update_transcripts(db_conn)        # every content, or content_ids=[...]
        split_sentences(db_conn, content_id, "127.0.0.1:8060", model)   # or server=None: on punctuation

The words of a transcription result (cache/whisper_result/) are stored once: a result computed
again replaces them. A new diarization of the content aligns them again and deletes its sentences.
"""

import bisect
import datetime
import itertools
import re
import sqlite3
import unicodedata
from pathlib import Path

import helpers.database as db
from helpers.whisper_client import load_whisper_result, whisper_words


def hms(ms: int) -> str:
    return str(datetime.timedelta(seconds=ms // 1000))


def parse_seconds(text: str) -> float:
    """'75', '75.5', '1:15', '1:02:03.5' -> seconds (ValueError otherwise)."""
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def content_link(content: db.Content, ms: int) -> str:
    """The content's URL at this moment, for the platforms whose URLs take a start time."""
    if content.platform == "Youtube":
        return f"https://www.youtube.com/watch?v={content.external_id}&t={ms // 1000}s"
    if content.platform == "TwitchVod":
        h, rest = divmod(ms // 1000, 3600)
        return f"https://www.twitch.tv/videos/{content.external_id.lstrip('v')}?t={h}h{rest // 60}m{rest % 60}s"
    return f"{content.url} (at {hms(ms)})"


# What normalize() strips around a word: anything but letters and digits
_AROUND_WORD = re.compile(r"^[\W_]+|[\W_]+$")


def normalize(text: str) -> str:
    """The form of a word for counting: " L’histoire," -> "l'histoire", " 51%," -> "51", " ?" -> ""."""
    text = unicodedata.normalize("NFC", text).replace("’", "'").casefold()
    return _AROUND_WORD.sub("", text)


def store_words(db_conn: sqlite3.Connection, content_id: int, result_path: Path) -> int:
    """Store the words of a transcription result, replacing those stored for the content, and align
    them. Returns how many words were stored."""
    result = load_whisper_result(result_path)
    words = whisper_words(result)
    db.add_transcription(
        db_conn,
        content_id,
        result_path,
        result.get("language"),
        [(w.text.strip(), normalize(w.text), w.start_ms, w.end_ms, w.prob) for w in words],
    )
    db.align_words(db_conn, content_id)
    return len(words)


def aligned_share(db_conn: sqlite3.Connection, content_id: int) -> float | None:
    """The share of a content's words that fall in a turn (None without words)."""
    in_turn, total = db_conn.execute(
        "SELECT COUNT(turn_id), COUNT(*) FROM words WHERE content_id = ?", (content_id,)
    ).fetchone()
    return in_turn / total if total else None


def update_transcripts(db_conn: sqlite3.Connection, content_ids: list[int] | None = None) -> list[tuple[int, Exception]]:
    """Store the words of the transcription results not stored yet, and align again the words of
    the contents diarized since. Returns the failures, as (content id, error)."""
    failures = []
    unstored = db.get_unstored_transcriptions(db_conn, content_ids)
    for i, (content_id, path) in enumerate(unstored, 1):
        try:
            n = store_words(db_conn, content_id, path)
            share = aligned_share(db_conn, content_id)
            diarization_id = db.get_transcription_diarization(db_conn, content_id)
            aligned = f", {share:.0%} in a turn of diarization {diarization_id}" if diarization_id and share is not None else ""
            print(f"({i}/{len(unstored)}) Content {content_id}: {n} words stored{aligned}", flush=True)
        except Exception as e:
            failures.append((content_id, e))
            print(f"({i}/{len(unstored)}) Content {content_id}: cannot store the words of {path}:\n{e}", flush=True)
    unaligned = db.get_unaligned_transcriptions(db_conn, content_ids)
    for content_id in unaligned:
        diarization_id = db.align_words(db_conn, content_id)
        share = aligned_share(db_conn, content_id)
        if diarization_id is None:
            print(f"Content {content_id}: no diarization left, its words have no turn", flush=True)
        elif share is not None:
            print(f"Content {content_id}: words aligned to diarization {diarization_id}, {share:.0%} in a turn", flush=True)
    for content_id, e in failures:
        print(f"  Failed: content {content_id}: {e}")
    print(
        f"Words: {len(unstored) - len(failures)} transcriptions stored, {len(unaligned)} aligned again,"
        f" {len(failures)} failures."
    )
    return failures


PUNCTUATION = "punctuation"  # the splitter that needs no server
# A sentence ends after a word ending with one of these, and the closing quotes or brackets after it
_SENTENCE_END = re.compile(r"[.?!…][»\"')\]]*$")


def speaker_runs(words_with_speakers: list[tuple[db.Word, str | None]]) -> list[tuple[str | None, list[db.Word]]]:
    """The words in runs of one speaker label, in order. A word in no turn joins the run before it
    (the words before the first turn join the first run): the short gaps between the turns of a
    diarization never cut a sentence. Without any turn, all the words are one run of speaker None."""
    runs = []
    for word, speaker in words_with_speakers:
        if runs and (speaker is None or speaker == runs[-1][0]):
            runs[-1][1].append(word)
        elif runs and runs[-1][0] is None:
            runs[-1] = (speaker, runs[-1][1] + [word])
        else:
            runs.append((speaker, [word]))
    return runs


def split_on_punctuation(texts_per_run: list[list[str]]) -> list[list[int]]:
    """For each run of word texts, the index of the first word of each of its sentences: a sentence
    ends after a word ending with . ? ! or …, with the punctuation words after it (« ? », « » »)."""
    starts_per_run = []
    for texts in texts_per_run:
        starts, ended = [0], False
        for i, text in enumerate(texts):
            if ended and (any(ch.isalnum() for ch in text) or (text[:1] != "" and text[:1] in "«(\"[")):
                starts.append(i)
                ended = False
            if _SENTENCE_END.search(text):
                ended = True
        starts_per_run.append(starts)
    return starts_per_run


def split_with_wtpsplit(server: str, texts_per_run: list[list[str]]) -> tuple[list[list[int]], str]:
    """split_on_punctuation(), by the wtpsplit server (one request for all the runs), which also
    finds the sentences Whisper did not punctuate. Returns the model of the server too. A word
    belongs to the sentence its first character is in."""
    from helpers.wtpsplit_client import texts_to_sentences

    sentences_per_run, model = texts_to_sentences(server, [" ".join(texts) for texts in texts_per_run])
    starts_per_run = []
    for texts, sentences in zip(texts_per_run, sentences_per_run):
        offsets = list(itertools.accumulate((len(text) + 1 for text in texts[:-1]), initial=0))
        ends = list(itertools.accumulate(len(sentence) for sentence in sentences))
        sentence_of = [bisect.bisect_right(ends, offset) for offset in offsets]
        starts_per_run.append([i for i in range(len(texts)) if i == 0 or sentence_of[i] != sentence_of[i - 1]])
    return starts_per_run, model


def split_sentences(db_conn: sqlite3.Connection, content_id: int, server: str | None = None, model: str | None = None) -> str:
    """Split the stored words of a content into sentences, run of one speaker by run, with the
    wtpsplit server (which must run `model`), or on punctuation when server is None, and store them
    in place of its sentences. Aligns the words first if the content was diarized since. Returns a
    description of what was stored."""
    if db.get_unaligned_transcriptions(db_conn, [content_id]):
        db.align_words(db_conn, content_id)
    state = db.get_transcription_state(db_conn, content_id)
    if state is None:
        raise ValueError(f"The words of content {content_id} are not stored (see pipeline/store_words.py)")
    runs = speaker_runs(db.get_words_with_speakers(db_conn, content_id))
    texts_per_run = [[word.text for word in words] for _, words in runs]
    if server is None:
        starts_per_run, splitter = split_on_punctuation(texts_per_run), PUNCTUATION
    else:
        starts_per_run, splitter = split_with_wtpsplit(server, texts_per_run) if runs else ([], model)
        if splitter != model:
            raise RuntimeError(f"Server {server} runs {splitter}, not {model}")
    sentences = build_sentences(runs, starts_per_run)
    db.replace_sentences(db_conn, content_id, state, splitter, sentences)
    return f"{len(sentences)} sentences in {len(runs)} speaker runs ({splitter})"


def build_sentences(runs: list[tuple[str | None, list[db.Word]]], starts_per_run: list[list[int]]) -> list[tuple]:
    """The sentences of speaker runs, cut where a splitter said, as (first_idx, last_idx, start_ms,
    end_ms, speaker, text): the rows of db.replace_sentences()."""
    sentences = []
    for (speaker, words), starts in zip(runs, starts_per_run):
        for first, end in zip(starts, starts[1:] + [len(words)]):
            in_sentence = words[first:end]
            sentences.append((
                in_sentence[0].idx,
                in_sentence[-1].idx,
                min(word.start_ms for word in in_sentence),
                max(max(word.start_ms, word.end_ms) for word in in_sentence),
                speaker,
                " ".join(word.text for word in in_sentence),
            ))
    return sentences


def content_sentences(db_conn: sqlite3.Connection, content_id: int) -> list[tuple]:
    """The sentences of a content, as db.get_sentences() gives them: those stored, or its words cut
    on punctuation when none are (not split yet, see pipeline/split_sentences.py)."""
    sentences = db.get_sentences(db_conn, content_id)
    if not sentences:
        runs = speaker_runs(db.get_words_with_speakers(db_conn, content_id))
        sentences = build_sentences(runs, split_on_punctuation([[word.text for word in words] for _, words in runs]))
    return sentences
