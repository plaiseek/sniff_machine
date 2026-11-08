"""Download the full videos of contents already in the database, to the video disk.

    python3.11 pipeline/download_videos.py                       # every content
    python3.11 pipeline/download_videos.py 167 243               # only these contents
    python3.11 pipeline/download_videos.py --uploader Psyhodelik --from 2026-03-01 --to 2026-03-31
    python3.11 pipeline/download_videos.py --dry-run ...         # only count what would be downloaded
    python3.11 pipeline/download_videos.py --newest-first ...    # the latest uploads first
    python3.11 pipeline/download_videos.py --skip-category Gaming --tag macron ...   # by YouTube category, tag
    python3.11 pipeline/download_videos.py --max-rate 1.5 ...    # at most 1.5 MB/s (default 2, 0: no limit)
    python3.11 pipeline/download_videos.py --cookies youtube_cookies.txt ...   # signed in to YouTube

The videos go to cache/ytdlp_video, a link to a folder on the video disk (see README.md), at most
720p (helpers/yt_dlp.py VIDEO_FORMAT_SORT), oldest upload first (--newest-first: the latest first), at most 2 MB/s (VIDEO_MAX_BYTES_PER_S:
YouTube's bot check refuses this machine after an hour at full speed). --cookies: the cookies of a
YouTube account, exported from a private window (see the pipeline-job skill), git-ignored: YouTube
may ban an account that downloads this much, use a throwaway one. A content whose video is there is
skipped, so running it again resumes. Nothing else is downloaded or computed: jobs with "video" in
STEPS download the videos of the contents they select. No server needed.

Exit codes: 0 done, 1 some videos failed (run it again to retry them), 2 no video or data disk, 6 the
downloads from a platform were stopped (it refuses this machine for now: run it again later).
"""

import argparse
import datetime
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db
import helpers.yt_dlp as ytdlp
from helpers.pipeline import run_steps
from helpers.yt_dlp import VIDEO_DIR, video_dir_problem


def date(text: str) -> str:
    return datetime.date.fromisoformat(text).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("content_ids", type=int, nargs="*", help="contents to download (default: all)")
    parser.add_argument("--uploader", help="only the contents of this channel (part of its name)")
    parser.add_argument("--from", dest="uploaded_from", type=date, help="only those uploaded from this date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="uploaded_to", type=date, help="only those uploaded until this date (YYYY-MM-DD)")
    parser.add_argument("--category", action="append", default=[], metavar="NAME", help='only the videos of this YouTube category, e.g. "Entertainment" (repeatable: any of them)')
    parser.add_argument("--skip-category", action="append", default=[], metavar="NAME", help='leave out the videos of this YouTube category, e.g. "Gaming" (repeatable)')
    parser.add_argument("--tag", action="append", default=[], metavar="TAG", help="only the videos with this tag (repeatable: any of them)")
    parser.add_argument("--dry-run", action="store_true", help="only count the videos to download")
    parser.add_argument("--newest-first", action="store_true", help="the latest uploads first (default: the oldest first)")
    parser.add_argument(
        "--max-rate", type=float, metavar="MB_PER_S", default=ytdlp.VIDEO_MAX_BYTES_PER_S / 1e6,
        help=f"download speed limit in MB/s (default {ytdlp.VIDEO_MAX_BYTES_PER_S / 1e6:g}, 0: no limit)",
    )
    parser.add_argument("--cookies", type=Path, metavar="FILE", help="the cookies of a YouTube account (Netscape format)")
    args = parser.parse_args()
    if args.max_rate < 0:
        parser.error("--max-rate cannot be negative")
    ytdlp.VIDEO_MAX_BYTES_PER_S = int(args.max_rate * 1e6) or None
    if args.cookies:
        try:
            first_line = args.cookies.read_text(encoding="utf-8").partition("\n")[0]
        except (OSError, UnicodeDecodeError) as e:
            parser.error(f"cannot read the cookies: {e}")
        if first_line.strip() not in ("# Netscape HTTP Cookie File", "# HTTP Cookie File"):
            parser.error(f"{args.cookies} is not a cookies.txt file (its first line is not '# Netscape HTTP Cookie File')")
        ytdlp.COOKIE_FILE = args.cookies

    if problems := db.unmounted_cache_links() or [p for p in [video_dir_problem()] if p is not None]:
        print(f"Nothing downloaded: {'; '.join(problems)}")
        return 2
    with db.connect() as db_conn:
        selected = db.find_contents(
            db_conn,
            ids=args.content_ids or None,
            uploader=args.uploader,
            uploaded_from=args.uploaded_from,
            uploaded_to=args.uploaded_to,
        )
        if missing := sorted(set(args.content_ids) - {content.id for content in selected}):
            print(f"Contents {missing} are not in the database, or not selected by the other options: left out")
        if args.category or args.skip_category or args.tag:
            matching, unknown = ytdlp.select_by_details(db_conn, selected, args.category, args.skip_category, args.tag)
            print(
                f"{len(matching)} of {len(selected)} contents have the categories and tags asked for"
                + (f" ({unknown} have no details on disk: no category, no tag)" if unknown else "")
            )
            selected = matching
        contents = [c for c in selected if db.get_existing_content_file(db_conn, c.id, "ytdlp_video") is None]
    # Oldest first, as in the jobs: the sources that expire first (e.g. Twitch VODs) come first
    # --newest-first: when the disk cannot hold them all, the latest uploads are those kept
    contents.sort(key=lambda c: (c.upload_date or "", c.id), reverse=args.newest_first)
    contents.sort(key=lambda c: c.upload_date is None)  # stable: those without a date last
    hours = sum(c.duration_s or 0 for c in contents) / 3600
    free = shutil.disk_usage(VIDEO_DIR).free / 1e9
    print(
        f"{len(contents)} videos to download ({hours:.0f} h), {len(selected) - len(contents)} already there;"
        f" {free:.0f} GB free on the disk of {VIDEO_DIR}."
        + (f" At most {args.max_rate:g} MB/s ({args.max_rate * 3.6:.1f} GB per hour)." if args.max_rate else "")
        + (f" Signed in with {args.cookies}." if args.cookies else "")
    )
    if args.dry_run or not contents:
        return 0
    return run_steps(contents, [], video=True).exit_code()


if __name__ == "__main__":
    code = main()
    # Run in the background, its log would not show the exit code otherwise
    print(f"Finished with exit code {code}.")
    sys.exit(code)
