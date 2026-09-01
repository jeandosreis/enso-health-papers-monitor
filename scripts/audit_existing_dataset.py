#!/usr/bin/env python3
"""Standalone audit of an existing publications.json using the v1.2 title-first rule.

This version is intentionally self-contained: it does NOT import
update_publications.py, so it can audit an older v1.1 dataset before the main
collector is replaced.

Outputs:
  * data/title_audit_summary.json
  * data/title_audit_records.csv

It never modifies data/publications.json and makes no external API calls.
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "search_config.json"
INPUT = ROOT / "data" / "publications.json"
SUMMARY = ROOT / "data" / "title_audit_summary.json"
CSV_PATH = ROOT / "data" / "title_audit_records.csv"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def phrase_in_text(text: str, phrase: str) -> bool:
    haystack = normalize_text(text)
    needle = normalize_text(phrase)
    if not haystack or not needle:
        return False
    return f" {needle} " in f" {haystack} "


def unique_preserve(values: Iterable[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        marker = str(value)
        if marker not in seen and value not in (None, ""):
            seen.add(marker)
            result.append(value)
    return result


def find_term_matches(text: str, terms: Iterable[str]) -> list[str]:
    matches: list[str] = []
    seen_normalized: set[str] = set()
    for term in terms:
        marker = normalize_text(term)
        if marker in seen_normalized:
            continue
        if phrase_in_text(text, term):
            seen_normalized.add(marker)
            matches.append(term)
    return matches


def screen_title(title: str, enso_terms: list[str], categories: dict[str, list[str]]) -> dict[str, Any]:
    enso_matches = find_term_matches(title, enso_terms)
    health_categories: list[str] = []
    health_matches: list[str] = []
    for category, terms in categories.items():
        found = find_term_matches(title, terms)
        if found:
            health_categories.append(category)
            health_matches.extend(found)

    health_categories = unique_preserve(health_categories)
    health_matches = unique_preserve(health_matches)
    included = bool(enso_matches and health_matches)

    if included:
        reason = "ENSO term and predefined human-health term both occur in the publication title."
    elif not enso_matches and not health_matches:
        reason = "Title contains neither a configured ENSO term nor a configured human-health term."
    elif not enso_matches:
        reason = "Title contains a health term but no configured ENSO term."
    else:
        reason = "Title contains an ENSO term but no configured human-health term."

    return {
        "included": included,
        "criterion": "enso_and_health_in_title",
        "enso_title_matches": enso_matches,
        "health_title_matches": health_matches,
        "health_categories": health_categories,
        "reason": reason,
    }


def main() -> int:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing configuration: {CONFIG_PATH}")
    if not INPUT.exists():
        raise FileNotFoundError(f"Missing dataset: {INPUT}")

    config = load_config()
    if "enso_terms" not in config or "health_categories" not in config:
        raise RuntimeError(
            "search_config.json is not the v1.2 title-first configuration. "
            "Replace config/search_config.json with the v1.2 file and run again."
        )

    with INPUT.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    records = payload.get("publications") or []
    accepted = 0
    rejected = 0
    health_counts = Counter()
    health_term_counts = Counter()
    enso_counts = Counter()
    rejection_reasons = Counter()

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "status", "year", "title", "enso_title_matches",
                "health_title_matches", "health_categories", "sources", "reason"
            ],
        )
        writer.writeheader()

        for rec in records:
            title = str(rec.get("title") or "")
            evidence = screen_title(title, config["enso_terms"], config["health_categories"])
            status = "include" if evidence["included"] else "exclude"

            if evidence["included"]:
                accepted += 1
                health_counts.update(evidence["health_categories"])
                health_term_counts.update(evidence["health_title_matches"])
                enso_counts.update(evidence["enso_title_matches"])
            else:
                rejected += 1
                rejection_reasons[evidence["reason"]] += 1

            sources = rec.get("source_databases") or rec.get("sources") or []
            if isinstance(sources, str):
                sources = [sources]

            writer.writerow({
                "status": status,
                "year": rec.get("year") or "",
                "title": title,
                "enso_title_matches": " | ".join(evidence["enso_title_matches"]),
                "health_title_matches": " | ".join(evidence["health_title_matches"]),
                "health_categories": " | ".join(evidence["health_categories"]),
                "sources": " | ".join(str(x) for x in sources),
                "reason": evidence["reason"],
            })

    rate = (accepted / len(records) * 100) if records else 0.0
    summary = {
        "criterion": "ENSO term + predefined human-health term must both occur in title",
        "input_records": len(records),
        "included": accepted,
        "excluded": rejected,
        "inclusion_rate_percent": round(rate, 2),
        "top_enso_title_terms": enso_counts.most_common(),
        "top_health_title_terms": health_term_counts.most_common(),
        "health_categories": health_counts.most_common(),
        "rejection_reasons": rejection_reasons.most_common(),
        "note": "This audit does not modify publications.json and makes no API calls."
    }
    with SUMMARY.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=" * 72)
    print("TITLE-FIRST AUDIT OF EXISTING DATASET — v1.2.1 standalone")
    print("=" * 72)
    print(f"Input records : {len(records)}")
    print(f"Included      : {accepted}")
    print(f"Excluded      : {rejected}")
    print(f"Inclusion rate: {rate:.2f}%")
    print("-")
    print(f"Summary saved : {SUMMARY}")
    print(f"CSV saved     : {CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
