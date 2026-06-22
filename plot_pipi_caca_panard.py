from helpers.subtitles import *
from helpers.yt_dlp import *
import os
from pathlib import Path

workdir = "cache/medoliie"

data = []
for file in os.listdir(f"{workdir}/infos"):
    if file.endswith(".json"):
        info_path = os.path.join(f"{workdir}/infos", file)
        with open(info_path) as f:
            video_info = json.load(f)

        txt_path = f"cache/medoliie/transcripts/{video_info['id']}.fr.txt"
        transcription = load_sparsified_transcription(txt_path, 0)

        data.append((video_info, transcription))


# import matplotlib.pyplot as plt
# from collections import defaultdict
# import re

# target_words = ("pipi", "caca", "pied")


# def count_target_words(transcription):
#     clean = re.sub(r"[^\w\s\'-]", "", transcription.lower())
#     words = clean.split()

#     counts = {}
#     for word in target_words:
#         counts[word] = sum(1 for w in words if w == word)
#     return counts


# # Extract timestamps and word counts per video
# timestamps = []
# counts_dict = defaultdict(list)

# for i, (video_info, transcription) in enumerate(data):
#     if video_info["upload_date"] < "202603":
#         continue
#     timestamps.append(video_info["timestamp"])

#     counts = count_target_words(transcription)

#     for word in target_words:
#         counts_dict[word].append(counts[word])

#     # normalize by stream duration
#     # for word in target_words:
#     #     counts_dict[word].append(counts[word] / ((video_info["duration"]) / 3600))

# sorted_indices = sorted(range(len(timestamps)), key=lambda i: timestamps[i])
# timestamps_sorted = [timestamps[i] for i in sorted_indices]

# counts_sorted = {
#     word: [counts_dict[word][i] for i in sorted_indices] for word in target_words
# }


# # transform to cumulative
# for word in target_words:
#     for i in range(1, len(counts_sorted[word])):
#         counts_sorted[word][i] += counts_sorted[word][i - 1]


# # Plot
# plt.figure(figsize=(10, 6))

# plt.plot(
#     timestamps_sorted, counts_sorted["pipi"], label=f"'pipi'", marker="o", alpha=0.7
# )
# plt.plot(
#     timestamps_sorted, counts_sorted["caca"], label=f"'caca'", marker="s", alpha=0.7
# )
# plt.plot(
#     timestamps_sorted, counts_sorted["pied"], label=f"'pied'", marker="x", alpha=0.7
# )

# plt.xlabel("Date")
# plt.ylabel("Nombre total")
# plt.title("'pipi' ou 'caca' ou 'pied' ?")
# plt.legend()
# plt.grid(True, linestyle="--", alpha=0.5)

# # Optional: Convert x-axis to readable dates
# from datetime import datetime

# ax = plt.gca()
# ax.set_xticks(ax.get_xticks())
# ax.set_xticklabels(
#     [datetime.utcfromtimestamp(ts).strftime("%Y-%m") for ts in ax.get_xticks()],
#     rotation=45,
# )

# plt.tight_layout()
# plt.show()


import re
from collections import defaultdict
from datetime import datetime

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

themes = {
    "pipi": {
        "regexp": re.compile("pipi", re.IGNORECASE),
        "color": "#FF6B6B",
        "marker": "o",
    },
    "caca": {
        "regexp": re.compile("caca", re.IGNORECASE),
        "color": "#4D96FF",
        "marker": "s",
    },
    "panard": {
        "regexp": re.compile("(pied|panard)", re.IGNORECASE),
        "color": "#51CF66",
        "marker": "^",
    },
}


def count_regexp(text: str, pattern: re.Pattern):
    return sum(1 for _ in re.finditer(pattern, text))




def build_series(data):
    """Extract sorted timestamps + cumulative per-word counts."""
    timestamps = []
    counts_dict = defaultdict(list)

    for video_info, transcription in data:
        if video_info["upload_date"] < "202605":
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

    # dates_sorted = [datetime.utcfromtimestamp(ts) for ts in timestamps_sorted]
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
        "Médolie : « pipi », « caca » ou « panard » ?",
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
    # plt.savefig("/mnt/user-data/outputs/word_counts.png", bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    # `data` must be defined/loaded beforehand as a list of (video_info, transcription) tuples
    plot_word_counts(data)
