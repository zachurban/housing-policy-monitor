#!/usr/bin/env python3
"""
Analytics & Reporting Tools
============================
Query processed meetings, aggregate insights, and generate intelligence reports.

Usage:
    python example_analysis.py --report                     # full report
    python example_analysis.py --city Denver                # city report
    python example_analysis.py --search "inclusionary zoning"
    python example_analysis.py --compare Denver Aurora
    python example_analysis.py --funding-tracker
    python example_analysis.py --sentiment-trends
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    ANALYSIS_DIR,
    COLORADO_CITIES,
    DATA_DIR,
    HOUSING_KEYWORDS,
    MEETINGS_DB,
)
from meeting_ingestion_pipeline import Meeting, MeetingDatabase

log = logging.getLogger("analytics")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


# ---------------------------------------------------------------------------
# Analysis data loader
# ---------------------------------------------------------------------------

class AnalysisLoader:
    """Load and cache analysis JSON files for querying."""

    def __init__(self) -> None:
        self.db = MeetingDatabase()
        self._cache: dict[str, dict[str, Any]] = {}

    def get_analysis(self, meeting: Meeting) -> dict[str, Any]:
        """Load analysis JSON for a meeting."""
        if meeting.id in self._cache:
            return self._cache[meeting.id]

        if not meeting.analysis_path:
            return {}

        path = Path(meeting.analysis_path)
        if not path.exists():
            return {}

        try:
            data = json.loads(path.read_text())
            self._cache[meeting.id] = data
            return data
        except (json.JSONDecodeError, OSError):
            return {}

    def get_processed_meetings(
        self,
        jurisdiction: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        min_relevance: float = 0.0,
    ) -> list[tuple[Meeting, dict[str, Any]]]:
        """Get processed meetings with their analysis data, optionally filtered."""
        results: list[tuple[Meeting, dict[str, Any]]] = []
        for meeting in self.db.meetings.values():
            if not meeting.processed:
                continue
            if jurisdiction and meeting.jurisdiction != jurisdiction:
                continue
            if date_from and meeting.date < date_from:
                continue
            if date_to and meeting.date > date_to:
                continue
            if meeting.housing_relevance_score < min_relevance:
                continue

            analysis = self.get_analysis(meeting)
            if analysis:
                results.append((meeting, analysis))

        results.sort(key=lambda x: x[0].date, reverse=True)
        return results


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def query_by_city(city: str) -> None:
    """Print a summary of all processed meetings for a city."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings(jurisdiction=city)

    if not pairs:
        print(f"\nNo processed meetings found for {city}.")
        return

    print(f"\n{'=' * 70}")
    print(f"  Housing Intelligence Report: {city}")
    print(f"  Meetings analyzed: {len(pairs)}")
    print(f"{'=' * 70}\n")

    for meeting, analysis in pairs:
        score = analysis.get("housing_relevance_score", 0)
        topics = analysis.get("housing_topics", [])
        sentiment = analysis.get("sentiment", {}).get("overall", "N/A")

        print(f"  [{meeting.date}] {meeting.title[:60]}")
        print(f"    Relevance: {score:.1%}  |  Sentiment: {sentiment}")
        if topics:
            print(f"    Topics: {', '.join(topics[:5])}")
        proposals = analysis.get("policy_proposals", [])
        if proposals:
            for p in proposals[:3]:
                print(f"    -> {p.get('type', '?').upper()}: {p.get('description', '')[:60]}")
        print()


def search_meetings(query: str) -> None:
    """Search analyses for a specific term or topic."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    print(f"\n{'=' * 70}")
    print(f"  Search Results: \"{query}\"")
    print(f"{'=' * 70}\n")

    matches = 0
    for meeting, analysis in pairs:
        text = json.dumps(analysis).lower()
        if query.lower() not in text:
            continue

        matches += 1
        score = analysis.get("housing_relevance_score", 0)
        print(f"  [{meeting.jurisdiction}] [{meeting.date}] {meeting.title[:50]}")
        print(f"    Relevance: {score:.1%}")

        # Show matching context from the analysis
        summary = analysis.get("summary", "")
        if query.lower() in summary.lower():
            # Find the sentence containing the query
            for sentence in re.split(r'[.!?]', summary):
                if query.lower() in sentence.lower():
                    print(f"    > ...{sentence.strip()[:100]}...")
                    break

        # Show matching proposals
        for p in analysis.get("policy_proposals", []):
            desc = p.get("description", "")
            if query.lower() in desc.lower():
                print(f"    Proposal: {desc[:80]}")

        print()

    print(f"  Found {matches} matching meeting(s).\n")


def filter_housing_content(min_relevance: float = 0.3) -> None:
    """Show only meetings with significant housing content."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings(min_relevance=min_relevance)

    print(f"\n{'=' * 70}")
    print(f"  High-Relevance Housing Meetings (>= {min_relevance:.0%})")
    print(f"  Total: {len(pairs)}")
    print(f"{'=' * 70}\n")

    for meeting, analysis in pairs:
        score = analysis.get("housing_relevance_score", 0)
        topics = analysis.get("housing_topics", [])
        print(f"  [{meeting.jurisdiction}] [{meeting.date}] {meeting.title[:50]}")
        print(f"    Score: {score:.1%}  |  Mentions: {meeting.housing_mentions}")
        if topics:
            print(f"    Topics: {', '.join(topics[:5])}")
        print()


def extract_policy_proposals() -> None:
    """Extract and list all policy proposals across jurisdictions."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    print(f"\n{'=' * 70}")
    print(f"  Policy Proposals Tracker")
    print(f"{'=' * 70}\n")

    by_status: dict[str, list[dict]] = defaultdict(list)
    for meeting, analysis in pairs:
        for proposal in analysis.get("policy_proposals", []):
            status = proposal.get("status", "unknown")
            by_status[status].append({
                "jurisdiction": meeting.jurisdiction,
                "date": meeting.date,
                "type": proposal.get("type", "?"),
                "description": proposal.get("description", ""),
                "vote": proposal.get("vote_result"),
            })

    status_order = ["approved", "introduced", "discussed", "pending", "tabled", "denied"]
    for status in status_order:
        items = by_status.get(status, [])
        if not items:
            continue
        print(f"  --- {status.upper()} ({len(items)}) ---")
        for item in items:
            vote_str = f" [{item['vote']}]" if item.get("vote") else ""
            print(
                f"    [{item['jurisdiction']}] [{item['date']}] "
                f"{item['type'].upper()}: {item['description'][:60]}{vote_str}"
            )
        print()


def track_funding() -> None:
    """Track funding commitments across all meetings."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    print(f"\n{'=' * 70}")
    print(f"  Funding Commitment Tracker")
    print(f"{'=' * 70}\n")

    all_funding: list[dict] = []
    for meeting, analysis in pairs:
        for f_item in analysis.get("funding", []):
            all_funding.append({
                "jurisdiction": meeting.jurisdiction,
                "date": meeting.date,
                "amount": f_item.get("amount", "?"),
                "source": f_item.get("source", "?"),
                "purpose": f_item.get("purpose", ""),
                "status": f_item.get("status", "?"),
            })

    if not all_funding:
        print("  No funding commitments found in processed meetings.\n")
        return

    # Group by jurisdiction
    by_city: dict[str, list[dict]] = defaultdict(list)
    for item in all_funding:
        by_city[item["jurisdiction"]].append(item)

    for city, items in sorted(by_city.items()):
        print(f"  {city}:")
        for item in items:
            print(
                f"    [{item['date']}] {item['amount']} from {item['source']}"
                f" — {item['purpose'][:50]} ({item['status']})"
            )
        print()

    print(f"  Total funding items tracked: {len(all_funding)}\n")


def sentiment_analysis() -> None:
    """Aggregate sentiment data across meetings and jurisdictions."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    print(f"\n{'=' * 70}")
    print(f"  Sentiment Analysis Summary")
    print(f"{'=' * 70}\n")

    by_city: dict[str, Counter] = defaultdict(Counter)
    for meeting, analysis in pairs:
        sentiment = analysis.get("sentiment", {})
        overall = sentiment.get("overall", "unknown")
        by_city[meeting.jurisdiction][overall] += 1

    for city, counts in sorted(by_city.items()):
        total = sum(counts.values())
        print(f"  {city} ({total} meetings):")
        for sentiment_val in ["supportive", "mixed", "neutral", "opposed"]:
            count = counts.get(sentiment_val, 0)
            pct = (count / total * 100) if total else 0
            bar = "#" * int(pct / 5)
            print(f"    {sentiment_val:>12s}: {count:3d} ({pct:5.1f}%) {bar}")
        print()


def topic_frequency() -> None:
    """Analyze which housing topics are discussed most frequently."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    topic_counts: Counter = Counter()
    topic_by_city: dict[str, Counter] = defaultdict(Counter)

    for meeting, analysis in pairs:
        for topic in analysis.get("housing_topics", []):
            normalized = topic.lower().strip()
            topic_counts[normalized] += 1
            topic_by_city[meeting.jurisdiction][normalized] += 1

    print(f"\n{'=' * 70}")
    print(f"  Topic Frequency Analysis")
    print(f"{'=' * 70}\n")

    print("  Top 20 Topics (all jurisdictions):")
    for topic, count in topic_counts.most_common(20):
        bar = "#" * count
        print(f"    {count:3d}  {topic[:50]:50s}  {bar}")
    print()

    for city, counts in sorted(topic_by_city.items()):
        print(f"  Top topics for {city}:")
        for topic, count in counts.most_common(10):
            print(f"    {count:3d}  {topic[:50]}")
        print()


def compare_jurisdictions(cities: list[str]) -> None:
    """Generate a comparison report across multiple jurisdictions."""
    loader = AnalysisLoader()

    print(f"\n{'=' * 70}")
    print(f"  Jurisdiction Comparison: {' vs '.join(cities)}")
    print(f"{'=' * 70}\n")

    for city in cities:
        pairs = loader.get_processed_meetings(jurisdiction=city)
        if not pairs:
            print(f"  {city}: No data available\n")
            continue

        # Aggregate stats
        scores = [a.get("housing_relevance_score", 0) for _, a in pairs]
        avg_score = sum(scores) / len(scores) if scores else 0

        all_topics: Counter = Counter()
        all_proposals = 0
        all_funding_items = 0
        sentiments: Counter = Counter()

        for _, analysis in pairs:
            for topic in analysis.get("housing_topics", []):
                all_topics[topic.lower()] += 1
            all_proposals += len(analysis.get("policy_proposals", []))
            all_funding_items += len(analysis.get("funding", []))
            sentiments[analysis.get("sentiment", {}).get("overall", "unknown")] += 1

        print(f"  {city}:")
        print(f"    Meetings analyzed:     {len(pairs)}")
        print(f"    Avg relevance score:   {avg_score:.1%}")
        print(f"    Policy proposals:      {all_proposals}")
        print(f"    Funding items:         {all_funding_items}")
        print(f"    Sentiment breakdown:   {dict(sentiments)}")
        top_topics = all_topics.most_common(5)
        if top_topics:
            print(f"    Top topics:            {', '.join(t for t, _ in top_topics)}")
        print()


# ---------------------------------------------------------------------------
# Intelligence report generator
# ---------------------------------------------------------------------------

def generate_report(output_path: Path | None = None) -> str:
    """Generate a comprehensive markdown intelligence report."""
    loader = AnalysisLoader()
    pairs = loader.get_processed_meetings()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Civic Housing Intelligence Report",
        f"*Generated: {now}*",
        f"*Meetings Analyzed: {len(pairs)}*",
        "",
    ]

    if not pairs:
        lines.append("No processed meetings found. Run the pipeline first.")
        report = "\n".join(lines)
        if output_path:
            output_path.write_text(report)
        return report

    # --- Overview ---
    lines.append("## Overview")
    by_city: dict[str, list[tuple]] = defaultdict(list)
    for m, a in pairs:
        by_city[m.jurisdiction].append((m, a))

    lines.append("| Jurisdiction | Meetings | Avg Relevance | Proposals | Funding Items |")
    lines.append("|---|---|---|---|---|")

    for city in sorted(by_city.keys()):
        city_pairs = by_city[city]
        n = len(city_pairs)
        avg_rel = sum(a.get("housing_relevance_score", 0) for _, a in city_pairs) / n
        proposals = sum(len(a.get("policy_proposals", [])) for _, a in city_pairs)
        funding = sum(len(a.get("funding", [])) for _, a in city_pairs)
        lines.append(f"| {city} | {n} | {avg_rel:.1%} | {proposals} | {funding} |")
    lines.append("")

    # --- Recent high-relevance meetings ---
    lines.append("## Recent High-Relevance Meetings")
    high_rel = [(m, a) for m, a in pairs if a.get("housing_relevance_score", 0) >= 0.5]
    high_rel.sort(key=lambda x: x[0].date, reverse=True)

    for meeting, analysis in high_rel[:10]:
        score = analysis.get("housing_relevance_score", 0)
        lines.append(f"### [{meeting.jurisdiction}] {meeting.title}")
        lines.append(f"*Date: {meeting.date} | Relevance: {score:.1%}*\n")

        summary = analysis.get("summary", "")
        if summary:
            lines.append(summary)
            lines.append("")

        proposals = analysis.get("policy_proposals", [])
        if proposals:
            lines.append("**Policy Proposals:**")
            for p in proposals:
                lines.append(
                    f"- [{p.get('status', '?').upper()}] "
                    f"{p.get('type', '?')}: {p.get('description', '')}"
                )
            lines.append("")

    # --- Policy tracking ---
    lines.append("## Policy Proposal Summary")
    status_counts: Counter = Counter()
    for _, a in pairs:
        for p in a.get("policy_proposals", []):
            status_counts[p.get("status", "unknown")] += 1

    if status_counts:
        lines.append("| Status | Count |")
        lines.append("|---|---|")
        for status, count in status_counts.most_common():
            lines.append(f"| {status} | {count} |")
        lines.append("")

    # --- Funding ---
    lines.append("## Funding Commitments")
    all_funding: list[dict] = []
    for m, a in pairs:
        for f_item in a.get("funding", []):
            f_item["jurisdiction"] = m.jurisdiction
            f_item["date"] = m.date
            all_funding.append(f_item)

    if all_funding:
        lines.append("| Date | Jurisdiction | Amount | Source | Purpose |")
        lines.append("|---|---|---|---|---|")
        for item in sorted(all_funding, key=lambda x: x.get("date", ""), reverse=True)[:20]:
            lines.append(
                f"| {item.get('date', '')} | {item.get('jurisdiction', '')} "
                f"| {item.get('amount', '?')} | {item.get('source', '?')} "
                f"| {item.get('purpose', '')[:40]} |"
            )
        lines.append("")
    else:
        lines.append("No funding commitments tracked.\n")

    # --- Topic trends ---
    lines.append("## Topic Frequency")
    topic_counts: Counter = Counter()
    for _, a in pairs:
        for t in a.get("housing_topics", []):
            topic_counts[t.lower()] += 1

    if topic_counts:
        lines.append("| Topic | Mentions |")
        lines.append("|---|---|")
        for topic, count in topic_counts.most_common(15):
            lines.append(f"| {topic} | {count} |")
        lines.append("")

    # --- Sentiment ---
    lines.append("## Sentiment Overview")
    for city in sorted(by_city.keys()):
        city_pairs = by_city[city]
        sentiments = Counter(
            a.get("sentiment", {}).get("overall", "unknown") for _, a in city_pairs
        )
        lines.append(f"**{city}:** {dict(sentiments)}")
    lines.append("")

    report = "\n".join(lines)

    if output_path is None:
        output_path = DATA_DIR / "intelligence_report.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    log.info("Report saved: %s", output_path)

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Housing Intelligence Analytics & Reporting",
    )
    parser.add_argument("--report", action="store_true", help="Generate full intelligence report")
    parser.add_argument("--city", type=str, help="Show report for a specific city")
    parser.add_argument("--search", type=str, help="Search analyses for a term")
    parser.add_argument("--compare", type=str, nargs="+", help="Compare jurisdictions")
    parser.add_argument("--proposals", action="store_true", help="List all policy proposals")
    parser.add_argument("--funding-tracker", action="store_true", help="Show funding tracker")
    parser.add_argument("--sentiment-trends", action="store_true", help="Sentiment analysis")
    parser.add_argument("--topics", action="store_true", help="Topic frequency analysis")
    parser.add_argument("--housing-filter", type=float, default=0.3,
                        help="Min relevance score filter (default: 0.3)")
    parser.add_argument("--high-relevance", action="store_true",
                        help="Show only high-relevance meetings")

    args = parser.parse_args()

    if args.report:
        report = generate_report()
        print(report)
    elif args.city:
        query_by_city(args.city)
    elif args.search:
        search_meetings(args.search)
    elif args.compare:
        compare_jurisdictions(args.compare)
    elif args.proposals:
        extract_policy_proposals()
    elif args.funding_tracker:
        track_funding()
    elif args.sentiment_trends:
        sentiment_analysis()
    elif args.topics:
        topic_frequency()
    elif args.high_relevance:
        filter_housing_content(min_relevance=args.housing_filter)
    else:
        # Default: show stats for all cities
        print("\nHousing Intelligence Overview")
        print("=" * 50)
        loader = AnalysisLoader()
        for city in COLORADO_CITIES:
            pairs = loader.get_processed_meetings(jurisdiction=city)
            if pairs:
                avg = sum(a.get("housing_relevance_score", 0) for _, a in pairs) / len(pairs)
                print(f"  {city}: {len(pairs)} meetings, avg relevance {avg:.1%}")
            else:
                print(f"  {city}: no data")
        print()
        print("Use --help for more options.")


if __name__ == "__main__":
    main()
