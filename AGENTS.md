# Notes for AI agents

Sniff Machine downloads videos and audios (YouTube, Twitch, French TV and radio, through yt-dlp), transcribes them (Whisper), diarizes them (Pyannote: who speaks when) and computes voice embeddings (SpeechBrain). The model servers run in Docker containers on AMD GPU machines. Everything is tracked in one SQLite database, `cache/test.db`. See `README.md` for the architecture and `PLAN.md` for the roadmap.

## Layout

| Path | What it is |
|---|---|
| `jobs/job_template.py` | template of a job: select contents (channel, URLs, upload dates), then download / transcribe / diarize them |
| `jobs/*.py` | jobs created from the template (one per request) |
| `*.py` at the root | one script per feature (`diarize_contents.py`, `transcript_pending_audios.py`, `run_*_servers.py`, ...) |
| `servers.py` | addresses of the model servers; `check_servers.py` checks them |
| `helpers/` | shared code: `database.py` (schema, migrations, queries), `pipeline.py` (steps run on the servers), `jobs.py`, `yt_dlp.py`, one client and one server module per model |
| `cache/` | the database and every downloaded or computed file |
| `.claude/skills/*/SKILL.md` | step-by-step procedures (see below) |

## Skills

Before doing one of these tasks, read the matching `SKILL.md` and follow it step by step:

| Task | Skill |
|---|---|
| download, transcribe or diarize the videos of a channel, a playlist, a date range or a list of URLs | `.claude/skills/pipeline-job/SKILL.md` |
| answer a question about what the database contains (videos, transcriptions, speakers, turns) | `.claude/skills/query-database/SKILL.md` |
| check, start or stop the model servers, change their addresses | `.claude/skills/model-servers/SKILL.md` |
| anything else that needs a new script (custom selection, new step, export) | `.claude/skills/write-pipeline-script/SKILL.md` |

## Rules

- Run every command from the repository root.
- Never delete, move or overwrite `cache/`, `cache/test.db` or `cache/backups/`: they cannot be recomputed. Never change the database by hand (no `DELETE`, `UPDATE`, `DROP`): read it with `.claude/skills/query-database/query.py`. In Claude Code, `.claude/hooks/protect_data.py` blocks the usual ways of doing it.
- Never run a `run_*_servers.py` script while `check_servers.py` says its servers are OK or BUSY: it restarts them and kills the work in progress.
- Long commands (jobs, server builds) run in the background with a log file; check the log instead of waiting.
- Do not commit `cache/`, `hf.token` or job logs.
