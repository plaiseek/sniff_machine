"""Name the labels of the diarizations after the enrolled speakers (speakers/enroll_speaker.py).

    python3.11 speakers/identify_speakers.py                    # every diarization with embedded turns
    python3.11 speakers/identify_speakers.py 243 418            # only these contents
    python3.11 speakers/identify_speakers.py --dry-run          # print, store nothing

Each label's voice (the duration-weighted mean of its clean turns: at least 3 s, little overlapped
speech) is compared with each speaker's voice (the mean of their samples). The closest speaker
names the label when the cosine similarity reaches --threshold; otherwise the label is stored as
unknown, with the similarity of the closest speaker. Labels set by a human are never changed,
and labels without clean turns are left out. Re-run it after enrolling someone.

The default threshold is provisional: on the first real data (5 videos of one channel), the
voices of one person in different videos scored 0.90-0.95 and different people at most 0.45.
Calibrate it with speakers/evaluate_speakers.py once a few speakers are labelled.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
import helpers.speakers as sp

DEFAULT_THRESHOLD = 0.6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_ids", type=int, nargs="*", help="default: every content")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"default {DEFAULT_THRESHOLD}")
    parser.add_argument("--model", help="embedding model (default: the only one stored)")
    parser.add_argument("--dry-run", action="store_true", help="print the labels, store nothing")
    args = parser.parse_args()

    with db.connect() as db_conn:
        model = args.model or sp.default_model(db_conn)
        speakers = sp.speaker_voices(db_conn, model)
        if not speakers:
            sys.exit(f"No speaker has samples with {model} embeddings: enroll someone with speakers/enroll_speaker.py first")
        query = """SELECT d.id, d.content_id FROM diarizations d
WHERE EXISTS (SELECT 1 FROM turns t JOIN turn_embeddings e ON e.turn_id = t.id AND e.model = ? WHERE t.diarization_id = d.id)"""
        params = [model]
        if args.content_ids:
            query += f" AND d.content_id IN ({', '.join('?' for _ in args.content_ids)})"
            params += args.content_ids
        diarizations = db_conn.execute(query + " ORDER BY d.content_id, d.id", params).fetchall()
        if not diarizations:
            sys.exit("No diarization with embedded turns: run pipeline/embed_speaker_turns.py first")
        print(f"{len(speakers)} speakers ({', '.join(name for name, _ in speakers.values())}), threshold {args.threshold}, {model}")

        counts = {"named": 0, "unknown": 0, "human": 0, "no clean turn": 0}
        for diarization_id, content_id in diarizations:
            content = db.get_content(db_conn, content_id)
            voices = sp.label_voices(db_conn, diarization_id, model)
            labels = db.get_speaker_labels(db_conn, diarization_id)
            all_labels = {turn.speaker for turn in db.get_turns(db_conn, diarization_id, exclusive=True)}
            print(f"\nContent {content_id} (diarization {diarization_id}): {(content.title or '')[:70]}")
            if not voices and not labels:
                print(f"  no label with clean turns ({len(all_labels)} labels)")
            for label in sorted(all_labels, key=lambda label: -(voices[label].seconds if label in voices else 0)):
                if label in labels and labels[label][2] == "human":
                    counts["human"] += 1
                    print(f"  {label:12} {labels[label][1] or 'nobody known'} (human, kept)")
                    continue
                if label not in voices:
                    counts["no clean turn"] += 1
                    continue
                voice = voices[label]
                speaker_id, name, similarity = sp.best_match(voice, speakers)
                named = similarity >= args.threshold
                counts["named" if named else "unknown"] += 1
                if not args.dry_run:
                    db.set_speaker_label(db_conn, diarization_id, label, speaker_id if named else None, "auto", model, similarity)
                who = name if named else f"unknown (closest: {name})"
                print(f"  {label:12} {voice.seconds / 60:5.1f} clean min  {who}  {similarity:.2f}")
    print(
        f"\n{counts['named']} labels named, {counts['unknown']} unknown, {counts['human']} set by a human (kept),"
        f" {counts['no clean turn']} without clean turns (left out)." + (" Dry run: nothing stored." if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
