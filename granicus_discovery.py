#!/usr/bin/env python3
"""
Granicus Platform Integration
==============================
Discover and download meeting recordings and agendas from Granicus-hosted
city government portals.

Granicus is used by many Colorado municipalities (Boulder, etc.) that may
not publish all meetings on YouTube.

Usage:
    python granicus_discovery.py --city Boulder
    python granicus_discovery.py --city Boulder --download-agendas
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from config import (
    AUDIO_DIR,
    COLORADO_CITIES,
    DATA_DIR,
    MEETING_TITLE_KEYWORDS,
    PROCESSING,
)
from meeting_ingestion_pipeline import Meeting, MeetingDatabase

log = logging.getLogger("granicus")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# ---------------------------------------------------------------------------
# Granicus clip metadata
# ---------------------------------------------------------------------------

@dataclass
class GranicusClip:
    clip_id: str
    title: str
    date: str
    duration: int  # seconds
    video_url: str
    agenda_url: str
    minutes_url: str
    body_name: str  # e.g. "City Council"


# ---------------------------------------------------------------------------
# Granicus Discovery
# ---------------------------------------------------------------------------

class GranicusDiscovery:
    """
    Scrape meeting metadata from Granicus sites.

    Granicus sites typically expose JSON/XML feeds or HTML pages listing clips
    for each legislative body. This class handles the common patterns.
    """

    # Granicus JSON API endpoint pattern
    CLIPS_API = "https://{site}/api/clips"
    BODIES_API = "https://{site}/api/bodies"
    CLIP_DETAIL = "https://{site}/player/clip/{clip_id}"

    def __init__(self, site: str, jurisdiction: str):
        self.site = site
        self.jurisdiction = jurisdiction
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "CivicHousingIntelligence/1.0 (housing policy research)",
            "Accept": "application/json",
        })

    def discover_meetings(
        self, max_clips: int = 50, body_filter: list[str] | None = None,
    ) -> list[Meeting]:
        """
        Discover meetings from the Granicus site.
        Returns Meeting objects compatible with the main pipeline.
        """
        log.info("Discovering Granicus meetings for %s (%s)", self.jurisdiction, self.site)
        clips = self._fetch_clips(max_clips)
        if body_filter:
            clips = [c for c in clips if self._matches_body(c, body_filter)]

        meetings: list[Meeting] = []
        for clip in clips:
            if not self._is_relevant_meeting(clip):
                continue

            meeting = Meeting(
                id=f"granicus_{clip.clip_id}",
                jurisdiction=self.jurisdiction,
                title=clip.title,
                date=clip.date,
                video_url=clip.video_url,
                source="granicus",
                duration_minutes=round(clip.duration / 60, 1) if clip.duration else 0,
            )
            meetings.append(meeting)
            log.info("  Found: %s (%s)", clip.title[:70], clip.date)

        log.info("Discovered %d relevant Granicus meetings", len(meetings))
        return meetings

    def get_clip_video_url(self, clip_id: str) -> str:
        """Extract the direct video download URL for a clip."""
        detail_url = self.CLIP_DETAIL.format(site=self.site, clip_id=clip_id)
        try:
            resp = self.session.get(detail_url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.error("Failed to fetch clip detail %s: %s", clip_id, exc)
            return ""

        # Look for video source URL in page content
        # Granicus pages typically embed an MP4 or streaming URL
        patterns = [
            re.compile(r'"(https?://[^"]+\.mp4[^"]*)"'),
            re.compile(r'"(https?://stream\.granicus\.com/[^"]+)"'),
            re.compile(r'source\s+src="(https?://[^"]+)"'),
            re.compile(r'"mediaUrl"\s*:\s*"(https?://[^"]+)"'),
        ]
        for pat in patterns:
            match = pat.search(resp.text)
            if match:
                return match.group(1)

        log.warning("Could not extract video URL for clip %s", clip_id)
        return detail_url  # fallback to player page

    def download_agenda(self, clip: GranicusClip, output_dir: Path | None = None) -> str:
        """Download the agenda PDF for a clip. Returns path to downloaded file."""
        if not clip.agenda_url:
            return ""

        output_dir = output_dir or (DATA_DIR / "agendas")
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{clip.clip_id}_agenda.pdf"
        output_path = output_dir / filename

        if output_path.exists():
            log.info("Agenda already downloaded: %s", filename)
            return str(output_path)

        log.info("Downloading agenda: %s", clip.agenda_url[:80])
        try:
            resp = self.session.get(clip.agenda_url, timeout=60)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            log.info("Agenda saved: %s (%.1f KB)", filename, len(resp.content) / 1024)
            return str(output_path)
        except requests.RequestException as exc:
            log.error("Failed to download agenda: %s", exc)
            return ""

    def download_minutes(self, clip: GranicusClip, output_dir: Path | None = None) -> str:
        """Download the minutes PDF for a clip."""
        if not clip.minutes_url:
            return ""

        output_dir = output_dir or (DATA_DIR / "minutes")
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{clip.clip_id}_minutes.pdf"
        output_path = output_dir / filename

        if output_path.exists():
            return str(output_path)

        try:
            resp = self.session.get(clip.minutes_url, timeout=60)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            return str(output_path)
        except requests.RequestException as exc:
            log.error("Failed to download minutes: %s", exc)
            return ""

    # ---- internal ----------------------------------------------------------

    def _fetch_clips(self, max_clips: int) -> list[GranicusClip]:
        """Fetch clip listings from the Granicus API or scrape HTML."""
        clips: list[GranicusClip] = []

        # Try JSON API first
        api_url = self.CLIPS_API.format(site=self.site)
        try:
            resp = self.session.get(api_url, timeout=30)
            if resp.status_code == 200:
                return self._parse_api_response(resp.json(), max_clips)
        except requests.RequestException:
            pass

        # Fallback: scrape the HTML listing page
        listing_url = f"https://{self.site}/ViewPublisher.php?view_id=1"
        try:
            resp = self.session.get(listing_url, timeout=30)
            resp.raise_for_status()
            clips = self._parse_html_listing(resp.text, max_clips)
        except requests.RequestException as exc:
            log.error("Failed to fetch Granicus listing: %s", exc)

        return clips

    def _parse_api_response(
        self, data: list[dict[str, Any]] | dict[str, Any], max_clips: int,
    ) -> list[GranicusClip]:
        """Parse the Granicus JSON API response."""
        if isinstance(data, dict):
            data = data.get("clips", data.get("results", []))

        clips: list[GranicusClip] = []
        for item in data[:max_clips]:
            clip_id = str(item.get("id", item.get("clip_id", "")))
            if not clip_id:
                continue

            # Extract date
            date_str = item.get("date", item.get("start_date", ""))
            if date_str and "T" in date_str:
                date_str = date_str.split("T")[0]

            # Build video URL
            video_url = item.get("video_url", "")
            if not video_url:
                video_url = self.CLIP_DETAIL.format(site=self.site, clip_id=clip_id)

            clips.append(GranicusClip(
                clip_id=clip_id,
                title=item.get("title", item.get("name", "Untitled")),
                date=date_str,
                duration=item.get("duration", 0),
                video_url=video_url,
                agenda_url=item.get("agenda_url", item.get("agenda", "")),
                minutes_url=item.get("minutes_url", item.get("minutes", "")),
                body_name=item.get("body_name", item.get("body", "")),
            ))
        return clips

    def _parse_html_listing(self, html: str, max_clips: int) -> list[GranicusClip]:
        """Scrape clip info from Granicus HTML listing pages."""
        clips: list[GranicusClip] = []

        # Pattern for clip links: /player/clip/XXXX
        clip_pattern = re.compile(
            r'/player/clip/(\d+)["\'].*?'
            r'(?:title|alt|>)\s*["\']?\s*([^"\'<]+)',
            re.DOTALL | re.IGNORECASE,
        )

        # Simpler fallback: just find clip IDs
        id_pattern = re.compile(r'clip[_/](\d+)', re.IGNORECASE)

        for match in clip_pattern.finditer(html):
            if len(clips) >= max_clips:
                break
            clip_id = match.group(1)
            title = match.group(2).strip()
            clips.append(GranicusClip(
                clip_id=clip_id,
                title=title,
                date="",
                duration=0,
                video_url=self.CLIP_DETAIL.format(site=self.site, clip_id=clip_id),
                agenda_url="",
                minutes_url="",
                body_name="",
            ))

        if not clips:
            # Fallback: just grab clip IDs
            seen: set[str] = set()
            for match in id_pattern.finditer(html):
                clip_id = match.group(1)
                if clip_id in seen:
                    continue
                seen.add(clip_id)
                if len(clips) >= max_clips:
                    break
                clips.append(GranicusClip(
                    clip_id=clip_id,
                    title=f"Meeting (Clip {clip_id})",
                    date="",
                    duration=0,
                    video_url=self.CLIP_DETAIL.format(site=self.site, clip_id=clip_id),
                    agenda_url="",
                    minutes_url="",
                    body_name="",
                ))

        return clips

    @staticmethod
    def _is_relevant_meeting(clip: GranicusClip) -> bool:
        """Check if clip title suggests it's a government meeting."""
        lower = clip.title.lower()
        # Accept all clips from Granicus since they're all government meetings,
        # but filter out test clips or obviously irrelevant content
        ignore_terms = ["test", "training", "demo", "sample"]
        if any(t in lower for t in ignore_terms):
            return False
        return True

    @staticmethod
    def _matches_body(clip: GranicusClip, bodies: list[str]) -> bool:
        """Check if clip matches a legislative body filter."""
        if not clip.body_name:
            return True  # if unknown, include it
        lower = clip.body_name.lower()
        return any(b.lower() in lower for b in bodies)


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def discover_all_granicus(cities: list[str] | None = None) -> dict[str, list[Meeting]]:
    """Run Granicus discovery for all configured cities that have Granicus sites."""
    db = MeetingDatabase()
    results: dict[str, list[Meeting]] = {}

    for city_name, city_config in COLORADO_CITIES.items():
        if cities and city_name not in cities:
            continue

        granicus_site = city_config.get("granicus_site")
        if not granicus_site:
            continue

        body_filter = city_config.get("meeting_bodies")
        discovery = GranicusDiscovery(granicus_site, city_name)

        meetings = discovery.discover_meetings(
            max_clips=PROCESSING["youtube_max_videos"],
            body_filter=body_filter,
        )

        new_count = 0
        for meeting in meetings:
            if not db.get(meeting.id):
                db.upsert(meeting)
                new_count += 1

        results[city_name] = meetings
        log.info("%s: %d meetings found, %d new", city_name, len(meetings), new_count)

        # Rate limit between cities
        time.sleep(2)

    return results


def download_agendas_for_city(city_name: str) -> list[str]:
    """Download all available agendas for a city's Granicus meetings."""
    city_config = COLORADO_CITIES.get(city_name)
    if not city_config:
        log.error("Unknown city: %s", city_name)
        return []

    granicus_site = city_config.get("granicus_site")
    if not granicus_site:
        log.error("No Granicus site configured for %s", city_name)
        return []

    discovery = GranicusDiscovery(granicus_site, city_name)
    clips = discovery._fetch_clips(PROCESSING["youtube_max_videos"])

    downloaded: list[str] = []
    for clip in clips:
        if clip.agenda_url:
            path = discovery.download_agenda(clip)
            if path:
                downloaded.append(path)
            time.sleep(1)  # polite rate limiting

    log.info("Downloaded %d agendas for %s", len(downloaded), city_name)
    return downloaded


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Granicus Meeting Discovery",
    )
    parser.add_argument(
        "--city", type=str, nargs="+",
        help="City/cities to discover (default: all with Granicus sites)",
    )
    parser.add_argument(
        "--download-agendas", action="store_true",
        help="Download agenda PDFs",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Max clips per city (default: 50)",
    )
    args = parser.parse_args()

    if args.download_agendas and args.city:
        for city in args.city:
            download_agendas_for_city(city)
    else:
        discover_all_granicus(cities=args.city)


if __name__ == "__main__":
    main()
