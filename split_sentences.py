"""Split the transcriptions stored in the database into sentences, never across two speakers.

    python3.11 split_sentences.py                  # every content, with the wtpsplit servers of servers.py
    python3.11 split_sentences.py 7 243            # only these contents
    python3.11 split_sentences.py --punctuation    # no server: a sentence ends after . ? ! or …
                                                   # (only the contents not split yet, by any splitter)

First stores the words not stored yet (as store_words.py does). Then cuts the words of each content
into runs of one speaker (the labels of its latest diarization; one run when it is not diarized)
and each run into sentences. wtpsplit (SaT) also finds the sentences that Whisper did not
punctuate: it replaces the sentences split on punctuation. A content already split with the same
splitter is skipped. A new diarization deletes the sentences of its content: run this again to
split them again.
"""

import argparse
import sys

import helpers.database as db
import servers
from helpers.transcripts import split_sentences, update_transcripts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_ids", type=int, nargs="*", help="contents to split (default: all)")
    parser.add_argument("--punctuation", action="store_true", help="split on . ? ! … without any server")
    args = parser.parse_args()

    step = None
    if not args.punctuation:
        from helpers.pipeline import run_steps, sentence_step

        step = sentence_step(servers.WTPSPLIT_SERVERS)  # exits now if no server is ready
    with db.connect() as db_conn:
        word_failures = update_transcripts(db_conn, args.content_ids or None)
        stored = [content_id for content_id, in db_conn.execute("SELECT content_id FROM transcriptions ORDER BY content_id")]
        if args.content_ids:
            if missing := sorted(set(args.content_ids) - set(stored)):
                print(f"No words stored for contents {missing} (not transcribed?): left out")
            stored = [content_id for content_id in stored if content_id in set(args.content_ids)]
        contents = [db.get_content(db_conn, content_id) for content_id in stored]
        # Without a server, the sentences split by wtpsplit (better) are kept: any splitter will do
        todo = [
            content for content in contents
            if not (db.sentences_done(db_conn, content.id, None) if step is None else step.is_done(db_conn, content))
        ]
    if not todo:
        print("Nothing to split.")
        return 1 if word_failures else 0
    print(f"{len(todo)} transcriptions to split into sentences.")
    if step is not None:
        report = run_steps(todo, [step])
        return report.exit_code() or (1 if word_failures else 0)

    failed = []
    with db.connect() as db_conn:
        for i, content in enumerate(todo, 1):
            try:
                print(f"({i}/{len(todo)}) Content {content.id}: {split_sentences(db_conn, content.id)}", flush=True)
            except Exception as e:
                failed.append((content, e))
                print(f"({i}/{len(todo)}) Content {content.id}: cannot split it:\n{e}", flush=True)
    for content, e in failed:
        print(f"  Failed: {content.url} : {e}")
    print(f"Summary: {len(todo)} transcriptions, {len(todo) - len(failed)} split, {len(failed)} failures.")
    return 1 if failed or word_failures else 0


if __name__ == "__main__":
    sys.exit(main())
