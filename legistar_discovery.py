#!/usr/bin/env python3
"""
Legistar Web API Integration
==============================
Discover meetings, agenda items, legislation, and vote records from
Legistar-hosted city council portals.

Legistar provides structured legislative metadata (agendas, votes, bill text,
attachments) via a free public REST API at https://webapi.legistar.com/v1/{client}/.
No API key is required for read access.

Usage:
    python legistar_discovery.py --city Denver --days 90
    python legistar_discovery.py --city Denver --list-bodies
    python legistar_discovery.py --city Denver --event-id 1381438
    python legistar_discovery.py --city Denver --body "Community Planning and Housing"
    python legistar_discovery.py --city Denver --download-agendas --download-minutes
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from config import (
    COLORADO_CITIES,
    DATA_DIR,
    HOUSING_KEYWORDS,
    LEGISTAR_CONFIG,
)
from meeting_ingestion_pipeline import Meeting, MeetingDatabase

log = logging.getLogger("legistar")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Legistar data structures
# ---------------------------------------------------------------------------

@dataclass
class LegistarBody:
    """A legislative body (committee) from Legistar."""
    body_id: int
    name: str
    type_name: str  # e.g. "Committee", "Council"


@dataclass
class LegistarEventItem:
    """An agenda item from a Legistar event."""
    event_item_id: int
    title: str
    action_text: str
    matter_id: int | None
    matter_name: str
    matter_type: str
    matter_status: str
    votes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LegistarEvent:
    """A meeting event from Legistar."""
    event_id: int
    body_id: int
    body_name: str
    date: str  # ISO YYYY-MM-DD
    time: str
    location: str
    video_url: str
    agenda_url: str
    minutes_url: str
    agenda_status: str
    minutes_status: str
    items: list[LegistarEventItem] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Legistar Discovery
# ---------------------------------------------------------------------------

class LegistarDiscovery:
    """
    Discover meetings, agenda items, and legislation from the Legistar Web API.

    The Legistar API is a free public REST API that provides structured
    legislative metadata for cities that use the Legistar platform.
    """

    def __init__(self, client: str, jurisdiction: str):
        self.client = client
        self.jurisdiction = jurisdiction
        self.base_url = f"{LEGISTAR_CONFIG['base_url']}/{client}"
        self.rate_delay = LEGISTAR_CONFIG["rate_limit_delay"]
        self.page_size = LEGISTAR_CONFIG["page_size"]
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "CivicHousingIntelligence/1.0 (housing policy research)",
            "Accept": "application/json",
        })

    # ---- Public API -------------------------------------------------------

    def get_bodies(self) -> list[LegistarBody]:
        """Fetch all legislative bodies (committees) for this client."""
        data = self._api_get("/bodies")
        bodies: list[LegistarBody] = []
        for item in data:
            bodies.append(LegistarBody(
                body_id=item.get("BodyId", 0),
                name=item.get("BodyName", ""),
                type_name=item.get("BodyTypeName", ""),
            ))
        return bodies

    def get_housing_bodies(
        self, body_filter: list[str] | None = None,
    ) -> list[LegistarBody]:
        """Get bodies filtered to housing-relevant ones."""
        all_bodies = self.get_bodies()
        if not body_filter:
            return all_bodies
        lower_filter = [b.lower() for b in body_filter]
        return [
            b for b in all_bodies
            if any(f in b.name.lower() for f in lower_filter)
        ]

    def discover_meetings(
        self,
        days: int | None = None,
        body_filter: list[str] | None = None,
        body_name: str | None = None,
    ) -> list[Meeting]:
        """
        Discover meetings from Legistar events.
        Returns Meeting objects compatible with the main pipeline.
        """
        days = days or LEGISTAR_CONFIG["default_lookback_days"]
        log.info(
            "Discovering Legistar meetings for %s (last %d days)",
            self.jurisdiction, days,
        )

        events = self._fetch_events(days)
        if body_name:
            events = [e for e in events if body_name.lower() in e.body_name.lower()]
        elif body_filter:
            lower_filter = [b.lower() for b in body_filter]
            events = [
                e for e in events
                if any(f in e.body_name.lower() for f in lower_filter)
            ]

        meetings: list[Meeting] = []
        for event in events:
            try:
                event.items = self._fetch_event_items(event.event_id)
            except Exception as exc:
                log.warning(
                    "Failed to fetch items for event %d: %s",
                    event.event_id, exc,
                )

            relevance = self._score_housing_relevance(event)
            meeting = self._event_to_meeting(event, relevance)
            meetings.append(meeting)
            log.info(
                "  Found: %s – %s (relevance=%.2f)",
                event.date, event.body_name[:50], relevance,
            )

        log.info("Discovered %d Legistar meetings", len(meetings))
        return meetings

    def get_event_details(self, event_id: int) -> LegistarEvent | None:
        """Fetch full details for a single event including agenda items."""
        data = self._api_get(f"/events/{event_id}")
        if not data:
            return None

        event = self._parse_event(data)
        event.items = self._fetch_event_items(event_id)

        # Enrich items with vote data
        for item in event.items:
            try:
                item.votes = self._fetch_votes(event_id, item.event_item_id)
            except Exception as exc:
                log.debug("Failed to fetch votes for item %d: %s", item.event_item_id, exc)

        return event

    def get_matter_details(self, matter_id: int) -> dict[str, Any]:
        """Fetch legislation details for a matter."""
        return self._api_get(f"/matters/{matter_id}")

    def get_matter_attachments(self, matter_id: int) -> list[dict[str, Any]]:
        """Fetch attachments (bill text, staff reports) for a matter."""
        return self._api_get(f"/matters/{matter_id}/attachments")

    def download_agenda(self, event: LegistarEvent) -> str:
        """Download agenda PDF for an event. Returns path to file."""
        if not event.agenda_url:
            return ""

        output_dir = DATA_DIR / "agendas"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"legistar_{self.client}_{event.event_id}_agenda.pdf"
        output_path = output_dir / filename

        if output_path.exists():
            log.info("Agenda already downloaded: %s", filename)
            return str(output_path)

        log.info("Downloading agenda: %s", event.agenda_url[:80])
        try:
            resp = self.session.get(event.agenda_url, timeout=60)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            log.info("Agenda saved: %s (%.1f KB)", filename, len(resp.content) / 1024)
            return str(output_path)
        except requests.RequestException as exc:
            log.error("Failed to download agenda: %s", exc)
            return ""

    def download_minutes(self, event: LegistarEvent) -> str:
        """Download minutes PDF for an event. Returns path to file."""
        if not event.minutes_url:
            return ""

        output_dir = DATA_DIR / "minutes"
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"legistar_{self.client}_{event.event_id}_minutes.pdf"
        output_path = output_dir / filename

        if output_path.exists():
            log.info("Minutes already downloaded: %s", filename)
            return str(output_path)

        log.info("Downloading minutes: %s", event.minutes_url[:80])
        try:
            resp = self.session.get(event.minutes_url, timeout=60)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            log.info("Minutes saved: %s (%.1f KB)", filename, len(resp.content) / 1024)
            return str(output_path)
        except requests.RequestException as exc:
            log.error("Failed to download minutes: %s", exc)
            return ""

    # ---- Internal API methods ---------------------------------------------

    def _api_get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request to the Legistar API with rate limiting."""
        url = f"{self.base_url}{endpoint}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            time.sleep(self.rate_delay)
            return resp.json()
        except requests.RequestException as exc:
            log.error("Legistar API error (%s): %s", endpoint, exc)
            return [] if endpoint.endswith("s") else {}

    def _api_get_paginated(
        self, endpoint: str, params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages of a paginated Legistar API endpoint."""
        params = dict(params or {})
        params["$top"] = self.page_size
        all_results: list[dict[str, Any]] = []
        skip = 0

        while True:
            params["$skip"] = skip
            data = self._api_get(endpoint, params)
            if not isinstance(data, list):
                break
            all_results.extend(data)
            if len(data) < self.page_size:
                break
            skip += self.page_size

        return all_results

    def _fetch_events(self, days: int) -> list[LegistarEvent]:
        """Fetch events within a date range."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        params = {
            "$filter": f"EventDate ge datetime'{since}'",
            "$orderby": "EventDate desc",
        }
        data = self._api_get_paginated("/events", params)

        events: list[LegistarEvent] = []
        for item in data:
            try:
                events.append(self._parse_event(item))
            except Exception as exc:
                log.debug("Skipping malformed event: %s", exc)
        return events

    def _parse_event(self, item: dict[str, Any]) -> LegistarEvent:
        """Parse a raw API event dict into a LegistarEvent."""
        date_raw = item.get("EventDate", "")
        date_str = date_raw.split("T")[0] if "T" in date_raw else date_raw

        # Extract URLs from nested objects or direct fields
        agenda_url = ""
        minutes_url = ""
        if item.get("EventAgendaFile"):
            agenda_url = item["EventAgendaFile"]
        if item.get("EventMinutesFile"):
            minutes_url = item["EventMinutesFile"]

        video_url = item.get("EventVideoPath", "") or ""

        return LegistarEvent(
            event_id=item.get("EventId", 0),
            body_id=item.get("EventBodyId", 0),
            body_name=item.get("EventBodyName", ""),
            date=date_str,
            time=item.get("EventTime", ""),
            location=item.get("EventLocation", ""),
            video_url=video_url,
            agenda_url=agenda_url,
            minutes_url=minutes_url,
            agenda_status=item.get("EventAgendaStatusName", ""),
            minutes_status=item.get("EventMinutesStatusName", ""),
        )

    def _fetch_event_items(self, event_id: int) -> list[LegistarEventItem]:
        """Fetch agenda items for an event."""
        data = self._api_get(f"/events/{event_id}/eventitems")
        if not isinstance(data, list):
            return []

        items: list[LegistarEventItem] = []
        for raw in data:
            items.append(LegistarEventItem(
                event_item_id=raw.get("EventItemId", 0),
                title=raw.get("EventItemTitle", "") or "",
                action_text=raw.get("EventItemActionText", "") or "",
                matter_id=raw.get("EventItemMatterId"),
                matter_name=raw.get("EventItemMatterName", "") or "",
                matter_type=raw.get("EventItemMatterType", "") or "",
                matter_status=raw.get("EventItemMatterStatus", "") or "",
            ))
        return items

    def _fetch_votes(
        self, event_id: int, event_item_id: int,
    ) -> list[dict[str, Any]]:
        """Fetch vote records for an agenda item."""
        data = self._api_get(
            f"/events/{event_id}/eventitems/{event_item_id}/votes",
        )
        if not isinstance(data, list):
            return []
        return [
            {
                "person_name": v.get("VotePersonName", ""),
                "value": v.get("VoteValueName", ""),
            }
            for v in data
        ]

    def _score_housing_relevance(self, event: LegistarEvent) -> float:
        """Score an event's housing relevance based on agenda items."""
        if not event.items:
            # Fall back to body name matching
            lower_body = event.body_name.lower()
            housing_body_terms = ["housing", "planning", "land use", "zoning"]
            return 0.3 if any(t in lower_body for t in housing_body_terms) else 0.1

        text_parts: list[str] = []
        for item in event.items:
            text_parts.append(item.title)
            text_parts.append(item.matter_name)
            text_parts.append(item.action_text)
            text_parts.append(item.matter_type)

        combined = " ".join(text_parts).lower()
        if not combined.strip():
            return 0.1

        matches = sum(1 for kw in HOUSING_KEYWORDS if kw.lower() in combined)
        # Normalize: 5+ matches = 1.0, scale linearly below
        return min(1.0, matches / 5.0)

    def _event_to_meeting(
        self, event: LegistarEvent, relevance: float,
    ) -> Meeting:
        """Convert a LegistarEvent to a pipeline-compatible Meeting."""
        meeting = Meeting(
            id=f"legistar_{self.client}_{event.event_id}",
            jurisdiction=self.jurisdiction,
            title=f"{event.body_name} – {event.date}",
            date=event.date,
            video_url=event.video_url,
            source="legistar",
            housing_relevance_score=relevance,
        )
        return meeting

    # ---- Utility methods --------------------------------------------------

    def format_agenda_text(self, event: LegistarEvent) -> str:
        """Format agenda items into text suitable for Claude analysis."""
        lines: list[str] = [
            f"Meeting: {event.body_name}",
            f"Date: {event.date}",
            f"Location: {event.location}",
            "",
            "AGENDA ITEMS:",
            "",
        ]
        for i, item in enumerate(event.items, 1):
            lines.append(f"{i}. {item.title}")
            if item.matter_name:
                lines.append(f"   Matter: {item.matter_name}")
            if item.matter_type:
                lines.append(f"   Type: {item.matter_type}")
            if item.matter_status:
                lines.append(f"   Status: {item.matter_status}")
            if item.action_text:
                lines.append(f"   Action: {item.action_text}")
            if item.votes:
                vote_summary = _summarize_votes(item.votes)
                lines.append(f"   Votes: {vote_summary}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Vote helpers
# ---------------------------------------------------------------------------

def _summarize_votes(votes: list[dict[str, Any]]) -> str:
    """Summarize a list of vote records into a readable string."""
    if not votes:
        return ""
    counts: dict[str, int] = {}
    for v in votes:
        value = v.get("value", "Unknown")
        counts[value] = counts.get(value, 0) + 1
    parts = [f"{val}: {cnt}" for val, cnt in sorted(counts.items())]
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Batch operations
# ---------------------------------------------------------------------------

def discover_all_legistar(
    cities: list[str] | None = None,
    days: int | None = None,
) -> dict[str, list[Meeting]]:
    """Run Legistar discovery for all configured cities that have Legistar clients."""
    db = MeetingDatabase()
    results: dict[str, list[Meeting]] = {}

    for city_name, city_config in COLORADO_CITIES.items():
        if cities and city_name not in cities:
            continue

        legistar_client = city_config.get("legistar_client")
        if not legistar_client:
            continue

        body_filter = city_config.get("legistar_housing_bodies")
        discovery = LegistarDiscovery(legistar_client, city_name)

        meetings = discovery.discover_meetings(
            days=days,
            body_filter=body_filter,
        )

        new_count = 0
        for meeting in meetings:
            if not db.get(meeting.id):
                db.upsert(meeting)
                new_count += 1

        results[city_name] = meetings
        log.info(
            "%s: %d Legistar meetings found, %d new",
            city_name, len(meetings), new_count,
        )

        # Rate limit between cities
        time.sleep(2)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Legistar Meeting & Legislation Discovery",
    )
    parser.add_argument(
        "--city", type=str, default="Denver",
        help="City to discover (default: Denver)",
    )
    parser.add_argument(
        "--days", type=int,
        default=LEGISTAR_CONFIG["default_lookback_days"],
        help="Number of days to look back (default: 90)",
    )
    parser.add_argument(
        "--list-bodies", action="store_true",
        help="List all legislative bodies (committees)",
    )
    parser.add_argument(
        "--event-id", type=int,
        help="Fetch details for a specific event ID",
    )
    parser.add_argument(
        "--body", type=str,
        help="Filter to a specific committee/body name",
    )
    parser.add_argument(
        "--download-agendas", action="store_true",
        help="Download agenda PDFs",
    )
    parser.add_argument(
        "--download-minutes", action="store_true",
        help="Download minutes PDFs",
    )
    args = parser.parse_args()

    city_config = COLORADO_CITIES.get(args.city)
    if not city_config:
        log.error("Unknown city: %s", args.city)
        log.info("Configured cities: %s", ", ".join(COLORADO_CITIES.keys()))
        return

    legistar_client = city_config.get("legistar_client")
    if not legistar_client:
        log.error("No Legistar client configured for %s", args.city)
        return

    discovery = LegistarDiscovery(legistar_client, args.city)

    # --list-bodies
    if args.list_bodies:
        bodies = discovery.get_bodies()
        print(f"\nLegislative Bodies for {args.city} ({legistar_client}.legistar.com):")
        print("=" * 60)
        housing_bodies = city_config.get("legistar_housing_bodies", [])
        for body in sorted(bodies, key=lambda b: b.name):
            marker = " [HOUSING]" if any(
                h.lower() in body.name.lower() for h in housing_bodies
            ) else ""
            print(f"  [{body.body_id:5d}] {body.name} ({body.type_name}){marker}")
        print(f"\nTotal: {len(bodies)} bodies")
        return

    # --event-id
    if args.event_id:
        event = discovery.get_event_details(args.event_id)
        if not event:
            log.error("Event %d not found", args.event_id)
            return

        print(f"\nEvent: {event.body_name}")
        print(f"Date: {event.date} {event.time}")
        print(f"Location: {event.location}")
        if event.video_url:
            print(f"Video: {event.video_url}")
        if event.agenda_url:
            print(f"Agenda: {event.agenda_url}")
        print(f"\nAgenda Items ({len(event.items)}):")
        print("-" * 50)
        for i, item in enumerate(event.items, 1):
            print(f"\n  {i}. {item.title}")
            if item.matter_name:
                print(f"     Matter: {item.matter_name}")
            if item.matter_type:
                print(f"     Type: {item.matter_type}")
            if item.matter_status:
                print(f"     Status: {item.matter_status}")
            if item.action_text:
                print(f"     Action: {item.action_text}")
            if item.votes:
                print(f"     Votes: {_summarize_votes(item.votes)}")
        return

    # Default: discover meetings
    body_filter = city_config.get("legistar_housing_bodies")
    meetings = discovery.discover_meetings(
        days=args.days,
        body_filter=body_filter,
        body_name=args.body,
    )

    # Optionally download agendas/minutes
    if args.download_agendas or args.download_minutes:
        events = discovery._fetch_events(args.days)
        for event in events:
            if args.download_agendas:
                discovery.download_agenda(event)
            if args.download_minutes:
                discovery.download_minutes(event)
            time.sleep(0.5)

    # Store in database
    db = MeetingDatabase()
    new_count = 0
    for meeting in meetings:
        if not db.get(meeting.id):
            db.upsert(meeting)
            new_count += 1

    print(f"\n{args.city} Legistar Discovery Summary:")
    print(f"  Meetings found: {len(meetings)}")
    print(f"  New meetings stored: {new_count}")
    high_rel = [m for m in meetings if m.housing_relevance_score >= 0.3]
    print(f"  Housing-relevant: {len(high_rel)}")


if __name__ == "__main__":
    main()
