#!/usr/bin/env python3
"""Build the ENSO & Health Research Monitor bibliographic dataset.

Version 1.4 implements a title-first inclusion rule plus a second-stage
false-positive screen:
  * a publication title must contain at least one ENSO term; AND
  * the same title must contain at least one predefined human-health term; AND
  * clear false positives are excluded (non-human/ecological outcomes, Spanish
    "el niño" meaning child, meteorological outbreaks, corrections/errata,
    lexical false positives, and food-security/famine/nutrition-only titles).

Generic health titles and mechanistic/vector titles are retained in the public
catalogue, following the project's inclusion policy.

Discovery sources:
  * OpenAlex: title-only scholarly discovery
  * PubMed: title-only biomedical discovery

Metadata enrichment:
  * Crossref: DOI metadata validation/enrichment when important fields are missing

Outputs:
  * data/publications.json   -- catalogue consumed by the website
  * data/audit_report.json   -- transparent counts for discovery, screening,
                                deduplication, source overlap and enrichment

No server or database is required.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "search_config.json"
DIRECT_SCREENING_PATH = ROOT / "config" / "direct_screening.json"
OUTPUT_PATH = ROOT / "data" / "publications.json"
AUDIT_PATH = ROOT / "data" / "audit_report.json"

OPENALEX_URL = "https://api.openalex.org/"
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
CROSSREF_URL = "https://api.crossref.org/works"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ENSO-Health-Research-Monitor/1.4"})


class RateLimitError(RuntimeError):
    """Raised when an API rate limit/budget has been exhausted."""


def log(message: str) -> None:
    print(message, flush=True)


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_direct_screening_config() -> dict[str, Any]:
    """Load second-stage screening rules.

    The file is kept separate so exclusion rules remain transparent and editable,
    but the rules are enforced directly by this update script on every run.
    """
    if not DIRECT_SCREENING_PATH.exists():
        raise FileNotFoundError(
            f"Missing direct-screening configuration: {DIRECT_SCREENING_PATH}"
        )
    with DIRECT_SCREENING_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def phrase_in_text(text: str, phrase: str) -> bool:
    """Exact normalized token/phrase matching.

    This is deliberately stricter than the API search engine. For example, the
    health term ``health`` matches the title token ``health`` but not ``healthcare``.
    Hyphens, accents and punctuation normalize to spaces, so ``El Niño-driven``
    still matches the configured phrase ``El Niño``.
    """
    haystack = normalize_text(text)
    needle = normalize_text(phrase)
    if not haystack or not needle:
        return False
    return f" {needle} " in f" {haystack} "


def unique_preserve(values: Iterable[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        marker = json.dumps(value, sort_keys=True, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if marker not in seen and value not in (None, ""):
            seen.add(marker)
            result.append(value)
    return result


def find_term_matches(text: str, terms: Iterable[str]) -> list[str]:
    # Collapse accent/spelling aliases that normalize to the same phrase
    # (e.g. "El Niño" and "El Nino") while preserving the first display form.
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


def health_title_evidence(title: str, categories: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    matched_categories: list[str] = []
    matched_terms: list[str] = []
    for category, terms in categories.items():
        found = find_term_matches(title, terms)
        if found:
            matched_categories.append(category)
            matched_terms.extend(found)
    return unique_preserve(matched_categories), unique_preserve(matched_terms)


def enso_title_evidence(title: str, enso_terms: list[str]) -> list[str]:
    return find_term_matches(title, enso_terms)


def screen_title(title: str, enso_terms: list[str], categories: dict[str, list[str]]) -> dict[str, Any]:
    enso_matches = enso_title_evidence(title, enso_terms)
    health_categories, health_matches = health_title_evidence(title, categories)
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


def compile_any(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


def looks_like_child_not_enso(title: str) -> bool:
    """Detect Spanish/Portuguese medical titles where 'el niño' means 'the child'."""
    child_context = re.search(
        r"\b(?:en|del|para)\s+el\s+ni[nñ]o\b|"
        r"\by\s+el\s+ni[nñ]o\b|"
        r"\bel\s+ni[nñ]o\s+(?:menor|inmigrante|sano)\b|"
        r"\bentrevista\s+para\s+el\s+ni[nñ]o\b",
        title,
        re.I,
    )
    if not child_context:
        return False

    climate_cue = re.search(
        r"\b(?:ENSO|ENOS|southern\s+oscillation|oscila\w*|fen[oó]men\w*|phenomen\w*|"
        r"costero|global|climat\w*|weather|drought|rainfall|rain|event|evento|phase|episod\w*|"
        r"la\s+ni[nñ]a)\b",
        title,
        re.I,
    )
    return climate_cue is None


def second_stage_screen(
    title: str,
    title_evidence: dict[str, Any],
    screen_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Apply the project's clear-false-positive exclusions.

    Policy in v1.4:
      * specific human-health titles -> included_direct
      * generic-health titles -> included_broad
      * mechanistic/vector titles -> included_broad
      * only clear false positives are excluded
    """
    if not title_evidence.get("included"):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "title_gate",
            "second_stage_reason": title_evidence.get("reason", "Title gate failed."),
        }

    enso = list(title_evidence.get("enso_title_matches") or [])
    health = list(title_evidence.get("health_title_matches") or [])
    categories = list(title_evidence.get("health_categories") or [])

    # Ambiguous standalone Spanish 'El Niño' meaning 'the child'.  Explicit ENSO,
    # ENOS or La Niña signals override this exclusion.
    if looks_like_child_not_enso(title):
        explicit = [m for m in enso if normalize_text(m) not in {"el nino"}]
        if not explicit:
            return {
                **title_evidence,
                "included": False,
                "tier": "excluded",
                "reason_code": "child_not_enso",
                "second_stage_reason": "'El Niño' is used as 'the child', not the climate phenomenon.",
            }

    if re.search(r"\bEl\s+Ni[nñ]o\s+Sano\b", title, re.I):
        explicit = [m for m in enso if normalize_text(m) != "el nino"]
        if not explicit:
            return {
                **title_evidence,
                "included": False,
                "tier": "excluded",
                "reason_code": "child_not_enso",
                "second_stage_reason": "'El Niño' is part of a child-health expression, not ENSO.",
            }

    if re.search(r"^(?:publisher\s+)?correction\b|^errata\b|^corrigendum\b|^retraction\b", title, re.I):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "correction",
            "second_stage_reason": "Correction/erratum/retraction rather than the primary scientific work.",
        }

    nonhuman_re = compile_any(screen_cfg.get("nonhuman_exclusion_patterns", []))
    if nonhuman_re and nonhuman_re.search(title):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "nonhuman",
            "second_stage_reason": "Health-like term refers to a non-human ecological, veterinary or agricultural outcome.",
        }

    met_re = compile_any(screen_cfg.get("meteorological_false_positive_patterns", []))
    if met_re and met_re.search(title):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "meteorological_outbreak",
            "second_stage_reason": "'Outbreak' refers to a meteorological phenomenon rather than disease.",
        }

    if re.search(r"\bDeath\s+Valley\b", title, re.I) and all(
        normalize_text(x) in {"death", "deaths", "mortality"} for x in health
    ):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "lexical_false_positive",
            "second_stage_reason": "The health-like word is part of a place name rather than a health outcome.",
        }

    indirect = {normalize_text(x) for x in screen_cfg.get("indirect_only_terms", [])}
    health_norm = {normalize_text(x) for x in health}
    if health_norm and health_norm.issubset(indirect):
        return {
            **title_evidence,
            "included": False,
            "tier": "excluded",
            "reason_code": "indirect_food_nutrition",
            "second_stage_reason": "Title concerns food security/famine/nutrition only, without a direct human-health outcome.",
        }

    # The user chose to retain these potentially mechanistic records in the public
    # catalogue because the title explicitly links ENSO with a health/disease topic.
    mechanism_re = compile_any(screen_cfg.get("mechanistic_vector_patterns", []))
    if mechanism_re and mechanism_re.search(title):
        return {
            **title_evidence,
            "included": True,
            "tier": "included_broad",
            "reason_code": "mechanistic_vector",
            "second_stage_reason": "Retained: title explicitly links ENSO with a health/disease topic through a vector/host/mechanistic pathway.",
        }

    generic = {normalize_text(x) for x in screen_cfg.get("generic_review_terms", [])}
    specific = [x for x in health if normalize_text(x) not in generic and normalize_text(x) not in indirect]
    if not specific:
        return {
            **title_evidence,
            "included": True,
            "tier": "included_broad",
            "reason_code": "generic_health_signal",
            "second_stage_reason": "Retained: title explicitly links ENSO with a broad human-health, mortality, morbidity or disease signal.",
        }

    return {
        **title_evidence,
        "included": True,
        "tier": "included_direct",
        "reason_code": "specific_health_title_signal",
        "second_stage_reason": "Title links ENSO with a specific human-health disease or outcome signal and no exclusion rule was triggered.",
    }


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.strip()


def abstract_from_inverted_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, locs in index.items():
        for pos in locs:
            positions.append((pos, word))
    positions.sort(key=lambda x: x[0])
    return clean_text(" ".join(word for _, word in positions))


def safe_year(value: Any) -> int | None:
    try:
        year = int(value)
        if 1800 <= year <= 2100:
            return year
    except (TypeError, ValueError):
        pass
    return None


def iso_date(value: str | None) -> str:
    if not value:
        return ""
    m = re.search(r"\d{4}(?:-\d{2})?(?:-\d{2})?", value)
    return m.group(0) if m else ""


def oql_term(term: str) -> str:
    """Render a term for OpenAlex OQL discovery.

    Discovery remains slightly broader than the final local gate: single words are
    stemmed by OpenAlex and multi-word terms are stemmed adjacent phrases. Every
    returned title is then checked by ``screen_title`` using exact normalized terms.
    """
    escaped = str(term).replace('"', "")
    if re.search(r"[\s-]", escaped):
        return f'stemmed "{escaped}"'
    return escaped


def make_oql_query(enso_terms: list[str], health_terms: list[str], types: list[str]) -> str:
    enso = " or ".join(oql_term(x) for x in enso_terms)
    health = " or ".join(oql_term(x) for x in health_terms)
    work_types = " or ".join(types)
    return (
        f"works where title has (({enso}) and ({health})) "
        f"and type is ({work_types})"
    )


def request_json(url: str, *, params: dict[str, Any] | None = None, timeout: int = 60,
                 attempts: int = 4) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)
            if response.status_code == 429:
                reset_raw = response.headers.get("X-RateLimit-Reset", "")
                retry_raw = response.headers.get("Retry-After", "")
                try:
                    reset_seconds = int(float(reset_raw)) if reset_raw else None
                except ValueError:
                    reset_seconds = None
                try:
                    retry_seconds = int(float(retry_raw)) if retry_raw else None
                except ValueError:
                    retry_seconds = None

                if reset_seconds is not None and reset_seconds > 60:
                    raise RateLimitError(
                        f"HTTP 429; API budget exhausted (reset in ~{reset_seconds}s)"
                    )

                wait = retry_seconds if retry_seconds is not None else min(2 ** attempt, 20)
                if attempt == attempts - 1:
                    raise RateLimitError("HTTP 429; rate limit reached")
                log(f"  HTTP 429; retrying in {wait}s")
                time.sleep(max(1, min(wait, 60)))
                continue

            if response.status_code >= 500:
                wait = min(2 ** attempt, 20)
                log(f"  HTTP {response.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()
        except RateLimitError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Request failed: {url}: {last_error}")


def request_text(url: str, *, params: dict[str, Any] | None = None, timeout: int = 60,
                 attempts: int = 4) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = SESSION.get(url, params=params, timeout=timeout)
            if response.status_code == 429 or response.status_code >= 500:
                wait = min(2 ** attempt, 20)
                log(f"  HTTP {response.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"Request failed: {url}: {last_error}")


def classify_enso_from_title(title: str) -> list[str]:
    norm = normalize_text(title)
    phases: list[str] = []
    if phrase_in_text(norm, "el nino"):
        phases.append("El Niño")
    if phrase_in_text(norm, "la nina"):
        phases.append("La Niña")
    if phrase_in_text(norm, "enso") or phrase_in_text(norm, "el nino southern oscillation"):
        phases.append("ENSO")
    return phases


def make_record(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "id": "", "title": "", "year": None, "publication_date": "",
        "authors": [], "journal": "", "doi": "", "pmid": "", "openalex_id": "",
        "abstract": "", "cited_by_count": 0, "is_oa": False, "oa_url": "",
        "landing_page_url": "", "health_topics": [], "enso_phases": [],
        "source_databases": [], "type": "", "affiliation_countries": [],
        "inclusion": {},
    }
    defaults.update(kwargs)
    return defaults


def openalex_record(
    work: dict[str, Any],
    category_config: dict[str, list[str]],
    enso_terms: list[str],
    screen_cfg: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    title = clean_text(work.get("title"))
    title_evidence = screen_title(title, enso_terms, category_config)
    evidence = second_stage_screen(title, title_evidence, screen_cfg)
    if not evidence["included"]:
        return None, evidence

    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    oa = work.get("open_access") or {}
    best_oa = work.get("best_oa_location") or {}
    authors = []
    countries = set()
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        name = clean_text(author.get("display_name"))
        if name:
            authors.append(name)
        for inst in authorship.get("institutions") or []:
            code = inst.get("country_code")
            if code:
                countries.add(code)

    abstract = abstract_from_inverted_index(work.get("abstract_inverted_index"))
    doi = normalize_doi(work.get("doi"))
    openalex_id = str(work.get("id") or "").rsplit("/", 1)[-1]

    record = make_record(
        id=f"oa:{openalex_id}",
        title=title,
        year=safe_year(work.get("publication_year")),
        publication_date=iso_date(work.get("publication_date")),
        authors=authors,
        journal=clean_text(source.get("display_name")),
        doi=doi,
        openalex_id=openalex_id,
        abstract=abstract,
        cited_by_count=int(work.get("cited_by_count") or 0),
        is_oa=bool(oa.get("is_oa")),
        oa_url=best_oa.get("pdf_url") or best_oa.get("landing_page_url") or "",
        landing_page_url=primary.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else ""),
        health_topics=evidence["health_categories"],
        enso_phases=classify_enso_from_title(title),
        source_databases=["OpenAlex"],
        type=clean_text(work.get("type")),
        affiliation_countries=sorted(countries),
        inclusion={
            "criterion": "enso_health_title_plus_false_positive_exclusions",
            "tier": evidence.get("tier"),
            "reason_code": evidence.get("reason_code"),
            "reason": evidence.get("second_stage_reason"),
            "enso_title_matches": evidence["enso_title_matches"],
            "health_title_matches": evidence["health_title_matches"],
        },
    )
    return record, evidence


def fetch_openalex(config: dict[str, Any], screen_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    oa_cfg = config["openalex"]
    enso_terms = config["enso_terms"]
    categories = config["health_categories"]
    health_terms = unique_preserve(term for terms in categories.values() for term in terms)
    api_key = os.getenv("OPENALEX_API_KEY", "").strip()
    email = os.getenv("CONTACT_EMAIL", "").strip()
    records: list[dict[str, Any]] = []

    query = make_oql_query(enso_terms, health_terms, list(oa_cfg["types"]))
    per_page = int(oa_cfg.get("per_page", 100))
    maximum = int(oa_cfg.get("max_records_total", 50000))

    audit = {
        "search_scope": "title",
        "api_reported_matches": None,
        "retrieved": 0,
        "title_gate_candidates": 0,
        "title_gate_excluded": 0,
        "second_stage_included": 0,
        "second_stage_excluded": 0,
        "second_stage_exclusion_reasons": {},
        "inclusion_tiers": {},
        "partial_due_to_rate_limit": False,
    }

    log("OpenAlex: strict ENSO × health TITLE search")
    if not api_key:
        log("  note: OPENALEX_API_KEY is not set; anonymous daily budget is much smaller")

    cursor = "*"
    retrieved = 0
    total_count: int | None = None
    while cursor and retrieved < maximum:
        params: dict[str, Any] = {
            "oql": query,
            "per-page": per_page,
            "cursor": cursor,
            "select": ",".join([
                "id", "doi", "title", "publication_year", "publication_date", "type",
                "authorships", "primary_location", "best_oa_location", "open_access",
                "abstract_inverted_index", "cited_by_count"
            ]),
        }
        if api_key:
            params["api_key"] = api_key
        if email:
            params["mailto"] = email

        try:
            data = request_json(OPENALEX_URL, params=params)
        except RateLimitError as exc:
            log(f"WARNING: OpenAlex rate limit reached: {exc}")
            log(f"  keeping {len(records)} title-screened OpenAlex records already retrieved")
            audit["partial_due_to_rate_limit"] = True
            break

        meta = data.get("meta") or {}
        if total_count is None:
            try:
                total_count = int(meta.get("count"))
                audit["api_reported_matches"] = total_count
                log(f"  API reports {total_count} title matches")
            except (TypeError, ValueError):
                total_count = None

        batch = data.get("results") or []
        if not batch:
            break
        remaining = maximum - retrieved
        if len(batch) > remaining:
            batch = batch[:remaining]

        exclusion_counts = Counter(audit.get("second_stage_exclusion_reasons") or {})
        tier_counts = Counter(audit.get("inclusion_tiers") or {})
        for work in batch:
            record, evidence = openalex_record(work, categories, enso_terms, screen_cfg)
            if evidence.get("reason_code") == "title_gate":
                audit["title_gate_excluded"] += 1
            else:
                audit["title_gate_candidates"] += 1
            if record is not None:
                records.append(record)
                audit["second_stage_included"] += 1
                tier_counts[evidence.get("tier") or "included"] += 1
            else:
                if evidence.get("reason_code") != "title_gate":
                    audit["second_stage_excluded"] += 1
                    exclusion_counts[evidence.get("reason_code") or "other"] += 1
        audit["second_stage_exclusion_reasons"] = dict(exclusion_counts)
        audit["inclusion_tiers"] = dict(tier_counts)

        retrieved += len(batch)
        audit["retrieved"] = retrieved
        cursor = meta.get("next_cursor")
        log(
            f"  retrieved {retrieved}"
            + (f"/{min(total_count, maximum)}" if total_count is not None else "")
            + f"; final screening kept {len(records)}"
        )
        if len(batch) < per_page:
            break
        time.sleep(0.10)

    if total_count is not None and total_count > maximum:
        log(f"WARNING: OpenAlex result set exceeds configured cap ({maximum}); increase max_records_total to harvest all matches.")
    return records, audit


def pubmed_query(enso_terms: list[str], health_terms: list[str]) -> str:
    def title_field(term: str) -> str:
        escaped = str(term).replace('"', "")
        return f'"{escaped}"[Title]'
    return (
        f"({' OR '.join(title_field(x) for x in enso_terms)}) "
        f"AND ({' OR '.join(title_field(x) for x in health_terms)})"
    )


def text_from_xml(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return clean_text("".join(node.itertext()))


def first_text(parent: ET.Element, path: str) -> str:
    return text_from_xml(parent.find(path))


def parse_pubmed_article(
    article: ET.Element,
    categories: dict[str, list[str]],
    enso_terms: list[str],
    screen_cfg: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    medline = article.find("MedlineCitation")
    pubmed_data = article.find("PubmedData")
    if medline is None:
        title_evidence = screen_title("", enso_terms, categories)
        return None, second_stage_screen("", title_evidence, screen_cfg)

    pmid = first_text(medline, "PMID")
    art = medline.find("Article")
    if art is None:
        title_evidence = screen_title("", enso_terms, categories)
        return None, second_stage_screen("", title_evidence, screen_cfg)

    title = first_text(art, "ArticleTitle")
    title_evidence = screen_title(title, enso_terms, categories)
    evidence = second_stage_screen(title, title_evidence, screen_cfg)
    if not evidence["included"]:
        return None, evidence

    abstract_parts = [text_from_xml(x) for x in art.findall("Abstract/AbstractText")]
    abstract = clean_text(" ".join(x for x in abstract_parts if x))

    authors: list[str] = []
    for author in art.findall("AuthorList/Author"):
        collective = first_text(author, "CollectiveName")
        if collective:
            authors.append(collective)
            continue
        fore = first_text(author, "ForeName")
        last = first_text(author, "LastName")
        name = clean_text(f"{fore} {last}")
        if name:
            authors.append(name)

    journal = first_text(art, "Journal/Title") or first_text(art, "Journal/ISOAbbreviation")
    date_fields = [
        first_text(art, "Journal/JournalIssue/PubDate/MedlineDate"),
        first_text(art, "Journal/JournalIssue/PubDate/Year"),
        first_text(medline, "DateCompleted/Year"),
    ]
    year = None
    for value in date_fields:
        m = re.search(r"(18|19|20)\d{2}", value or "")
        if m:
            year = safe_year(m.group(0))
            break

    doi = ""
    if pubmed_data is not None:
        for ident in pubmed_data.findall("ArticleIdList/ArticleId"):
            if ident.attrib.get("IdType") == "doi":
                doi = normalize_doi(text_from_xml(ident))
                break

    publication_types = [text_from_xml(x) for x in art.findall("PublicationTypeList/PublicationType")]
    ptype = publication_types[0] if publication_types else "article"
    landing = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""

    record = make_record(
        id=f"pmid:{pmid}", title=title, year=year,
        publication_date=str(year) if year else "", authors=authors, journal=journal,
        doi=doi, pmid=pmid, abstract=abstract, landing_page_url=landing,
        health_topics=evidence["health_categories"],
        enso_phases=classify_enso_from_title(title), source_databases=["PubMed"], type=ptype,
        inclusion={
            "criterion": "enso_health_title_plus_false_positive_exclusions",
            "tier": evidence.get("tier"),
            "reason_code": evidence.get("reason_code"),
            "reason": evidence.get("second_stage_reason"),
            "enso_title_matches": evidence["enso_title_matches"],
            "health_title_matches": evidence["health_title_matches"],
        },
    )
    return record, evidence


def fetch_pubmed(config: dict[str, Any], screen_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pm_cfg = config["pubmed"]
    categories = config["health_categories"]
    enso_terms = config["enso_terms"]
    health_terms = unique_preserve(term for terms in categories.values() for term in terms)
    email = os.getenv("CONTACT_EMAIL", "").strip()
    api_key = os.getenv("NCBI_API_KEY", "").strip()
    tool = "enso_health_research_monitor"

    audit = {
        "search_scope": "title",
        "query_ids": 0,
        "records_fetched": 0,
        "title_gate_candidates": 0,
        "title_gate_excluded": 0,
        "second_stage_included": 0,
        "second_stage_excluded": 0,
        "second_stage_exclusion_reasons": {},
        "inclusion_tiers": {},
    }

    log("PubMed: strict ENSO × health TITLE search + clear false-positive exclusions")
    params: dict[str, Any] = {
        "db": "pubmed",
        "term": pubmed_query(enso_terms, health_terms),
        "retmode": "json",
        "retmax": int(pm_cfg.get("max_records_total", 10000)),
        "tool": tool,
    }
    if email:
        params["email"] = email
    if api_key:
        params["api_key"] = api_key
    data = request_json(PUBMED_ESEARCH, params=params)
    search_result = data.get("esearchresult") or {}
    ids = search_result.get("idlist") or []
    audit["query_ids"] = len(ids)
    log(f"  query IDs: {len(ids)}")
    time.sleep(0.12 if api_key else 0.35)

    records: list[dict[str, Any]] = []
    batch_size = int(pm_cfg["batch_size"])
    for start in range(0, len(ids), batch_size):
        batch = ids[start:start + batch_size]
        efetch_params: dict[str, Any] = {
            "db": "pubmed", "id": ",".join(batch), "retmode": "xml", "tool": tool,
        }
        if email:
            efetch_params["email"] = email
        if api_key:
            efetch_params["api_key"] = api_key
        xml_text = request_text(PUBMED_EFETCH, params=efetch_params)
        root = ET.fromstring(xml_text)
        articles = root.findall("PubmedArticle")
        audit["records_fetched"] += len(articles)
        exclusion_counts = Counter(audit.get("second_stage_exclusion_reasons") or {})
        tier_counts = Counter(audit.get("inclusion_tiers") or {})
        for article in articles:
            rec, evidence = parse_pubmed_article(article, categories, enso_terms, screen_cfg)
            if evidence.get("reason_code") == "title_gate":
                audit["title_gate_excluded"] += 1
            else:
                audit["title_gate_candidates"] += 1
            if rec is not None:
                records.append(rec)
                audit["second_stage_included"] += 1
                tier_counts[evidence.get("tier") or "included"] += 1
            else:
                if evidence.get("reason_code") != "title_gate":
                    audit["second_stage_excluded"] += 1
                    exclusion_counts[evidence.get("reason_code") or "other"] += 1
        audit["second_stage_exclusion_reasons"] = dict(exclusion_counts)
        audit["inclusion_tiers"] = dict(tier_counts)
        log(
            f"  fetched {min(start + batch_size, len(ids))}/{len(ids)}; "
            f"final screening kept {len(records)}"
        )
        time.sleep(0.12 if api_key else 0.35)
    return records, audit


def dedup_key(record: dict[str, Any]) -> str:
    doi = normalize_doi(record.get("doi"))
    if doi:
        return f"doi:{doi}"
    pmid = str(record.get("pmid") or "").strip()
    if pmid:
        return f"pmid:{pmid}"
    title = normalize_text(record.get("title") or "")
    year = record.get("year") or ""
    if title:
        return f"title:{title}|{year}"
    return record.get("id") or f"anon:{id(record)}"


def merge_inclusion(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    a = a or {}
    b = b or {}
    tiers = [x for x in [a.get("tier"), b.get("tier")] if x]
    tier = "included_direct" if "included_direct" in tiers else (tiers[0] if tiers else "included_broad")
    reason_codes = unique_preserve(
        [x for x in [a.get("reason_code"), b.get("reason_code")] if x]
    )
    reasons = unique_preserve([x for x in [a.get("reason"), b.get("reason")] if x])
    return {
        "criterion": "enso_health_title_plus_false_positive_exclusions",
        "tier": tier,
        "reason_code": reason_codes[0] if len(reason_codes) == 1 else reason_codes,
        "reason": reasons[0] if len(reasons) == 1 else reasons,
        "enso_title_matches": unique_preserve(
            list(a.get("enso_title_matches") or []) + list(b.get("enso_title_matches") or [])
        ),
        "health_title_matches": unique_preserve(
            list(a.get("health_title_matches") or []) + list(b.get("health_title_matches") or [])
        ),
    }


def merge_two(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    prefer_longer = {"title", "abstract"}
    prefer_nonempty = {
        "year", "publication_date", "journal", "doi", "pmid", "openalex_id",
        "oa_url", "landing_page_url", "type"
    }
    list_fields = {"authors", "health_topics", "enso_phases", "source_databases", "affiliation_countries"}

    for field in prefer_longer:
        av, bv = str(out.get(field) or ""), str(b.get(field) or "")
        if len(bv) > len(av):
            out[field] = b.get(field)
    for field in prefer_nonempty:
        if not out.get(field) and b.get(field):
            out[field] = b.get(field)
    for field in list_fields:
        out[field] = unique_preserve(list(out.get(field) or []) + list(b.get(field) or []))

    out["inclusion"] = merge_inclusion(out.get("inclusion"), b.get("inclusion"))
    out["cited_by_count"] = max(int(out.get("cited_by_count") or 0), int(b.get("cited_by_count") or 0))
    out["is_oa"] = bool(out.get("is_oa") or b.get("is_oa"))
    if out.get("doi"):
        out["id"] = f"doi:{normalize_doi(out['doi'])}"
    elif out.get("pmid"):
        out["id"] = f"pmid:{out['pmid']}"
    return out


def safe_title_merge(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Allow title+year fallback only when identifiers do not contradict each other."""
    doi_a, doi_b = normalize_doi(a.get("doi")), normalize_doi(b.get("doi"))
    if doi_a and doi_b and doi_a != doi_b:
        return False
    pmid_a, pmid_b = str(a.get("pmid") or ""), str(b.get("pmid") or "")
    if pmid_a and pmid_b and pmid_a != pmid_b:
        return False
    return True


def deduplicate(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stats = {
        "input_records": len(records),
        "first_pass_merges": 0,
        "first_pass_merges_by_key": {"doi": 0, "pmid": 0, "title_year": 0, "other": 0},
        "safe_title_year_merges": 0,
        "title_year_collisions_kept_separate": 0,
        "output_records": 0,
    }

    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        key = dedup_key(record)
        if key in merged:
            merged[key] = merge_two(merged[key], record)
            stats["first_pass_merges"] += 1
            prefix = key.split(":", 1)[0]
            bucket = {"doi": "doi", "pmid": "pmid", "title": "title_year"}.get(prefix, "other")
            stats["first_pass_merges_by_key"][bucket] += 1
        else:
            merged[key] = record

    # Second pass: catch records from different databases where one source lacks the
    # DOI/PMID, but do not collapse same-title records carrying contradictory IDs.
    by_title: dict[str, list[dict[str, Any]]] = {}
    no_title: list[dict[str, Any]] = []
    for record in merged.values():
        title_key = normalize_text(record.get("title") or "")
        year = record.get("year") or ""
        if not title_key:
            no_title.append(record)
            continue
        group_key = f"{title_key}|{year}"
        bucket = by_title.setdefault(group_key, [])
        merged_here = False
        for idx, existing in enumerate(bucket):
            if safe_title_merge(existing, record):
                bucket[idx] = merge_two(existing, record)
                stats["safe_title_year_merges"] += 1
                merged_here = True
                break
        if not merged_here:
            if bucket:
                stats["title_year_collisions_kept_separate"] += 1
            bucket.append(record)

    output = no_title + [record for bucket in by_title.values() for record in bucket]
    stats["output_records"] = len(output)
    return output, stats


def crossref_enrich(record: dict[str, Any], email: str) -> tuple[dict[str, Any], bool]:
    doi = normalize_doi(record.get("doi"))
    if not doi:
        return record, False
    url = f"{CROSSREF_URL}/{quote(doi, safe='')}"
    params = {"mailto": email} if email else None

    try:
        response = SESSION.get(url, params=params, timeout=45)
        if response.status_code == 404:
            log(f"  Crossref not found: {doi}")
            return record, False
        response.raise_for_status()
        data = response.json()
        msg = data.get("message") or {}
    except (requests.RequestException, ValueError) as exc:
        log(f"  Crossref skipped {doi}: {exc}")
        return record, False

    title = clean_text((msg.get("title") or [""])[0])
    journal = clean_text((msg.get("container-title") or [""])[0])
    cr_authors = []
    for author in msg.get("author") or []:
        name = clean_text(f"{author.get('given', '')} {author.get('family', '')}")
        if name:
            cr_authors.append(name)

    if not record.get("title") and title:
        record["title"] = title
    if not record.get("journal") and journal:
        record["journal"] = journal
    if not record.get("authors") and cr_authors:
        record["authors"] = cr_authors
    if not record.get("landing_page_url"):
        record["landing_page_url"] = msg.get("URL") or f"https://doi.org/{doi}"
    record["source_databases"] = unique_preserve(list(record.get("source_databases") or []) + ["Crossref"])
    return record, True


def enrich_crossref(records: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    cr_cfg = config.get("crossref") or {}
    report = {"attempted": 0, "successful": 0, "failed_or_not_found": 0}
    if not cr_cfg.get("enabled", True):
        return records, report
    email = os.getenv("CONTACT_EMAIL", "").strip()
    maximum = int(cr_cfg.get("max_enrichments_per_run", 500))

    for i, rec in enumerate(records):
        if report["attempted"] >= maximum:
            break
        needs = bool(rec.get("doi")) and (
            not rec.get("journal") or not rec.get("authors") or not rec.get("landing_page_url")
        )
        if not needs:
            continue
        report["attempted"] += 1
        updated, success = crossref_enrich(rec, email)
        records[i] = updated
        if success:
            report["successful"] += 1
        else:
            report["failed_or_not_found"] += 1
        if report["attempted"] % 50 == 0:
            log(
                "Crossref: "
                f"attempted {report['attempted']}; successful {report['successful']}; "
                f"failed/not found {report['failed_or_not_found']}"
            )
        time.sleep(0.06)

    log(
        "Crossref summary: "
        f"attempted {report['attempted']}; successful {report['successful']}; "
        f"failed/not found {report['failed_or_not_found']}"
    )
    return records, report


def finalize(
    records: list[dict[str, Any]],
    categories: dict[str, list[str]],
    enso_terms: list[str],
    screen_cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Reapply both title stages after merges/enrichment.

    Only clear false positives are removed in the second stage. Generic health and
    mechanistic/vector records are retained according to the project's policy.
    """
    result: list[dict[str, Any]] = []
    report = {
        "input_records": len(records),
        "title_gate_excluded": 0,
        "second_stage_excluded": 0,
        "second_stage_exclusion_reasons": {},
        "inclusion_tiers": {},
        "output_records": 0,
    }
    exclusion_counts = Counter()
    tier_counts = Counter()

    for rec in records:
        rec["doi"] = normalize_doi(rec.get("doi"))
        rec["title"] = clean_text(rec.get("title"))
        rec["abstract"] = clean_text(rec.get("abstract"))
        rec["journal"] = clean_text(rec.get("journal"))
        rec["authors"] = unique_preserve(
            clean_text(x) for x in rec.get("authors") or [] if clean_text(x)
        )

        title_evidence = screen_title(rec["title"], enso_terms, categories)
        evidence = second_stage_screen(rec["title"], title_evidence, screen_cfg)
        if not evidence["included"]:
            if evidence.get("reason_code") == "title_gate":
                report["title_gate_excluded"] += 1
            else:
                report["second_stage_excluded"] += 1
                exclusion_counts[evidence.get("reason_code") or "other"] += 1
            continue

        tier_counts[evidence.get("tier") or "included"] += 1
        rec["health_topics"] = evidence["health_categories"]
        rec["enso_phases"] = classify_enso_from_title(rec["title"])
        rec["inclusion"] = {
            "criterion": "enso_health_title_plus_false_positive_exclusions",
            "tier": evidence.get("tier"),
            "reason_code": evidence.get("reason_code"),
            "reason": evidence.get("second_stage_reason"),
            "enso_title_matches": evidence["enso_title_matches"],
            "health_title_matches": evidence["health_title_matches"],
        }
        rec["source_databases"] = sorted(set(rec.get("source_databases") or []))
        rec["affiliation_countries"] = sorted(set(rec.get("affiliation_countries") or []))
        if rec.get("doi") and not rec.get("landing_page_url"):
            rec["landing_page_url"] = f"https://doi.org/{rec['doi']}"
        if rec.get("doi"):
            rec["id"] = f"doi:{rec['doi']}"
        elif rec.get("pmid"):
            rec["id"] = f"pmid:{rec['pmid']}"
        if rec.get("title"):
            result.append(rec)

    report["second_stage_exclusion_reasons"] = dict(exclusion_counts)
    report["inclusion_tiers"] = dict(tier_counts)
    report["output_records"] = len(result)

    result = sorted(
        result,
        key=lambda r: (
            r.get("publication_date") or str(r.get("year") or ""),
            int(r.get("cited_by_count") or 0),
        ),
        reverse=True,
    )
    return result, report


def build_stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts = Counter()
    topic_counts = Counter()
    year_counts = Counter()
    for rec in records:
        source_counts.update(rec.get("source_databases") or [])
        topic_counts.update(rec.get("health_topics") or [])
        if rec.get("year"):
            year_counts[str(rec["year"])] += 1
    return {
        "total": len(records),
        "open_access": sum(1 for r in records if r.get("is_oa")),
        "sources": dict(source_counts.most_common()),
        "topics": dict(topic_counts.most_common()),
        "years": dict(sorted(year_counts.items())),
    }


def source_overlap(records: list[dict[str, Any]]) -> dict[str, int]:
    overlap = Counter()
    for rec in records:
        sources = set(rec.get("source_databases") or [])
        has_oa = "OpenAlex" in sources
        has_pm = "PubMed" in sources
        if has_oa and has_pm:
            overlap["OpenAlex + PubMed"] += 1
        elif has_oa:
            overlap["OpenAlex only"] += 1
        elif has_pm:
            overlap["PubMed only"] += 1
        else:
            overlap["Other"] += 1
    return dict(overlap)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    temp.replace(path)


def main() -> int:
    config = load_config()
    screen_cfg = load_direct_screening_config()
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    log("=" * 72)
    log("ENSO & Health Research Monitor — bibliographic update v1.4")
    log("Inclusion rule: ENSO + health in TITLE; exclude only clear false positives")
    log("=" * 72)

    openalex_audit: dict[str, Any] = {"status": "failed"}
    pubmed_audit: dict[str, Any] = {"status": "failed"}

    try:
        openalex, openalex_audit = fetch_openalex(config, screen_cfg)
        openalex_audit["status"] = "ok"
        log(f"OpenAlex final-screened records: {len(openalex)}")
    except Exception as exc:
        log(f"WARNING: OpenAlex failed: {exc}")
        openalex = []
        openalex_audit = {"status": "failed", "error": str(exc)}

    try:
        pubmed, pubmed_audit = fetch_pubmed(config, screen_cfg)
        pubmed_audit["status"] = "ok"
        log(f"PubMed final-screened records: {len(pubmed)}")
    except Exception as exc:
        log(f"WARNING: PubMed failed: {exc}")
        pubmed = []
        pubmed_audit = {"status": "failed", "error": str(exc)}

    if not openalex and not pubmed:
        raise RuntimeError("Both discovery sources failed or returned zero screened records; refusing to overwrite the existing dataset.")

    combined_input = openalex + pubmed
    records, dedup_audit = deduplicate(combined_input)
    log(f"After deduplication: {len(records)}")

    records, crossref_audit = enrich_crossref(records, config)
    records, final_screen_audit = finalize(records, config["health_categories"], config["enso_terms"], screen_cfg)

    stats = build_stats(records)
    payload = {
        "generated_at": generated_at,
        "method_version": "1.4.0",
        "inclusion_criterion": config.get("inclusion") or {},
        "stats": stats,
        "publications": records,
    }

    audit_payload = {
        "generated_at": generated_at,
        "method_version": "1.4.0",
        "criterion": {
            "strategy": "title_first_with_false_positive_exclusions",
            "required": ["ENSO term in title", "human-health term in title"],
            "abstract_used_for_inclusion": False,
            "included_tiers": ["included_direct", "included_broad"],
            "excluded_clear_false_positives": [
                "Spanish 'el niño' meaning child",
                "non-human/ecological/veterinary/agricultural outcomes",
                "meteorological outbreaks",
                "corrections/errata/retractions",
                "lexical place-name false positives",
                "food-security/famine/nutrition-only titles without a direct health outcome"
            ],
            "generic_health_titles_included": True,
            "mechanistic_vector_titles_included": True,
        },
        "discovery": {
            "openalex": openalex_audit,
            "pubmed": pubmed_audit,
        },
        "screened_input_records": len(combined_input),
        "deduplication": dedup_audit,
        "post_merge_final_screen": final_screen_audit,
        "crossref": crossref_audit,
        "source_overlap_final": source_overlap(records),
        "final": stats,
    }

    write_json_atomic(OUTPUT_PATH, payload)
    write_json_atomic(AUDIT_PATH, audit_payload)

    log(f"Saved catalogue: {OUTPUT_PATH}")
    log(f"Saved audit report: {AUDIT_PATH}")
    log(f"Final publications: {len(records)}")
    log(f"Source overlap: {source_overlap(records)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        raise SystemExit(1)
