"""Print the UPLOADED_FROM / UPLOADED_TO settings lines of a job for a period.

    python .claude/skills/pipeline-job/dates.py last-week
    python .claude/skills/pipeline-job/dates.py 2026-03        # a whole month
    python .claude/skills/pipeline-job/dates.py 2026           # a whole year

Periods (relative to today, UTC): today, yesterday, this-week, last-week (the previous Monday to
Sunday), this-month, last-month, this-year, last-year, last-N-days (e.g. last-30-days: today minus
N days to today).
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
        return today - int(m.group(1)) * day, today
    if m := re.fullmatch(r"(\d{4})-(\d{2})", name):
        first = datetime.date(int(m.group(1)), int(m.group(2)), 1)
        following = (first + 31 * day).replace(day=1)
        return first, following - day
    if re.fullmatch(r"\d{4}", name):
        return datetime.date(int(name), 1, 1), datetime.date(int(name), 12, 31)
    raise ValueError(name)


if len(sys.argv) != 2:
    sys.exit(f"usage: python {sys.argv[0]} PERIOD, PERIOD being one of: {PERIODS}")
today = datetime.datetime.now(datetime.timezone.utc).date()
try:
    first, last = period(sys.argv[1].lower(), today)
except ValueError:
    sys.exit(f"Unknown period {sys.argv[1]!r}: use one of {PERIODS}")
print(f"# {sys.argv[1]} (today is {today:%A} {today})")
print(f'UPLOADED_FROM = "{first}"')
print(f'UPLOADED_TO = "{last}"')
