"""Print the UPLOADED_FROM / UPLOADED_TO settings lines of a job for a period.

    python3.11 .claude/skills/pipeline-job/dates.py last-week
    python3.11 .claude/skills/pipeline-job/dates.py 2026-03        # a whole month
    python3.11 .claude/skills/pipeline-job/dates.py 2026           # a whole year

Periods (relative to today in this machine's time zone): today, yesterday, this-week, last-week
(the previous Monday to Sunday), this-month, last-month, this-year, last-year, last-N-days (e.g.
last-7-days: the 7 days ending today, today included).
"""

import datetime
import re
import sys

PERIODS = "today, yesterday, this-week, last-week, this-month, last-month, this-year, last-year, last-N-days, YYYY-MM, YYYY"


def period(name: str, today: datetime.date) -> tuple[datetime.date, datetime.date]:
    day = datetime.timedelta(days=1)
    if name == "today":
        return today, today
    if name == "yesterday":
        return today - day, today - day
    if name == "this-week":
        return today - today.weekday() * day, today
    if name == "last-week":
        monday = today - (today.weekday() + 7) * day
        return monday, monday + 6 * day
    if name == "this-month":
        return today.replace(day=1), today
    if name == "last-month":
        last = today.replace(day=1) - day
        return last.replace(day=1), last
    if name == "this-year":
        return today.replace(month=1, day=1), today
    if name == "last-year":
        return datetime.date(today.year - 1, 1, 1), datetime.date(today.year - 1, 12, 31)
    if m := re.fullmatch(r"last-(\d+)-days?", name):
        return today - (max(int(m.group(1)), 1) - 1) * day, today
    if m := re.fullmatch(r"(\d{4})-(\d{2})", name):
        first = datetime.date(int(m.group(1)), int(m.group(2)), 1)
        following = (first + 31 * day).replace(day=1)
        return first, following - day
    if re.fullmatch(r"\d{4}", name):
        return datetime.date(int(name), 1, 1), datetime.date(int(name), 12, 31)
    raise ValueError(name)


if len(sys.argv) != 2:
    sys.exit(f"usage: python3.11 {sys.argv[0]} PERIOD, PERIOD being one of: {PERIODS}")
today = datetime.date.today()  # local date: at 00:30 in Paris, UTC is still yesterday
try:
    first, last = period(sys.argv[1].lower(), today)
except ValueError:
    sys.exit(f"Unknown period {sys.argv[1]!r}: use one of {PERIODS}")
print(f"# {sys.argv[1]} (today is {today:%A} {today})")
print(f'UPLOADED_FROM = "{first}"')
print(f'UPLOADED_TO = "{last}"')
