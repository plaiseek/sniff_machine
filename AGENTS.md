# Notes for AI agents

Sniff Machine downloads videos and audios (YouTube, Twitch, French TV and radio, through yt-dlp), transcribes them (Whisper), diarizes them (Pyannote: who speaks when), computes voice embeddings (SpeechBrain) and splits the transcriptions into sentences (wtpsplit), each word and sentence with its speaker. An LLM (vLLM) can then be asked where something is said (`explore/ask_transcripts.py`). The model servers run in Docker containers on AMD GPU machines. Everything is tracked in one SQLite database, `cache/test.db`. See `README.md` for the architecture and `PLAN.md` for the roadmap.

## Layout

| Path | What it is |
|---|---|
| `jobs/job_template.py` | template of a job: select contents (channel, URLs, upload dates), then download / transcribe / diarize them |
| `jobs/*.py` | jobs created from the template (one per request) |
| `pipeline/` | steps on what is in the database: `transcript_pending_audios.py`, `diarize_contents.py`, `store_words.py`, `split_sentences.py`, `embed_speaker_turns.py` |
| `speakers/` | who the speakers are: `group_voices.py`, `listen_speaker.py`, `enroll_speaker.py`, `identify_speakers.py`, `evaluate_speakers.py` |
| `explore/` | read and search the transcriptions, ask an LLM: `show_transcript.py`, `search_transcripts.py`, `ask_transcripts.py`, `plot_word_counts.py` |
| `model_servers/` | start and check the model servers: `run_*_servers.py`, `check_servers.py` |
| `admin/` | the database file itself: `create_sqlite_db.py`, `restore_backup.py` (for the user only) |
| `servers.py` | addresses of the model servers of every machine; `model_servers/check_servers.py` checks them; a step uses those that answer |
| `local_servers.yaml` | which model servers this machine runs, on which GPUs and ports (git-ignored, one per machine; template `local_servers.example.yaml`) |
| `helpers/` | shared code: `database.py` (`connect()`, and every query re-exported: `import helpers.database as db`), `migrations.py` (the schema), `tables/` (the queries, by group of tables), `pipeline.py` (steps run on the servers), `jobs.py`, `yt_dlp.py`, one client and one server module per model |
| `cache/` | the database and every downloaded or computed file |
| `criteria/` | criteria files for `explore/ask_transcripts.py --criteria` (e.g. hateful speech under French law) |
| `exports/` | what is exported to hand over (findings with clips, CSV): ignored by git |
| `tests/` | tests without GPU, network or Docker: `python3.11 -m unittest discover tests` |
| `PLAN.md`, `docs/` | what is still to do; the media to add (`docs/sources.md`); the October 2026 review with the design of every phase (`docs/history/`) |
| `.claude/skills/*/SKILL.md` | step-by-step procedures (see below) |

## Skills

Before doing one of these tasks, read the matching `SKILL.md` and follow it step by step:

| Task | Skill |
|---|---|
| download, transcribe, diarize, compute the voice embeddings of or split into sentences the videos of a channel, a playlist, a date range or a list of URLs | `.claude/skills/pipeline-job/SKILL.md` |
| answer a question about what the database contains (videos, transcriptions, speakers, turns, what was said and by whom, word counts) | `.claude/skills/query-database/SKILL.md` |
| find with an LLM where something is said that no fixed words find (hateful speech for a lawsuit, a topic, an opinion), or show what it found | `.claude/skills/ask-transcripts/SKILL.md` |
| hand what the LLM found over (e.g. to a lawyer): the findings as files, with an audio clip of each passage | `.claude/skills/findings-dossier/SKILL.md` |
| find out who the speakers are: voice embeddings, listening to a label, naming it, identifying it in other videos | `.claude/skills/speaker-identity/SKILL.md` |
| check, start or stop the model servers, run them on this machine, change their addresses | `.claude/skills/model-servers/SKILL.md` |
| say what is running, how the last jobs and questions ended, the disk space and GPU memory left | `.claude/skills/work-status/SKILL.md` |
| anything else that needs a new script (custom selection, new step, export) | `.claude/skills/write-pipeline-script/SKILL.md` |
| check that every skill still works after a change | `.claude/skills/check-skills/SKILL.md` |

## Rules

- Run every command from the repository root.
- Never delete, move or overwrite `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed. Never change the database by hand (no `DELETE`, `UPDATE`, `DROP`): read it with `.claude/skills/query-database/query.py`. In Claude Code, `.claude/hooks/protect_data.py` blocks the usual ways of doing it. Restoring a backup (`admin/restore_backup.py`) is for the user to do.
- Never name a speaker yourself (from a title, a channel name or a transcript): only the user says who a diarization label is (`speakers/enroll_speaker.py`), and automatic names are reported as guesses with their similarity. `speakers/enroll_speaker.py --forget` is for the user to run.
- Never run a `model_servers/run_*_servers.py` script while `model_servers/check_servers.py` says a server of its kind on this machine is OK, BUSY or LOADING: it restarts them and kills the work in progress.
- Stop the model servers you started once the batch they were started for is done (`model-servers` skill); never stop the others without asking.
- Long commands (jobs, server builds) run in the background with a log file (`python3.11 -u`, so that the log is written as it goes); check the log instead of waiting.
- After changing `helpers/`, run `python3.11 -m unittest discover tests`: every test must pass.
- Do not commit `cache/`, `exports/`, `local_servers.yaml`, `hf.token` or job logs.
