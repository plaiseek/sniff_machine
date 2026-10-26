"""The schema of the database, as the migrations that helpers/database.py's connect() applies."""

# The schema, as a list of migrations applied in order by connect(): PRAGMA user_version is the
# number of migrations already applied to a database. Never edit a migration that may have been
# applied somewhere: append a new one. A migration that rebuilds a table must recreate its indexes
# and triggers (views that use it keep working).
MIGRATIONS = [
    # v1: the original schema (does nothing on databases created before migrations existed)
    [
        """CREATE TABLE IF NOT EXISTS contents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    duration_s INTEGER NOT NULL,
    uploader TEXT NOT NULL,
    upload_date TEXT NOT NULL,
    UNIQUE (platform, external_id),
    UNIQUE (url)
)""",
        """CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL,
    file_type TEXT NOT NULL CHECK(file_type IN ('ytdlp_info', 'ytdlp_audio', 'ytdlp_subtitles', '16k_mono_wav', 'whisper_result', 'pyannote_result')),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    UNIQUE (content_id, file_type)
)""",
    ],
    # v2: contents where only the identity is mandatory (extractors often leave out uploader,
    # duration or dates: '' and 0 placeholders become NULL), plus channel and series; files without
    # the CHECK on file_type (a new feature only needs a new file type) and with a variant (e.g. a
    # subtitles language) and a creation time. SQLite cannot drop a constraint: both are rebuilt.
    [
        "CREATE TEMP TABLE contents_sequence AS SELECT seq FROM sqlite_sequence WHERE name = 'contents'",
        """CREATE TABLE contents_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,                    -- yt-dlp extractor_key
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    duration_s REAL,
    uploader TEXT,
    channel_id TEXT,                           -- yt-dlp channel_id (or uploader_id): stable channel key
    series TEXT,                               -- programme name, when the extractor gives one
    upload_date TEXT,                          -- ISO 8601 date or datetime
    added_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (platform, external_id),
    UNIQUE (url)
)""",
        """INSERT INTO contents_new (id, platform, external_id, url, title, duration_s, uploader, upload_date, added_at)
SELECT id, platform, external_id, url, NULLIF(title, ''), NULLIF(duration_s, 0), NULLIF(uploader, ''), NULLIF(upload_date, ''), NULL
FROM contents""",
        "DROP TABLE contents",
        "ALTER TABLE contents_new RENAME TO contents",
        # Keep AUTOINCREMENT's high-water mark: ids of deleted contents are never reused
        """UPDATE sqlite_sequence SET seq = MAX(seq, COALESCE((SELECT seq FROM temp.contents_sequence), 0))
WHERE name = 'contents'""",
        "DROP TABLE temp.contents_sequence",
        """CREATE TABLE files_new (
    path TEXT PRIMARY KEY,
    content_id INTEGER NOT NULL,
    file_type TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT '',          -- '' unless several files of one type exist (e.g. 'fr')
    created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY(content_id) REFERENCES contents(id) ON DELETE CASCADE,
    UNIQUE (content_id, file_type, variant)
)""",
        # Rows left behind by a content deleted without foreign keys (e.g. from datasette) are dropped
        """INSERT INTO files_new (path, content_id, file_type, created_at)
SELECT path, content_id, file_type, NULL FROM files WHERE content_id IN (SELECT id FROM contents)""",
        "DROP TABLE files",
        "ALTER TABLE files_new RENAME TO files",
    ],
    # v3: diarization runs, their speaker turns, and one voice embedding per turn and model
    [
        """CREATE TABLE diarizations (                -- one row per run: provenance, and 'done' even with 0 turns
    id INTEGER PRIMARY KEY AUTOINCREMENT,      -- never reused: ids of deleted runs cannot point to other contents
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                       -- e.g. 'pyannote/speaker-diarization-community-1'
    params TEXT NOT NULL DEFAULT '{}',         -- json.dumps(params, sort_keys=True)
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (content_id, model, params)
)""",
        """CREATE TABLE turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,                     -- e.g. 'SPEAKER_00': only meaningful inside its diarization
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    exclusive INTEGER NOT NULL CHECK (exclusive IN (0, 1)),  -- 1 = from the exclusive diarization
    overlap_ms INTEGER NOT NULL DEFAULT 0      -- overlapped speech inside this turn
)""",
        "CREATE INDEX turns_by_diarization ON turns(diarization_id, exclusive, start_ms)",
        """CREATE TABLE turn_embeddings (
    turn_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    model TEXT NOT NULL,                       -- e.g. 'speechbrain/spkrec-ecapa-voxceleb'
    embedding BLOB,                            -- L2-normalised float32 (sqlite_vec.serialize_float32), NULL = too short
    PRIMARY KEY (turn_id, model)
)""",
    ],
    # v4: named speakers, the reference ranges of their voice (enrollment, kept across
    # re-diarizations and model changes), and who each diarization label is. Deleting a speaker
    # deletes everything derived from their voice (cascades).
    [
        """CREATE TABLE speakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)""",
        """CREATE TABLE speaker_samples (               -- a range of a content where only this speaker talks
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id INTEGER NOT NULL REFERENCES speakers(id) ON DELETE CASCADE,
    content_id INTEGER NOT NULL REFERENCES contents(id) ON DELETE CASCADE,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL CHECK (end_ms > start_ms),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (speaker_id, content_id, start_ms, end_ms)
)""",
        """CREATE TABLE sample_embeddings (             -- like turn_embeddings: one per sample and model
    sample_id INTEGER NOT NULL REFERENCES speaker_samples(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    embedding BLOB,                            -- L2-normalised float32, NULL = too short
    PRIMARY KEY (sample_id, model)
)""",
        """CREATE TABLE speaker_labels (                -- who a label of a diarization is
    diarization_id INTEGER NOT NULL REFERENCES diarizations(id) ON DELETE CASCADE,
    speaker TEXT NOT NULL,                     -- the diarization label, e.g. 'SPEAKER_01'
    speaker_id INTEGER REFERENCES speakers(id) ON DELETE CASCADE,  -- NULL = nobody known
    source TEXT NOT NULL CHECK (source IN ('human', 'auto')),      -- 'auto' never replaces 'human'
    model TEXT,                                -- 'auto': the embedding model compared
    similarity REAL,                           -- 'auto': cosine similarity to the best speaker
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (diarization_id, speaker)
)""",
        "CREATE INDEX speaker_samples_by_content ON speaker_samples(content_id)",
        "CREATE INDEX speaker_labels_by_speaker ON speaker_labels(speaker_id)",
    ],
    # v5: the words of each content's transcription, and the speaker turn each one falls in
    [
        """CREATE TABLE transcriptions (               -- the words of a whisper_result file were stored: 'done' even with 0 words
    content_id INTEGER PRIMARY KEY REFERENCES contents(id) ON DELETE CASCADE,
    path TEXT NOT NULL,                        -- the whisper_result file they come from
    language TEXT,                             -- as Whisper reports it, e.g. 'french'
    diarization_id INTEGER REFERENCES diarizations(id) ON DELETE SET NULL,  -- words.turn_id are its turns; NULL = none
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)""",
        """CREATE TABLE words (
    content_id INTEGER NOT NULL REFERENCES transcriptions(content_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,                      -- position in the transcription, from 0
    text TEXT NOT NULL,                        -- as Whisper wrote it: 'l''histoire', 'compte.', '?'
    norm TEXT NOT NULL,                        -- casefolded, without the punctuation around it: 'compte'; '' for '?'
    start_ms INTEGER NOT NULL,                 -- DTW time of its first token
    end_ms INTEGER NOT NULL,                   -- DTW time of its last token: often = start_ms, sometimes before it
    prob REAL,                                 -- product of its token probabilities
    turn_id INTEGER REFERENCES turns(id) ON DELETE SET NULL,  -- the exclusive turn its middle falls in, NULL = none
    PRIMARY KEY (content_id, idx)
) WITHOUT ROWID""",
        "CREATE INDEX words_by_norm ON words(norm)",
        "CREATE INDEX words_by_turn ON words(turn_id)",  # also makes deleting a diarization fast (SET NULL)
    ],
    # v6: the sentences of the transcriptions, never across two speakers, with a full-text index
    [
        """CREATE TABLE sentences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id INTEGER NOT NULL REFERENCES transcriptions(content_id) ON DELETE CASCADE,
    first_idx INTEGER NOT NULL,                -- its words: words.idx from first_idx to last_idx
    last_idx INTEGER NOT NULL CHECK (last_idx >= first_idx),
    start_ms INTEGER NOT NULL,                 -- the earliest time of its words
    end_ms INTEGER NOT NULL,                   -- the latest
    diarization_id INTEGER REFERENCES diarizations(id) ON DELETE CASCADE,  -- = transcriptions.diarization_id
    speaker TEXT,                              -- the label of its words' turns in that diarization, NULL = unknown
    text TEXT NOT NULL,                        -- its words joined with spaces
    splitter TEXT NOT NULL,                    -- the wtpsplit model (e.g. 'sat-12l-sm'), or 'punctuation'
    UNIQUE (content_id, first_idx)
)""",
        # Search: sentences_fts MATCH 'pete*' finds péter, pété, Pète... (case and accents ignored)
        """CREATE VIRTUAL TABLE sentences_fts USING fts5(
    text, content='sentences', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
)""",
        """CREATE TRIGGER sentences_fts_insert AFTER INSERT ON sentences BEGIN
    INSERT INTO sentences_fts (rowid, text) VALUES (new.id, new.text);
END""",
        """CREATE TRIGGER sentences_fts_delete AFTER DELETE ON sentences BEGIN
    INSERT INTO sentences_fts (sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
END""",
        """CREATE TRIGGER sentences_fts_update AFTER UPDATE ON sentences BEGIN
    INSERT INTO sentences_fts (sentences_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO sentences_fts (rowid, text) VALUES (new.id, new.text);
END""",
        "CREATE INDEX sentences_by_diarization ON sentences(diarization_id)",
    ],
    # v7: questions asked to an LLM about the transcriptions (explore/ask_transcripts.py), and the passages
    # it found: leads to listen to, not verdicts
    [
        """CREATE TABLE questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,                        -- the question, with its criteria
    speaker TEXT NOT NULL DEFAULT '',          -- only the sentences of the labels named so; '' = anyone
    model TEXT NOT NULL,                       -- as the vLLM server names it
    settings TEXT NOT NULL,                    -- JSON (params_key()): prompt version, window sizes, thinking
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (text, speaker, model, settings)
)""",
        """CREATE TABLE question_contents (            -- the question was asked on the whole transcription of a content
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    content_id INTEGER NOT NULL REFERENCES transcriptions(content_id) ON DELETE CASCADE,
    diarization_id INTEGER REFERENCES diarizations(id) ON DELETE SET NULL,  -- the speakers it was shown; NULL = none
    windows INTEGER NOT NULL,                  -- transcript windows read in the first pass
    candidates INTEGER NOT NULL,               -- passages checked in the second pass
    prompt_tokens INTEGER NOT NULL,            -- tokens read and written by the LLM, both passes
    completion_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (question_id, content_id)
)""",
        """CREATE TABLE findings (                      -- a passage the LLM checked, whatever its verdict
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    content_id INTEGER NOT NULL,
    first_idx INTEGER NOT NULL,                -- its words: words.idx from first_idx to last_idx
    last_idx INTEGER NOT NULL CHECK (last_idx >= first_idx),
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    speakers TEXT,                             -- the labels of its sentences when asked, e.g. 'SPEAKER_01'; NULL = unknown
    verdict TEXT NOT NULL CHECK (verdict IN ('yes', 'unsure', 'no')),
    confidence REAL,                           -- the LLM's, from 0 to 1
    attribution TEXT,                          -- 'own words', 'quoting, reading or reporting' or 'unclear'
    quote TEXT,                                -- the words the LLM says match
    quote_found INTEGER NOT NULL,              -- 1: the quote is in the transcription of the passage
    explanation TEXT,                          -- why, from the second pass
    screening TEXT,                            -- why the first pass picked it
    FOREIGN KEY (question_id, content_id) REFERENCES question_contents(question_id, content_id) ON DELETE CASCADE,
    UNIQUE (question_id, content_id, first_idx)
)""",
        "CREATE INDEX question_contents_by_content ON question_contents(content_id)",
        "CREATE INDEX question_contents_by_diarization ON question_contents(diarization_id)",
    ],
]


# The columns migration v2 copies when it rebuilds contents and files
V1_COLUMNS = {
    "contents": ["id", "platform", "external_id", "url", "title", "duration_s", "uploader", "upload_date"],
    "files": ["path", "content_id", "file_type"],
}
