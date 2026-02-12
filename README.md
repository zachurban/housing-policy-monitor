# HousingEar - Housing Sentiment and Policy Monitor

AI-powered pipeline for ingesting public city council, planning commission, housing policy committee meetings and extracting affordable housing policy intelligence. Automatically transcribes, analyzes, and structures housing discussions from YouTube, Granicus, and Legistar to track zoning changes, funding commitments, community sentiment, and regulatory trends across Colorado jurisdictions.

## Features

- **Meeting Discovery** – Scrape YouTube channels, Granicus portals, and Legistar APIs for city council, planning commission, and housing committee recordings and agendas
- **Audio Download** – Extract audio via yt-dlp with configurable quality
- **Transcription** – Convert speech to text with speaker diarization (Deepgram nova-2)
- **AI Analysis** – Extract structured housing policy data using Claude (Anthropic API)
- **Data Storage** – JSON database + markdown summaries for every meeting
- **Analytics** – Query tools for policy tracking, sentiment trends, funding commitments, and jurisdiction comparison

## Configured Cities

| City | YouTube | Granicus | Legistar |
|------|---------|----------|----------|
| Denver | @Denver8TV | denver.granicus.com | denver.legistar.com |
| Aurora | @theaurorachannel | aurora.granicus.com | aurora.legistar.com |
| Lakewood | @LakewoodCOgov | lakewood.granicus.com | — |
| Boulder | @CityofBoulderGov | boulder.granicus.com | — |

Add new cities in `config.py` without changing pipeline code.

## Quick Start

```bash
# 1. Clone and install
git clone <repo-url>
cd housing-policy-monitor
pip install -r requirements.txt

# 2. Set up API keys
cp .env.example .env
# Edit .env with your Deepgram and Anthropic keys

# 3. Verify environment
python setup.py

# 4. Run the pipeline
python meeting_ingestion_pipeline.py --city Denver --limit 3
```

## Prerequisites

- **Python 3.8+**
- **FFmpeg** – `sudo apt install ffmpeg` (Ubuntu) or `brew install ffmpeg` (macOS)
- **yt-dlp** – installed via requirements.txt
- **Deepgram API key** – [console.deepgram.com](https://console.deepgram.com/)
- **Anthropic API key** – [console.anthropic.com](https://console.anthropic.com/)

Run `python setup.py --wizard` for interactive setup.

## Usage

### Full Pipeline

```bash
# Process all configured cities
python meeting_ingestion_pipeline.py

# Single city, limited to 5 meetings
python meeting_ingestion_pipeline.py --city Denver --limit 5

# Skip YouTube discovery, process existing meetings only
python meeting_ingestion_pipeline.py --skip-discovery
```

### Granicus Discovery

```bash
# Discover meetings from Granicus portals
python granicus_discovery.py --city Boulder

# Download agenda PDFs
python granicus_discovery.py --city Boulder --download-agendas
```

### Legistar Discovery

```bash
# Discover meetings from Legistar (structured legislative data)
python legistar_discovery.py --city Denver --days 90

# List all legislative bodies (committees)
python legistar_discovery.py --city Denver --list-bodies

# Fetch a specific meeting's agenda items and legislation
python legistar_discovery.py --city Denver --event-id 1381438

# Filter to a specific committee
python legistar_discovery.py --city Denver --body "Community Planning and Housing"

# Download agendas and minutes
python legistar_discovery.py --city Denver --download-agendas --download-minutes
```

Legistar provides structured legislative metadata (agenda items, votes, legislation text, attachments) that complements the audio transcription pipeline. No API key is required.

### Analytics & Reporting

```bash
# Full intelligence report (markdown)
python example_analysis.py --report

# City-specific report
python example_analysis.py --city Denver

# Search across all analyses
python example_analysis.py --search "inclusionary zoning"

# Compare jurisdictions
python example_analysis.py --compare Denver Aurora Lakewood

# Track policy proposals
python example_analysis.py --proposals

# Funding commitment tracker
python example_analysis.py --funding-tracker

# Sentiment trends
python example_analysis.py --sentiment-trends

# Topic frequency analysis
python example_analysis.py --topics

# Filter high-relevance meetings only
python example_analysis.py --high-relevance --housing-filter 0.5

# Track legislation lifecycle (Legistar)
python example_analysis.py --legislation

# Show vote records for housing matters (Legistar)
python example_analysis.py --votes

# Compare Legistar agenda data with transcription analysis
python example_analysis.py --legistar-sync
```

## Data Structure

```
meetings_data/
├── audio/           # Downloaded MP3 files
├── transcripts/     # Raw JSON from Deepgram
├── analysis/        # Claude analysis JSON + markdown summaries
├── agendas/         # Agenda PDFs from Granicus
├── minutes/         # Minutes PDFs from Granicus
├── meetings.json    # Master meeting database
└── intelligence_report.md  # Generated report
```

## Meeting Record Schema

```json
{
  "id": "video_id",
  "jurisdiction": "Denver",
  "title": "City Council Meeting",
  "date": "2025-01-15",
  "video_url": "https://youtube.com/watch?v=...",
  "source": "youtube",
  "duration_minutes": 120,
  "transcript_path": "meetings_data/transcripts/...",
  "analysis_path": "meetings_data/analysis/...",
  "summary_path": "meetings_data/analysis/...",
  "housing_mentions": 5,
  "housing_relevance_score": 0.72,
  "processed": true,
  "legistar_event_id": null,
  "agenda_items": []
}
```

For Legistar-sourced meetings, the `id` follows the format `legistar_{client}_{EventId}` and `agenda_items` contains structured data from the Legistar API.

## Analysis Output Schema

Each processed meeting produces a structured JSON with:

| Field | Description |
|-------|-------------|
| `housing_topics` | List of housing topics discussed |
| `policy_proposals` | Proposals with type, description, status, vote result |
| `sentiment` | Overall sentiment + stakeholder positions |
| `projects` | Development projects with address, units, affordability |
| `regulatory_changes` | Zoning/ordinance/policy changes |
| `funding` | Dollar amounts, sources, purposes |
| `actions` | Concrete actions, votes, next steps |
| `quotes` | Notable quotes with speaker attribution |
| `housing_relevance_score` | 0.0–1.0 relevance rating |
| `summary` | Executive summary paragraph |

## Housing Topics Tracked

- **Zoning**: inclusionary zoning, density bonus, rezoning, ADU, TOD, missing middle
- **Programs**: housing trust fund, LIHTC, Section 8, CDBG, HOME, TIF
- **Affordability**: AMI thresholds, rent stabilization, workforce housing
- **Development**: unit counts, affordability levels, building permits
- **Funding**: dollar amounts, federal/state/local sources
- **Regulatory**: ordinances, resolutions, code changes
- **Sentiment**: support/opposition, public comment themes

## Architecture

```
config.py                      # Cities, keywords, prompts, API settings
meeting_ingestion_pipeline.py  # Core pipeline classes
├── MeetingDiscovery           #   YouTube channel scraping
├── VideoProcessor             #   Audio download via yt-dlp
├── TranscriptionService       #   Deepgram integration
├── HousingAnalyzer            #   Claude API analysis (transcripts + agendas)
├── MeetingPipeline            #   Orchestrator (YouTube + Granicus + Legistar)
└── MeetingDatabase            #   JSON-backed storage
granicus_discovery.py          # Granicus platform integration
legistar_discovery.py          # Legistar Web API integration
├── LegistarDiscovery          #   API client for events, bodies, legislation
├── LegistarEvent/Body/Item    #   Structured data models
└── CLI                        #   Standalone discovery commands
example_analysis.py            # Analytics and reporting tools
setup.py                       # Environment verification
```

### Data Flow

```
YouTube ──────→ VideoProcessor → TranscriptionService → HousingAnalyzer ─→ MeetingDatabase
Granicus ─────→ VideoProcessor → TranscriptionService → HousingAnalyzer ─→ MeetingDatabase
Legistar ─────→ Agenda Items ──────────────────────→ HousingAnalyzer ─→ MeetingDatabase
                  (+ votes, legislation, attachments)
```

Legistar meetings with video URLs feed into the standard video→transcribe→analyze pipeline. Meetings without video are analyzed directly from structured agenda items using a specialized agenda analysis prompt.

## Configuration

All configuration lives in `config.py`:

- **COLORADO_CITIES** – Add/remove jurisdictions with their YouTube, Granicus, and Legistar details
- **MEETING_TITLE_KEYWORDS** – Patterns for identifying meeting videos
- **HOUSING_KEYWORDS** – Terms for relevance scoring
- **ANALYSIS_PROMPT_TEMPLATE** – Claude prompt for policy extraction
- **API_CONFIG** – Deepgram and Anthropic settings
- **LEGISTAR_CONFIG** – Legistar API base URL, lookback period, rate limits, pagination
- **PROCESSING** – Rate limits, quality, batch sizes

## Adding a New City

```python
# In config.py, add to COLORADO_CITIES:
"Fort Collins": {
    "youtube_channel": "@CityofFortCollins",
    "youtube_url": "https://www.youtube.com/@CityofFortCollins/videos",
    "granicus_site": "fortcollins.granicus.com",
    "granicus_clip_id_prefix": "fortcollins",
    "meeting_bodies": ["City Council", "Planning & Zoning Board"],
    # Optional: add Legistar if the city uses it
    "legistar_client": "fortcollins",
    "legistar_housing_bodies": ["City Council", "Planning & Zoning Board"],
},
```

Then run:
```bash
python meeting_ingestion_pipeline.py --city "Fort Collins" --limit 5
```

## Error Handling

- Missing API keys: pipeline skips transcription/analysis with warnings
- Individual meeting failures: logged and skipped, pipeline continues
- Rate limiting: configurable delays between API calls (default 5s)
- Duplicate detection: meetings are identified by video ID, re-runs are safe
- Malformed transcripts: graceful fallback with error logging

## License

Copyright (c) 2026 Zachary Urban

All rights reserved.

No part of this software or any of its associated documentation may be reproduced, distributed, or transmitted in any form or by any means, including photocopying, recording, or other electronic or mechanical methods, without the prior written permission of the copyright holder, except in the case of brief quotations embodied in critical reviews and certain other noncommercial uses permitted by copyright law.
This project analyzes publicly available government meeting recordings for civic transparency and housing policy research.
