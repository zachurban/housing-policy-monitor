"""
Centralized configuration for the Civic Housing Intelligence pipeline.

All cities, keywords, prompt templates, and API settings live here.
Add new jurisdictions by extending COLORADO_CITIES without touching pipeline code.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "meetings_data"
AUDIO_DIR = DATA_DIR / "audio"
TRANSCRIPT_DIR = DATA_DIR / "transcripts"
ANALYSIS_DIR = DATA_DIR / "analysis"
MEETINGS_DB = DATA_DIR / "meetings.json"

# ---------------------------------------------------------------------------
# Colorado cities – YouTube channels & Granicus site IDs
# ---------------------------------------------------------------------------
COLORADO_CITIES = {
    "Denver": {
        "youtube_channel": "@Denver8TV",
        "youtube_url": "https://www.youtube.com/@Denver8TV/videos",
        "granicus_site": "denver.granicus.com",
        "granicus_clip_id_prefix": "denver",
        "meeting_bodies": [
            "City Council",
            "Planning Board",
            "Land Use, Transportation & Infrastructure Committee",
        ],
    },
    "Aurora": {
        "youtube_channel": "@theaurorachannel",
        "youtube_url": "https://www.youtube.com/@theaurorachannel/videos",
        "granicus_site": "aurora.granicus.com",
        "granicus_clip_id_prefix": "aurora",
        "meeting_bodies": [
            "City Council",
            "Planning Commission",
            "Housing and Managed Care Committee",
        ],
    },
    "Lakewood": {
        "youtube_channel": "@LakewoodCOgov",
        "youtube_url": "https://www.youtube.com/@LakewoodCOgov/videos",
        "granicus_site": "lakewood.granicus.com",
        "granicus_clip_id_prefix": "lakewood",
        "meeting_bodies": [
            "City Council",
            "Planning Commission",
        ],
    },
    "Boulder": {
        "youtube_channel": "@CityofBoulderGov",
        "youtube_url": "https://www.youtube.com/@CityofBoulderGov",
        "granicus_site": "boulder.granicus.com",
        "granicus_site_id": 5,
        "granicus_clip_id_prefix": "boulder",
        "meeting_bodies": [
            "City Council",
            "Planning Board",
            "Housing Advisory Board",
        ],
    },
}

# ---------------------------------------------------------------------------
# Meeting title patterns (for YouTube filtering)
# ---------------------------------------------------------------------------
MEETING_TITLE_KEYWORDS = [
    "city council",
    "council meeting",
    "planning commission",
    "planning board",
    "public hearing",
    "land use",
    "zoning",
    "housing committee",
    "housing authority",
    "study session",
    "work session",
    "committee of the whole",
    "budget hearing",
]

# ---------------------------------------------------------------------------
# Housing-specific keywords (for relevance scoring)
# ---------------------------------------------------------------------------
HOUSING_KEYWORDS = [
    # Zoning & land use
    "inclusionary zoning",
    "density bonus",
    "rezoning",
    "upzoning",
    "mixed-use",
    "accessory dwelling unit",
    "ADU",
    "transit-oriented development",
    "TOD",
    "single-family zoning",
    "missing middle",
    # Programs & funding
    "housing trust fund",
    "LIHTC",
    "low-income housing tax credit",
    "Section 8",
    "housing choice voucher",
    "CDBG",
    "community development block grant",
    "HOME funds",
    "tax increment financing",
    "TIF",
    "opportunity zone",
    # Affordability metrics
    "area median income",
    "AMI",
    "affordable housing",
    "workforce housing",
    "attainable housing",
    "below market rate",
    "rent stabilization",
    "rent control",
    "just cause eviction",
    # Development
    "housing development",
    "apartment",
    "multifamily",
    "condominium",
    "townhome",
    "subdivision",
    "building permit",
    "site plan",
    # Organizations & entities
    "housing authority",
    "Colorado Housing and Finance Authority",
    "CHFA",
    "HUD",
    "Habitat for Humanity",
    # Homelessness
    "homelessness",
    "unhoused",
    "shelter",
    "supportive housing",
    "permanent supportive housing",
    "navigation center",
]

# ---------------------------------------------------------------------------
# Claude analysis prompt
# ---------------------------------------------------------------------------
ANALYSIS_PROMPT_TEMPLATE = """You are an expert housing policy analyst reviewing a city council meeting transcript.

JURISDICTION: {jurisdiction}
MEETING TITLE: {title}
MEETING DATE: {date}

Analyze the following transcript and extract ALL affordable housing-related information.
Return your analysis as a single JSON object with exactly these keys:

{{
  "housing_topics": ["list of housing topics discussed"],
  "policy_proposals": [
    {{
      "type": "ordinance|resolution|amendment|motion|recommendation",
      "description": "what is being proposed",
      "status": "introduced|discussed|tabled|approved|denied|pending",
      "vote_result": "unanimous|split (X-Y)|voice vote|null"
    }}
  ],
  "sentiment": {{
    "overall": "supportive|opposed|mixed|neutral",
    "details": "brief summary of stakeholder positions",
    "public_comment_summary": "summary of public testimony if any"
  }},
  "projects": [
    {{
      "name": "project name if given",
      "address": "address or location",
      "units_total": null,
      "units_affordable": null,
      "affordability_level": "e.g. 60% AMI",
      "developer": "developer name if mentioned",
      "status": "proposed|under_review|approved|under_construction|completed"
    }}
  ],
  "regulatory_changes": [
    {{
      "type": "zoning|building_code|ordinance|policy",
      "description": "what changed or is proposed to change",
      "impact": "expected impact on housing"
    }}
  ],
  "funding": [
    {{
      "amount": "dollar amount as string",
      "source": "funding source",
      "purpose": "what the money is for",
      "status": "proposed|approved|allocated|disbursed"
    }}
  ],
  "actions": [
    "list of concrete actions taken or scheduled (votes, next steps, deadlines)"
  ],
  "quotes": [
    {{
      "speaker": "speaker name or role",
      "quote": "exact or near-exact quote",
      "context": "brief context for the quote"
    }}
  ],
  "housing_relevance_score": 0.0,
  "summary": "2-3 paragraph executive summary of housing-related content"
}}

IMPORTANT:
- housing_relevance_score should be 0.0-1.0 based on how much of the meeting focused on housing.
- If no housing topics are found, still return the JSON with empty lists and a low score.
- Be precise with dollar amounts and unit counts.
- Distinguish between motions, ordinances, and informal discussion.
- Identify speakers by name when possible, otherwise by role (councilmember, public commenter, staff).

TRANSCRIPT:
{transcript}
"""

# ---------------------------------------------------------------------------
# API configuration
# ---------------------------------------------------------------------------
API_CONFIG = {
    "deepgram": {
        "api_key_env": "DEEPGRAM_API_KEY",
        "model": "nova-2",
        "base_url": "https://api.deepgram.com/v1/listen",
        "options": {
            "model": "nova-2",
            "smart_format": True,
            "punctuate": True,
            "diarize": True,
            "utterances": True,
            "paragraphs": True,
            "language": "en-US",
        },
    },
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 8192,
        "max_transcript_chars": 50000,
    },
}

# ---------------------------------------------------------------------------
# Processing settings
# ---------------------------------------------------------------------------
PROCESSING = {
    "audio_format": "mp3",
    "audio_quality": "128k",
    "rate_limit_seconds": 5,
    "max_concurrent_downloads": 2,
    "max_meetings_per_run": 20,
    "youtube_max_videos": 50,
}

# ---------------------------------------------------------------------------
# Helper to get API keys
# ---------------------------------------------------------------------------

def get_api_key(service: str) -> str | None:
    """Retrieve an API key from the environment."""
    env_var = API_CONFIG[service]["api_key_env"]
    return os.environ.get(env_var)
