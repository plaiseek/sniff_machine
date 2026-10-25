"""How well identify_speakers.py names the labels that a human set (enroll_speaker.py --label),
for a few thresholds: run it to choose identify_speakers.py --threshold, and again after every
change of the embedding model or of its server image.

    python3.11 evaluate_speakers.py
    python3.11 evaluate_speakers.py --thresholds 0.4 0.5 0.6 0.7

Each human-labelled label is identified as identify_speakers.py would, but against speaker voices
computed without the samples of its own content (leave one content out). The right answer is the
human's speaker when that speaker also has samples in other contents, and "unknown" otherwise
(e.g. a speaker labelled in one content only). A speaker labelled in at least 2 contents is
needed to measure anything but false names.
"""

import argparse
import sys

import helpers.database as db
import helpers.speakers as sp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--thresholds", type=float, nargs="+", default=[0.3, 0.4, 0.5, 0.6, 0.7, 0.8])
    parser.add_argument("--model", help="embedding model (default: the only one stored)")
    args = parser.parse_args()

    cases = []  # (content id, label, the right speaker id or None, best speaker id, name, similarity)
    with db.connect() as db_conn:
        model = args.model or sp.default_model(db_conn)
        labelled = db_conn.execute(
            """SELECT l.diarization_id, d.content_id, l.speaker, l.speaker_id FROM speaker_labels l
JOIN diarizations d ON d.id = l.diarization_id WHERE l.source = 'human' ORDER BY d.content_id, l.speaker"""
        ).fetchall()
        if not labelled:
            sys.exit("No label set by a human: label some with enroll_speaker.py --label first")
        names = dict(db_conn.execute("SELECT id, name FROM speakers"))
        for diarization_id, content_id, label, truth in labelled:
            voice = sp.label_voices(db_conn, diarization_id, model).get(label)
            if voice is None:
                continue  # no clean, embedded turn
            others = sp.speaker_voices(db_conn, model, exclude_content_id=content_id)
            expected = truth if truth in others else None
            best_id, best_name, similarity = sp.best_match(voice, others)
            cases.append((content_id, label, expected, best_id, best_name, similarity))

    print(f"{len(cases)} human-labelled labels with clean turns ({model}):")
    for content_id, label, expected, best_id, best_name, similarity in sorted(cases, key=lambda c: -(c[5] or -1)):
        right = names.get(expected, "?") if expected is not None else "unknown"
        closest = f"closest {best_name} {similarity:.2f}" if best_id is not None else "no other speaker"
        print(f"  content {content_id:>5} {label:12} right answer: {right:20} {closest}")
    positives = sum(1 for c in cases if c[2] is not None)
    if positives == 0:
        print("\nNo speaker is labelled in 2 contents: only false names can be counted.")

    print(f"\n{'threshold':>9} {'right names':>11} {'wrong names':>11} {'missed':>6} {'precision':>9} {'recall':>6}")
    for threshold in args.thresholds:
        right = wrong = missed = 0
        for _, _, expected, best_id, _, similarity in cases:
            named = best_id if similarity is not None and similarity >= threshold else None
            if named is not None and named == expected:
                right += 1
            elif named is not None:
                wrong += 1
            elif expected is not None:
                missed += 1
        precision = f"{right / (right + wrong):.2f}" if right + wrong else "-"
        recall = f"{right / positives:.2f}" if positives else "-"
        print(f"{threshold:>9.2f} {right:>11} {wrong:>11} {missed:>6} {precision:>9} {recall:>6}")
    print("\nprecision = right names / names given; recall = right names / labels whose speaker is enrolled elsewhere.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
