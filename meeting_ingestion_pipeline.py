#!/usr/bin/env python3
"""
Civic Housing Intelligence – Meeting Ingestion Pipeline
========================================================
Discovers, downloads, transcribes, and analyzes public city council meetings
from Colorado cities to extract structured affordable housing policy data.

Usage:
    python meeting_ingestion_pipeline.py                  # process all cities
    python meeting_ingestion_pipeline.py --city Denver    # single city
    python meeting_ingestion_pipeline.py --limit 5        # cap per city
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from config import (
    ANALYSIS_DIR,
    ANALYSIS_PROMPT_TEMPLATE,
    API_CONFIG,
    AUDIO_DIR,
    COLORADO_CITIES,
    DATA_DIR,
    HOUSING_KEYWORDS,
    MEETING_TITLE_KEYWORDS,
    MEETINGS_DB,
    PROCESSING,
    TRANSCRIPT_DIR,
    get_api_key,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("pipeline")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Meeting:
    id: str
    jurisdiction: str
    title: str
    date: str  # ISO format YYYY-MM-DD
    video_url: str
    source: str  # "youtube" | "granicus"
    duration_minutes: float = 0.0
    audio_path: str = ""
    transcript_path: str = ""
    analysis_path: str = ""
    summary_path: str = ""
    housing_mentions: int = 0
    housing_relevance_score: float = 0.0
    processed: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Meeting":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Meeting database (JSON-backed)
# ---------------------------------------------------------------------------

class MeetingDatabase:
    """Simple JSON file-backed database of meeting records."""

    def __init__(self, db_path: Path = MEETINGS_DB):
        self.db_path = db_path
        self._ensure_dirs()
        self.meetings: dict[str, Meeting] = self._load()

    def _ensure_dirs(self) -> None:
        for d in (DATA_DIR, AUDIO_DIR, TRANSCRIPT_DIR, ANALYSIS_DIR):
            d.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Meeting]:
        if self.db_path.exists():
            try:
                raw = json.loads(self.db_path.read_text())
                return {mid: Meeting.from_dict(m) for mid, m in raw.items()}
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("Corrupt meetings DB, starting fresh: %s", exc)
        return {}

    def save(self) -> None:
        data = {mid: m.to_dict() for mid, m in self.meetings.items()}
        self.db_path.write_text(json.dumps(data, indent=2, default=str))

    def upsert(self, meeting: Meeting) -> None:
        self.meetings[meeting.id] = meeting
        self.save()

    def get(self, meeting_id: str) -> Meeting | None:
        return self.meetings.get(meeting_id)

    def list_by_jurisdiction(self, jurisdiction: str) -> list[Meeting]:
        return [m for m in self.meetings.values() if m.jurisdiction == jurisdiction]

    def list_unprocessed(self) -> list[Meeting]:
        return [m for m in self.meetings.values() if not m.processed]


# ---------------------------------------------------------------------------
# 1. Meeting Discovery (YouTube)
# ---------------------------------------------------------------------------

class MeetingDiscovery:
    """Discover meetings from YouTube channels using yt-dlp."""

    DATE_PATTERNS = [
        # "January 15, 2025" or "Jan 15, 2025"
        re.compile(
            r"(?P<month>\w+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})"
        ),
        # "01/15/2025" or "1-15-2025"
        re.compile(
            r"(?P<month>\d{1,2})[/\-](?P<day>\d{1,2})[/\-](?P<year>\d{4})"
        ),
        # "2025-01-15"
        re.compile(
            r"(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})"
        ),
    ]

    MONTH_MAP = {
        "january": 1, "february": 2, "march": 3, "april": 4,
        "may": 5, "june": 6, "july": 7, "august": 8,
        "september": 9, "october": 10, "november": 11, "december": 12,
        "jan": 1, "feb": 2, "mar": 3, "apr": 4,
        "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
        "oct": 10, "nov": 11, "dec": 12,
    }

    def discover_from_youtube(
        self, jurisdiction: str, channel_url: str, max_videos: int | None = None,
    ) -> list[Meeting]:
        """Scrape a YouTube channel for meeting videos."""
        max_videos = max_videos or PROCESSING["youtube_max_videos"]
        log.info("Discovering meetings for %s from %s", jurisdiction, channel_url)

        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            "--playlist-end", str(max_videos),
            channel_url,
        ]
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )
        except FileNotFoundError:
            log.error("yt-dlp not found. Install with: pip install yt-dlp")
            return []
        except subprocess.TimeoutExpired:
            log.error("yt-dlp timed out for %s", channel_url)
            return []

        if result.returncode != 0:
            log.error("yt-dlp failed for %s: %s", channel_url, result.stderr[:500])
            return []

        meetings: list[Meeting] = []
        for line in result.stdout.strip().splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            title = entry.get("title", "")
            video_id = entry.get("id", "")
            if not video_id:
                continue

            if not self._is_meeting_title(title):
                continue

            date_str = self._extract_date(title) or entry.get("upload_date", "")
            if date_str and len(date_str) == 8 and date_str.isdigit():
                date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

            duration = entry.get("duration") or 0
            meetings.append(
                Meeting(
                    id=video_id,
                    jurisdiction=jurisdiction,
                    title=title,
                    date=date_str,
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    source="youtube",
                    duration_minutes=round(duration / 60, 1) if duration else 0.0,
                )
            )
            log.info("  Found: %s (%s)", title[:80], date_str)

        log.info("Discovered %d meetings for %s", len(meetings), jurisdiction)
        return meetings

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _is_meeting_title(title: str) -> bool:
        lower = title.lower()
        return any(kw in lower for kw in MEETING_TITLE_KEYWORDS)

    def _extract_date(self, title: str) -> str:
        for pattern in self.DATE_PATTERNS:
            m = pattern.search(title)
            if m:
                parts = m.groupdict()
                try:
                    month = parts["month"]
                    if month.isdigit():
                        month_int = int(month)
                    else:
                        month_int = self.MONTH_MAP.get(month.lower(), 0)
                    if month_int == 0:
                        continue
                    day_int = int(parts["day"])
                    year_int = int(parts["year"])
                    return f"{year_int:04d}-{month_int:02d}-{day_int:02d}"
                except (ValueError, KeyError):
                    continue
        return ""


# ---------------------------------------------------------------------------
# 2. Video / Audio Processor
# ---------------------------------------------------------------------------

class VideoProcessor:
    """Download audio from video URLs using yt-dlp."""

    def download_audio(self, meeting: Meeting) -> str:
        """Download audio as MP3. Returns path to the audio file."""
        output_path = AUDIO_DIR / f"{meeting.id}.{PROCESSING['audio_format']}"
        if output_path.exists():
            log.info("Audio already exists: %s", output_path.name)
            return str(output_path)

        log.info("Downloading audio: %s", meeting.title[:60])
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", PROCESSING["audio_format"],
            "--audio-quality", PROCESSING["audio_quality"],
            "-o", str(AUDIO_DIR / f"{meeting.id}.%(ext)s"),
            "--no-playlist",
            meeting.video_url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except FileNotFoundError:
            log.error("yt-dlp not found")
            return ""
        except subprocess.TimeoutExpired:
            log.error("Audio download timed out for %s", meeting.id)
            return ""

        if result.returncode != 0:
            log.error("Download failed for %s: %s", meeting.id, result.stderr[:500])
            return ""

        if output_path.exists():
            log.info("Audio saved: %s", output_path.name)
            return str(output_path)

        # yt-dlp may have written with a slightly different extension
        candidates = list(AUDIO_DIR.glob(f"{meeting.id}.*"))
        if candidates:
            log.info("Audio saved (alt ext): %s", candidates[0].name)
            return str(candidates[0])

        log.error("Audio file not found after download for %s", meeting.id)
        return ""


# ---------------------------------------------------------------------------
# 3. Transcription Service (Deepgram)
# ---------------------------------------------------------------------------

class TranscriptionService:
    """Send audio to Deepgram for transcription with speaker diarization."""

    def __init__(self) -> None:
        self.api_key = get_api_key("deepgram")
        self.config = API_CONFIG["deepgram"]

    def is_available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio_path: str, meeting: Meeting) -> str:
        """Transcribe audio file. Returns path to transcript JSON."""
        if not self.api_key:
            log.error("Deepgram API key not set (DEEPGRAM_API_KEY)")
            return ""

        transcript_path = TRANSCRIPT_DIR / f"{meeting.id}_transcript.json"
        if transcript_path.exists():
            log.info("Transcript already exists: %s", transcript_path.name)
            return str(transcript_path)

        log.info("Transcribing: %s", meeting.title[:60])

        # Build query params from config options
        params = {k: str(v).lower() if isinstance(v, bool) else v
                  for k, v in self.config["options"].items()}

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Content-Type": "audio/mpeg",
        }

        file_size = os.path.getsize(audio_path)
        log.info("Uploading %.1f MB to Deepgram...", file_size / (1024 * 1024))

        try:
            with open(audio_path, "rb") as f:
                response = requests.post(
                    self.config["base_url"],
                    headers=headers,
                    params=params,
                    data=f,
                    timeout=600,
                )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.error("Deepgram API error: %s", exc)
            return ""

        result = response.json()
        transcript_path.write_text(json.dumps(result, indent=2))
        log.info("Transcript saved: %s", transcript_path.name)
        return str(transcript_path)

    @staticmethod
    def format_transcript(transcript_path: str) -> str:
        """Convert Deepgram JSON into a readable text transcript with speaker labels."""
        try:
            data = json.loads(Path(transcript_path).read_text())
        except (json.JSONDecodeError, FileNotFoundError) as exc:
            log.error("Cannot read transcript %s: %s", transcript_path, exc)
            return ""

        # Try utterances first (best with diarization)
        utterances = (
            data.get("results", {}).get("utterances")
            or data.get("utterances")
        )
        if utterances:
            lines = []
            for u in utterances:
                speaker = u.get("speaker", "?")
                text = u.get("transcript", "").strip()
                if text:
                    lines.append(f"[Speaker {speaker}]: {text}")
            return "\n\n".join(lines)

        # Fallback: paragraphs → channels → alternatives
        paragraphs = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("paragraphs", {})
            .get("paragraphs", [])
        )
        if paragraphs:
            lines = []
            for para in paragraphs:
                speaker = para.get("speaker", "?")
                sentences = " ".join(
                    s.get("text", "") for s in para.get("sentences", [])
                )
                if sentences.strip():
                    lines.append(f"[Speaker {speaker}]: {sentences.strip()}")
            return "\n\n".join(lines)

        # Last resort: plain transcript text
        transcript_text = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        )
        return transcript_text


# ---------------------------------------------------------------------------
# 4. Housing Analyzer (Claude API)
# ---------------------------------------------------------------------------

class HousingAnalyzer:
    """Use Claude to extract structured housing policy insights."""

    def __init__(self) -> None:
        self.api_key = get_api_key("anthropic")
        self.config = API_CONFIG["anthropic"]

    def is_available(self) -> bool:
        return bool(self.api_key)

    def analyze(self, transcript_text: str, meeting: Meeting) -> dict[str, Any]:
        """Analyze a transcript and return structured housing data."""
        if not self.api_key:
            log.error("Anthropic API key not set (ANTHROPIC_API_KEY)")
            return {}

        analysis_json_path = ANALYSIS_DIR / f"{meeting.id}_analysis.json"
        summary_md_path = ANALYSIS_DIR / f"{meeting.id}_summary.md"

        if analysis_json_path.exists():
            log.info("Analysis already exists: %s", analysis_json_path.name)
            try:
                return json.loads(analysis_json_path.read_text())
            except json.JSONDecodeError:
                pass  # re-analyze

        # Truncate transcript if too long
        max_chars = self.config["max_transcript_chars"]
        if len(transcript_text) > max_chars:
            log.info(
                "Truncating transcript from %d to %d chars",
                len(transcript_text), max_chars,
            )
            transcript_text = transcript_text[:max_chars] + "\n\n[TRANSCRIPT TRUNCATED]"

        prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            jurisdiction=meeting.jurisdiction,
            title=meeting.title,
            date=meeting.date,
            transcript=transcript_text,
        )

        log.info("Analyzing with Claude: %s", meeting.title[:60])

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.config["model"],
            "max_tokens": self.config["max_tokens"],
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            log.error("Claude API error: %s", exc)
            return {}

        result = response.json()
        raw_text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                raw_text += block["text"]

        analysis = self._extract_json(raw_text)
        if not analysis:
            log.warning("Could not parse JSON from Claude response for %s", meeting.id)
            analysis = {"raw_response": raw_text, "parse_error": True}

        # Save analysis JSON
        analysis_json_path.write_text(json.dumps(analysis, indent=2, default=str))
        log.info("Analysis saved: %s", analysis_json_path.name)

        # Save markdown summary
        summary = self._build_summary(analysis, meeting)
        summary_md_path.write_text(summary)
        log.info("Summary saved: %s", summary_md_path.name)

        return analysis

    def count_housing_mentions(self, text: str) -> int:
        """Count occurrences of housing keywords in text."""
        lower = text.lower()
        return sum(1 for kw in HOUSING_KEYWORDS if kw.lower() in lower)

    # ---- helpers -----------------------------------------------------------

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        """Pull a JSON object out of Claude's response text."""
        # Try to find a JSON block in markdown fences
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if fence_match:
            try:
                return json.loads(fence_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try the whole text as JSON
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find the outermost { ... }
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return {}

    @staticmethod
    def _build_summary(analysis: dict[str, Any], meeting: Meeting) -> str:
        """Generate a markdown summary from analysis data."""
        lines = [
            f"# Meeting Summary: {meeting.title}",
            f"**Jurisdiction:** {meeting.jurisdiction}",
            f"**Date:** {meeting.date}",
            f"**Source:** {meeting.video_url}",
            "",
        ]

        summary_text = analysis.get("summary", "")
        if summary_text:
            lines.append("## Executive Summary")
            lines.append(summary_text)
            lines.append("")

        topics = analysis.get("housing_topics", [])
        if topics:
            lines.append("## Housing Topics")
            for t in topics:
                lines.append(f"- {t}")
            lines.append("")

        proposals = analysis.get("policy_proposals", [])
        if proposals:
            lines.append("## Policy Proposals")
            for p in proposals:
                status = p.get("status", "unknown")
                ptype = p.get("type", "proposal")
                desc = p.get("description", "")
                vote = p.get("vote_result")
                vote_str = f" (Vote: {vote})" if vote else ""
                lines.append(f"- **[{ptype.upper()}]** {desc} — *{status}*{vote_str}")
            lines.append("")

        projects = analysis.get("projects", [])
        if projects:
            lines.append("## Projects")
            for proj in projects:
                name = proj.get("name") or proj.get("address", "Unknown")
                units = proj.get("units_total")
                affordable = proj.get("units_affordable")
                level = proj.get("affordability_level", "")
                unit_str = f" | {units} units" if units else ""
                aff_str = f" ({affordable} affordable @ {level})" if affordable else ""
                lines.append(f"- **{name}**{unit_str}{aff_str}")
            lines.append("")

        funding = analysis.get("funding", [])
        if funding:
            lines.append("## Funding")
            for f_item in funding:
                amt = f_item.get("amount", "?")
                src = f_item.get("source", "?")
                purpose = f_item.get("purpose", "")
                lines.append(f"- **{amt}** from {src} — {purpose}")
            lines.append("")

        sentiment = analysis.get("sentiment", {})
        if sentiment:
            lines.append("## Sentiment")
            lines.append(f"**Overall:** {sentiment.get('overall', 'N/A')}")
            details = sentiment.get("details", "")
            if details:
                lines.append(f"\n{details}")
            pub = sentiment.get("public_comment_summary", "")
            if pub:
                lines.append(f"\n**Public Comment:** {pub}")
            lines.append("")

        actions = analysis.get("actions", [])
        if actions:
            lines.append("## Actions & Next Steps")
            for a in actions:
                lines.append(f"- {a}")
            lines.append("")

        quotes = analysis.get("quotes", [])
        if quotes:
            lines.append("## Notable Quotes")
            for q in quotes:
                speaker = q.get("speaker", "Unknown")
                quote = q.get("quote", "")
                ctx = q.get("context", "")
                lines.append(f'> "{quote}" — *{speaker}*')
                if ctx:
                    lines.append(f"> _{ctx}_")
                lines.append("")

        score = analysis.get("housing_relevance_score", 0)
        lines.append(f"---\n*Housing Relevance Score: {score}*")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Pipeline Orchestrator
# ---------------------------------------------------------------------------

class MeetingPipeline:
    """End-to-end pipeline: discover → download → transcribe → analyze."""

    def __init__(self) -> None:
        self.db = MeetingDatabase()
        self.discovery = MeetingDiscovery()
        self.video = VideoProcessor()
        self.transcription = TranscriptionService()
        self.analyzer = HousingAnalyzer()

    def run(
        self,
        cities: list[str] | None = None,
        limit_per_city: int | None = None,
        skip_discovery: bool = False,
    ) -> None:
        """Run the full pipeline."""
        cities = cities or list(COLORADO_CITIES.keys())
        limit = limit_per_city or PROCESSING["max_meetings_per_run"]

        log.info("=" * 60)
        log.info("Civic Housing Intelligence Pipeline")
        log.info("Cities: %s", ", ".join(cities))
        log.info("=" * 60)

        # ---------- Phase 1: Discovery ----------
        if not skip_discovery:
            self._phase_discovery(cities, limit)

        # ---------- Phase 2-4: Process unprocessed meetings ----------
        unprocessed = self.db.list_unprocessed()
        if cities:
            unprocessed = [m for m in unprocessed if m.jurisdiction in cities]
        total = len(unprocessed)
        log.info("Meetings to process: %d", total)

        for idx, meeting in enumerate(unprocessed, 1):
            log.info("-" * 50)
            log.info("[%d/%d] %s – %s", idx, total, meeting.jurisdiction, meeting.title[:60])

            try:
                self._process_meeting(meeting)
            except Exception:
                log.exception("Unexpected error processing %s", meeting.id)
                meeting.error = "unexpected_error"
                self.db.upsert(meeting)

            # Rate limit between meetings
            if idx < total:
                log.info("Rate limiting: %ds pause", PROCESSING["rate_limit_seconds"])
                time.sleep(PROCESSING["rate_limit_seconds"])

        # ---------- Summary ----------
        self._print_summary(cities)

    def _phase_discovery(self, cities: list[str], limit: int) -> None:
        log.info("--- Phase 1: Discovery ---")
        for city in cities:
            city_config = COLORADO_CITIES.get(city)
            if not city_config:
                log.warning("Unknown city: %s", city)
                continue

            yt_url = city_config.get("youtube_url")
            if yt_url:
                meetings = self.discovery.discover_from_youtube(city, yt_url, limit)
                for m in meetings:
                    if not self.db.get(m.id):
                        self.db.upsert(m)
                        log.info("  Added: %s", m.title[:60])
                    else:
                        log.debug("  Already known: %s", m.id)

    def _process_meeting(self, meeting: Meeting) -> None:
        # Step 1: Download audio
        audio_path = self.video.download_audio(meeting)
        if not audio_path:
            meeting.error = "download_failed"
            self.db.upsert(meeting)
            return
        meeting.audio_path = audio_path

        # Step 2: Transcribe
        if self.transcription.is_available():
            transcript_path = self.transcription.transcribe(audio_path, meeting)
            if not transcript_path:
                meeting.error = "transcription_failed"
                self.db.upsert(meeting)
                return
            meeting.transcript_path = transcript_path

            # Format transcript for analysis
            transcript_text = TranscriptionService.format_transcript(transcript_path)
        else:
            log.warning("Deepgram not available, skipping transcription")
            meeting.error = "no_deepgram_key"
            self.db.upsert(meeting)
            return

        if not transcript_text:
            meeting.error = "empty_transcript"
            self.db.upsert(meeting)
            return

        # Count housing mentions
        meeting.housing_mentions = self.analyzer.count_housing_mentions(transcript_text)

        # Step 3: Analyze with Claude
        if self.analyzer.is_available():
            analysis = self.analyzer.analyze(transcript_text, meeting)
            if analysis:
                meeting.analysis_path = str(
                    ANALYSIS_DIR / f"{meeting.id}_analysis.json"
                )
                meeting.summary_path = str(
                    ANALYSIS_DIR / f"{meeting.id}_summary.md"
                )
                meeting.housing_relevance_score = analysis.get(
                    "housing_relevance_score", 0.0
                )
                meeting.processed = True
                meeting.error = ""
            else:
                meeting.error = "analysis_failed"
        else:
            log.warning("Anthropic not available, skipping analysis")
            meeting.error = "no_anthropic_key"

        self.db.upsert(meeting)

    def _print_summary(self, cities: list[str]) -> None:
        log.info("=" * 60)
        log.info("Pipeline Summary")
        log.info("=" * 60)
        for city in cities:
            meetings = self.db.list_by_jurisdiction(city)
            processed = [m for m in meetings if m.processed]
            high_relevance = [m for m in processed if m.housing_relevance_score >= 0.5]
            log.info(
                "  %s: %d discovered, %d processed, %d high-relevance",
                city, len(meetings), len(processed), len(high_relevance),
            )
        total = len(self.db.meetings)
        total_processed = sum(1 for m in self.db.meetings.values() if m.processed)
        log.info("  TOTAL: %d meetings, %d processed", total, total_processed)
        log.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Civic Housing Intelligence – Meeting Ingestion Pipeline",
    )
    parser.add_argument(
        "--city", type=str, nargs="+",
        help="City/cities to process (default: all configured)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max meetings to discover per city",
    )
    parser.add_argument(
        "--skip-discovery", action="store_true",
        help="Skip YouTube discovery phase, process existing only",
    )
    args = parser.parse_args()

    pipeline = MeetingPipeline()
    pipeline.run(
        cities=args.city,
        limit_per_city=args.limit,
        skip_discovery=args.skip_discovery,
    )


if __name__ == "__main__":
    main()
