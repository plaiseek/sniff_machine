"""Name a speaker from reference samples of their voice (for identify_speakers.py).

    python3.11 enroll_speaker.py NAME CONTENT_ID --label SPEAKER_01      # a diarization label is NAME
    python3.11 enroll_speaker.py NAME CONTENT_ID 12.5 19 1:02:03 1:02:15  # ranges where only NAME talks
    python3.11 enroll_speaker.py --forget NAME                           # delete NAME and all that names them

With --label (listen to it first with listen_speaker.py), the label's clean turns (at least 3 s,
little overlapped speech) become samples of NAME, with the embeddings already computed for them
(embed_speaker_turns.py), and the label is set to NAME (source 'human', which identification never
replaces). Uses the latest diarization of the content unless --diarization is given.

Ranges are START END pairs, in seconds or [H:]MM:SS[.s], of at least 1 s (3 s or more is better):
they are embedded by the SpeechBrain servers of servers.py.

--forget deletes the speaker, their samples, and every label naming them (human or automatic).
Older copies of the database in cache/backups/ still hold them.
"""

import argparse
import sys

import helpers.database as db
import helpers.speakers as sp
import servers


def seconds(text: str) -> float:
    """'75', '75.5', '1:15', '1:02:03.5' -> seconds."""
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def enroll_label(db_conn, speaker_name: str, content: db.Content, label: str, diarization_id: int | None, model: str | None) -> int:
    diarization_id = diarization_id or db_conn.execute(
        "SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)
    ).fetchone()[0]
    owner = db_conn.execute("SELECT content_id FROM diarizations WHERE id = ?", (diarization_id,)).fetchone()
    if diarization_id is None or owner is None or owner[0] != content.id:
        sys.exit(f"No such diarization of content {content.id}" if diarization_id else f"Content {content.id} is not diarized")
    model = model or sp.default_model(db_conn)
    own = [(turn, blob) for turn, blob in db.get_turn_embeddings(db_conn, diarization_id, model) if turn.speaker == label]
    if not own:
        sys.exit(f"No label {label} in diarization {diarization_id}")
    clean = [(turn, blob) for turn, blob in own if sp.is_clean(turn)]
    if not clean:
        sys.exit(f"{label} has no clean turn (at least {sp.MIN_TURN_MS // 1000} s, little overlap): give ranges instead")
    if any(blob is None for _, blob in clean):
        sys.exit(f"Some turns of diarization {diarization_id} have no {model} embedding: run embed_speaker_turns.py first")

    speaker_id = db.add_speaker(db_conn, speaker_name)
    samples = [(turn.start_ms, turn.end_ms, sp.to_vector(blob).tolist()) for turn, blob in clean]
    added = db.add_speaker_samples(db_conn, speaker_id, content.id, model, samples)
    db.set_speaker_label(db_conn, diarization_id, label, speaker_id, "human")
    minutes = sum(end - start for start, end, _ in samples) / 60000
    print(f"{speaker_name}: {added} new samples ({len(samples)} clean turns, {minutes:.1f} min) from {label} of content {content.id}.")
    print(f"Diarization {diarization_id}: {label} is now {speaker_name} (human).")
    return 0


def enroll_ranges(db_conn, speaker_name: str, content: db.Content, values: list[str]) -> int:
    from helpers.pipeline import prepare_wav
    from helpers.speechbrain_client import assert_speechbrain_servers, wav_to_speechbrain_embeddings

    if len(values) % 2:
        sys.exit("Give START END pairs")
    try:
        ranges = [(round(seconds(a) * 1000), round(seconds(b) * 1000)) for a, b in zip(values[::2], values[1::2])]
    except ValueError as e:
        sys.exit(f"Invalid time: {e}")
    if bad := [(start, end) for start, end in ranges if not 0 <= start < end or end - start < 1000]:
        sys.exit(f"Each range needs 0 <= START < END and at least 1 s, got (ms) {bad}")
    if content.duration_s is not None and (late := [r for r in ranges if r[1] > content.duration_s * 1000 + 1000]):
        sys.exit(f"Ranges past the end of the content ({content.duration_s:.0f} s): {late}")
    if short := [r for r in ranges if r[1] - r[0] < sp.MIN_TURN_MS]:
        print(f"Warning: ranges shorter than {sp.MIN_TURN_MS // 1000} s give less reliable embeddings: {short}")

    model = assert_speechbrain_servers(servers.SPEECHBRAIN_SERVERS)
    wav_path = prepare_wav(db_conn, content)
    result = wav_to_speechbrain_embeddings(
        servers.SPEECHBRAIN_SERVERS[0], wav_path, [[start / 1000, end / 1000] for start, end in ranges]
    )
    if result["model"] != model:
        sys.exit(f"Server {servers.SPEECHBRAIN_SERVERS[0]} runs {result['model']}, not {model}")
    samples = [(start, end, embedding) for (start, end), embedding in zip(ranges, result["embeddings"])]
    if missing := [(start, end) for start, end, embedding in samples if embedding is None]:
        sys.exit(f"No embedding for {missing} (past the end of the audio?): nothing stored")
    speaker_id = db.add_speaker(db_conn, speaker_name)
    added = db.add_speaker_samples(db_conn, speaker_id, content.id, model, samples)
    minutes = sum(end - start for start, end in ranges) / 60000
    print(f"{speaker_name}: {added} new samples ({len(ranges)} ranges, {minutes:.1f} min) from content {content.id}.")
    return 0


def forget(db_conn, speaker_name: str, yes: bool) -> int:
    speaker_id = db.get_speaker_id(db_conn, speaker_name)
    if speaker_id is None:
        sys.exit(f"No speaker named {speaker_name!r}")
    samples = db_conn.execute("SELECT COUNT(*) FROM speaker_samples WHERE speaker_id = ?", (speaker_id,)).fetchone()[0]
    labels = db_conn.execute(
        "SELECT source, COUNT(*) FROM speaker_labels WHERE speaker_id = ? GROUP BY source", (speaker_id,)
    ).fetchall()
    print(f"{speaker_name}: {samples} samples, labels {dict(labels) or 'none'}.")
    if not yes and input("Type the name again to delete all of it: ") != speaker_name:
        print("Nothing deleted.")
        return 1
    db.delete_speaker(db_conn, speaker_name)
    print(f"Deleted {speaker_name}, their samples and labels.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("name", help="the speaker's name, as it will be shown")
    parser.add_argument("content_id", type=int, nargs="?")
    parser.add_argument("ranges", nargs="*", help="START END pairs (seconds or [H:]MM:SS[.s])")
    parser.add_argument("--label", help="a diarization label, e.g. SPEAKER_01, instead of ranges")
    parser.add_argument("--diarization", type=int, help="with --label: diarization id (default: the latest)")
    parser.add_argument("--model", help="with --label: embedding model (default: the only one stored)")
    parser.add_argument("--forget", action="store_true", help="delete the speaker and all that names them")
    parser.add_argument("--yes", action="store_true", help="with --forget: do not ask for confirmation")
    args = parser.parse_args()

    name = args.name.strip()
    if not name:
        sys.exit("Empty name")
    with db.connect() as db_conn:
        if args.forget:
            if args.content_id is not None:
                sys.exit("--forget takes only the name")
            return forget(db_conn, name, args.yes)
        if args.content_id is None:
            sys.exit("Give CONTENT_ID, then --label LABEL or START END ranges")
        content = db.get_content(db_conn, args.content_id)
        if content is None:
            sys.exit(f"No content {args.content_id}")
        if (args.label is None) == (not args.ranges):
            sys.exit("Give either --label LABEL or START END ranges")
        if args.label is not None:
            return enroll_label(db_conn, name, content, args.label, args.diarization, args.model)
        return enroll_ranges(db_conn, name, content, args.ranges)


if __name__ == "__main__":
    sys.exit(main())
