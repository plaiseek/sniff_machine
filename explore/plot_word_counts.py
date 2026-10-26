"""Plot how often some words are said in the contents of a channel, cumulated by upload date.

    python3.11 explore/plot_word_counts.py Psyhodelik                      # its transcribed contents
    python3.11 explore/plot_word_counts.py Psyhodelik --from 2026-04-01 --to 2026-06-30 --output counts.png
    python3.11 explore/plot_word_counts.py Psyhodelik --speaker "Jean Dupont"

The words come from the database (pipeline/store_words.py stores them). A word counts for a theme when its
normalised form (words.norm: lower case, without the punctuation around it) holds one of the
theme's words as a whole word: "l'pipi" and "pipi-caca" count for « pipi ». With --speaker, only
the words of the labels named so (by a person, or automatically by speakers/identify_speakers.py) count, in
the contents where such a label is: it keeps the other voices (guests, TTS donations, game
dialogue) out. Edit THEMES to count other words.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)  # the database and cache/ paths are relative to the repository root
sys.path.insert(0, str(ROOT))

import helpers.database as db


def word_regexp(pattern: str) -> re.Pattern:
    """Whole words only: lookarounds don't consume the neighbouring characters (so repeated words
    and words at the start/end of the text count), and accented letters are letters."""
    return re.compile(f"(?<![^\\W\\d_])({pattern})(?![^\\W\\d_])", re.IGNORECASE)


THEMES = {
    "pipi/pisser": {
        "regexp": word_regexp("pipis?|pisser?"),
        "color": "#FF6B6B",
        "marker": "o",
    },
    "caca/chier": {
        "regexp": word_regexp("cacas?|chier?"),
        "color": "#CF7226",
        "marker": "v",
    },
    "panard/pied": {
        "regexp": word_regexp("pieds?|panards?"),
        "color": "#51CF66",
        "marker": "^",
    },
    "prout/pet/péter": {
        "regexp": word_regexp("pets?|prout(s|er|es|e|ons|ent)?|p(é|è)t(er|e|es|ons|ent|é|és|ée|ées)?"),
        "color": "#4D96FF",
        "marker": "s",
    },
    "OST": {
        "regexp": word_regexp("OST"),
        "color": "#C551CF",
        "marker": "d",
    },
}



def build_series(db_conn, uploader: str, uploaded_from: str | None, uploaded_to: str | None, speaker: str | None):
    """The upload dates of the selected contents (oldest first) and, per theme, the cumulated counts."""
    where, params = ["instr(casefold(c.uploader), casefold(?)) > 0", "c.upload_date IS NOT NULL"], [uploader]
    if uploaded_from:
        where.append("substr(c.upload_date, 1, 10) >= ?")
        params.append(uploaded_from)
    if uploaded_to:
        where.append("substr(c.upload_date, 1, 10) <= ?")
        params.append(uploaded_to)
    # The speaker's words: those whose turn's label is named so, in the diarization the words follow
    speaker_join = ""
    if speaker:
        where.append(
            """EXISTS (SELECT 1 FROM speaker_labels l JOIN speakers p ON p.id = l.speaker_id
WHERE l.diarization_id = t.diarization_id AND p.name = ?)"""
        )
        params.append(speaker)
        speaker_join = """JOIN turns u ON u.id = w.turn_id
JOIN speaker_labels l ON l.diarization_id = u.diarization_id AND l.speaker = u.speaker
JOIN speakers p ON p.id = l.speaker_id AND p.name = ?"""
    contents = db_conn.execute(
        f"""SELECT c.id, substr(c.upload_date, 1, 10) FROM contents c JOIN transcriptions t ON t.content_id = c.id
WHERE {' AND '.join(where)} ORDER BY c.upload_date, c.id""",
        params,
    ).fetchall()
    ids = [content_id for content_id, _ in contents]
    # The regexps run once per distinct word, not once per word said
    norms = [norm for norm, in db_conn.execute("SELECT DISTINCT norm FROM words WHERE norm != ''")]
    counts = {}
    for theme_name, theme in THEMES.items():
        matching = [norm for norm in norms if theme["regexp"].search(norm)]
        per_content = dict.fromkeys(ids, 0)
        if matching and ids:
            rows = db_conn.execute(
                f"""SELECT w.content_id, COUNT(*) FROM words w {speaker_join}
WHERE w.content_id IN ({', '.join('?' for _ in ids)}) AND w.norm IN ({', '.join('?' for _ in matching)})
GROUP BY w.content_id""",
                ([speaker] if speaker else []) + ids + matching,
            )
            per_content.update(rows)
        cumulated, total = [], 0
        for content_id in ids:
            total += per_content[content_id]
            cumulated.append(total)
        counts[theme_name] = cumulated
    return [datetime.fromisoformat(date) for _, date in contents], counts


def plot_word_counts(dates_sorted, counts_sorted, title: str, output: str | None = None):
    try:
        import matplotlib.dates as mdates
        import matplotlib.pyplot as plt
    except ImportError as e:
        sys.exit(f"matplotlib cannot be imported ({e}). Under python3.11 on Ubuntu, the system Pillow is"
                 " the one of python3.10: python3.11 -m pip install --user pillow (or run this with python3).")

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.edgecolor": "#444444",
            "axes.labelcolor": "#333333",
            "text.color": "#333333",
            "xtick.color": "#555555",
            "ytick.color": "#555555",
        }
    )

    fig, ax = plt.subplots(figsize=(11, 6.5), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#FAFAFA")

    for theme_name, theme in THEMES.items():
        ax.plot(
            dates_sorted,
            counts_sorted[theme_name],
            label=f"« {theme_name} »",
            color=theme["color"],
            marker=theme["marker"],
            markersize=5,
            markerfacecolor="white",
            markeredgewidth=1.4,
            linewidth=2.4,
            alpha=0.95,
            solid_capstyle="round",
        )
        ax.fill_between(
            dates_sorted, counts_sorted[theme_name], color=theme["color"], alpha=0.06
        )

        # label the final value at the end of each line
        ax.annotate(
            f"{counts_sorted[theme_name][-1]}",
            xy=(dates_sorted[-1], counts_sorted[theme_name][-1]),
            xytext=(8, 0),
            textcoords="offset points",
            va="center",
            fontsize=10,
            fontweight="bold",
            color=theme["color"],
        )

    # Titles & labels
    ax.set_title(
        title,
        fontsize=18,
        fontweight="bold",
        pad=16,
    )
    ax.set_xlabel("Date", fontsize=12, labelpad=10)
    ax.set_ylabel("Occurrences cumulées", fontsize=12, labelpad=10)

    # Date axis formatting
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=10, maxticks=20))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate(rotation=40, ha="right")

    # Clean spines & grid
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.grid(True, axis="y", linestyle="--", linewidth=0.6, alpha=0.4)
    ax.set_axisbelow(True)

    # Legend
    legend = ax.legend(
        loc="upper left",
        frameon=True,
        framealpha=0.9,
        edgecolor="#DDDDDD",
        fontsize=11,
    )
    legend.get_frame().set_facecolor("white")

    plt.tight_layout()
    if output:
        fig.savefig(output)
        print(f"Saved {output}")
    else:
        plt.show()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("uploader", help="the channel (part of its name, as in contents.uploader)")
    parser.add_argument("--from", dest="uploaded_from", help="first upload date, YYYY-MM-DD")
    parser.add_argument("--to", dest="uploaded_to", help="last upload date, YYYY-MM-DD")
    parser.add_argument("--speaker", help="only the words of the labels named so")
    parser.add_argument("--output", help="save the plot to this file (e.g. counts.png) instead of showing it")
    args = parser.parse_args()
    with db.connect() as db_conn:
        dates, counts = build_series(db_conn, args.uploader, args.uploaded_from, args.uploaded_to, args.speaker)
        names = [name for name, in db_conn.execute(
            "SELECT DISTINCT uploader FROM contents WHERE instr(casefold(uploader), casefold(?)) > 0", (args.uploader,)
        )]
    if not dates:
        print("No transcribed content with an upload date matches" + (" (with this speaker named)." if args.speaker else "."))
        return 1
    for theme_name, cumulated in counts.items():
        print(f"{theme_name}: {cumulated[-1]}")
    print(f"{len(dates)} contents, {dates[0]:%Y-%m-%d} to {dates[-1]:%Y-%m-%d}")
    who = f" ({args.speaker})" if args.speaker else ""
    title = f"{' / '.join(names)}{who} : " + " vs ".join(f"« {name.split('/')[0]} »" for name in THEMES)
    plot_word_counts(dates, counts, title, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
