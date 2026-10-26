"""Example: download the subtitles that people wrote for some contents, when a request asks for them.

    python3.11 .claude/skills/write-pipeline-script/manual_subtitles.py --listing https://www.youtube.com/@name/videos
    python3.11 .claude/skills/write-pipeline-script/manual_subtitles.py --url URL --url URL --lang en
    python3.11 .claude/skills/write-pipeline-script/manual_subtitles.py --listing URL --uploaded-from 2026-03-01

Not part of the normal workflow. The Whisper large-v3 transcriptions are far better than automatic
subtitles, and mixing subtitles into them would bias what is counted and searched: only subtitles
written by people are fetched (never the platform's automatic ones), each stored as
cache/ytdlp_subtitles/<platform>_<id>.<lang>.srt and registered as a `ytdlp_subtitles` file. They
never become words or sentences in the database. Contents without such subtitles are listed, not
failed.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))
import helpers.database as db  # noqa: E402
from helpers.yt_dlp import get_ytdlp_content, get_ytdlp_content_srt, list_contents  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--listing", action="append", default=[], help="a channel tab, playlist or show URL (repeatable)")
    parser.add_argument("--url", action="append", default=[], help="a content URL (repeatable)")
    parser.add_argument("--lang", default="fr", help="the language of the subtitles (default fr)")
    parser.add_argument("--uploaded-from", help="only the listed contents uploaded from this day (YYYY-MM-DD)")
    parser.add_argument("--uploaded-to", help="only the listed contents uploaded until this day (YYYY-MM-DD)")
    args = parser.parse_args()
    if not args.listing and not args.url:
        parser.error("give --listing or --url")

    found, missing, failed = [], [], []
    with db.connect() as db_conn:
        contents = []
        for listing in args.listing:
            listed, skipped = list_contents(db_conn, listing, args.uploaded_from, args.uploaded_to)
            contents += listed
            failed += [(url, reason) for url, reason, is_error in skipped if is_error]
        for url in args.url:
            try:
                contents.append(get_ytdlp_content(db_conn, url))
            except Exception as e:
                failed.append((url, e))
        for content in contents:
            try:
                path = get_ytdlp_content_srt(content.url, db_conn, content, lang=args.lang, automatic_sub=False)
                found.append(path)
                print(f"{content.url}: {path}")
            except ValueError:  # no subtitles written by people in this language
                missing.append(content.url)
            except Exception as e:
                failed.append((content.url, e))

    print(f"{len(found)} subtitles, {len(missing)} contents without subtitles in '{args.lang}' written by people, {len(failed)} failures")
    for url, error in failed:
        print(f"  {url} : {error}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
