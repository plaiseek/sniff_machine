# Sniff Machine

A project for political speech analytics by retrieving video transcriptions and analyzing keywords and text embeddings with LLMs.

## Overview

The Sniff Machine is designed to analyze political content created by influencers, content creators, and public figures. By automating the collection of video transcriptions and performing linguistic analysis, this tool helps answer questions about discourse patterns, keyword frequency, sentiment trends, and more over time.

The first question this project addresses is **"pee vs poo"**: identifying which words occur most frequently in a creator's content and tracking how the frequency of these words varies over time. This simple yet powerful metric provides insights into linguistic habits, evolving topics, and content focus shifts.

## Features

- **Automated Transcription Pipeline**: Download YouTube videos or Twitch VODs and transcribe audio using Whisper models
- **Subtitle Extraction**: Retrieve existing subtitles (manual or automatic) from video platforms
- **Keyword Frequency Analysis**: Count word occurrences across all content
- **Time-series Visualization**: Track keyword frequency over time to identify trends
- **LLM Integration**: Analyze text embeddings and perform semantic analysis with large language models

## Project Structure

```
.
├── helpers/
│   ├── yt_dlp.py              # YouTube video downloading utilities
│   ├── whisper.py             # Local Whisper transcription (CPU)
│   ├── whisper_client.py      # Client for remote Whisper server
│   └── subtitles.py           # SRT file processing and formatting
├── cache/                     # Downloaded files and transcriptions
│   ├── sardoche_txt/          # Text transcriptions from Sardoche's YouTube channel
│   ├── medolie_srt/           # Subtitles for Medoliie's Twitch VODs
│   └── ...
├── transcript_youtube_videos.py  # Script to download and transcribe YouTube content
├── transcript_twicth_vods.py     # Script to download and transcribe Twitch content
└── run_whisper_servers.py         # Start Whisper server instances for parallel transcription
```

## Getting Started

### Prerequisites

- Python 3.8+
- Docker (for running Whisper GPU servers)
- FFmpeg

### Installation

1. Clone the repository:
```bash
git clone https://github.com/your-org/sniff-machine.git
cd sniff-machine
```

2. Install Python dependencies:
```bash
pip install -r yt_dlp whisper docker
```

3. Set up Whisper servers (optional, for faster transcription):
```bash
python run_whisper_servers.py
```

### Usage

#### Transcribe YouTube Videos

1. Add video URLs to the script or modify `transcript_youtube_videos.py`:
```python
video_urls = ls_channel_videos("https://www.youtube.com/@ChannelName/videos")
```

2. Run the transcription pipeline:
```bash
python transcript_youtube_videos.py
```

This will:
- Download subtitles (SRT format) for each video
- Convert SRT files to plain text transcriptions

#### Transcribe Twitch VODs

1. Add video URLs to `transcript_twicth_vods.py` or modify as needed.

2. Ensure Whisper servers are running, then execute:
```bash
python transcript_twicth_vods.py
```

### Analysis Pipeline (Coming Soon)

Future versions will include:

- Word frequency analysis and visualization
- Time-series trend detection
- Keyword co-occurrence networks
- Sentiment analysis using LLM embeddings

## "Pee vs Poo" Analysis

The project's first quantitative question is the **"pee vs poo"** problem: 
1. Identify which words occur most frequently in a creator's entire content catalog
2. Track how these frequencies evolve over time

This simple word count serves as a proxy for:
- Topics and themes emphasized by the creator
- Linguistic habits and verbal tics
- Content focus shifts across different periods

Example analysis outputs will show:
- Top N most frequent words with their occurrence counts
- Frequency heatmaps over time (day/week/month)
- Correlation between word usage and video metrics (views, duration)

## Contributing

Contributions are welcome! Areas for improvement include:

- Adding more transcription platforms (YouTube Live, TikTok, etc.)
- Enhancing analysis modules (topic modeling, sentiment detection)
- Implementing LLM-based summarization of transcriptions
- Creating a web interface for result visualization

## License

This project is provided as-is for educational and personal use.

## Acknowledgments

- [Whisper](https://github.com/openai/whisper) for speech recognition
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) for video downloading
- Twitch and YouTube APIs for content access
