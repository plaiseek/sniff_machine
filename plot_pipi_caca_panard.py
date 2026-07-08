from helpers.subtitles import *
from helpers.yt_dlp import *
import os

workdir = "old_cache/medoliie"

data = []
for file in os.listdir(f"{workdir}/infos"):
    if file.endswith(".json"):
        info_path = os.path.join(f"{workdir}/infos", file)
        with open(info_path) as f:
            video_info = json.load(f)

        txt_path = f"old_cache/medoliie/transcripts/{video_info['id']}.fr.txt"
        transcription = load_sparsified_transcription(txt_path, 0)

        data.append((video_info, transcription))


import re
from collections import defaultdict
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

themes = {
    "pipi/pisser": {
        "regexp": re.compile("[^a-z](pipis?|pisser?)[^a-z]", re.IGNORECASE),
        "color": "#FF6B6B",
        "marker": "o",
    },
    "caca/chier": {
        "regexp": re.compile("[^a-z](cacas?|chier?)[^a-z]", re.IGNORECASE),
        "color": "#CF7226",
        "marker": "v",
    },
    "panard/pied": {
        "regexp": re.compile("[^a-z](pieds?|panards?)[^a-z]", re.IGNORECASE),
        "color": "#51CF66",
        "marker": "^",
    },
    "prout/pet/péter": {
        "regexp": re.compile("[^a-z](pets?|prout(s|er|es|e|ons|ent)?|p(é|è)t(er|e|es|ons|ent)?)[^a-z]", re.IGNORECASE),
        "color": "#4D96FF",
        "marker": "s",
    },
    "OST": {
        "regexp": re.compile("[^a-z]OST[^a-z]", re.IGNORECASE),
        "color": "#C551CF",
        "marker": "d",
    },
}


def count_regexp(text: str, pattern: re.Pattern):
    return sum(1 for _ in re.finditer(pattern, text))


def build_series(data):
    """Extract sorted timestamps + cumulative per-word counts."""
    timestamps = []
    counts_dict = defaultdict(list)

    for video_info, transcription in data:
        if video_info["upload_date"] < "202604":
            continue

        timestamps.append(video_info["timestamp"])
        for theme_name, theme in themes.items():
            counts_dict[theme_name].append(count_regexp(transcription, theme["regexp"]))

    order = sorted(range(len(timestamps)), key=lambda i: timestamps[i])
    timestamps_sorted = [timestamps[i] for i in order]
    counts_sorted = {
        theme_name: [counts_dict[theme_name][i] for i in order]
        for theme_name in themes.keys()
    }

    # cumulative
    for theme_name in themes.keys():
        for i in range(1, len(counts_sorted[theme_name])):
            counts_sorted[theme_name][i] += counts_sorted[theme_name][i - 1]

    dates_sorted = [datetime.fromtimestamp(ts) for ts in timestamps_sorted]
    return dates_sorted, counts_sorted


def plot_word_counts(data):
    dates_sorted, counts_sorted = build_series(data)

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

    for theme_name, theme in themes.items():
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
        "Médolie : « pipi » vs « caca » vs « panard » vs « prout » vs « OST »",
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
    plt.show()


if __name__ == "__main__":
    plot_word_counts(data)
