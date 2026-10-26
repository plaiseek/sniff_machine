"""Print who said what in a content: its transcription, in paragraphs of one speaker.

    python3.11 explore/show_transcript.py CONTENT_ID                          # all of it
    python3.11 explore/show_transcript.py CONTENT_ID --from 10:00 --to 12:30  # a part ([H:]MM:SS or seconds)
    python3.11 explore/show_transcript.py CONTENT_ID --speaker SPEAKER_01     # one label, or a speaker's name

The speakers are the labels of the content's latest diarization, with who they are when known: a
name set by a person (human), or found by speakers/identify_speakers.py (auto, with its similarity: a
guess). Each paragraph starts with its time and a link to that moment. Uses the sentences stored
by pipeline/split_sentences.py, or cuts the words on punctuation when there are none.
"""

import argparse
import os
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
from helpers.speakers import describe_label
from helpers.transcripts import content_link, content_sentences, hms, parse_seconds, update_transcripts

PARAGRAPH_WORDS = 80  # a speaker's sentences are put in paragraphs of about this many words


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_id", type=int)
    parser.add_argument("--from", dest="start", default="0", help="start time ([H:]MM:SS or seconds)")
    parser.add_argument("--to", dest="end", help="end time ([H:]MM:SS or seconds)")
    parser.add_argument("--speaker", help="only this label (e.g. SPEAKER_01) or this speaker's name")
    args = parser.parse_args()
    try:
        start_ms = parse_seconds(args.start) * 1000
        end_ms = parse_seconds(args.end) * 1000 if args.end else float("inf")
    except ValueError as e:
        sys.exit(f"Invalid time: {e}")

    with db.connect() as db_conn:
        content = db.get_content(db_conn, args.content_id)
        if content is None:
            sys.exit(f"No content {args.content_id}")
        if db.get_unstored_transcriptions(db_conn, [content.id]) or db.get_unaligned_transcriptions(db_conn, [content.id]):
            update_transcripts(db_conn, [content.id])
        if db.get_transcription_state(db_conn, content.id) is None:
            sys.exit(f"Content {content.id} is not transcribed")
        diarization_id = db.get_transcription_diarization(db_conn, content.id)
        labels = db.get_speaker_labels(db_conn, diarization_id) if diarization_id else {}
        sentences = content_sentences(db_conn, content.id)

    wanted = None
    if args.speaker:
        found = {speaker for *_, speaker, _ in sentences}
        named = {label for label, (_, name, _, _) in labels.items() if name == args.speaker}
        wanted = {args.speaker} if args.speaker in found else named
        if not wanted:
            names = sorted({name for _, name, _, _ in labels.values() if name})
            sys.exit(f"No label or speaker {args.speaker!r} here: labels {sorted(s for s in found if s)}, names {names}")

    print(f"Content {content.id}: {content.title}\n{content.url}")
    print(f"Speakers of diarization {diarization_id} (the latest)\n" if diarization_id else "Not diarized: speakers unknown\n")
    paragraph, shown = [], 0
    end_of_sentences = (0, -1, 0, 0, None, None)  # prints the last paragraph
    for first, last, s_start, _, speaker, text in sentences + [end_of_sentences]:
        keep = text is not None and start_ms <= s_start < end_ms and (wanted is None or speaker in wanted)
        if paragraph and (
            not keep
            or speaker != paragraph[0][1]
            or sum(n for _, _, n, _ in paragraph) >= PARAGRAPH_WORDS
        ):
            p_start, p_speaker = paragraph[0][0], paragraph[0][1]
            who = f"{p_speaker} = {describe_label(labels, p_speaker)}" if describe_label(labels, p_speaker) else p_speaker
            print(f"[{hms(p_start)}] {who or 'speaker unknown'}  {content_link(content, p_start)}")
            print(textwrap.fill(" ".join(t for *_, t in paragraph), 100, initial_indent="  ", subsequent_indent="  ") + "\n")
            paragraph = []
        if keep:
            paragraph.append((s_start, speaker, last - first + 1, text))
            shown += 1
    if not shown:
        print("Nothing said in this part." if sentences else "No words: nothing was said, or Whisper heard nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
