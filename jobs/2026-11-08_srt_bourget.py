"""Job: the SRT of a local wav that is not in the database (2026-11-08): "🔴 Mélenchon PLIE LE GAME au
Bourget ？ À la santé du Colonel ： TOUT était FAUX ？ [React Nocturne].wav".

The wav (16 kHz mono) is cut in two at the quietest point near its middle; the halves are sent to the
Whisper servers that answer (servers.WHISPER_SERVERS, one half per free server) and the words of the
second half are shifted by the exact cut offset. Writes a readable SRT next to the wav, and in
exports/<this file's name>/ the Whisper results, the same SRT and a word-level SRT (one word per cue,
like helpers.whisper_client.whisper_result_to_srt). Nothing is written to the database or cache/.
Re-running it skips the halves already transcribed.

    python3.11 -u jobs/2026-11-08_srt_bourget.py > jobs/2026-11-08_srt_bourget.log 2>&1
"""

import os
import queue
import shutil
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT); sys.path.insert(0, str(ROOT))
import servers  # noqa: E402
from helpers.whisper_client import (  # noqa: E402
    WhisperWord,
    assert_whisper_servers,
    dtw_to_srt_timestamp,
    wav_to_whisper_result,
    whisper_result_to_words,
)

WAV = Path("/media/plaiseek/2To Storage/test/🔴 Mélenchon PLIE LE GAME au Bourget ？ À la santé du Colonel ： TOUT était FAUX ？ [React Nocturne].wav")
EXPORT = Path("exports") / Path(__file__).stem
# The two halves of the wav, deleted once both are transcribed
PARTS = Path("/tmp/claude-1000/-home-plaiseek-Projects-sniff-machine/184ca575-6b4c-424a-9412-ce7bfa6a1ce7/scratchpad/srt_bourget_parts")
LANGUAGE = "fr"

RATE = 16000
SEARCH_S = 90  # look for the quietest point within this many seconds of the middle
FRAME = 6400  # 0.4 s

LINE_CHARS = 42
MAX_CHARS = 2 * LINE_CHARS
MAX_CUE_MS = 7000
GAP_MS = 800
MIN_CUE_MS = 700
TAIL_MS = 300  # shown after the last word's start when its end time is no later
# Whisper's word end times are unreliable: some run minutes past the word (music, long silences)
MAX_WORD_MS = 1500


def find_cut(wav_path: Path) -> int:
    with wave.open(str(wav_path), "rb") as w:
        assert w.getframerate() == RATE and w.getnchannels() == 1 and w.getsampwidth() == 2, w.getparams()
        n = w.getnframes()
        lo = max(0, n // 2 - SEARCH_S * RATE)
        hi = min(n, n // 2 + SEARCH_S * RATE)
        w.setpos(lo)
        samples = np.frombuffer(w.readframes(hi - lo), dtype=np.int16).astype(np.float32)
    frames = samples[: len(samples) // FRAME * FRAME].reshape(-1, FRAME)
    rms = np.sqrt((frames**2).mean(axis=1))
    best = int(rms.argmin())
    cut = lo + best * FRAME + FRAME // 2
    cut -= cut % 16  # a whole number of milliseconds
    print(f"Cut at {cut / RATE:.2f} s (RMS {rms[best]:.1f}, median around it {np.median(rms):.1f})", flush=True)
    return cut


def write_part(wav_path: Path, part_path: Path, start: int, end: int) -> None:
    if part_path.is_file():
        return
    tmp = part_path.with_suffix(".part.wav")
    with wave.open(str(wav_path), "rb") as src, wave.open(str(tmp), "wb") as dst:
        dst.setparams(src.getparams())
        src.setpos(start)
        left = end - start
        while left > 0:
            chunk = min(left, 10 * 60 * RATE)
            dst.writeframes(src.readframes(chunk))
            left -= chunk
    tmp.rename(part_path)


def transcribe(server: str, part_path: Path, result_path: Path) -> Path:
    if result_path.is_file():
        print(f"Already transcribed: {result_path}", flush=True)
        return result_path
    print(f"[{server}] Transcribing {part_path}...", flush=True)
    wav_to_whisper_result(server, part_path, result_path, "fr")
    print(f"[{server}] Done -> {result_path}", flush=True)
    return result_path


def eff_end(w: WhisperWord) -> int:
    return w.start_ms + min(max(w.end_ms - w.start_ms, TAIL_MS), MAX_WORD_MS)


def build_cues(words: list[WhisperWord]) -> list[tuple[int, int, str]]:
    words = [w for w in words if w.text.strip()]
    groups, cur = [], []
    for i, w in enumerate(words):
        if cur:
            text = "".join(x.text for x in cur).strip()
            new_len = len(text) + len(w.text)
            gap = w.start_ms - eff_end(cur[-1])
            sentence_end = text.endswith((".", "?", "!", "…")) and len(text) >= 12
            comma_break = text.endswith((",", ";", ":")) and len(text) >= 55
            if (
                gap > GAP_MS
                or sentence_end
                or comma_break
                or new_len > MAX_CHARS
                or w.start_ms - cur[0].start_ms > MAX_CUE_MS
            ):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    cues = []
    for k, g in enumerate(groups):
        start = g[0].start_ms
        if cues:
            start = max(start, cues[-1][1])
        end = max(eff_end(g[-1]), start + MIN_CUE_MS)
        if k + 1 < len(groups):
            end = min(end, groups[k + 1][0].start_ms)
        end = max(end, start + 1)
        cues.append((start, end, wrap("".join(w.text for w in g).strip())))
    return cues


def wrap(text: str) -> str:
    if len(text) <= LINE_CHARS:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    # never break before a French "?", "!", ":" or ";" that has a space before it
    spaces = [i for i in spaces if i + 1 < len(text) and text[i + 1] not in "?!:;»"]
    if not spaces:
        return text
    i = min(spaces, key=lambda i: abs(i - mid))
    return f"{text[:i]}\n{text[i + 1:]}"


def ts(ms: int) -> str:
    return dtw_to_srt_timestamp(ms // 10).rsplit(",", 1)[0] + f",{ms % 1000:03d}"


def write_srt(cues, srt_path: Path) -> None:
    tmp = srt_path.with_name(srt_path.name + ".part")
    with open(tmp, "w", encoding="utf-8") as f:
        for i, (start, end, text) in enumerate(cues, 1):
            f.write(f"{i}\n{ts(start)} --> {ts(end)}\n{text}\n\n")
    tmp.rename(srt_path)


def main():
    ready = assert_whisper_servers(servers.WHISPER_SERVERS)
    EXPORT.mkdir(parents=True, exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    with wave.open(str(WAV), "rb") as w:
        n = w.getnframes()
    cut_file = EXPORT / "cut_sample.txt"
    if cut_file.is_file():
        cut = int(cut_file.read_text())
    else:
        cut = find_cut(WAV)
        cut_file.write_text(str(cut))
    results = [EXPORT / "whisper_part1.json", EXPORT / "whisper_part2.json"]
    parts = [(PARTS / "part1.wav", 0, cut), (PARTS / "part2.wav", cut, n)]
    todo = [(p, r) for p, r in zip(parts, results) if not r.is_file()]
    for (path, start, end), _ in todo:
        write_part(WAV, path, start, end)

    free = queue.Queue()
    for server in ready[: len(todo)] or ready[:1]:
        free.put(server)

    def run(item):
        (path, _, _), result = item
        server = free.get()
        try:
            transcribe(server, path, result)
        finally:
            free.put(server)

    with ThreadPoolExecutor(max(1, free.qsize())) as pool:
        list(pool.map(run, todo))  # raises the first failure, after the other half is done
    shutil.rmtree(PARTS, ignore_errors=True)

    offset_ms = cut // 16
    words = whisper_result_to_words(results[0]) + [
        w._replace(start_ms=w.start_ms + offset_ms, end_ms=w.end_ms + offset_ms)
        for w in whisper_result_to_words(results[1])
    ]
    print(f"{len(words)} words, second half shifted by {ts(offset_ms)}", flush=True)

    with open(EXPORT / "words.srt", "w", encoding="utf-8") as f:
        for i, w in enumerate(words, 1):
            f.write(f"{i}\n{ts(w.start_ms)} --> {ts(max(w.end_ms, w.start_ms))}\n{w.text.strip()}\n\n")

    cues = build_cues(words)
    write_srt(cues, EXPORT / f"{WAV.stem}.srt")
    srt_path = WAV.with_suffix(".srt")
    write_srt(cues, srt_path)
    short = sum(1 for s, e, _ in cues if e - s < 300)
    print(f"{len(cues)} cues ({short} shorter than 0.3 s) -> {srt_path}", flush=True)


if __name__ == "__main__":
    main()
