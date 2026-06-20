import math
import re
from pathlib import Path


def srt_to_transcription(srt_path: Path, output_folder: str) -> Path:
    """Convert an SRT subtitle file to a plain text transcription format.

    This function strips timestamp codes and line numbers from SRT files,
    formatting the result as time-stamped sentences in a plain text file.

    Args:
        srt_path: Path to the source SRT file
        output_folder: Directory where the transcribed text will be saved

    Returns:
        Path to the converted text file
    """
    with open(srt_path, "r") as file:
        srt_content = file.read()

        result = re.sub(
            "[1-9]\d*\n(\d{2}:\d{2}:\d{2}),\d{3}[^\n]+", "[\\1]", srt_content
        )
        result = re.sub("^(\[\d{2}:\d{2}:\d{2}\])", "[\\1]", result)
        result = re.sub("\.\n\n(\[\d{2}:\d{2}:\d{2}\])", ".\n[\\1]", result)
        result = re.sub("\[\d{2}:\d{2}:\d{2}\]\n", "", result)
        result = re.sub("\n", " ", result)
        result = re.sub("\ +", " ", result)
        result = re.sub("\.\ +", ".\n", result)
        result = re.sub("\[(\[\d{2}:\d{2}:\d{2}\])\] ", "\\1\n", result)

        txt_path = Path(f"{output_folder}/{str(srt_path.stem)[0:-4]}.txt")

        with open(txt_path, "w") as file:
            file.write(result)
            return txt_path


def sparsify_transcription_timecodes(path: Path, number_to_keep: int) -> None:
    """Reduce the density of time codes in a transcription file by removing intermediate timestamps.

    This function strategically removes timestamps to leave only a specified number,
    preserving those that maximize the spacing between remaining timestamps.

    Args:
        path: Path to the transcription text file with timestamp codes
        number_to_keep: Target number of timestamps to retain

    Note:
        Modifies the input file in place.
    """
    with open(path, "r") as file:
        text = file.read()
        if number_to_keep < 2:
            result = re.sub("\[\d{2}:\d{2}:\d{2}\]\n", "", text)
        else:
            matches = re.findall("\[(\d{2}):(\d{2}):(\d{2})\]", text)
            timestamps = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in matches]

            lookup_dict = {i: True for i in range(0, len(timestamps))}
            prev_dict = {i: i - 1 for i in range(1, len(timestamps))}
            next_dict = {i: i + 1 for i in range(0, len(timestamps) - 1)}

            number_to_remove = len(timestamps) - number_to_keep
            idx_to_remove = []
            while len(idx_to_remove) < number_to_remove:
                min_space = math.inf
                min_space_idx = None
                for i in range(1, len(timestamps) - 1):
                    if not lookup_dict[i]:
                        continue
                    space = timestamps[next_dict[i]] - timestamps[prev_dict[i]]
                    if space < min_space:
                        min_space = space
                        min_space_idx = i

                lookup_dict[min_space_idx] = False
                prev_dict[next_dict[min_space_idx]] = prev_dict[min_space_idx]
                next_dict[prev_dict[min_space_idx]] = next_dict[min_space_idx]
                idx_to_remove.append(min_space_idx)
            timestamps_to_remove = [timestamps[i] for i in idx_to_remove]

            result = re.sub(
                "({})\n".format(
                    "|".join(
                        [
                            f"\\[{int(t / 3600):02}:{int((t % 3600)/60):02}:{t % 60:02}\\]"
                            for t in timestamps_to_remove
                        ]
                    )
                ),
                "",
                text,
            )

        with open(path, "w") as file:
            file.write(result)
