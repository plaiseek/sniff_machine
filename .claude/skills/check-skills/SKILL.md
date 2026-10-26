---
name: check-skills
description: Checks that every skill of this repository still works after a change (scripts moved, helpers changed, a skill edited, servers moved to another machine) - the tests, a review of each SKILL.md against the code, and a real run of each skill on a few short contents with the model servers - then fixes what disagrees and reports. Use for requests like "check the skills", "test every skill after this refactoring", "do the skills still work with the servers on this machine?" (in French vérifier les skills, tester chaque skill).
---

# Check the skills

A skill is checked when an agent that follows it word for word gets the result it promises. Reading is not enough: run each one on real data, on the smallest inputs that exercise it. Run every command from the repository root. With subagents, give one skill to each (the brief is in step 4); without, do the same yourself, one skill after the other.

## Rules

- Never name a speaker, never run `speakers/enroll_speaker.py` (any option) or `--forget`, never run `admin/restore_backup.py`: the check stops where the skill asks the user.
- Never run a `model_servers/run_*_servers.py` of a kind that `model_servers/check_servers.py` says OK, BUSY or LOADING on `127.0.0.1`, or that `docker ps --filter ancestor=amd_<kind>:latest` shows running; start servers only when the user asked (the `model-servers` skill).
- Nothing is deleted: not `cache/`, not the database, not the files a check created (list them in the report). A re-check uses new names (`<short_name>_2`) for its jobs and exports, and new contents for `pipeline-job`, so that it overwrites nothing and tests something new.
- A check agent does not edit the skills or the code: it reports. You fix afterwards (step 6).
- Runs of more than a few minutes on the GPU need the user's go-ahead: use short contents.

## 1. Before

`<scratch>` is a folder of your own outside the repository (in Claude Code, the session's scratchpad directory).

```bash
git status --short > <scratch>/git_before.txt              # to list afterwards the files the checks created
python3.11 -m unittest discover tests
for t in contents files transcriptions diarizations turns turn_embeddings words sentences questions question_contents findings speakers speaker_samples speaker_labels; do echo "$t $(python3.11 .claude/skills/query-database/query.py "SELECT COUNT(*) FROM $t" | sed -n 3p)"; done > <scratch>/counts_before.txt
ls cache/backups > <scratch>/backups_before.txt
```

Every test must pass before you start: if one fails, stop and tell the user. Keep the counts: they show afterwards what the checks added and that nobody named a speaker.

## 2. Servers and inputs

Follow the `work-status` skill, steps 1 and 2. Each kind a check needs must have at least one server up; vLLM runs on another machine. Leave out of the inputs the contents a running job or question works on (their `CONTENT_URLS`, `--content` or selection). Then pick the inputs:

```bash
# for pipeline-job (their URLs go in CONTENT_URLS): the shortest contents never downloaded...
python3.11 .claude/skills/query-database/query.py "SELECT id, url, duration_s, title FROM contents c WHERE NOT EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = '16k_mono_wav') ORDER BY duration_s LIMIT 5"
# ... or, when the two shortest last more than 15 minutes together, the shortest transcribed contents not diarized yet
python3.11 .claude/skills/query-database/query.py "SELECT id, url, duration_s, title FROM contents c WHERE EXISTS (SELECT 1 FROM words w WHERE w.content_id = c.id) AND NOT EXISTS (SELECT 1 FROM diarizations d WHERE d.content_id = c.id) ORDER BY duration_s LIMIT 5"
# 2 or 3 short diarized contents with words never asked to the LLM, for ask-transcripts (and speaker-identity)
python3.11 .claude/skills/query-database/query.py "SELECT c.id, c.duration_s, c.title FROM contents c JOIN transcriptions t ON t.content_id = c.id WHERE t.diarization_id IS NOT NULL AND c.duration_s < 1800 AND EXISTS (SELECT 1 FROM words w WHERE w.content_id = c.id) AND NOT EXISTS (SELECT 1 FROM question_contents q WHERE q.content_id = c.id) ORDER BY c.duration_s"
```

## 3. What to run, and what passes

| Skill | Request to give | Passes when |
|---|---|---|
| `pipeline-job` | "download, transcribe, diarize, compute the voice embeddings of and split into sentences these videos: <the 2 URLs>" (go-ahead given), or for transcribed contents "diarize, compute the voice embeddings of and split into sentences these videos: <the 2 URLs>" | the job ends with `Job finished with exit code 0: everything is done.` (a second run is allowed after exit 1, as the skill says); the 2 contents have a `whisper_result` file, a diarization, embeddings, words and sentences (a content with 0 words is music: no sentences) |
| `query-database` | 4 or 5 questions on those contents and older ones: status, speaking time per label, a phrase, a word count | each answer is confirmed by a second, independent query |
| `speaker-identity` | "who is SPEAKER_00 in <one of the new contents>?", then "find this person in the other videos" | the agent shows the labels table and a clip path, then stops and asks the user who it is; `identify_speakers.py` only with `--dry-run`; `speakers`, `speaker_samples` and `speaker_labels` unchanged |
| `ask-transcripts` | the hateful-speech question with `criteria/haine_loi_1881.txt` on 2 or 3 short diarized contents (go-ahead given); then a request about a person who is not enrolled | the run's log ends with `Finished with exit code 0.`; `--show` prints the findings; the second request stops at step 1 and offers the `speaker-identity` skill |
| `findings-dossier` | "prepare the findings of that question for my lawyer" | `exports/<today>_<name>/` holds `findings.md`, `findings.csv`, `question.txt` and `clips/` with one mp3 per exported finding whose audio is on disk |
| `write-pipeline-script` | "export to CSV the sentences of content N with speaker, start time and link" | a script in `jobs/` that only reads; a CSV in `exports/` with one row per sentence of the content |
| `model-servers` | "are the servers ready to transcribe and diarize?", "which servers run on this machine?", "restart the Whisper of <a remote address that is down>" | correct answers; the last request stops (another machine); nothing started or stopped |
| `work-status` | "what is running, and how did the last jobs end?" | the report of its step 5; nothing started, stopped or run with `--status` but job files |

Run `pipeline-job` first, then the checks that use its contents. The checks that only read, or use another server (vLLM), can run at the same time; two checks never send work to the same contents at once.

## 4. Brief for a check agent

Give each agent: the request (from the table), what is pre-approved (a short real run) and where it must stop, the rules above, and this:

> Follow `.claude/skills/<skill>/SKILL.md` literally, as an agent that received this request. Report, with the exact skill sentence and the exact output: every place where the skill and what happened disagree (commands, paths, options, quoted output lines, exit codes), every point where you had to guess, every stop point and what you would show the user, every file you created and every row the scripts added, and the requests that came up that no skill covers. Do not edit any skill or code.

Also give one agent per skill a static review: check every command, path, option, quoted output, exit code, table and function name of the SKILL.md against the code (grep the prints, read the argparse, `python3.11 .claude/skills/query-database/query.py --schema`), running only read-only commands.

## 5. After

Run the counts again into `<scratch>/counts_after.txt` and compare: `diff <scratch>/counts_before.txt <scratch>/counts_after.txt`. `speakers`, `speaker_samples` and `speaker_labels` must not change. The other counts mostly grow, but `sentences`, `transcriptions`, `question_contents`, `findings` and `files` are replaced in place when a content is diarized, stored or asked again, so a count can stay equal or drop: check the inputs' rows per content instead (`WHERE content_id IN (...)`). The counts cover the whole database: they hold the checks' work only if nothing else ran meanwhile (work-status step 2). `ls cache/backups` must be unchanged (a backup appears only if a script migrated the database). Run the tests again, and `git status --short` to list the files the checks created.

## 6. Fix and report

Check each reported deviation in the code before acting on it: the agents can be wrong. Fix the skill text when it disagrees with the code. Fix the code only for a real bug, with a test (`python3.11 -m unittest discover tests` must pass); a change of behaviour the user did not ask for is reported, not made. Then give each fixed skill to a fresh agent with the same request, and repeat until it passes.

Report to the user, per skill: the request, the outcome, what was fixed, and what could not be checked and why (naming a speaker, `--forget`, restoring a backup, stopping servers, long runs). Add: the rows added (from the counts), the files created (jobs, logs, exports, clips in `cache/speaker_clips/`), any change to `servers.py` or to the running servers, and the problems found outside the skills (server bugs, empty transcriptions).
