---
name: speaker-identity
description: Finds out who the speakers of diarized videos are - computes the voice embeddings of their turns, gives the user a clip of a diarization label (SPEAKER_01, ...) to listen to, stores the name the user gives for it (enrollment), then names the labels of the other videos by voice similarity and measures how reliable that is. Use for requests like "who is SPEAKER_01 in video 243?", "SPEAKER_00 of video 418 is Jean Dupont", "find Jean Dupont in the other videos", "identify the speakers of channel X" (in French qui parle, identifier les intervenants, c'est X).
---

# Speaker identity

Diarization labels (`SPEAKER_00`, ...) only mean something inside one diarization. A person gets a name in three stages: their turns get voice embeddings, the user hears a label and names it (enrollment), then the other labels are compared with the named voices. Run every command from the repository root.

## Rules

- **Never name a speaker yourself**, whether from a video title, the channel name, what the transcript says or a guess such as "the host". Voiceprints identify real people: only the user decides who a label is, and only for a label or time range they point to. If they give a name without a label, ask which label (show them the `speakers/listen_speaker.py` table).
- **You cannot listen** to a clip. Give the user its path and the links to the video; the transcript excerpts show what is said, not who says it.
- Automatic names are guesses: report them as "identified automatically (similarity 0.92)", never as facts.
- `speakers/enroll_speaker.py --forget` deletes what the user entered: only the user runs it (`.claude/hooks/protect_data.py` blocks it).
- The clips in `cache/speaker_clips/` are voice recordings of real people: never send them anywhere.

## 1. Voice embeddings

Needs diarized contents (the `pipeline-job` skill) and a SpeechBrain server:

```bash
python3.11 model_servers/check_servers.py embed          # must say OK or BUSY (otherwise see the model-servers skill)
python3.11 pipeline/embed_speaker_turns.py >> cache/embed_speaker_turns.log 2>&1   # in the background if many contents
```

It embeds the exclusive turns that have no embedding yet, and ends with a `Summary:` line (exit code 0, or 1 if some contents failed: run it again). A job with `"embed"` in `STEPS` does the same after its diarizations.

## 2. Listen to a label

```bash
python3.11 speakers/listen_speaker.py CONTENT_ID                # the labels: turns, minutes, clean minutes, who they are
python3.11 speakers/listen_speaker.py CONTENT_ID SPEAKER_01     # a clip of its 3 longest clean turns
```

Show the user the table, or the clip path (`Clip: ...`) with each turn's time, link and text, and ask who it is. A label with no clean minutes cannot be enrolled with `--label`, and identification leaves it out.

## 3. Store the name the user gives

```bash
python3.11 speakers/enroll_speaker.py "Name" CONTENT_ID --label SPEAKER_01          # the whole label is this person
python3.11 speakers/enroll_speaker.py "Name" CONTENT_ID 12.5 19 1:02:03 1:02:15      # only these ranges are (START END pairs)
```

Use the exact name the user gives (it is matched exactly: "Jean Dupont" and "jean dupont" would be two people). One or two contents per person are enough (each `--label` stores every clean turn of the label as a sample): identification names them in the other contents. Use ranges when the user says a label mixes voices (e.g. a clip played in the video), with times taken from `speakers/listen_speaker.py`. `--label` uses the latest diarization of the content (`--diarization ID` for another) and needs the label's turns embedded (step 1); ranges need a SpeechBrain server.

## 4. Name the other labels

```bash
python3.11 speakers/identify_speakers.py --dry-run          # or with content ids: python3.11 speakers/identify_speakers.py 243 418 --dry-run
python3.11 speakers/identify_speakers.py
```

Run the dry run first and show the user what would be named; then store. A label is named when its similarity to an enrolled speaker reaches the threshold (`--threshold`, 0.6 by default, provisional); the others are stored as unknown. Labels set by the user are never changed. Run it again after each enrollment.

## 5. Check the threshold

```bash
python3.11 speakers/evaluate_speakers.py
```

Once a speaker is labelled by the user in at least 2 contents, it shows, for several thresholds, how many labels would be named right or wrong. Suggest the threshold with no wrong names and the most right ones, and say how many cases it rests on. Run it again after a change of the SpeechBrain model or server image.

To report who speaks in a video and for how long afterwards, use the `query-database` skill; to show what each speaker says, with the names, run `python3.11 explore/show_transcript.py CONTENT_ID`.
