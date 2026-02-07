# Housing Sentiment and Policy Monitor

AI-powered pipeline for ingesting public city council meetings and extracting affordable housing policy intelligence. Automatically transcribes, analyzes, and structures housing discussions from YouTube and Granicus to track zoning changes, funding commitments, community sentiment, and regulatory trends across Colorado jurisdictions.

## Features

- **Meeting Discovery** – Scrape YouTube channels and Granicus portals for city council, planning commission, and housing committee recordings
- **Audio Download** – Extract audio via yt-dlp with configurable quality
- **Transcription** – Convert speech to text with speaker diarization (Deepgram nova-2)
- **AI Analysis** – Extract structured housing policy data using Claude (Anthropic API)
- **Data Storage** – JSON database + markdown summaries for every meeting
- **Analytics** – Query tools for policy tracking, sentiment trends, funding commitments, and jurisdiction comparison

## Configured Cities

| City | YouTube | Granicus |
|------|---------|----------|
| Denver | @DenverCityCouncil | denver.granicus.com |
| Aurora | @CityofAuroraCO | aurora.granicus.com |
| Lakewood | @CityofLakewood | lakewood.granicus.com |
| Boulder | — | boulder.granicus.com |

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
  "processed": true
}
```

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
├── HousingAnalyzer            #   Claude API analysis
├── MeetingPipeline            #   Orchestrator
└── MeetingDatabase            #   JSON-backed storage
granicus_discovery.py          # Granicus platform integration
example_analysis.py            # Analytics and reporting tools
setup.py                       # Environment verification
```

## Configuration

All configuration lives in `config.py`:

- **COLORADO_CITIES** – Add/remove jurisdictions with their YouTube and Granicus details
- **MEETING_TITLE_KEYWORDS** – Patterns for identifying meeting videos
- **HOUSING_KEYWORDS** – Terms for relevance scoring
- **ANALYSIS_PROMPT_TEMPLATE** – Claude prompt for policy extraction
- **API_CONFIG** – Deepgram and Anthropic settings
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

This project analyzes publicly available government meeting recordings for civic transparency and housing policy research.
