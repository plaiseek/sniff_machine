"""The full videos (helpers/yt_dlp.py, the "video" step of run_steps and of the jobs), and what
happens when a platform refuses this machine, on made-up contents in a database of its own and a
video folder of their own: nothing is downloaded.

    python3.11 -m unittest discover tests
"""

import contextlib
import io
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from yt_dlp.utils import DownloadError, ExtractorError

import helpers.database as db
import helpers.yt_dlp as ytdlp
from helpers.jobs import _check_settings
from helpers.pipeline import print_report, run_steps


def download_error(message: str, expected: bool) -> DownloadError:
    """A yt-dlp error as extract_info() raises it."""
    original = ExtractorError(message, expected=expected)
    return DownloadError(f"ERROR: [test] x: {message}", exc_info=(type(original), original, None))


# yt-dlp marks the rate limit as expected, as it does a removed video
RATE_LIMITED = download_error(
    "This content isn't available, try again later. The current session has been rate-limited by YouTube for up to an hour.", True
)
BOT_CHECK = download_error("Sign in to confirm you’re not a bot. This helps protect our community.", True)
REMOVED = download_error("Video unavailable. This video has been removed by the uploader", True)
FORBIDDEN = DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")


class VideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.saved = db.db_path, ytdlp.VIDEO_DIR
        db.db_path = self.root / "test.db"
        ytdlp.VIDEO_DIR = self.root / "ytdlp_video"
        with contextlib.redirect_stdout(io.StringIO()), db.connect() as db_conn:
            self.contents = [
                db.add_content(db_conn, "Test", f"x{i}", f"https://example.com/x{i}", "Test", 60.0, "Tester", "2026-01-01")
                for i in (1, 2, 3, 4)
            ]

    def tearDown(self):
        db.db_path, ytdlp.VIDEO_DIR = self.saved
        self.tmp.cleanup()

    def settings_errors(self, steps):
        return _check_settings([], ["https://example.com/x1"], None, None, steps, "fr", {})


class VideoDirTest(VideoTest):
    def test_a_folder_or_a_link_to_one_is_used(self):
        ytdlp.VIDEO_DIR.mkdir()
        self.assertIsNone(ytdlp.video_dir_problem())
        ytdlp.VIDEO_DIR.rmdir()
        (self.root / "disk").mkdir()
        os.symlink(self.root / "disk", ytdlp.VIDEO_DIR)
        self.assertIsNone(ytdlp.video_dir_problem())
        self.assertEqual(self.settings_errors(["video", "transcribe"]), [])

    def test_never_created_when_missing(self):
        self.assertIn("does not exist", ytdlp.video_dir_problem())
        self.assertFalse(ytdlp.VIDEO_DIR.exists())

    def test_a_link_to_a_disk_not_mounted_is_reported(self):
        os.symlink(self.root / "unmounted", ytdlp.VIDEO_DIR)
        self.assertIn("is its disk mounted?", ytdlp.video_dir_problem())

    def test_a_job_with_video_stops_before_anything_without_the_folder(self):
        [error] = self.settings_errors(["video", "transcribe"])
        self.assertIn('STEPS has "video"', error)
        self.assertEqual(self.settings_errors(["download", "transcribe"]), [])

    def test_no_download_starts_on_a_nearly_full_disk(self):
        ytdlp.VIDEO_DIR.mkdir()
        full = mock.Mock(free=ytdlp.VIDEO_MIN_FREE_BYTES - 1)
        with mock.patch("shutil.disk_usage", return_value=full), mock.patch("yt_dlp.YoutubeDL") as youtube_dl:
            with db.connect() as db_conn, self.assertRaisesRegex(RuntimeError, "GB left"):
                ytdlp.get_ytdlp_content_video(db_conn, self.contents[0].url, self.contents[0])
        youtube_dl.assert_not_called()


class DownloadFailureTest(unittest.TestCase):
    def test_a_block_is_told_apart_from_a_video_refused(self):
        self.assertEqual(ytdlp.download_failure(RATE_LIMITED), "blocked")
        self.assertEqual(ytdlp.download_failure(download_error("Sign in to confirm you’re not a bot", True)), "blocked")
        self.assertEqual(ytdlp.download_failure(DownloadError("ERROR: HTTP Error 429: Too Many Requests")), "blocked")
        self.assertEqual(ytdlp.download_failure(download_error("Sign in to confirm your age", True)), "content")
        self.assertEqual(ytdlp.download_failure(REMOVED), "content")
        self.assertEqual(ytdlp.download_failure(FORBIDDEN), "download")
        self.assertEqual(ytdlp.download_failure(RuntimeError("Only 12 GB left")), "other")


class VideoStepTest(VideoTest):
    def run_videos(self, errors=None, **options):
        """run_steps(video=True) where the video of content i fails with errors[i] (from 1):
        (report, the contents whose video was asked for, those whose wav was prepared)."""
        errors = errors or {}
        asked, prepared = [], []

        def download(db_conn, url, content):
            asked.append(content.id)
            if self.contents.index(content) + 1 in errors:
                raise errors[self.contents.index(content) + 1]
            path = ytdlp.VIDEO_DIR / f"{content.external_id}.mp4"
            path.write_bytes(b"video")
            db.add_content_file(db_conn, path, content.id, "ytdlp_video")
            return path

        ytdlp.VIDEO_DIR.mkdir(exist_ok=True)
        with (
            mock.patch("helpers.yt_dlp.get_ytdlp_content_video", side_effect=download),
            mock.patch("helpers.pipeline.prepare_wav", side_effect=lambda db_conn, c: prepared.append(c.id) or pathlib.Path("x.wav")),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            report = run_steps(self.contents, [], video=True, **options)
        return report, asked, prepared

    def ids(self, *numbers):
        return [self.contents[n - 1].id for n in numbers]

    def test_only_the_videos_are_downloaded(self):
        report, _, prepared = self.run_videos({2: REMOVED})
        self.assertEqual(report.done["video download"], [self.contents[n] for n in (0, 2, 3)])
        self.assertEqual([c for c, _ in report.failed["video download"]], [self.contents[1]])
        self.assertEqual(prepared, [])
        self.assertEqual(report.exit_code(), 1)

    def test_a_failed_video_does_not_stop_the_audio(self):
        _, _, prepared = self.run_videos({2: FORBIDDEN}, audio=True)
        self.assertEqual(prepared, self.ids(1, 2, 3, 4))

    def test_a_video_already_there_is_not_downloaded_again(self):
        self.run_videos({2: REMOVED})
        report, asked, _ = self.run_videos()
        self.assertEqual(report.already_done, 3)
        self.assertEqual(asked, self.ids(2))

    def test_a_platform_that_blocks_gets_no_more_requests(self):
        report, asked, prepared = self.run_videos({2: RATE_LIMITED}, audio=True)
        self.assertEqual(asked, self.ids(1, 2))
        self.assertEqual(prepared, self.ids(1))  # the others would download their audio
        why, not_downloaded = report.stopped["Test"]
        self.assertIn("rate-limited", why)
        self.assertEqual(not_downloaded, self.contents[1:])
        self.assertEqual(report.exit_code(), 6)

    def test_the_contents_not_tried_are_not_failures(self):
        report, _, _ = self.run_videos({2: BOT_CHECK})
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            print_report(report)
        self.assertIn("1 failures, 2 not tried (downloads stopped).", printed.getvalue())
        self.assertIn("run this again tomorrow", printed.getvalue())

    def test_what_is_on_disk_goes_on_after_a_block(self):
        mp3 = self.root / "x3.mp3"  # its wav is made without downloading anything
        mp3.write_bytes(b"mp3")
        with db.connect() as db_conn:
            db.add_content_file(db_conn, mp3, self.contents[2].id, "ytdlp_audio")
        _, _, prepared = self.run_videos({1: RATE_LIMITED}, audio=True)
        self.assertEqual(prepared, self.ids(3))

    def test_errors_in_a_row_stop_the_downloads_too(self):
        report, asked, _ = self.run_videos({1: FORBIDDEN, 2: FORBIDDEN, 3: FORBIDDEN})
        self.assertEqual(asked, self.ids(1, 2, 3))
        self.assertIn("3 download errors in a row", report.stopped["Test"][0])

    def test_a_video_refused_shows_that_the_platform_answers(self):
        report, asked, _ = self.run_videos({1: FORBIDDEN, 2: REMOVED, 3: FORBIDDEN, 4: FORBIDDEN})
        self.assertEqual(asked, self.ids(1, 2, 3, 4))
        self.assertEqual(report.stopped, {})


class ListingTest(VideoTest):
    def test_after_a_block_no_more_details_are_asked(self):
        entries = [{"url": f"https://example.com/new{i}", "ie_key": "Test", "id": f"new{i}"} for i in (1, 2)]
        entries.insert(1, {"url": self.contents[0].url, "ie_key": "Test", "id": "x1"})  # in the database
        with (
            mock.patch("helpers.yt_dlp._iter_listings", return_value=iter([("Videos", iter(entries))])),
            mock.patch("helpers.yt_dlp.add_ytdlp_content", side_effect=RATE_LIMITED) as add,
            contextlib.redirect_stdout(io.StringIO()),
            db.connect() as db_conn,
        ):
            contents, skipped = ytdlp.list_contents(db_conn, "https://example.com/listing")
        add.assert_called_once()
        self.assertEqual(contents, [self.contents[0]])
        listing_url, reason, is_error = skipped[-1]
        self.assertEqual((listing_url, is_error), ("https://example.com/listing", True))
        self.assertIn("1 entries not checked", reason)


class UnmountedDiskTest(VideoTest):
    def test_nothing_runs_while_a_cache_folder_is_on_a_disk_not_mounted(self):
        os.symlink(self.root / "unmounted" / "16k_mono_wav", self.root / "16k_mono_wav")
        [error] = self.settings_errors(["transcribe"])
        self.assertIn("16k_mono_wav", error)
        self.assertIn("is its disk mounted?", error)
        with (
            mock.patch("helpers.yt_dlp.get_ytdlp_content_video") as download,
            self.assertRaisesRegex(RuntimeError, "16k_mono_wav"),
        ):
            run_steps(self.contents, [], video=True)
        download.assert_not_called()

    def test_the_video_disk_not_mounted_is_reported_once(self):
        os.symlink(self.root / "unmounted" / "ytdlp_video", ytdlp.VIDEO_DIR)
        [error] = self.settings_errors(["video", "transcribe"])
        self.assertIn("is its disk mounted?", error)


class DetailsTest(VideoTest):
    def test_videos_are_selected_by_category_and_tag(self):
        details = {1: (["Gaming"], ["Let's play"]), 2: (["Entertainment"], ["news", "Macron"]), 3: (["Entertainment"], None)}
        with contextlib.redirect_stdout(io.StringIO()), db.connect() as db_conn:
            for n, (categories, tags) in details.items():
                path = self.root / f"x{n}.json"
                path.write_text(json.dumps({"categories": categories, "tags": tags}))
                db.add_content_file(db_conn, path, self.contents[n - 1].id, "ytdlp_info")

            def select(**filters):
                selected, unknown = ytdlp.select_by_details(db_conn, self.contents, **filters)
                return [self.contents.index(c) + 1 for c in selected], unknown

            self.assertEqual(select(categories=["entertainment"]), ([2, 3], 1))
            self.assertEqual(select(skip_categories=["Gaming"]), ([2, 3, 4], 1))  # 4 has no details
            self.assertEqual(select(tags=["macron", "woke"]), ([2], 1))
            self.assertEqual(select(skip_categories=["Gaming"], tags=["let's play"]), ([], 1))


class PaceTest(unittest.TestCase):
    def test_requests_to_one_site_are_spaced(self):
        with (
            mock.patch.dict(ytdlp._last_requests, clear=True),
            mock.patch("random.uniform", return_value=10.0),
            mock.patch("time.monotonic", side_effect=[100.0, 103.0, 110.0, 111.0]),
            mock.patch("time.sleep") as sleep,
        ):
            ytdlp._pace("https://www.youtube.com/watch?v=a")
            ytdlp._pace("https://youtube.com/watch?v=b")  # 3 s later: waits 7 s
            ytdlp._pace("https://www.twitch.tv/videos/1")  # another site: no wait
        sleep.assert_called_once_with(7.0)


if __name__ == "__main__":
    unittest.main()
