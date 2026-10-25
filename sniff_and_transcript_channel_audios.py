from helpers.jobs import run_job

import sys

# Downloads and transcribes every video of these channels: a job without dates (see
# jobs/job_template.py for upload dates, diarization, single URLs). The Whisper servers are
# listed in servers.py. Re-running it resumes: what is already in the database is skipped.
channel_urls = ["https://www.youtube.com/@psyhodelik/videos"]
language = "fr"

sys.exit(
    run_job(__file__, channel_urls, [], None, None, ["download", "transcribe"], language)
)
