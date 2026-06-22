import math
import os
import re
from pathlib import Path


def srt_to_transcription(srt_path: Path, output_folder: str) -> Path:
    """
    Converts an SRT subtitle file into a simplified transcription format.
    
    The function performs the following steps:
      1. Reads the entire .srt content as a string.
      2. Strips numeric indices and converts timecodes to [HH:MM:SS] format.
      3. Normalizes whitespace, line breaks, and punctuation to produce a clean paragraph-style text,
         with each subtitle block separated by newlines after sentences (e.g., ending in ".\n").
    
    Args:
        srt_path: Full path to the input .srt file.
        output_folder: Directory where the output .txt will be saved.
    
    Returns:
        Path: The full path to the generated transcription .txt file.
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

        txt_path = Path(f"{output_folder}/{str(srt_path.stem)}.txt")

        os.makedirs(txt_path.parent, exist_ok=True)
        with open(txt_path, "w") as file:
            file.write(result)
            return txt_path


def load_sparsified_transcription(txt_path: Path, max_number_timecodes: int) -> str:
    """
    Load a transcription file and reduce the number of timestamp markers to at most `max_number_timecodes`.
    
    Timestamps are expected in the format [HH:MM:SS], e.g., [01:23:45].
    
    If there are ≤ `max_number_timecodes` timestamps, they are all retained.
    Otherwise, the function iteratively removes the timestamp that lies between the **smallest time gap**,
    mimicking a greedy "coarsening" of dense segments (e.g., to reduce redundancy in very frequent subtitles).
    
    Args:
        txt_path (Path): Path to the input .txt file containing the transcription with timestamps.
        max_number_timecodes (int): Maximum number of timestamps to retain. Must be ≥ 0.
            - If < 2: All timestamps are removed (no timecodes remain in output).
            - If ≥ total timestamp count: All timestamps remain unchanged.
    
    Returns:
        str: Transcription with sparsified timestamps (as a string), preserving all non-timestamp text.
        
    Note:
        This is a *greedy heuristic*: it removes the "least informative" gap first.
    """
    with open(txt_path, "r") as file:
        text = file.read()
        if max_number_timecodes < 2:
            result = re.sub("\[\d{2}:\d{2}:\d{2}\]\n", "", text)
        else:
            matches = re.findall("\[(\d{2}):(\d{2}):(\d{2})\]", text)
            timestamps = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in matches]

            lookup_dict = {i: True for i in range(0, len(timestamps))}
            prev_dict = {i: i - 1 for i in range(1, len(timestamps))}
            next_dict = {i: i + 1 for i in range(0, len(timestamps) - 1)}

            number_to_remove = len(timestamps) - max_number_timecodes
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
        return result
