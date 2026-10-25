---
name: query-database
description: Answers questions about what is stored in this repository's SQLite database (cache/test.db) - the downloaded videos and audios of a channel, their upload dates and durations, which ones are transcribed or diarized, the speakers and speaking turns of a video - with read-only SQL queries. Use for questions like "which videos of X are not diarized yet?", "how many speakers in video N?", "how long does SPEAKER_01 speak?" (in French quelles vidéos, combien d'intervenants, temps de parole).
---

# Query the database

Always use the read-only query script (it cannot change the database):

```bash
python .claude/skills/query-database/query.py "SELECT ..." [value for each ?]
python .claude/skills/query-database/query.py --schema
```

Put values in `?` placeholders instead of writing them in the SQL text: `query.py "SELECT id, title FROM contents WHERE casefold(uploader) LIKE casefold(?)" "%name%"`. Compare names and titles with `casefold(...) LIKE casefold(?)`: plain `LIKE` ignores the case of A-Z only, so `LIKE '%élection%'` would miss "Élection". It prints at most 100 rows (`--limit N` for more). Never open `cache/test.db` with another tool to change it, and never delete it.

## Tables

| Table | One row per | Main columns |
|---|---|---|
| `contents` | video or audio | `id`, `platform` (`Youtube`, `TwitchVod`, ...), `external_id`, `url`, `title`, `duration_s`, `uploader`, `channel_id`, `series`, `upload_date` (UTC, `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS+00:00`) |
| `files` | file of a content | `content_id`, `file_type`, `variant`, `path`. File types: `ytdlp_info`, `ytdlp_audio` (mp3), `16k_mono_wav`, `whisper_result` (transcription JSON), `pyannote_result` (diarization JSON, `variant` = diarization id), `ytdlp_subtitles` |
| `diarizations` | diarization run of a content | `id`, `content_id`, `model`, `params` (JSON, `{}` by default), `created_at` |
| `turns` | speaking turn | `diarization_id`, `speaker` (`SPEAKER_00`, ...: only meaningful inside one diarization), `start_ms`, `end_ms`, `exclusive` (1 = at most one speaker at a time; 0 = regular, turns can overlap), `overlap_ms` |
| `turn_embeddings` | voice embedding of a turn | `turn_id`, `model`, `embedding` |

Rules that avoid wrong answers:
- A content is **downloaded** when it has a `16k_mono_wav` file, **transcribed** when it has a `whisper_result` file, **diarized** when it has a `diarizations` row.
- For speaking time and speaker counts, use the turns with `exclusive = 1` (regular turns overlap and would count overlapped speech twice).
- Upload dates are text: compare their first 10 characters, `substr(upload_date, 1, 10) BETWEEN '2026-03-01' AND '2026-03-31'`. `upload_date` can be NULL (unknown).
- `uploader` is the channel name as the platform shows it; `channel_id` is the stable channel key (YouTube `UC...`), NULL for old contents.

## Find the channel first

The database stores a channel's display name (`uploader`, e.g. `Sniff Demo`) and its id (`channel_id`), not the `@handle` the user may give. List the channels, pick the one meant, and use its exact `uploader` (or `channel_id`) in the queries below:

```bash
python .claude/skills/query-database/query.py "SELECT uploader, channel_id, platform, COUNT(*) AS contents FROM contents GROUP BY 1, 2, 3 ORDER BY contents DESC"
```

An empty result can just mean the name did not match: never answer "there is none" before checking the name with this list.

## Ready-made queries

Contents of a channel in a date range, with what is done:

```sql
SELECT c.id, substr(c.upload_date, 1, 10) AS uploaded, c.title,
  EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = '16k_mono_wav') AS downloaded,
  EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = 'whisper_result') AS transcribed,
  EXISTS (SELECT 1 FROM diarizations d WHERE d.content_id = c.id) AS diarized
FROM contents c
WHERE casefold(c.uploader) LIKE casefold(?) AND substr(c.upload_date, 1, 10) BETWEEN ? AND ?
ORDER BY c.upload_date
```
(values: `"%name%" 2026-03-01 2026-03-31`)

Speakers of a content (its latest diarization) and how long each speaks:

```sql
SELECT t.speaker, COUNT(*) AS turns, ROUND(SUM(t.end_ms - t.start_ms) / 60000.0, 1) AS minutes
FROM turns t
WHERE t.exclusive = 1 AND t.diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?)
GROUP BY t.speaker ORDER BY minutes DESC
```

The turns of one speaker, in seconds:

```sql
SELECT start_ms / 1000.0 AS start_s, end_ms / 1000.0 AS end_s, overlap_ms
FROM turns WHERE exclusive = 1 AND speaker = ?
AND diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?)
ORDER BY start_ms
```

Per channel, how many contents are downloaded, transcribed and diarized:

```sql
SELECT c.uploader, COUNT(*) AS contents,
  SUM(EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = '16k_mono_wav')) AS downloaded,
  SUM(EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = 'whisper_result')) AS transcribed,
  SUM(EXISTS (SELECT 1 FROM diarizations d WHERE d.content_id = c.id)) AS diarized
FROM contents c GROUP BY c.uploader ORDER BY contents DESC
```

The text of a transcription is in the JSON file of its `whisper_result` row (`segments[].text`, times in seconds), not in the database.

When you report numbers to the user, say which query they come from, and give durations in minutes or hours.
