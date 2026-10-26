---
name: speaker-identity
description: Finds out who the speakers of diarized videos are - computes the voice embeddings of their turns, gives the user a clip of a diarization label (SPEAKER_01, ...) to listen to, stores the name the user gives for it (enrollment), then names the labels of the other videos by voice similarity and measures how reliable that is. Use for requests like "who is SPEAKER_01 in video 243?", "SPEAKER_00 of video 418 is Jean Dupont", "find Jean Dupont in the other videos", "identify the speakers of channel X" (in French qui parle, identifier les intervenants, c'est X).
---

# Speaker identity

Diarization labels (`SPEAKER_00`, ...) only mean something inside one diarization. A person gets a name in three stages: their turns get voice embeddings, the user hears a label and names it (enrollment), then the other labels are compared with the named voices. Run every command from the repository root. `CONTENT_ID` is the `id` of a content: find it from a URL, a video id or a title with the `query-database` skill.

## Rules

- **Never name a speaker yourself**, whether from a video title, the channel name, what the transcript says or a guess such as "the host". Voiceprints identify real people: only the user decides who a label is, and only for a label or time range they point to. If they give a name without a label, ask which label (show them the `speakers/listen_speaker.py` table).
- **You cannot listen** to a clip. Give the user its path and the links to the video; the transcript excerpts show what is said, not who says it.
- Automatic names are guesses: report them as "identified automatically (similarity 0.92)", never as facts.
- `speakers/enroll_speaker.py --forget` deletes a speaker, their samples and every label naming them: only the user runs it (`.claude/hooks/protect_data.py` blocks it).
- The clips in `cache/speaker_clips/` are voice recordings of real people: never send them anywhere but to the user who asked (their path, or the file itself to the user's own device).

## 1. Voice embeddings

Needs diarized contents (the `pipeline-job` skill) and a SpeechBrain server:

```bash
python3.11 model_servers/check_servers.py embed          # a server must say OK, BUSY or LOADING (otherwise see the model-servers skill)
python3.11 -u pipeline/embed_speaker_turns.py >> cache/embed_speaker_turns.log 2>&1   # in the background if many contents
```

It embeds the exclusive turns that have no embedding yet (it has no `--help`: any run embeds) and ends with `Nothing to embed.` (exit code 0) when every turn has an embedding, otherwise with a `Summary:` line (followed by lines saying a server `is probably stuck` with exit code 5). The log is appended to: read the lines of this run (from its first `Checking http://` line, one per server). It prints no exit code: run it with `run_in_background`, or read the end of the log. Exit code 0: done; 1: some contents failed (run it again once, report what still fails), or it stopped before starting (the last line says why: `No SpeechBrain server of [...] is ready`: see the model-servers skill; `No SpeechBrain server answered /health within 600 s`: it is working for another job, run it again later); 5: a server is probably stuck: tell the user it needs a restart, do not run it again. A `GPU out of memory` error while the server says OK means its GPU is full (it is shared with the other model servers of that machine): running it again does not help until memory is freed; tell the user, stopping a server is their decision (`model-servers` skill). A job with `"embed"` in `STEPS` does the same after its diarizations. Stop the SpeechBrain server you started once the embeddings are done (`model-servers` skill).

## 1b. Find the recurring voices (nobody named yet, or a new channel)

```bash
python3.11 speakers/group_voices.py --uploader NAME           # the voices heard in several contents, the most frequent first
```

It groups the labels of different contents whose voices match, reads only and names no one. Its first line says how many diarized contents it could use (`N not embedded yet`: step 1 first). The groups heard in the most contents come first, each with its first 5 labels (`--all` lists them all), the names already given, and a `Listen: python3.11 speakers/listen_speaker.py CONTENT LABEL` command for its first (longest) label. A label's `similarity` is to that first label: the one step 4 finds once that label is enrolled. For the first groups, do step 2 with that command and ask the user who it is; enroll the name they give with `--label` on that label (step 3); then the dry run of step 4 shows the group's other labels named, and those of other contents that match. `python3.11 speakers/group_voices.py --label CONTENT LABEL` shows where one voice is heard.

## 2. Listen to a label

```bash
python3.11 speakers/listen_speaker.py CONTENT_ID                # the labels: turns, minutes, clean minutes, who they are
python3.11 speakers/listen_speaker.py CONTENT_ID SPEAKER_01     # a clip of its 3 longest clean turns
```

Show the user the table, or the clip path (`Clip: ...`) with each turn's time, link and text, and ask who it is. In the `who` column: empty, never identified; `Name (human)`, set by the user; `nobody known (human)`, the user said it is nobody they know; `Name (auto, 0.92)`, a guess; `unknown (auto, 0.41)`, nobody enrolled matched. Each run rewrites the clip (it can be recomputed). A label with no clean minutes cannot be enrolled with `--label`, and identification leaves it out: its clip then has its longest turns, overlapped speech included (`No clean turn ...`), so the user can only give ranges for it. The output names the diarization it used (`(diarization N)`): if you passed `--diarization N`, pass it to `enroll_speaker.py` too. To find an unnamed voice in other videos, `speakers/group_voices.py` (step 1b) shows where it is heard; identification needs a name first: ask who it is (the user may give a placeholder name of their own, such as "Inconnu 1"; never propose one), then steps 3 and 4.

## 3. Store the name the user gives

```bash
python3.11 speakers/enroll_speaker.py "Name" CONTENT_ID --label SPEAKER_01          # the whole label is this person
python3.11 speakers/enroll_speaker.py "Name" CONTENT_ID 12.5 19 1:02:03 1:02:15      # only these ranges are (START END pairs)
```

Use the exact name the user gives (it is matched exactly: "Jean Dupont" and "jean dupont" would be two people). One or two contents per person are enough (each `--label` stores every clean turn of the label as a sample): identification names them in the other contents. Use ranges when the user says a label mixes voices (e.g. a clip played in the video), with times taken from `speakers/listen_speaker.py`. Ranges store voice samples only: the label itself stays unnamed, identification may name it automatically later, and evaluation (step 5) counts only labels enrolled with `--label`. To confirm or correct an automatic name, enroll the label with `--label` and the name the user gives: it replaces the automatic one. If the user says a label they enrolled is someone else, enrolling it again relabels it, but the first person keeps the samples taken from it: tell the user (only they can remove them, with `--forget`). `--label` uses the latest diarization of the content (`--diarization ID` for another) and needs the label's turns embedded (step 1); ranges need a SpeechBrain server.

## 4. Name the other labels

```bash
python3.11 speakers/identify_speakers.py --dry-run          # or with content ids: python3.11 speakers/identify_speakers.py 243 418 --dry-run
python3.11 speakers/identify_speakers.py
```

Run the dry run first, show the user what would be named, and store only with their go-ahead. A label is named when its similarity to an enrolled speaker reaches the threshold (`--threshold`, 0.6 by default, provisional); the others are stored as unknown. Labels set by the user are never changed. Run it again after each enrollment. It needs at least one enrolled speaker: before that it exits 1 with `No speaker has samples ...` (go back to steps 2 and 3).

## 5. Check the threshold

```bash
python3.11 speakers/evaluate_speakers.py
```

Once a speaker is enrolled with `--label` in at least 2 contents (or with `--label` in one and ranges in another), it shows, for several thresholds, how many labels would be named right or wrong. Suggest the threshold with no wrong names and the most right ones, and say how many cases it rests on. With speakers enrolled in one content each, it prints `No speaker is labelled in 2 contents: only false names can be counted.`: say so. Before any `--label` enrollment it exits 1 with `No label set by a human ...`: then answer that the threshold is 0.6 by default and provisional (in a first test on 5 videos, one person scored 0.90-0.95 across videos, different people at most 0.45).

After a change of the SpeechBrain model, run step 1 again; the scripts then exit with `Turn embeddings of several models [...]: choose one with --model`: pass `--model <the new model>` to `enroll_speaker.py --label`, `identify_speakers.py` and `evaluate_speakers.py`, and enroll the speakers again under the new model before steps 4 and 5 mean anything. Run this step again after a change of the server image.

To report who speaks in a video and for how long afterwards, use the `query-database` skill; to show what each speaker says, with the names, run `python3.11 explore/show_transcript.py CONTENT_ID`.
