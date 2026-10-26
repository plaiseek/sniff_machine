---
name: findings-dossier
description: Prepares what an LLM found in the transcriptions (explore/ask_transcripts.py) for a lawyer or another reader - a folder with the findings as Markdown and CSV, the question and its criteria, and an audio clip of each passage - then reports what it holds. Use for requests like "prepare the passages for my lawyer", "export the hateful speech found in channel X", "give me the clips of what the LLM found", "make a file of the findings" (in French dossier pour l'avocat, exporter les passages, extraits audio des propos, constituer un dossier).
---

# Findings dossier

`explore/ask_transcripts.py --show --export DIR` writes what a question found to a folder that can be handed over: `findings.md` (to read), `findings.csv` (to sort and annotate in a spreadsheet: `;` between columns, UTF-8 with a BOM, as a spreadsheet set to French expects), `question.txt` (the question and its criteria, word for word) and `clips/` (an mp3 of each passage, 3 s wider on each side, cut from this machine's copy of the audio). Finding the passages is the `ask-transcripts` skill: this one only exports what is stored. Run every command from the repository root.

## Rules

- **The findings are leads, not evidence of an offence.** They are what an LLM thinks of an automatic transcription. Never write that a person said or did something on the strength of a finding, and never tell the user that something is an offence: that is for the lawyer.
- **Never name a speaker yourself.** The dossier shows the diarization labels (`SPEAKER_01`), with a name only as the database gives it: set by a person (`human`) or guessed from the voice (`auto, 0.92`, to report as a guess). Naming a label is the `speaker-identity` skill, and the user's decision.
- **The folder stays on this machine.** Never upload, email or copy it anywhere else: the user hands it over. The clips are voice recordings of real people.
- Export YES and UNSURE (the default). Add `--all` (the rejected passages too) only when the user asks.
- Never change the findings, the database or a criteria file to change what the dossier says.

## 1. Find the question

If the question was asked in this conversation, take its exact text, `--criteria` file and `--speaker`. Otherwise list the stored questions:

```bash
python3.11 .claude/skills/findings-dossier/questions.py        # every question: text, criteria file, speaker, model, counts
python3.11 .claude/skills/findings-dossier/questions.py 1      # question 1: its channels and upload dates, and its arguments
```

The second form ends with the arguments to reuse word for word, e.g. `'Quels propos ... ?' --criteria criteria/haine_loi_1881.txt --question 1` (`--question` picks that question when the same text was also asked with another model or settings). If it prints `NOT FOUND in criteria/`, the criteria file was edited, removed, or is not a `.txt` file of `criteria/`: if the user knows where the original file is, use that path; otherwise stop and tell the user (`--show` cannot find the question any more). If several questions match the request (another model, other criteria), show the user the list and ask which one.

## 2. Choose the contents

The selection is the same as for `ask_transcripts.py`: `--uploader NAME`, `--content ID` (repeatable), `--uploaded-from` / `--uploaded-to YYYY-MM-DD`. Only the findings of the selected contents are exported. Take what the user asks for: a whole channel is `--uploader NAME` with no dates. For a lawsuit, these offences are prescribed one year after publication (the lawyer confirms): when the user did not give a period, export what they asked for, and say in the report how many of the findings are from the last 365 days (`python3.11 .claude/skills/pipeline-job/dates.py last-365-days` prints the dates: run step 3 again with `--uploaded-from <UPLOADED_FROM> --uploaded-to <UPLOADED_TO>` to count them). Use those dates in the selection only when the user asks for that period.

Below, `<arguments>` stands for the arguments `questions.py N` printed (question, `--criteria`, `--speaker` when there is one, `--question N`), and `<selection>` for the options of the contents.

## 3. Look before exporting

```bash
python3.11 explore/ask_transcripts.py <arguments> <selection> --show
```

It uses no server and writes nothing. It prints `N contents selected` (and how many were left out: not transcribed, no words), `Question N, asked with MODEL since ...`, then each finding under a `Content ID (date): title` line, and ends with `Asked on N of M contents (...): N passages match, N unsure, N checked and rejected`. If no passage matches and none is unsure, tell the user and stop. If it stops earlier:
- `This question (with these criteria and speaker) was never asked`, or `Question N does not have this text, criteria and speaker`: an argument differs from step 1: take them from `questions.py N` again.
- `No content matches the selection`: the selection options match no content: check them, or ask the user.
- `0 contents selected (... left out: not transcribed, no words)` and nothing else: no content of the selection is transcribed: tell the user (the `pipeline-job` skill transcribes them) and stop.
- `No label is named '<NAME>' in these contents: ...`: nobody is named so in these contents: stop and tell the user (naming a label is the `speaker-identity` skill and the user's decision; never run the `enroll_speaker.py` this message mentions). If `Asked on N of M` shows that contents of the selection were never asked, say so: the dossier does not cover them (asking them is the `ask-transcripts` skill, with the user's go-ahead).

## 4. Export

```bash
python3.11 explore/ask_transcripts.py <arguments> <selection> --show --export exports/<today>_<short_name>
```

`<today>` is `date +%F`; `<short_name>` uses lowercase letters, digits and `_` (e.g. `exports/2026-10-26_psyhodelik_haine`). `exports/` is ignored by git: never commit it. The output ends with `Exported N findings to DIR: findings.md, findings.csv, question.txt, clips/`, then a `No clip for the findings of content N: <why>` line for each content where some or all findings got no clip (its audio is no longer on disk: a `pipeline-job` job with `STEPS = ["download"]` alone downloads it again, then export again; its audio ends before the passage; or ffmpeg failed): those findings have a link but no clip (`findings.md` says `Clip: none (...)` for each); say so in the report. `Nothing exported to DIR: no content selected has words`: the selection holds no transcribed content. Exporting again to the same folder rewrites it (the old clips are removed). `--export` does not go with `--dry-run`.

## 5. Check and report

```bash
ls exports/<folder>
ls exports/<folder>/clips | wc -l
grep -c '^- Clip: none' exports/<folder>/findings.md
head -n 30 exports/<folder>/findings.md
```

The number of clips must be the number exported, less the findings with `Clip: none`. Report to the user:

- the folder's full path, and what it holds (the four items above);
- the question, the criteria file, the model and since when it was asked;
- the contents covered (channel, upload dates, how many were asked), and the counts: `N match (YES), N unsure`;
- the findings without a clip, if any;
- that each passage is a lead to listen to (an automatic transcription read by an LLM), that the speakers are diarization labels (names only as the database gives them), and that the qualification, and how to preserve the passages as evidence, are for the lawyer. The clips come from this machine's copy of the audio, so they remain if a video is removed from its platform; the links point to the platform.

Give the user the counts and the path; do not paste the passages into the conversation unless the user asks.
