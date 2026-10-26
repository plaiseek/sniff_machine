"""Group the voices of the diarized contents that sound alike, before anyone is named: the labels of
different contents that are probably one person, so that one clip per group is enough to ask who it is.

    python3.11 speakers/group_voices.py                              # every diarized content
    python3.11 speakers/group_voices.py --uploader Psyhodelik --groups 10
    python3.11 speakers/group_voices.py --label 243 SPEAKER_01       # where this voice is heard, every label

Read-only, and it names no one. Each label's voice is the one identification uses (its clean turns,
in the content's latest diarization). Labels are taken longest first and join the group whose first
(longest) label is the closest when the similarity reaches --threshold: once the user has named that
first label with speakers/enroll_speaker.py --label, speakers/identify_speakers.py finds the same
similarities, so it names the group's other labels. The groups heard in the most contents come first,
each with the command to listen to its first label. Names already given show, with how many labels.
"""

import argparse
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
import helpers.speakers as sp

DEFAULT_THRESHOLD = 0.6  # as speakers/identify_speakers.py


def speech(seconds: float) -> str:
    return f"{seconds / 3600:.1f} h" if seconds >= 3600 else f"{seconds / 60:.0f} min"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--uploader", help="only the contents of this channel (part of its name)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help=f"default {DEFAULT_THRESHOLD}")
    parser.add_argument("--min-contents", type=int, default=2, help="show the groups heard in at least this many contents (default 2)")
    parser.add_argument("--groups", type=int, default=15, help="show at most this many groups (default 15)")
    parser.add_argument("--all", action="store_true", help="list every label of the groups shown, not the first 5")
    parser.add_argument("--label", nargs=2, metavar=("CONTENT_ID", "LABEL"), help="only the group of this label, with every label")
    parser.add_argument("--model", help="embedding model (default: the only one stored)")
    args = parser.parse_args()

    with db.connect() as db_conn:
        stored = db.get_turn_embedding_models(db_conn)
        if args.model and args.model not in stored:
            sys.exit(f"No turn embeddings of model {args.model}: stored models are {stored or 'none'}")
        model = args.model or sp.default_model(db_conn)
        selected = db.find_contents(db_conn, uploader=args.uploader)
        if not selected:
            sys.exit(f"No content whose channel name contains {args.uploader!r}" if args.uploader else "No content in the database")
        contents, voices, names = {}, {}, {}
        diarized = not_embedded = no_clean_turn = 0
        for content in selected:
            (diarization_id,) = db_conn.execute("SELECT MAX(id) FROM diarizations WHERE content_id = ?", (content.id,)).fetchone()
            if diarization_id is None:
                continue
            diarized += 1
            content_voices = sp.label_voices(db_conn, diarization_id, model)
            if not content_voices:
                embedded = db_conn.execute(
                    "SELECT 1 FROM turns t JOIN turn_embeddings e ON e.turn_id = t.id AND e.model = ? WHERE t.diarization_id = ? LIMIT 1",
                    (model, diarization_id),
                ).fetchone()
                if embedded:
                    no_clean_turn += 1
                else:
                    not_embedded += 1
                continue
            labels = db.get_speaker_labels(db_conn, diarization_id)
            contents[content.id] = content
            for label, voice in content_voices.items():
                voices[content.id, label] = voice
                # Only real names: an automatic "unknown" (nobody close enough) is no name
                speaker_id, name, source, _ = labels.get(label, (None, None, None, None))
                names[content.id, label] = f"{name} ({source})" if speaker_id is not None else "nobody known (human)" if source == "human" else ""
    print(
        f"{len(voices)} labels with clean turns in {len(contents)} of {diarized} diarized contents, {model}"
        + (f" ({not_embedded} not embedded yet: pipeline/embed_speaker_turns.py" if not_embedded else "")
        + ("; " if not_embedded and no_clean_turn else " (" if no_clean_turn else "")
        + (f"{no_clean_turn} with no clean turn" if no_clean_turn else "")
        + (")" if not_embedded or no_clean_turn else "")
    )
    if not voices:
        return 1

    def heard_in(group):
        return len({content_id for (content_id, _), _, _ in group[1]})

    groups = sp.group_voices(voices, args.threshold)
    if args.label:
        key = (int(args.label[0]), args.label[1])
        if key not in voices:
            sys.exit(f"Content {key[0]} has no label {key[1]} with clean, embedded turns in its latest diarization")
        shown = [group for group in groups if any(member_key == key for member_key, _, _ in group[1])]
        args.all = True
    else:
        shown = sorted((group for group in groups if heard_in(group) >= args.min_contents), key=lambda group: (-heard_in(group), -group[0].seconds))
        print(f"{len(groups)} groups at similarity {args.threshold}, {len(shown)} heard in at least {args.min_contents} contents.")

    for n, (group, members) in enumerate(shown[: args.groups], 1):
        named = Counter(names[key] for key, _, _ in members if names[key])
        print(
            f"\nGroup {n}: {heard_in((group, members))} contents, {len(members)} labels, {speech(group.seconds)} of clean speech,"
            f" lowest similarity {min(similarity for _, _, similarity in members):.2f}; "
            + (f"named: {', '.join(f'{name} x{count}' for name, count in named.most_common())}" if named else "never named")
        )
        for (content_id, label), voice, similarity in members if args.all else members[:5]:
            content = contents[content_id]
            print(
                f"  content {content_id:<5} {label:11} {voice.seconds / 60:6.1f} clean min  similarity {similarity:.2f}"
                f"  {(content.upload_date or '?')[:10]}  {(content.title or '')[:50]}"
                + (f"  [{names[content_id, label]}]" if names[content_id, label] else "")
            )
        if len(members) > 5 and not args.all:
            print(f"  ... and {len(members) - 5} more labels (--all lists them)")
        (content_id, label), _, _ = members[0]
        print(f"  Listen: python3.11 speakers/listen_speaker.py {content_id} {label}")
    if not args.label and len(shown) > args.groups:
        print(f"\n{len(shown) - args.groups} more groups (--groups N shows them).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
