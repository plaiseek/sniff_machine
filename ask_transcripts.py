"""Ask an LLM (served by vLLM) where something is said in the transcriptions, and by whom.

    python3.11 ask_transcripts.py "Où parle-t-on de la mairie de Paris ?" --content 243
    python3.11 ask_transcripts.py "Quand tient-il des propos haineux envers un groupe de personnes ?" \\
        --criteria criteria.txt --uploader Psyhodelik --speaker "Jean Dupont"
    python3.11 ask_transcripts.py "..." --uploader Psyhodelik --dry-run   # what it would read, without the LLM
    python3.11 ask_transcripts.py "..." --uploader Psyhodelik --show      # what was found, without the LLM

The LLM reads each transcription in windows (with its times, speaker labels and who they are when
known) and lists the passages that may match, then checks each one with more context: see
helpers/questions.py. It runs on the VLLM_SERVERS of servers.py that answer, several contents at
once. What it found is stored (tables questions, question_contents, findings) and printed, each
passage with its time, speaker, a link to that moment, the verdict, the LLM's confidence and why.
Asked again with the same question, criteria, speaker, model and settings, it only reads the
contents not read yet (or diarized since): run it in the background with a log on many contents.

--speaker: only the sentences of the diarization labels named so (by a person, or guessed from the
voice by identify_speakers.py) can match; the contents where no label is named so are left out.
--criteria FILE: a precise definition of what matches, added to the question: e.g.
criteria/haine_loi_1881.txt (hateful speech under French press law). Answers are leads to listen
to, not verdicts.

--clients, --kv-tokens, --tokens-per-s: what each vLLM server can do (defaults below). Each server
gets --clients requests at once, the contents that started first going first, never more than its
KV cache holds; --tokens-per-s gives the time a run takes (--dry-run) and is compared to the speed
measured while it runs.
"""

import argparse
import sys
import textwrap
from pathlib import Path

import helpers.database as db
import servers
from helpers.speakers import describe_label
from helpers.transcripts import content_link, content_sentences, hms, update_transcripts

TOKENS_PER_WORD = 2  # measured on the transcriptions with a Qwen3 tokenizer (numbered lines, times, labels)
CHECK_TOKENS = 3000  # about what one passage of the second pass reads
# Tokens the LLM writes per word of transcription, both passes: measured on an easy question (where is the
# police mentioned) and a hard one (criteria/haine_loi_1881.txt)
WRITTEN_PER_WORD = (1.8, 7.1)
# What each vLLM server can do (the defaults of --clients, --kv-tokens, --tokens-per-s)
CLIENTS = 8  # requests at once at which it is most efficient
KV_TOKENS = 440_000  # tokens its KV cache holds (its /metrics: kv_cache_size_tokens)
TOKENS_PER_S = 300  # tokens it writes per second with CLIENTS requests at once


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("question")
    parser.add_argument("--criteria", type=Path, help="a text file defining what matches, added to the question")
    parser.add_argument("--uploader", help="only the contents of this channel (part of its name)")
    parser.add_argument("--content", type=int, action="append", help="only this content (repeatable)")
    parser.add_argument("--uploaded-from", help="only the contents uploaded from this day (YYYY-MM-DD)")
    parser.add_argument("--uploaded-to", help="only the contents uploaded until this day (YYYY-MM-DD)")
    parser.add_argument("--speaker", help="only the sentences of the labels named so can match")
    parser.add_argument("--dry-run", action="store_true", help="say what would be read, without the LLM")
    parser.add_argument("--show", action="store_true", help="print what was found, without the LLM")
    parser.add_argument("--all", action="store_true", help="also print the passages the LLM judged not to match")
    parser.add_argument("--clients", type=int, default=CLIENTS, help=f"requests at once per server (default {CLIENTS})")
    parser.add_argument("--kv-tokens", type=int, default=KV_TOKENS, help=f"tokens the KV cache of a server holds (default {KV_TOKENS:,})")
    parser.add_argument("--tokens-per-s", type=float, default=TOKENS_PER_S, help=f"tokens a server writes per second (default {TOKENS_PER_S})")
    args = parser.parse_args()
    if args.clients < 1 or args.kv_tokens < 1 or args.tokens_per_s <= 0:
        sys.exit("--clients, --kv-tokens and --tokens-per-s must be positive")
    question, criteria = args.question.strip(), ""
    if args.criteria:
        try:
            criteria = args.criteria.read_text(encoding="utf-8").strip()
        except OSError as e:
            sys.exit(f"Cannot read the criteria: {e}")
    from helpers.questions import question_text

    text = question_text(question, criteria)

    with db.connect() as db_conn:
        contents = db.find_contents(
            db_conn, ids=args.content, uploader=args.uploader, uploaded_from=args.uploaded_from, uploaded_to=args.uploaded_to
        )
        if not contents:
            sys.exit("No content matches the selection")
        if not args.show:
            update_transcripts(db_conn, [c.id for c in contents])
        selected, sizes = [], {}
        for content in contents:
            state = db.get_transcription_state(db_conn, content.id)
            if state is None or not state[2]:
                continue  # not transcribed, or nothing was heard
            if args.speaker:
                labels = db.get_speaker_labels(db_conn, state[1]) if state[1] else {}
                if not any(name == args.speaker for _, name, _, _ in labels.values()):
                    continue
            selected.append(content)
            sizes[content.id] = state[2]
        left_out = len(contents) - len(selected)
        print(
            f"{len(selected)} contents to ask about"
            + (f" ({left_out} left out: not transcribed, no words" + (f", or no label named {args.speaker!r}" if args.speaker else "") + ")" if left_out else "")
        )
        if not selected and args.speaker:
            sys.exit(
                f"No label is named {args.speaker!r} in these contents: a person names one with enroll_speaker.py,"
                " then identify_speakers.py finds them in the other diarized contents"
            )
        if not selected:
            return 0

        if args.show:
            asked = db.find_questions(db_conn, text, args.speaker)
            if not asked:
                sys.exit("This question (with these criteria and speaker) was never asked")
            question_id, model, _, created_at = asked[0]
            print(f"Question {question_id}, asked with {model} since {created_at}" + (f" ({len(asked) - 1} older settings or models)" if len(asked) > 1 else ""))
            return print_findings(db_conn, question_id, selected, args.all)

        if args.dry_run:
            from helpers.questions import CONTEXT_WORDS, windows

            n_windows = sum(len(windows(content_sentences(db_conn, c.id))) for c in selected)
            words = sum(sizes.values())
            written = [round(words * per_word) for per_word in WRITTEN_PER_WORD]
            speed = args.tokens_per_s * len(servers.VLLM_SERVERS)
            print(
                f"Dry run: {words:,} words in {n_windows} windows: the first pass reads about {words * TOKENS_PER_WORD:,} tokens,"
                f" plus about {CHECK_TOKENS:,} per passage to check (±{CONTEXT_WORDS} words around it)."
                f" Both passes write about {written[0]:,} to {written[1]:,} tokens (easy to hard question):"
                f" {hms(int(written[0] * 1000 / speed))} to {hms(int(written[1] * 1000 / speed))} at {speed:.0f} tokens/s"
                f" (--tokens-per-s, {len(servers.VLLM_SERVERS)} server{'s' if len(servers.VLLM_SERVERS) > 1 else ''}), plus the time to read."
            )
            return 0

    from helpers.pipeline import run_steps
    from helpers.questions import question_step, settings
    from helpers.vllm_client import ServerHints, assert_vllm_servers, server_kv_tokens

    vllm = assert_vllm_servers(servers.VLLM_SERVERS)
    for address in vllm.addresses:
        kv_tokens = server_kv_tokens(address)
        if kv_tokens is not None and kv_tokens < args.kv_tokens:
            print(f"Warning: the KV cache of {address} holds {kv_tokens:,} tokens, not {args.kv_tokens:,} (--kv-tokens): vLLM may pause requests")
    hints = ServerHints(args.clients, args.kv_tokens, args.tokens_per_s)
    with db.connect() as db_conn:
        question_id = db.add_question(db_conn, text, args.speaker, vllm.model, settings(vllm.reasoning))
        words = sum(sizes[c.id] for c in selected if not db.question_done(db_conn, question_id, c.id))
    print(f"Question {question_id}, with {vllm.model} on {', '.join(vllm.addresses)} ({args.clients} requests at once on each)")
    report = run_steps(selected, [question_step(vllm, hints, question_id, question, criteria, args.speaker, words)])
    with db.connect() as db_conn:
        print_findings(db_conn, question_id, selected, args.all)
    return report.exit_code()


def print_findings(db_conn, question_id: int, contents: list[db.Content], show_all: bool) -> int:
    verdicts = ("yes", "unsure", "no") if show_all else ("yes", "unsure")
    by_id = {content.id: content for content in contents}
    rows = db.get_findings(db_conn, question_id, list(by_id), verdicts)
    asked = db_conn.execute(
        f"SELECT content_id, prompt_tokens, completion_tokens FROM question_contents WHERE question_id = ? AND content_id IN ({', '.join('?' for _ in by_id)})",
        (question_id, *by_id),
    ).fetchall()
    labels, previous, counts = {}, None, {verdict: 0 for verdict in ("yes", "unsure", "no")}
    for content_id, verdict in db_conn.execute("SELECT content_id, verdict FROM findings WHERE question_id = ?", (question_id,)):
        if content_id in by_id:
            counts[verdict] += 1
    for content_id, diarization_id, first_idx, last_idx, start_ms, _, speakers, verdict, confidence, attribution, quote, found, explanation, _ in rows:
        content = by_id[content_id]
        if content_id != previous:
            print(f"\nContent {content_id} ({(content.upload_date or '?')[:10]}): {content.title}")
            previous = content_id
        if diarization_id is not None and diarization_id not in labels:
            labels[diarization_id] = db.get_speaker_labels(db_conn, diarization_id)
        who = []
        for label in (speakers or "").split(",") if speakers else []:
            name = describe_label(labels.get(diarization_id, {}), label)
            who.append(f"{label} = {name}" if name else label)
        print(f"  [{hms(start_ms)}] {', '.join(who) or 'speaker unknown'}  {content_link(content, start_ms)}")
        details = ", ".join(x for x in (f"{confidence:.2f}" if confidence is not None else "", attribution or "") if x)
        first_line, *lines = (explanation or "").split("\n")
        print(textwrap.fill(f"{verdict.upper()}" + (f" ({details})" if details else "") + f": {first_line}", 100, initial_indent="    ", subsequent_indent="      "))
        for line in lines:  # Meets: ..., Against: ...
            print(textwrap.fill(line, 100, initial_indent="      ", subsequent_indent="        "))
        print(textwrap.fill(f"« {db.get_words_text(db_conn, content_id, first_idx, last_idx)} »", 100, initial_indent="    ", subsequent_indent="      "))
        if quote and not found:
            print(textwrap.fill(f"(the quote is not in this passage: « {quote} »)", 100, initial_indent="    ", subsequent_indent="      "))
    prompt_tokens = sum(row[1] for row in asked)
    completion_tokens = sum(row[2] for row in asked)
    print(
        f"\nAsked on {len(asked)} of {len(contents)} contents ({prompt_tokens:,} tokens read, {completion_tokens:,} written):"
        f" {counts['yes']} passages match, {counts['unsure']} unsure, {counts['no']} checked and rejected"
        + ("" if show_all else " (--all prints them)")
        + ". These are leads to listen to, not verdicts."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
