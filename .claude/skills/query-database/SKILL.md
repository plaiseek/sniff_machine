---
name: query-database
description: Answers questions about what is stored in this repository's SQLite database (cache/test.db) - the downloaded videos and audios of a channel, their upload dates and durations, which ones are transcribed, diarized or split into sentences, the speakers and speaking turns of a video, who the speakers are once named, what was said and by whom, how often a word is said, what an LLM found when asked where something is said - with read-only SQL queries. Use for questions like "which videos of X are not diarized yet?", "how many speakers in video N?", "how long does SPEAKER_01 speak?", "how long does <name> speak in each video?", "who said 'mairie de Paris'?", "how many times does <name> say 'putain'?" (in French quelles vidéos, combien d'intervenants, temps de parole, qui a dit, combien de fois).
---

# Query the database

Always use the read-only query script (it cannot change the database):

```bash
python3.11 .claude/skills/query-database/query.py "SELECT ..." [value for each ?]
python3.11 .claude/skills/query-database/query.py --schema
```

Put values in `?` placeholders instead of writing them in the SQL text: `query.py "SELECT id, title FROM contents WHERE casefold(uploader) LIKE casefold(?)" "%name%"`. Compare names and titles with `casefold(...) LIKE casefold(?)`: plain `LIKE` ignores the case of A-Z only, so `LIKE '%élection%'` would miss "Élection". `text REGEXP pattern` is Python's `re.search`. It prints at most 100 rows (`--limit N` for more) and cuts values longer than 80 characters: `--full` prints every value whole, one block per row (`--- row N`, then `column: value` lines). Use `--full` too for values with line breaks (`questions.text`, `findings.explanation`). The options (`--limit N`, `--full`, `--schema`) may come anywhere before a `--` argument, and a value may start with `-` (YouTube ids like `-zehjD6YcdA`); a value that starts with `--` goes after a `--` argument. A value that looks like a number (`243`) is passed as a number. Never open `cache/test.db` with another tool to change it, and never delete it.

## Tables

| Table | One row per | Main columns |
|---|---|---|
| `contents` | video or audio | `id`, `platform` (`Youtube`, `TwitchVod`, ...), `external_id`, `url`, `title`, `duration_s`, `uploader`, `channel_id`, `series`, `upload_date` (UTC, `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM:SS+00:00`) |
| `files` | file of a content | `content_id`, `file_type`, `variant`, `path`. File types: `ytdlp_info`, `ytdlp_audio` (mp3), `16k_mono_wav`, `whisper_result` (transcription JSON), `pyannote_result` (diarization JSON, `variant` = diarization id), `ytdlp_subtitles` |
| `diarizations` | diarization run of a content | `id`, `content_id`, `model`, `params` (JSON, `{}` by default), `created_at` |
| `turns` | speaking turn | `diarization_id`, `speaker` (`SPEAKER_00`, ...: only meaningful inside one diarization), `start_ms`, `end_ms`, `exclusive` (1 = at most one speaker at a time; 0 = regular, turns can overlap), `overlap_ms` (how much of the turn others speak over, in the regular diarization) |
| `turn_embeddings` | voice embedding of an exclusive turn | `turn_id`, `model`, `embedding` (192 float32, NULL = turn too short to embed) |
| `speakers` | named person | `id`, `name`, `notes` |
| `speaker_samples` | reference range of a speaker's voice | `speaker_id`, `content_id`, `start_ms`, `end_ms` (embeddings in `sample_embeddings`: `sample_id`, `model`, `embedding`) |
| `speaker_labels` | who a diarization label is | `diarization_id`, `speaker` (the label), `speaker_id` (NULL = nobody known), `source` (`human`: set by a person; `auto`: by `speakers/identify_speakers.py`), `similarity` (`auto` only: cosine, -1 to 1; named from `--threshold`, default 0.6; also stored, the closest speaker's, when `speaker_id` is NULL) |
| `transcriptions` | content whose words are stored | `content_id`, `path` (its `whisper_result` file), `language`, `diarization_id` (the diarization the words' turns come from: the content's latest; NULL = not diarized) |
| `words` | word said, in order | `content_id`, `idx` (0, 1, ...), `text` (as written: `l'histoire`, `compte.`, `?`), `norm` (lower case, no punctuation around: `compte`; `''` for `?`), `start_ms`, `end_ms`, `prob`, `turn_id` (the exclusive turn it falls in, NULL = none) |
| `sentences` | sentence, never across two speakers | `id`, `content_id`, `first_idx`, `last_idx` (its words), `start_ms`, `end_ms`, `diarization_id`, `speaker` (the label), `text`, `splitter` (`sat-12l-sm`: wtpsplit; `punctuation`) |
| `sentences_fts` | full-text index of `sentences.text` | search with `sentences_fts MATCH ?`, join `sentences s ON s.id = sentences_fts.rowid` |
| `questions` | question asked to an LLM (`explore/ask_transcripts.py`) | `id`, `text` (with its criteria), `speaker` (only the labels named so could match; `''` = anyone), `model`, `settings` (JSON) |
| `question_contents` | content a question was asked on | `question_id`, `content_id`, `diarization_id` (the speakers it was shown), `windows`, `candidates`, `prompt_tokens`, `completion_tokens` |
| `findings` | passage the LLM checked | `question_id`, `content_id`, `first_idx`, `last_idx` (its words), `start_ms`, `end_ms`, `speakers` (labels, comma-separated), `verdict` (`yes`, `unsure`, `no`), `confidence` (0 to 1), `attribution` (`own words`, `approves what they quote or read`, `quoting, reading or reporting` (no stance), `quotes to criticize`, `unclear`, or NULL: this is the full list, the `--schema` comment is stale), `quote`, `quote_found` (1: most of the quote's words are in the passage; 0: not, or no quote), `explanation` |

Rules that avoid wrong answers:
- A content is **downloaded** when it has a `16k_mono_wav` file, **transcribed** when it has a `whisper_result` file, **diarized** when it has a `diarizations` row, **split into sentences** when it has `sentences` rows whose `diarization_id` IS its latest diarization (NULL when not diarized; aligning the words to a new diarization deletes the sentences); `splitter = 'punctuation'` is split, but not by wtpsplit (a job's `"split"` splits it again). A transcription with no words has nothing to split: count it as done.
- For speaking time and speaker counts, use the turns with `exclusive = 1` (regular turns overlap and would count overlapped speech twice).
- A label is **named** only through `speaker_labels` with a `speaker_id`. Say how: "set by a person" (`source = 'human'`) or "identified automatically, similarity 0.92" (`auto`: a guess from voice similarity, never present it as certain). A label with no `speaker_labels` row was never identified; an `auto` row with `speaker_id` NULL means nobody enrolled matched. Never name a speaker from a video's title or channel yourself.
- **What was said**: the words are in `words` (and `sentences`) once stored; a content is transcribed but its words are not stored when it has a `whisper_result` file and no `transcriptions` row (then say so: `python3.11 pipeline/store_words.py` stores them). A transcription with no words (`transcriptions` row, no `words`) means Whisper heard no speech: mostly music clips, though Pyannote may find a few seconds of speech in them (say it needs listening).
- **Who said it**: a word's speaker is the `speaker` of its turn (`turns.id = words.turn_id`), in the content's latest diarization; a sentence has its `speaker` and `diarization_id`. Name it through `speaker_labels` as above. About 5 % of the words fall between turns (`turn_id` NULL): a join on `turns` leaves them out. A sentence gives every word a speaker: a word in no turn goes to the speaker before it, and the words before the first turn to the first speaker (`helpers/transcripts.py` `speaker_runs`), as `explore/show_transcript.py` shows it. Say which way you counted.
- **Counting a word**: match `norm`, not `text`, and count rows. `norm` keeps accents and French elisions: use `norm REGEXP '(^|'')mots?$'` in the SQL text, or `norm REGEXP ?` with the value `"(^|')mots?$"` (`l'histoire` holds `histoire`), or `norm IN ('pipi', 'pipis')`. Word times are Whisper's token times: use `start_ms` (`end_ms` is often equal to it, sometimes before it).
- **Searching a phrase**: `sentences_fts MATCH ?` ignores case and accents; values like `"mairie de paris"` (a phrase, with the double quotes), `pete*` (a prefix: péter, pété...), `knafo AND mairie`, `NEAR(knafo mairie, 5)`. It matches whole words or word starts only (`monétis*` misses démonétisation): for a part inside a word use `words.norm REGEXP ?` (`norm` keeps accents: `mon[ée]tis`) and join `sentences s ON s.content_id = w.content_id AND w.idx BETWEEN s.first_idx AND s.last_idx` (`GROUP BY s.id`). `explore/search_transcripts.py "query" --content N` searches one content. Sentences exist only for the contents split with `pipeline/split_sentences.py` (or a job with `"split"`); `sentences.splitter` says how.
- **Where something is said that no fixed words find** ("when does he say something hateful about a group", "where does she criticise her party"): SQL cannot answer it; follow the `ask-transcripts` skill (an LLM reads the transcriptions). With SQL, the `findings` of a question it asked can be counted or filtered (the `explanation` may end with a `Meets:` line and/or an `Against:` line, each only when the LLM gave one). Its findings are leads, like `auto` names: give the verdict and confidence, the link (`--show` prints them; a YouTube link is `https://www.youtube.com/watch?v=<external_id>&t=<start_ms / 1000>s`), and say they need to be listened to; never present them as facts about a person.
- Upload dates are text: compare their first 10 characters, `substr(upload_date, 1, 10) BETWEEN '2026-03-01' AND '2026-03-31'`. `upload_date` can be NULL (unknown).
- `uploader` is the channel name as the platform shows it; `channel_id` is the stable channel key (YouTube `UC...`), but it can be NULL for any content (all of them today): use `uploader`.

## Find the channel first

The database stores a channel's display name (`uploader`, e.g. `Psyhodelik`), not the `@handle` the user may give. List the channels, pick the one meant, and use its exact `uploader` in the queries below:

```bash
python3.11 .claude/skills/query-database/query.py "SELECT uploader, channel_id, platform, COUNT(*) AS contents FROM contents GROUP BY 1, 2, 3 ORDER BY contents DESC"
```

An empty result can just mean the name did not match: never answer "there is none" before checking the name with this list.

A content from a URL, a video id or words of its title (one value):

```bash
python3.11 .claude/skills/query-database/query.py "SELECT id, platform, external_id, substr(upload_date, 1, 10) AS uploaded, uploader, title FROM contents WHERE instr(?1, ltrim(external_id, 'v')) OR ?1 = url OR instr(casefold(title), casefold(?1))" "https://www.youtube.com/watch?v=-zehjD6YcdA"
```

The id match works for YouTube and Twitch URLs; for other platforms, give the exact `url` or words of the title.

A title can be in another language than the request (`WOMEN'S DAY` for "journée de la femme"): if nothing matches, search what is said with the `sentences_fts` query below.

## Ready-made queries

Contents of a channel in a date range, with what is done:

```sql
SELECT c.id, substr(c.upload_date, 1, 10) AS uploaded, c.title,
  EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = '16k_mono_wav') AS downloaded,
  EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = 'whisper_result') AS transcribed,
  EXISTS (SELECT 1 FROM diarizations d WHERE d.content_id = c.id) AS diarized,
  (SELECT (SELECT COUNT(*) FROM words w WHERE w.content_id = c.id) FROM transcriptions t WHERE t.content_id = c.id) AS words,
  EXISTS (SELECT 1 FROM sentences s WHERE s.content_id = c.id
    AND s.diarization_id IS (SELECT MAX(id) FROM diarizations d WHERE d.content_id = c.id)) AS split
FROM contents c
WHERE casefold(c.uploader) LIKE casefold(?) AND substr(c.upload_date, 1, 10) BETWEEN ? AND ?
ORDER BY c.upload_date
```
(values: `"%name%" 2026-03-01 2026-03-31`; `words` NULL = not transcribed or words not stored yet, 0 = no speech heard)

Speakers of a content (its latest diarization) and how long each speaks:

```sql
SELECT t.speaker, COUNT(*) AS turns, ROUND(SUM(t.end_ms - t.start_ms) / 60000.0, 1) AS minutes
FROM turns t
WHERE t.exclusive = 1 AND t.diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?)
GROUP BY t.speaker ORDER BY minutes DESC
```

The same, with who each label is (latest diarization):

```sql
SELECT t.speaker, s.name, l.source, ROUND(l.similarity, 2) AS similarity,
  ROUND(SUM(t.end_ms - t.start_ms) / 60000.0, 1) AS minutes
FROM turns t
LEFT JOIN speaker_labels l ON l.diarization_id = t.diarization_id AND l.speaker = t.speaker
LEFT JOIN speakers s ON s.id = l.speaker_id
WHERE t.exclusive = 1 AND t.diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?)
GROUP BY t.speaker ORDER BY minutes DESC
```

How long a named speaker speaks in each content (labels named by a person or automatically, latest diarization):

```sql
SELECT d.content_id, c.title, t.speaker, l.source, ROUND(SUM(t.end_ms - t.start_ms) / 60000.0, 1) AS minutes
FROM speaker_labels l JOIN speakers s ON s.id = l.speaker_id
JOIN diarizations d ON d.id = l.diarization_id JOIN contents c ON c.id = d.content_id
JOIN turns t ON t.diarization_id = l.diarization_id AND t.speaker = l.speaker AND t.exclusive = 1
WHERE s.name = ? AND d.id = (SELECT MAX(id) FROM diarizations WHERE content_id = d.content_id)
GROUP BY d.id, t.speaker ORDER BY c.upload_date
```

The turns of one speaker, in seconds:

```sql
SELECT start_ms / 1000.0 AS start_s, end_ms / 1000.0 AS end_s, overlap_ms
FROM turns WHERE exclusive = 1 AND speaker = ?
AND diarization_id = (SELECT MAX(id) FROM diarizations WHERE content_id = ?)
ORDER BY start_ms
```

Per channel, how many contents are downloaded, transcribed, diarized and split into sentences:

```sql
SELECT c.uploader, COUNT(*) AS contents,
  SUM(EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = '16k_mono_wav')) AS downloaded,
  SUM(EXISTS (SELECT 1 FROM files f WHERE f.content_id = c.id AND f.file_type = 'whisper_result')) AS transcribed,
  SUM(EXISTS (SELECT 1 FROM diarizations d WHERE d.content_id = c.id)) AS diarized,
  SUM(EXISTS (SELECT 1 FROM sentences s WHERE s.content_id = c.id
    AND s.diarization_id IS (SELECT MAX(id) FROM diarizations d WHERE d.content_id = c.id))) AS split
FROM contents c GROUP BY c.uploader ORDER BY contents DESC
```
(`split` leaves out the transcriptions with no words, which have nothing to split: add `SUM(EXISTS (SELECT 1 FROM transcriptions t WHERE t.content_id = c.id) AND NOT EXISTS (SELECT 1 FROM words w WHERE w.content_id = c.id)) AS no_words` to count them)

Who said what in one content, as text (paragraphs per speaker, with times and links): run `python3.11 explore/show_transcript.py CONTENT_ID [--from 10:00 --to 20:00] [--speaker SPEAKER_01 or a name]` instead of a query. It first stores or aligns again the content's words when needed, a database write like `pipeline/store_words.py` (it then prints a `Words: N transcriptions stored, M aligned again, F failures.` line); aligning again deletes the content's sentences until it is split again. Search across contents with links: `python3.11 explore/search_transcripts.py "query" [--uploader X] [--content N] [--speaker NAME] [--limit N]` (the first 30 sentences unless `--limit`).

What an LLM found: list the questions asked, with their criteria file, speaker, model and counts, and the exact arguments to show one:

```bash
python3.11 .claude/skills/findings-dossier/questions.py        # every question
python3.11 .claude/skills/findings-dossier/questions.py 1      # question 1: its channels, dates and arguments
```

Then run `python3.11 explore/ask_transcripts.py <those arguments> [--uploader X] [--content N] [--uploaded-from D] [--uploaded-to D] --show [--all]`: the selection options combine (AND), `--content` repeats, no selection means every content, and only the findings of the selected contents are printed. `--show` does not ask the LLM nor write to the database. Or query `findings` with `WHERE question_id = ?`. To export them for a lawyer, follow the `findings-dossier` skill.

Who said a phrase (with the speaker's name when known):

```sql
SELECT s.content_id, c.title, s.start_ms / 1000 AS start_s, s.speaker, p.name, l.source, s.text
FROM sentences_fts JOIN sentences s ON s.id = sentences_fts.rowid JOIN contents c ON c.id = s.content_id
LEFT JOIN speaker_labels l ON l.diarization_id = s.diarization_id AND l.speaker = s.speaker
LEFT JOIN speakers p ON p.id = l.speaker_id
WHERE sentences_fts MATCH ? ORDER BY c.upload_date, s.start_ms
```
(value: `'"mairie de paris"'`)

How many times a word is said in each content of a channel:

```sql
SELECT c.id, substr(c.upload_date, 1, 10) AS uploaded, c.title, COUNT(*) AS times
FROM words w JOIN contents c ON c.id = w.content_id
WHERE casefold(c.uploader) LIKE casefold(?) AND w.norm IN (?, ?)
GROUP BY c.id ORDER BY c.upload_date
```
(values: `"%name%" pipi pipis`)

How many words each speaker says in a content, and how often they say one word (`norm = ''` rows are punctuation, not words):

```sql
SELECT t.speaker, p.name, SUM(w.norm <> '') AS words, SUM(w.norm REGEXP ?) AS times
FROM words w LEFT JOIN turns t ON t.id = w.turn_id
LEFT JOIN speaker_labels l ON l.diarization_id = t.diarization_id AND l.speaker = t.speaker
LEFT JOIN speakers p ON p.id = l.speaker_id
WHERE w.content_id = ? GROUP BY t.speaker ORDER BY words DESC
```
(values: `"^putains?$" 243`; the row with speaker NULL holds the words in no turn)

To give those words a speaker too, on a split content, count through its sentences: `FROM sentences s JOIN words w ON w.content_id = s.content_id AND w.idx BETWEEN s.first_idx AND s.last_idx`, with `s.speaker` and `s.diarization_id` in place of `t.speaker` and `t.diarization_id`, and `WHERE s.content_id = ?`.

How many times a named speaker says a word, per content (words in no turn left out):

```sql
SELECT w.content_id, c.title, l.source, COUNT(*) AS times
FROM words w JOIN turns t ON t.id = w.turn_id
JOIN speaker_labels l ON l.diarization_id = t.diarization_id AND l.speaker = t.speaker
JOIN speakers p ON p.id = l.speaker_id JOIN contents c ON c.id = w.content_id
WHERE p.name = ? AND w.norm REGEXP ? GROUP BY w.content_id ORDER BY c.upload_date
```

The `whisper_result` JSON holds one word per segment (`segments[].text`, `start`/`end` in seconds, before the correction `words.start_ms` carries): for readable text, use `explore/show_transcript.py`.

When you report numbers to the user, say which query they come from, and give durations in minutes or hours.
