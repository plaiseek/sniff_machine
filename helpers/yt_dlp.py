import datetime
import itertools
import json
import os
import re
import yt_dlp
from pathlib import Path

import helpers.database as db


def _upload_date(info: dict) -> str | None:
    """ISO-8601 upload date from yt-dlp info, or None when the extractor gives none."""
    timestamp = info.get("timestamp") or info.get("release_timestamp")
    if timestamp is not None:
        return datetime.datetime.fromtimestamp(
            timestamp, tz=datetime.timezone.utc
        ).isoformat()
    date = info.get("upload_date") or info.get("release_date")  # YYYYMMDD
    if date:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    return None


def add_ytdlp_content(
    db_conn: db.sqlite3.Connection,
    url: str,
    force: bool = False,
) -> db.Content:
    content = db.get_content_from_url(db_conn, url)
    if content is not None and not force:
        if db.get_existing_content_file(db_conn, content.id, "ytdlp_info") is not None:
            return content

    # noplaylist: a watch?v=X&list=Y URL is video X; extract_flat: a real playlist is rejected
    # after one request instead of extracting all its entries first
    with yt_dlp.YoutubeDL(
        {"quiet": True, "noplaylist": True, "extract_flat": "in_playlist"}
    ) as ydl:
        info = ydl.extract_info(url, download=False)

        if info.get("_type") in ("playlist", "multi_video"):
            raise ValueError(f"'{url}' is a playlist, not a single content")
        # A live, upcoming or still recording content (e.g. the Twitch VOD of a running
        # stream) would be cached truncated: it is picked up by a later run instead
        if info.get("is_live") or info.get("live_status") in (
            "is_live",
            "is_upcoming",
            "post_live",
        ):
            raise ValueError(
                f"'{url}' is live or upcoming ({info.get('live_status')}), skipped"
            )

        if content is None:
            # The same content can be reached through several URLs (youtu.be/..., &t=..., embeds)
            content = db.get_content_from_external_id(
                db_conn, info["extractor_key"], info["id"]
            )
            if content is not None and not force:
                if db.get_existing_content_file(db_conn, content.id, "ytdlp_info") is not None:
                    return content

        if content is None:
            # Many extractors leave out uploader, timestamp or duration (stored as NULL)
            try:
                content = db.add_content(
                    db_conn,
                    info["extractor_key"],
                    info["id"],
                    url,
                    info.get("title"),
                    info.get("duration"),
                    info.get("uploader") or info.get("channel") or info.get("series"),
                    _upload_date(info),
                    channel_id=info.get("channel_id") or info.get("uploader_id"),
                    series=info.get("series"),
                )
            except db.sqlite3.IntegrityError:
                # Added by another script meanwhile (e.g. two jobs listing the same channel)
                content = db.get_content_from_url(db_conn, url) or db.get_content_from_external_id(
                    db_conn, info["extractor_key"], info["id"]
                )
                if content is None:
                    raise

        elif content.channel_id is None:
            db.set_content_channel(
                db_conn,
                content.id,
                info.get("channel_id") or info.get("uploader_id"),
                info.get("series"),
            )

        info_path = Path(
            f"cache/ytdlp_info/{content.platform}_{content.external_id}.json"
        )
        os.makedirs(info_path.parent, exist_ok=True)
        with open(info_path, "w") as f:
            json.dump(ydl.sanitize_info(info), f)
        db.add_content_file(db_conn, info_path, content.id, "ytdlp_info")

        return content


def get_ytdlp_content(db_conn: db.sqlite3.Connection, url: str) -> db.Content:
    content = db.get_content_from_url(db_conn, url)
    if content is None:
        content = add_ytdlp_content(db_conn, url)
    return content


def get_ytdlp_content_mp3(
    db_conn: db.sqlite3.Connection,
    url: str,
    content: db.Content | None = None,
    force: bool = False,
) -> Path:
    if content is None:
        content = get_ytdlp_content(db_conn, url)

    mp3_path = db.get_existing_content_file(db_conn, content.id, "ytdlp_audio")
    if mp3_path is not None and not force:
        return mp3_path

    mp3_path = Path(f"cache/ytdlp_audio/{content.platform}_{content.external_id}.mp3")

    def download(audio_format: str, **options) -> None:
        # yt-dlp appends to the partial file of a failed attempt or an earlier run, even one of
        # another format (the direct and HLS downloads share this name): start from scratch
        for suffix in (".part", ".ytdl"):
            Path(str(mp3_path)[:-4] + suffix).unlink(missing_ok=True)
        with yt_dlp.YoutubeDL(
            {
                **options,
                "quiet": True,
                "noprogress": True,  # the progress bar floods logs (one update every few ms)
                "noplaylist": True,
                "format": audio_format,
                "outtmpl": str(mp3_path)[:-4],
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        ) as ydl:
            ydl.download([url])

    try:
        download("bestaudio/best")
    except yt_dlp.utils.DownloadError as e:
        # Some YouTube videos answer 403 to every direct (https) format, even with an up-to-date
        # yt-dlp, but serve the same audio (original language first) through HLS
        if "HTTP Error 403" not in str(e):
            raise
        print(f"{url}: HTTP 403 on the direct formats, downloading through HLS", flush=True)
        # A throttled HLS download loses fragments: fail instead of keeping audio with holes, which
        # would shift every time after them
        download("bestaudio[protocol^=m3u8]", skip_unavailable_fragments=False)

    if not mp3_path.is_file():
        raise ValueError(f"yt-dlp did not produce '{mp3_path}'")
    db.add_content_file(db_conn, mp3_path, content.id, "ytdlp_audio")
    return mp3_path


def get_ytdlp_content_srt(
    url: str,
    db_conn: db.sqlite3.Connection,
    content: db.Content | None = None,
    lang: str = "fr",
    force: bool = False,
    automatic_sub: bool = False,
) -> Path:
    if content is None:
        content = get_ytdlp_content(db_conn, url)

    # Only one subtitles file per content: the cached one is reused only if it is in `lang`
    srt_path = Path(
        f"cache/ytdlp_subtitles/{content.platform}_{content.external_id}.{lang}.srt"
    )
    cached_path = db.get_existing_content_file(db_conn, content.id, "ytdlp_subtitles")
    if cached_path == srt_path and not force:
        return srt_path

    with yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "noplaylist": True,
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": automatic_sub,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt",
            # Platforms without srt subtitles (e.g. vtt/ttml only) are converted to srt
            "postprocessors": [
                {
                    "key": "FFmpegSubtitlesConvertor",
                    "format": "srt",
                    "when": "before_dl",
                }
            ],
            "outtmpl": str(srt_path)[: -(5 + len(lang))],
        }
    ) as ydl:
        ydl.download([url])

    if not srt_path.is_file():
        raise ValueError(f"It seems there are no subtitles for '{lang}'")

    db.add_content_file(db_conn, srt_path, content.id, "ytdlp_subtitles")
    return srt_path


_LISTING_OPTIONS = {
    "quiet": True,
    "extract_flat": True,
    # YouTube entries then carry an approximate upload time, computed from "3 weeks ago"
    "extractor_args": {"youtubetab": {"approximate_date": [""]}},
}


def _iter_listings(ydl: yt_dlp.YoutubeDL, listing_url: str):
    """Yield (name, entries) for each listing behind a URL, the entries being fetched page by page
    as they are iterated. A YouTube channel URL without a tab gives its Videos, Live and Shorts tabs."""
    try:
        result = ydl.extract_info(listing_url, download=False, process=False)
        while result.get("_type") in ("url", "url_transparent"):  # e.g. a redirect to another URL
            result = ydl.extract_info(
                result["url"], download=False, process=False, ie_key=result.get("ie_key")
            )
    except yt_dlp.utils.DownloadError as e:
        if "does not have a" in str(e):  # e.g. "This channel does not have a streams tab"
            print(f"  {str(e).splitlines()[0]}: nothing to list")
            return
        raise
    if result.get("_type") not in ("playlist", "multi_video"):
        raise ValueError(f"'{listing_url}' is a single content, not a listing: put it in CONTENT_URLS")
    entries = iter(result.get("entries") or [])
    first = next(entries, None)
    if first is None:
        return
    entries = itertools.chain([first], entries)
    if first.get("_type") == "playlist":  # nested listings
        for listing in entries:
            yield from _leaf_listings(listing)
    else:
        yield result.get("title") or listing_url, entries


def _leaf_listings(result: dict):
    entries = iter(result.get("entries") or [])
    first = next(entries, None)
    if first is None:
        return
    entries = itertools.chain([first], entries)
    if first.get("_type") == "playlist":
        for listing in entries:
            yield from _leaf_listings(listing)
    else:
        yield result.get("title") or result.get("id"), entries


def _is_newest_first(listing_url: str) -> bool:
    """Whether a listing is sorted by upload date, newest first (YouTube channel tabs and Twitch
    channel videos by default), so that it can be stopped once its contents get too old."""
    youtube = re.match(
        r"^https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)"
        r"(?:/(?:videos|streams|shorts))?/?(?:[?#]|$)",
        listing_url,
    )
    twitch = re.match(r"^https?://(?:www\.|m\.)?twitch\.tv/[^/?#]+/videos/?(?:[?#]|$)", listing_url)
    sort = re.search(r"[?&]sort=([^&#]*)", listing_url)
    if youtube:
        return sort is None or sort.group(1) == "dd"
    if twitch:
        # Clips and collections are sorted by views or by hand, whatever the sort parameter
        if re.search(r"[?&]filter=(?:clips|collections)\b", listing_url):
            return False
        return sort is None or sort.group(1) == "time"
    return False


def _flat_date_bounds(entry: dict, today: datetime.date) -> tuple | None:
    """(earliest, latest) possible upload date of a listing entry, from its flat timestamp, or None."""
    timestamp, approximate = entry.get("timestamp"), entry.get("ie_key") == "Youtube"
    if timestamp is None:  # an upcoming content: its scheduled start, exact
        timestamp, approximate = entry.get("release_timestamp"), False
    if timestamp is None:
        return None
    date = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).date()
    if approximate:
        # Approximate ("3 weeks ago"): the error can reach one unit, which is inferred from the age,
        # plus the rounding and the months or years of unequal lengths. yt-dlp rounds to the nearest
        # UTC midnight, so "1 week ago" can look 6 days old: the thresholds lean to the larger unit
        age = (today - date).days
        unit = 1 if age < 6 else 7 if age < 27 else 31 if age < 360 else 366
        # +4: "1 month ago" on March 1st is February 1st for yt-dlp, but up to 61 days for YouTube
        margin = datetime.timedelta(days=unit + 4 + age // 30)
    else:
        # Exact time, but the date the content gets may be a local one
        margin = datetime.timedelta(days=1)
    return date - margin, date + margin


def _backfill_channel(db_conn: db.sqlite3.Connection, content: db.Content) -> db.Content:
    """Fill the channel of a content added before schema v2, from its cached yt-dlp info."""
    if content.channel_id is not None:
        return content
    info_path = db.get_existing_content_file(db_conn, content.id, "ytdlp_info")
    if info_path is None:
        return content
    with open(info_path) as f:
        info = json.load(f)
    channel_id = info.get("channel_id") or info.get("uploader_id")
    if channel_id is None and info.get("series") is None:
        return content
    db.set_content_channel(db_conn, content.id, channel_id, info.get("series"))
    return db.get_content(db_conn, content.id)


def _is_temporary(e: Exception) -> bool:
    """Whether an extraction error may go away on a later run (network, rate limit), rather than
    being the platform's answer for this content (removed, private, geo-blocked, age-restricted)."""
    text = str(e).lower()
    temporary = (
        "try again later", "not a bot", "429", "too many requests", "timed out", "temporar",
        # YouTube served only images to this session (outdated yt-dlp, missing JS runtime, PO token)
        "requested format is not available", "only images are available",
    )
    if any(s in text for s in temporary):
        return True
    if isinstance(e, ValueError):  # raised by add_ytdlp_content: live, upcoming, a playlist
        return False
    original = e.exc_info[1] if isinstance(e, yt_dlp.utils.DownloadError) and e.exc_info else e
    # yt-dlp marks the platform's own refusals as expected
    return not getattr(original, "expected", False)


# Listing entries marked as never downloadable without an account
_UNAVAILABLE = {
    "subscriber_only": "members only",
    "premium_only": "premium only",
    "needs_auth": "needs an account",
    "private": "private",
}


def list_contents(
    db_conn: db.sqlite3.Connection,
    listing_url: str,
    uploaded_from: str | None = None,
    uploaded_to: str | None = None,
    older_streak: int = 5,
) -> tuple[list[db.Content], list[tuple[str, str, bool]]]:
    """The contents of a listing (channel or channel tab, playlist, show page) uploaded from
    `uploaded_from` to `uploaded_to` (inclusive "YYYY-MM-DD" UTC dates, or None), registered in
    the database, in listing order.

    Returns (contents, skipped): `skipped` lists (url, reason, is_error) for the entries left out
    without knowing their date: is_error is False when the content is unavailable (live or
    upcoming, members only, private, removed, no upload date), True when the error may be
    temporary (e.g. network, rate limit): running again may then select it.

    The listing is read page by page, and the upload date of its entries checked in two passes:
    1. the date given by the listing itself, when there is one (approximate for YouTube): entries
       surely outside the dates are left out, and a listing sorted newest first stops after
       `older_streak` entries in a row surely older than `uploaded_from`;
    2. the exact date, from each remaining entry's details (one request each, cached in the
       database), with the same stop rule."""
    dated = uploaded_from is not None or uploaded_to is not None
    first = datetime.date.fromisoformat(uploaded_from) if uploaded_from else datetime.date.min
    last = datetime.date.fromisoformat(uploaded_to) if uploaded_to else datetime.date.max
    today = datetime.datetime.now(datetime.timezone.utc).date()
    contents, skipped = [], []

    with yt_dlp.YoutubeDL(_LISTING_OPTIONS) as ydl:
        newest_first = _is_newest_first(listing_url)
        for name, entries in _iter_listings(ydl, listing_url):
            # Pass 1: read the listing
            candidates, older = [], 0
            for entry in entries:
                entry_url = entry.get("url") or entry.get("webpage_url")
                if not entry_url:
                    continue
                if entry.get("_type") == "playlist" or entry.get("ie_key") == "YoutubeTab":
                    skipped.append((entry_url, "a playlist, not a content: list it separately", False))
                    continue
                bounds = _flat_date_bounds(entry, today) if dated else None
                if entry.get("live_status") in ("is_live", "is_upcoming"):
                    # Listed first whatever their date (a stream scheduled long ago that never took
                    # place stays "upcoming"): they never stop the listing. Reported if maybe in range.
                    if bounds is None or (bounds[0] <= last and bounds[1] >= first):
                        reason = "live now" if entry["live_status"] == "is_live" else "upcoming"
                        skipped.append((entry_url, f"{reason}: run the job again once it is over", False))
                    continue
                if bounds is not None and bounds[0] > last:
                    older = 0
                    continue  # surely newer than uploaded_to
                if bounds is not None and bounds[1] < first:
                    older += 1
                    if newest_first and older >= older_streak:
                        break  # the rest of the listing is older
                    continue  # surely older than uploaded_from
                older = 0
                # Entries that cannot be downloaded, and whose date may be in the range: reported
                if entry.get("availability") in _UNAVAILABLE:
                    skipped.append((entry_url, _UNAVAILABLE[entry["availability"]], False))
                    continue
                if (entry.get("title") or "").lower() in ("[private video]", "[deleted video]"):
                    skipped.append((entry_url, entry["title"][1:-1].lower(), False))
                    continue
                candidates.append(entry)
            print(f"  {name}: {len(candidates)} entries to check")

            # Pass 2: the exact upload dates
            older = 0
            for i, entry in enumerate(candidates, 1):
                entry_url = entry.get("url") or entry.get("webpage_url")
                content = db.get_content_from_url(db_conn, entry_url)
                if content is None and entry.get("ie_key") and entry.get("id"):
                    content = db.get_content_from_external_id(db_conn, entry["ie_key"], entry["id"])
                if content is None:
                    print(f"  ({i}/{len(candidates)}) Fetching the details of {entry_url}...")
                    try:
                        content = add_ytdlp_content(db_conn, entry_url)
                    except Exception as e:  # e.g. private, members only, removed, geo-blocked, network
                        message = str(e).splitlines()[0] if str(e) else type(e).__name__
                        skipped.append((entry_url, message, _is_temporary(e)))
                        continue
                else:
                    content = _backfill_channel(db_conn, content)

                if not dated:
                    contents.append(content)
                    continue
                if content.upload_date is None:
                    skipped.append((entry_url, "upload date unknown", False))
                    continue
                date = datetime.date.fromisoformat(content.upload_date[:10])
                if date > last:
                    older = 0
                elif date < first:
                    older += 1
                    if newest_first and older >= older_streak:
                        break
                else:
                    older = 0
                    contents.append(content)
    return contents, skipped
