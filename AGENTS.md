# Notes for AI agents

Sniff Machine downloads videos and audios (YouTube, Twitch, French TV and radio, through yt-dlp), transcribes them (Whisper), diarizes them (Pyannote: who speaks when), computes voice embeddings (SpeechBrain) and splits the transcriptions into sentences (wtpsplit), each word and sentence with its speaker. An LLM (vLLM) can then be asked where something is said (`ask_transcripts.py`). The model servers run in Docker containers on AMD GPU machines. Everything is tracked in one SQLite database, `cache/test.db`. See `README.md` for the architecture and `PLAN.md` for the roadmap.

## Layout

| Path | What it is |
|---|---|
| `jobs/job_template.py` | template of a job: select contents (channel, URLs, upload dates), then download / transcribe / diarize them |
| `jobs/*.py` | jobs created from the template (one per request) |
| `*.py` at the root | one script per feature (`diarize_contents.py`, `transcript_pending_audios.py`, `run_*_servers.py`, ...) |
| `servers.py` | addresses of the model servers; `check_servers.py` checks them |
| `helpers/` | shared code: `database.py` (`connect()`, and every query re-exported: `import helpers.database as db`), `migrations.py` (the schema), `tables/` (the queries, by group of tables), `pipeline.py` (steps run on the servers), `jobs.py`, `yt_dlp.py`, one client and one server module per model |
| `cache/` | the database and every downloaded or computed file |
| `criteria/` | criteria files for `ask_transcripts.py --criteria` (e.g. hateful speech under French law) |
| `tests/` | tests without GPU, network or Docker: `python3.11 -m unittest discover tests` |
| `PLAN.md`, `docs/` | what is still to do; the media to add (`docs/sources.md`); the October 2026 review with the design of every phase (`docs/history/`) |
| `.claude/skills/*/SKILL.md` | step-by-step procedures (see below) |

## Skills

Before doing one of these tasks, read the matching `SKILL.md` and follow it step by step:

| Task | Skill |
|---|---|
| download, transcribe, diarize or split into sentences the videos of a channel, a playlist, a date range or a list of URLs | `.claude/skills/pipeline-job/SKILL.md` |
| answer a question about what the database contains (videos, transcriptions, speakers, turns, what was said and by whom, word counts) | `.claude/skills/query-database/SKILL.md` |
| find with an LLM where something is said that no fixed words find (hateful speech for a lawsuit, a topic, an opinion), or show what it found | `.claude/skills/ask-transcripts/SKILL.md` |
| find out who the speakers are: voice embeddings, listening to a label, naming it, identifying it in other videos | `.claude/skills/speaker-identity/SKILL.md` |
| check, start or stop the model servers, change their addresses | `.claude/skills/model-servers/SKILL.md` |
| anything else that needs a new script (custom selection, new step, export) | `.claude/skills/write-pipeline-script/SKILL.md` |

## Rules

- Run every command from the repository root.
- Never delete, move or overwrite `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed. Never change the database by hand (no `DELETE`, `UPDATE`, `DROP`): read it with `.claude/skills/query-database/query.py`. In Claude Code, `.claude/hooks/protect_data.py` blocks the usual ways of doing it. Restoring a backup (`restore_backup.py`) is for the user to do.
- Never name a speaker yourself (from a title, a channel name or a transcript): only the user says who a diarization label is (`enroll_speaker.py`), and automatic names are reported as guesses with their similarity. `enroll_speaker.py --forget` is for the user to run.
- Never run a `run_*_servers.py` script while `check_servers.py` says its servers are OK or BUSY: it restarts them and kills the work in progress.
- Long commands (jobs, server builds) run in the background with a log file; check the log instead of waiting.
- After changing `helpers/`, run `python3.11 -m unittest discover tests`: every test must pass.
- Do not commit `cache/`, `hf.token` or job logs.
