---
name: ask-transcripts
description: Finds where something is said in the transcriptions that no fixed words can find - hateful or discriminatory speech (leads for a lawsuit under French press law), insults, threats, a topic, an opinion, a promise - by asking an LLM served by vLLM to read them with explore/ask_transcripts.py, then shows what it found with times, speakers and links. Use for requests like "when does X say something hateful about a group?", "find the passages of channel Y that could be prosecuted", "where does he talk about the police?", "show what the LLM found" (in French propos haineux, discours de haine, incitation à la haine, où parle-t-il de, quand dit-il que, qu'a trouvé le LLM).
---

# Ask the transcriptions

`explore/ask_transcripts.py` reads each transcription in windows and lists the passages that may match a question (first pass), then checks each one with its context (second pass, the LLM thinks first). What it finds is stored in the database and printed. Run every command from the repository root.

## Rules

- **The findings are leads, not facts.** They say what an LLM thinks of an automatic transcription. Report each one as "the LLM judged...", with its verdict, confidence and link, and say it must be listened to. Never write that a person said or did something on the strength of a finding alone.
- **Never name a speaker yourself.** A finding shows the diarization labels (`SPEAKER_15`) and their names only when a person enrolled them, or `speakers/identify_speakers.py` guessed them (report the guess with its similarity). Never decide who a label is from a title, a channel or what is said: that is the `speaker-identity` skill, and the user's decision.
- **It is not legal analysis.** For a lawsuit, the findings are passages to show a lawyer; never tell the user that something is an offence.
- **A long run needs the user's go-ahead** (step 3): a whole channel takes hours of a GPU.
- **Never edit a criteria file that a stored question used.** The criteria are part of the question: an edited file is a new question that reads everything again, and `--show` can no longer find the old one. To change the criteria, copy the file under a new name, with the user (step 1).
- Never send the findings or the transcripts to another service.

## 1. Turn the request into arguments

| Argument | Value |
|---|---|
| question | one sentence, in the language of the videos (French for French channels: the LLM answers in that language). For hateful speech under French law, use exactly: `"Quels propos pourraient être poursuivis comme discours de haine selon la loi du 29 juillet 1881 ?"` |
| `--criteria FILE` | for hateful speech under French law: `criteria/haine_loi_1881.txt`. For another question, none, unless the request gives precise rules of what matches: then write them, with the user, in a new file `criteria/<short_name>.txt` |
| `--uploader NAME` | part of the channel name (`Psyhodelik`), as the `query-database` skill finds it |
| `--content ID` | one content, repeatable |
| `--uploaded-from`, `--uploaded-to` | `YYYY-MM-DD`, inclusive. For a relative period, take the dates of the `UPLOADED_FROM = "..."` and `UPLOADED_TO = "..."` lines printed by `python3.11 .claude/skills/pipeline-job/dates.py last-365-days` (or `last-month`, `2026-03`, ...); `<UPLOADED_FROM>` below stands for that date (add `--uploaded-to <UPLOADED_TO>` for a period that ends before today). A resume and `--show` reuse the dates of the first run, not those dates.py gives on another day. For a lawsuit, suggest the last 365 days: these offences are prescribed one year after publication (the lawyer confirms) |
| `--speaker NAME` | only when the request is about one person: the exact name of an enrolled speaker. Check it exists: `python3.11 .claude/skills/query-database/query.py "SELECT name FROM speakers"`. If the person is not enrolled, stop and offer the `speaker-identity` skill. It also uses the labels that `speakers/identify_speakers.py` only guessed (auto): a finding under that name may be another voice |

The question, the criteria and the speaker must be written the same way every time: re-running resumes, and `--show` finds the results, only for the exact same text.

**Stop and ask the user** when you cannot tell which channel or contents are meant, when the request is about a person who is not enrolled, or when the request asks to change the criteria.

## 2. Check the server

```bash
python3.11 model_servers/check_servers.py ask
```

At least one `vllm` line must say `OK (<model>)`: the run uses the servers that answer and leaves the others out. If none does, stop and tell the user which addresses are `DOWN`: vLLM is never started from this repository (see the `model-servers` skill). `BUSY` or `LOADING`: check again in a minute, up to 5 times, then tell the user (the run leaves out a vLLM server that does not answer within 10 s, or answers an error). Do not edit `servers.py` unless the user gives a new address.

## 3. Dry run, then the user's go-ahead

```bash
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from <UPLOADED_FROM> --dry-run
```

It is not read-only: like the run, it first stores the words of new transcriptions and aligns again those of contents diarized since (`Words: N transcriptions stored, M aligned again, K failures.`). Then it prints the number of contents to ask about and of those left out: not transcribed, transcribed with no words (Whisper heard nothing, often music only: say so to the user), or with no label named `--speaker`. The output does not name them: find them with the `query-database` skill (no `transcriptions` row, or no `words`). Then the words and the time: `Both passes write about A to B tokens (easy to hard question): H:MM:SS to H:MM:SS at 300 tokens/s (--tokens-per-s, 1 server), plus the time to read.` The hours are a lower bound: reading comes on top, and they count every listed server, even a `DOWN` one. Hateful speech with the criteria file is the hard end, an average over many contents: a few contents with passages to check can write 20 % or more beyond it. Tell the user the number of contents (and those left out, with why) and the hours, and wait for their go-ahead before running more than a few contents.

- `No content matches the selection` (exit 1): check the channel name and the dates.
- `0 contents to ask about (N left out: ...)` (exit 0): nothing to ask; tell the user why (the `pipeline-job` skill transcribes contents).
- `No label is named 'NAME' in these contents: ...` (exit 1): that speaker is not identified in these contents yet (the `speaker-identity` skill).

## 4. Run it in the background

```bash
nohup python3.11 -u explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from <UPLOADED_FROM> >> jobs/<today>_ask_<short_name>.log 2>&1 &
```

`<today>` is `date +%F`. (In Claude Code you can instead run the command without `nohup` and `&`, with `run_in_background`.) Do not start the same question twice at once: nothing prevents it, so check first with `ps -eo pid,args | grep "[e]xplore/ask_transcripts"` (or the `work-status` skill). Check the progress with `tail -n 5 jobs/<file>.log`: each content ends with a line like

```
( 12/340) Done! -> 2 windows, 9 passages checked: 0 yes, 2 unsure, 7 no (60,123 tokens read, 41,870 written; 287 tokens/s written so far (expected 300), about 7:42:10 left)
```

The run is finished when the log ends with the findings, the line `Asked on N of M contents ... These are leads to listen to, not verdicts.` and `Finished with exit code N.`: 0 everything done; 1 some contents failed (`Failed questions:` lines name them and their errors, or `No question, no server of the step was left (re-run to retry):`); 5 a server is probably stuck: tell the user it needs a restart, do not run it again. Without a `Finished` line, first check whether it still runs (the `ps` above). If it does not and the log has a `Question N, with MODEL on ...` line, it was killed or crashed after it started: report the last lines, and run the same command again to resume. Without that line, it stopped before starting: the last line says why (exit 1, or 0 after `0 contents to ask about`).

- **Resume**: run the same command again. Only the contents not asked yet (or diarized since) are read; the others print `Already done`. Run it once more when contents failed; if the same ones fail again, report their errors and do not change any code. If every content failed with `Read timed out` or `dropped the connection`, the vLLM server is probably stuck: tell the user it needs a restart, do not run it again.
- **Same model and reasoning setting**: resuming needs them too (see the warning below); otherwise the same command starts a new question that reads everything again (new dry run and go-ahead). Before resuming, note the question's number (`python3.11 .claude/skills/findings-dossier/questions.py`); if the run's `Question N, with MODEL on ...` line gives another N, stop it (`kill <pid>`) and ask the user.
- **Stopping** (`kill <pid>`) is safe: the contents in progress are lost, the finished ones are stored.
- `Warning: ... does not think before answering`: vLLM runs without the model's reasoning parser, so the checks are weaker. Tell the user (they restart vLLM). On a first run it may go on; on a resume it is a new question (the reasoning setting is part of it): stop it and ask the user.
- `Warning: the KV cache of ... holds N tokens, not M: vLLM may pause requests (use --kv-tokens N)`: the run goes on (do not restart it); tell the user, and with their agreement pass that `--kv-tokens N` next time.

### Server hints

`--clients 8 --kv-tokens 440000 --tokens-per-s 300` are the defaults: the user's figures for their GPU (8 requests at once is where it is most efficient, 440k tokens of KV cache, 300 tokens/s written). Each server gets `--clients` requests at once, never needing more than `--kv-tokens`. Change them only with the user: if the `tokens/s written so far` of a long run stays well under the expected speed, they may try `--clients 12`. The hints do not change the answers: a run with other hints resumes the same question.

## 5. Show and report the findings

```bash
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from <UPLOADED_FROM> --show          # yes and unsure
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from <UPLOADED_FROM> --show --all    # also the rejected ones
```

`--show` uses no server and writes nothing; its first line counts the contents selected, and its last line (`Asked on N of M`) how many the question was asked on. `This question (with these criteria and speaker) was never asked`: the question, criteria file or speaker differs from the run's (step 1). If you do not know them (a run of another conversation), list the stored questions: `python3.11 .claude/skills/findings-dossier/questions.py` (every question, with its criteria file, speaker, model, settings and counts), then `... questions.py N` for the exact arguments of question N. `--show` prints the newest question with this text and speaker (`(K older settings or models)` when the same text was asked with another model or settings: `--question N` shows another one). It prints:

```
409 contents selected (58 left out: not transcribed, no words)
Question 1, asked with qwen3.8-27b since 2026-10-26T15:41:03Z

Content 243 (2026-01-08): <title>
  [0:15:23] SPEAKER_01  https://www.youtube.com/watch?v=...&t=923s
    UNSURE (0.65, own words): <why, in the LLM's words>
      Meets: <offence (article) — group — protected criterion>
      Against: <what a defence could object>
    « <the transcription of the passage> »
```

- A named label prints `SPEAKER_01 = <name> (human)`, or `(auto, 0.92)` for a guess from the voice: report its similarity. `unknown (auto, 0.41)` and `nobody known (human)` are not names.
- The verdict: `YES` (with the criteria file: a lawyer could build a case on it), `UNSURE` (plausible but debatable: the lawyer decides), `NO` (checked and rejected, only with `--all`). For a lawsuit, the list to review is YES **and** UNSURE. Verdicts on borderline passages can change from one run to another; clear ones do not.
- The confidence (0 to 1) is the LLM's, and what the speaker does with the words: `own words`, `approves what they quote or read`, `quoting, reading or reporting` (no stance), `quotes to criticize`, `unclear`.
- `Meets:` the offence, group and protected criterion (with the criteria file); `Against:` what a defence could object. Each appears only when the LLM gave one.
- `(the quote is not in this passage: ...)`: the LLM's quote does not match the transcription: say so, it is a weaker lead.
- `UNSURE: No answer ...`: the LLM gave no answer for that passage; it still needs listening.

Report to the user: the number of contents asked, the contents left out with their reason (the `left out` line: `Asked on N of M` does not count them), the counts (`N passages match, N unsure, N checked and rejected`), then each YES and UNSURE finding with the content's title and date, the time and link, the label (with its name only as the database gives it), the verdict and confidence, the attribution, the `Meets:` line and the quote. End by saying that these are leads to listen to, not verdicts. To count or filter findings (per content, per verdict, per label), query the `findings` table with the `query-database` skill.

## 6. Hand the findings over

When the user wants them as files (for a lawyer), add `--export` to the `--show` command (with `--all`, the rejected passages are exported too: only when the user asks):

```bash
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from <UPLOADED_FROM> --show --export exports/<today>_<short_name>
```

It prints `Exported N findings to DIR: findings.md, findings.csv, question.txt, clips/`, with an mp3 of each passage (3 s more on each side) when the content's audio is still on disk (otherwise a `No clip for the findings of content N: <why>` line). `exports/` is never committed. For the whole procedure, see the `findings-dossier` skill.
