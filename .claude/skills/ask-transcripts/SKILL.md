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
| `--uploaded-from`, `--uploaded-to` | `YYYY-MM-DD`, inclusive. For a relative period, take the dates printed by `python3.11 .claude/skills/pipeline-job/dates.py last-365-days` (or `last-month`, `2026-03`, ...). For a lawsuit, suggest the last 365 days: these offences are prescribed one year after publication (the lawyer confirms) |
| `--speaker NAME` | only when the request is about one person: the exact name of an enrolled speaker. Check it exists: `python3.11 .claude/skills/query-database/query.py "SELECT name FROM speakers"`. If the person is not enrolled, stop and offer the `speaker-identity` skill |

The question, the criteria and the speaker must be written the same way every time: re-running resumes, and `--show` finds the results, only for the exact same text.

**Stop and ask the user** when you cannot tell which channel or contents are meant, when the request is about a person who is not enrolled, or when the request asks to change the criteria.

## 2. Check the server

```bash
python3.11 model_servers/check_servers.py ask
```

The line must say `OK` with a model name. If it says `DOWN`, stop and tell the user which address: vLLM is never started from this repository (see the `model-servers` skill). Do not edit `servers.py` unless the user gives a new address.

## 3. Dry run, then the user's go-ahead

```bash
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 --dry-run
```

It prints the number of contents (and those left out: not transcribed, or no label named `--speaker`), the words, and how long it takes: `Both passes write about A to B tokens (easy to hard question): H:MM:SS to H:MM:SS at 300 tokens/s`. Hateful speech with the criteria file is the hard end. Tell the user the number of contents and the hours, and wait for their go-ahead before running more than a few contents. `No content matches the selection`: check the channel name and the dates.

## 4. Run it in the background

```bash
nohup python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 >> jobs/<today>_ask_<short_name>.log 2>&1 &
```

`<today>` is `date +%F`. (In Claude Code you can instead run the command without `nohup` and `&`, with `run_in_background`.) Do not start the same question twice at once. Check the progress with `tail -n 5 jobs/<file>.log`: each content ends with a line like

```
(12/340) Done! -> 2 windows, 9 passages checked: 0 yes, 2 unsure, 7 no (60,123 tokens read, 41,870 written; 287 tokens/s written so far (expected 300), about 7:42:10 left)
```

The run is finished when the log ends with the findings and the line `Asked on N of M contents ... These are leads to listen to, not verdicts.` Its exit code: 0 everything done; 1 some contents failed (`Failed questions:` lines name them and their errors), or it stopped before starting (the last line says why); 5 the server is probably stuck: tell the user it needs a restart.

- **Resume**: run the same command again. Only the contents not asked yet (or diarized since) are read; the others print `Already done`. Run it once more when contents failed; if the same ones fail again, report their errors and do not change any code.
- **Stopping** (`kill <pid>`) is safe: the contents in progress are lost, the finished ones are stored.
- `Warning: ... does not think before answering`: vLLM runs without the model's reasoning parser, so the checks are weaker. Tell the user (they restart vLLM); the run goes on.
- `Warning: the KV cache of ... holds N tokens, not M`: pass `--kv-tokens N`.

### Server hints

`--clients 8 --kv-tokens 440000 --tokens-per-s 300` are the defaults: the user's figures for their GPU (8 requests at once is where it is most efficient, 440k tokens of KV cache, 300 tokens/s written). Each server gets `--clients` requests at once, never needing more than `--kv-tokens`. Change them only with the user: if the `tokens/s written so far` of a long run stays well under the expected speed, they may try `--clients 12`. The hints do not change the answers: a run with other hints resumes the same question.

## 5. Show and report the findings

```bash
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 --show          # yes and unsure
python3.11 explore/ask_transcripts.py "QUESTION" --criteria criteria/haine_loi_1881.txt --uploader Psyhodelik --uploaded-from 2025-10-26 --show --all    # also the rejected ones
```

`--show` uses no server. `This question (with these criteria and speaker) was never asked`: the question, criteria file or speaker differs from the run's (step 1). Each finding prints:

```
  [0:15:23] SPEAKER_01  https://www.youtube.com/watch?v=...&t=923s
    YES (0.72, own words): <why, in the LLM's words>
      Meets: Provocation directe à des destructions dangereuses (art. 24) — ...
      Against: Formulation hypothétique et exagérée, ...
    « <the transcription of the passage> »
```

- The verdict: `YES` (with the criteria file: a lawyer could build a case on it), `UNSURE` (plausible but debatable: the lawyer decides), `NO` (checked and rejected, only with `--all`). For a lawsuit, the list to review is YES **and** UNSURE. Verdicts on borderline passages can change from one run to another; clear ones do not.
- The confidence (0 to 1) is the LLM's, and what the speaker does with the words: `own words`, `approves what they quote or read`, `quoting, reading or reporting` (no stance), `quotes to criticize`, `unclear`.
- `Meets:` the offence, group and protected criterion (with the criteria file); `Against:` what a defence could object.
- `(the quote is not in this passage: ...)`: the LLM's quote does not match the transcription: say so, it is a weaker lead.
- `UNSURE: No answer ...`: the LLM gave no answer for that passage; it still needs listening.

Report to the user: the number of contents asked, the counts (`N passages match, N unsure, N checked and rejected`), then each YES and UNSURE finding with the content's title and date, the time and link, the label (with its name only as the database gives it), the verdict and confidence, the attribution, the `Meets:` line and the quote. End by saying that these are leads to listen to, not verdicts. To count or filter findings (per content, per verdict, per label), query the `findings` table with the `query-database` skill.
