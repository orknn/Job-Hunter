"""
fetch_jobs.py — Adzuna API üzerinden Barcelona'da Finance Director / Head of FP&A ilanlarını arar.
Hedef şirket listesiyle eşleştirir ve JSON olarak kaydeder.
"""

import os
import re
import sys
import json
import requests
from datetime import datetime

from titles import is_finance_title, title_gate, title_lane

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")
BASE_URL = "https://api.adzuna.com/v1/api/jobs/es/search"

# Search queries. Adzuna matches these words anywhere in the ad, so they are
# only a recall net — titles.title_gate() decides what actually goes on.
SEARCH_QUERIES = [
    # Target lane — Director / Head / VP / CFO
    "Finance Director",
    "Head of FP&A",
    "FP&A Director",
    "CFO",
    "Head of Finance",
    "VP Finance",
    "Head of Controlling",
    # Step-down lane — Senior Manager / Senior Finance Business Partner
    "Senior Finance Manager",
    "Senior Manager Finance",
    "Finance Business Partner",
    "FP&A Manager",
    "Commercial Finance",
    "Strategic Finance",
    # Spanish titles (Adzuna ES inventory is mostly Spanish-language)
    "Director Financiero",
    "Responsable Financiero",
    "Director de Finanzas",
]

# Only fetch jobs posted within the last N days (weekly digest)
MAX_DAYS_OLD = 8

# Location filter
LOCATION = "Barcelona"

# Max results per query — Adzuna API hard limit is 50 per page
RESULTS_PER_PAGE = 50

# Safety valve only — the title gate, not this cap, is what keeps the pool
# small. The old cap of 20 filled up from the first three queries and silently
# discarded everything the other nine found.
MAX_UNMATCHED = 80


def load_target_companies():
    """Load target companies from JSON file."""
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "target_companies.json")
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def search_adzuna(query, page=1):
    """Search Adzuna API for jobs matching query in Barcelona."""
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        print("ERROR: ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables required.")
        sys.exit(1)

    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "results_per_page": RESULTS_PER_PAGE,
        "what": query,
        "where": LOCATION,
        "max_days_old": MAX_DAYS_OLD,
        "content-type": "application/json",
        "sort_by": "date",
    }

    try:
        response = requests.get(f"{BASE_URL}/{page}", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("results", []), None
    except requests.exceptions.RequestException as e:
        # Surface the API's own error message — critical for debugging
        detail = ""
        if getattr(e, "response", None) is not None:
            detail = f" | body: {e.response.text[:200]}"
        print(f"  ⚠ API error for query '{query}': {e}{detail}")
        return [], str(e)


_COMPANY_SUFFIXES = [" s.a.", " s.l.", " sa", " sl", " inc.", " inc", " ltd", " gmbh",
                     " iberia", " spain", " españa", " barcelona", " bcn", " europe",
                     " emea", " global"]
# First words too generic to identify a company on their own ("Deutsche Bank"
# must not claim "Deutsche Telekom").
_GENERIC_FIRST_WORDS = {"deutsche", "grupo", "banco", "the", "new", "wolters"}


def normalize_company_name(name):
    """Normalize company name for matching."""
    if not name:
        return ""
    name = name.lower().strip()
    for suffix in _COMPANY_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def _has_words(needle, haystack):
    """True when `needle` appears in `haystack` as whole words."""
    return bool(needle) and bool(
        re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack))


def _name_variants(target):
    """Every name a target company may appear under in a job feed.

    "SEAT / CUPRA" and "Vueling (IAG)" list two names in one field; `aliases`
    carries the ones the name cannot express (TravelPerk now posts as "Perk").
    """
    raw = [target["name"]] + list(target.get("aliases", []))
    variants = []
    for name in raw:
        for part in re.split(r"[/()]", name):
            part = normalize_company_name(part)
            if part:
                variants.append(part)
    return variants


def match_company(job_company, target_companies):
    """Check if a job's company matches any target company. Returns match or None.

    Whole-word matching: the earlier substring test let "ABB" claim "AbbVie"
    and would have let "King" claim "Booking".
    """
    if not job_company:
        return None

    job_normalized = normalize_company_name(job_company)

    for target in target_companies:
        for variant in _name_variants(target):
            if _has_words(variant, job_normalized):
                return target
            # First word of a multi-word name ("Cellnex" for "Cellnex Telecom")
            words = variant.split()
            if (len(words) > 1 and len(words[0]) >= 4
                    and words[0] not in _GENERIC_FIRST_WORDS
                    and _has_words(words[0], job_normalized)):
                return target

    return None


# ──────────────────────────────────────────────
# Language fit classification
# ──────────────────────────────────────────────
# Rule: a Spanish-language ad with no English-environment signals almost
# always targets local Spanish-native candidates → not viable (B1 Spanish).
# Exception: target-list MNCs and ads with explicit English signals, since
# recruiters often post English-first roles using Spanish templates.

SPANISH_MARKERS = [
    " para ", " empresa ", " buscamos ", " experiencia ", " años ",
    " funciones ", " requisitos ", " conocimientos ", " gestión ",
    " equipo ", " sector ", " imprescindible ", " valorable ", " puesto ",
]

ENGLISH_ENV_SIGNALS = [
    "working language", "english-speaking", "english speaking",
    "international environment", "entorno internacional",
    "multinational", "multinacional", "english is", "fluent english",
    "english fluency", "global team", "emea", "headquarters", "shared service",
    "ingles nativo", "inglés nativo", "english native",
]

# Spanish ads explicitly demanding native/perfect Spanish → hard local signal
SPANISH_NATIVE_SIGNALS = [
    "español nativo", "castellano nativo", "catalán", "català",
    "nivel nativo de español",
]


def classify_language_fit(job_entry, is_target_match):
    """Classify ad language fit. Returns (tag, keep_in_pool)."""
    text = f"{job_entry.get('title','')} {job_entry.get('description','')}".lower()

    spanish_score = sum(1 for m in SPANISH_MARKERS if m in text)
    is_spanish_ad = spanish_score >= 2

    if not is_spanish_ad:
        return "ENGLISH_AD", True

    # Spanish ad demanding native Spanish/Catalan → local role, drop
    # unless it's a target-list company (let scoring decide with full context)
    if any(s in text for s in SPANISH_NATIVE_SIGNALS):
        return "SPANISH_LOCAL_NATIVE_REQ", is_target_match

    if any(s in text for s in ENGLISH_ENV_SIGNALS):
        return "SPANISH_AD_ENGLISH_SIGNALS", True

    if is_target_match:
        return "SPANISH_AD_TARGET_MNC", True

    return "SPANISH_LOCAL", False


def fetch_all_jobs():
    """Run all search queries and collect matching jobs."""
    target_companies = load_target_companies()
    print(f"📋 Loaded {len(target_companies)} target companies")

    all_jobs = {}  # Use dict to deduplicate by job ID
    unmatched_jobs = []  # Jobs that don't match target list but are relevant
    dropped = []  # finance-titled postings the title gate cut, kept for audit
    seen_pairs = set()  # normalized (title, company) — kills Adzuna's same-job-many-ids dupes

    failed_queries = []
    for query in SEARCH_QUERIES:
        print(f"\n🔍 Searching: '{query}' in {LOCATION}...")
        results, error = search_adzuna(query)
        if error:
            failed_queries.append(query)
        print(f"   Found {len(results)} results")

        for job in results:
            job_id = job.get("id", "")
            if job_id in all_jobs:
                continue  # Skip duplicates

            title = job.get("title", "")
            company_name = job.get("company", {}).get("display_name", "Unknown")

            # Title gate — applies to target-list companies too: a company
            # match alone once sent "Senior Software Engineer" to scoring.
            reason = title_gate(title)
            if reason:
                # Queries overlap, so the same posting is cut many times over.
                pair = (title.lower().strip(), company_name.lower().strip())
                if is_finance_title(title) and pair not in seen_pairs:
                    seen_pairs.add(pair)
                    dropped.append({"title": title, "company": company_name,
                                    "reason": reason, "source": "adzuna"})
                continue

            # Normalized pair dedupe (in addition to id-based) — Adzuna reposts
            # the same role under different ids, e.g. duplicate "Corporate Controller".
            pair = (title.lower().strip(), company_name.lower().strip())
            if pair in seen_pairs:
                continue

            matched_target = match_company(company_name, target_companies)

            job_entry = {
                "id": str(job_id),
                "title": title,
                "company": company_name,
                "location": job.get("location", {}).get("display_name", "Barcelona"),
                "description": job.get("description", ""),
                "salary_min": job.get("salary_min"),
                "salary_max": job.get("salary_max"),
                "salary_is_predicted": job.get("salary_is_predicted"),
                "url": job.get("redirect_url", ""),
                "created": job.get("created", ""),
                "contract_type": job.get("contract_type", ""),
                "category": job.get("category", {}).get("label", ""),
                "matched_query": query,
            }

            lang_tag, keep = classify_language_fit(job_entry, matched_target is not None)
            job_entry["language_fit"] = lang_tag
            if not keep:
                continue  # Spanish-local ad → not viable for non-native candidate

            seen_pairs.add(pair)
            if matched_target:
                job_entry["target_match"] = {
                    "name": matched_target["name"],
                    "tier": matched_target["tier"],
                    "tc_min": matched_target["tc_min"],
                    "tc_max": matched_target["tc_max"],
                    "notes": matched_target.get("notes", ""),
                }
                all_jobs[job_id] = job_entry
                print(f"   ✅ MATCH: {company_name} → {matched_target['name']} (Tier {matched_target['tier']})")
            else:
                # Keep unmatched jobs too — AI can evaluate them
                job_entry["target_match"] = None
                unmatched_jobs.append(job_entry)

    # Combine matched + unmatched. Director/Head titles first, so that if the
    # safety cap ever bites it cuts the step-down lane, not the target one.
    matched_list = list(all_jobs.values())
    unmatched_jobs.sort(key=lambda j: title_lane(j["title"]) != "target")
    unmatched_list = unmatched_jobs[:MAX_UNMATCHED]
    if len(unmatched_jobs) > MAX_UNMATCHED:
        print(f"⚠ {len(unmatched_jobs) - MAX_UNMATCHED} unmatched jobs over the cap were not scored")

    result = {
        "fetch_date": datetime.utcnow().isoformat(),
        "total_matched": len(matched_list),
        "total_unmatched_sampled": len(unmatched_list),
        "matched_jobs": matched_list,
        "unmatched_jobs": unmatched_list,
        "dropped": dropped,
    }

    # Save to output file
    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "fetched_jobs.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"✅ Fetch complete!")
    print(f"   Matched jobs: {len(matched_list)}")
    print(f"   Unmatched: {len(unmatched_list)}")
    print(f"   Finance titles cut by the title gate: {len(dropped)}")
    print(f"   Saved to: {output_path}")

    # Fail the workflow loudly if every single query errored —
    # otherwise we silently email a 0-job digest forever.
    if failed_queries and len(failed_queries) == len(SEARCH_QUERIES):
        print(f"\n❌ ALL {len(SEARCH_QUERIES)} queries failed. Check API credentials / parameters.")
        sys.exit(1)
    elif failed_queries:
        print(f"\n⚠ {len(failed_queries)} queries failed: {failed_queries}")

    return result


if __name__ == "__main__":
    fetch_all_jobs()
