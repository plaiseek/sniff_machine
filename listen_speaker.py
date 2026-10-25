"""Hear who a diarization label is, before naming them with enroll_speaker.py.

    python3.11 listen_speaker.py CONTENT_ID                 # the labels of the content and who they are
    python3.11 listen_speaker.py CONTENT_ID SPEAKER_01      # a clip of the label's longest clean turns

The clip (cache/speaker_clips/) joins the label's longest clean turns (at least 3 s, little
overlapped speech) with a short silence between them; each turn is printed with its time, a link
to that moment of the video and what is said (when the content is transcribed). Uses the latest
diarization of the content unless --diarization is given.
"""

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

import helpers.database as db
import helpers.speakers as sp
from helpers.pipeline import prepare_wav
from helpers.whisper_client import whisper_result_to_words


def hms(ms: int) -> str:
    return str(datetime.timedelta(seconds=ms // 1000))


def link(content: db.Content, ms: int) -> str:
    """The content's URL at this moment, for the platforms whose URLs take a start time."""
    if content.platform == "Youtube":
        return f"https://www.youtube.com/watch?v={content.external_id}&t={ms // 1000}s"
    if content.platform == "TwitchVod":
        h, rest = divmod(ms // 1000, 3600)
        return f"https://www.twitch.tv/videos/{content.external_id.lstrip('v')}?t={h}h{rest // 60}m{rest % 60}s"
    return f"{content.url} (at {hms(ms)})"


def describe_label(labels: dict, label: str) -> str:
    if label not in labels:
        return ""
    _, name, source, similarity = labels[label]
    if source == "human":
        return f"{name or 'nobody known'} (human)"
    return f"{name or 'unknown'} (auto, {similarity:.2f})" if similarity is not None else f"{name or 'unknown'} (auto)"


def print_labels(content: db.Content, diarization_id: int, turns: list[db.Turn], labels: dict) -> None:
    by_label = {}
    for turn in turns:
        total, clean, n = by_label.get(turn.speaker, (0, 0, 0))
        duration = turn.end_ms - turn.start_ms
        by_label[turn.speaker] = (total + duration, clean + duration * sp.is_clean(turn), n + 1)
    print(f"{'label':12} {'turns':>5} {'minutes':>7} {'clean':>6}  who")
    for label, (total, clean, n) in sorted(by_label.items(), key=lambda item: -item[1][0]):
        print(f"{label:12} {n:>5} {total / 60000:>7.1f} {clean / 60000:>6.1f}  {describe_label(labels, label)}")
    print(f"\nclean = minutes in turns of at least {sp.MIN_TURN_MS // 1000} s with little overlapped speech.")
    print(f"Listen to one: python3.11 listen_speaker.py {content.id} <label>   (diarization {diarization_id})")


def cut_clip(wav_path: Path, turns: list[db.Turn], clip_path: Path, gap_s: float = 0.7) -> None:
    """One wav with the turns in order, separated by gap_s of silence (input seeking: fast on long files)."""
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-nostdin"]
    for turn in turns:
        cmd += ["-ss", f"{turn.start_ms / 1000:.3f}", "-t", f"{(turn.end_ms - turn.start_ms) / 1000:.3f}", "-i", str(wav_path)]
    padded = "".join(f"[{i}:a]apad=pad_dur={gap_s}[a{i}];" for i in range(len(turns)))
    joined = "".join(f"[a{i}]" for i in range(len(turns)))
    cmd += ["-filter_complex", f"{padded}{joined}concat=n={len(turns)}:v=0:a=1[out]", "-map", "[out]", str(clip_path)]
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_id", type=int)
    parser.add_argument("label", nargs="?", help="e.g. SPEAKER_01; without it, list the labels")
    parser.add_argument("--diarization", type=int, help="diarization id (default: the latest of the content)")
    parser.add_argument("--turns", type=int, default=3, help="how many turns in the clip (default 3)")
    args = parser.parse_args()

    with db.connect() as db_conn:
        content = db.get_content(db_conn, args.content_id)
        if content is None:
            sys.exit(f"No content {args.content_id}")
        diarization_id = args.diarization or db_conn.execute(
            "SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)
        ).fetchone()[0]
        if diarization_id is None:
            sys.exit(f"Content {content.id} is not diarized")
        owner = db_conn.execute("SELECT content_id FROM diarizations WHERE id = ?", (diarization_id,)).fetchone()
        if owner is None or owner[0] != content.id:
            sys.exit(f"Diarization {diarization_id} is not one of content {content.id}")
        turns = db.get_turns(db_conn, diarization_id, exclusive=True)
        if not turns:
            sys.exit(f"Diarization {diarization_id} has no turns")
        labels = db.get_speaker_labels(db_conn, diarization_id)
        print(f"Content {content.id}: {content.title}\n{content.url}\nDiarization {diarization_id}\n")
        if args.label is None:
            print_labels(content, diarization_id, turns, labels)
            return 0

        own = [turn for turn in turns if turn.speaker == args.label]
        if not own:
            sys.exit(f"No label {args.label} in diarization {diarization_id}: {sorted({t.speaker for t in turns})}")
        clean = [turn for turn in own if sp.is_clean(turn)]
        longest = lambda ts: sorted(ts, key=lambda t: t.start_ms - t.end_ms)[: args.turns]  # noqa: E731
        chosen = sorted(longest(clean) or longest(own), key=lambda t: t.start_ms)
        wav_path = prepare_wav(db_conn, content)
        whisper_path = db.get_existing_content_file(db_conn, content.id, "whisper_result")

    clip_path = Path(f"cache/speaker_clips/{content.platform}_{content.external_id}_{diarization_id}_{args.label}.wav")
    cut_clip(wav_path, chosen, clip_path)
    words = whisper_result_to_words(whisper_path) if whisper_path else None
    who = describe_label(labels, args.label)
    print(f"{args.label}{' = ' + who if who else ''}: {len(own)} turns, {len(clean)} clean")
    if not clean:
        print(f"No clean turn (at least {sp.MIN_TURN_MS // 1000} s, little overlap): the clip has its longest turns.")
    for turn in chosen:
        print(f"\n{hms(turn.start_ms)} - {hms(turn.end_ms)} ({(turn.end_ms - turn.start_ms) / 1000:.1f} s)  {link(content, turn.start_ms)}")
        if words is None:
            print("  (not transcribed)")
        else:
            # The words whose middle falls inside the turn
            text = "".join(w for w, start, end in words if turn.start_ms <= (start + end) / 2 < turn.end_ms).strip()
            print(f"  {text[:400] + ('...' if len(text) > 400 else '') if text else '(no words)'}")
    print(f"\nClip: {clip_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
