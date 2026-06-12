"""
fetch_jobs.py — Adzuna API üzerinden Barcelona'da Finance Director / Head of FP&A ilanlarını arar.
Hedef şirket listesiyle eşleştirir ve JSON olarak kaydeder.
"""

import os
import re
import sys
import json
import requests
from datetime import datetime, timedelta

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")
BASE_URL = "https://api.adzuna.com/v1/api/jobs/es/search"

# Search queries — covers Director, FP&A, CFO, Controller titles
SEARCH_QUERIES = [
    # English titles (MNC / English-first postings)
    "Finance Director",
    "Head of FP&A",
    "FP&A Director",
    "CFO",
    "Finance Controller",
    "Head of Finance",
    "Senior Finance Manager",
    "VP Finance",
    # Spanish titles (Adzuna ES inventory is mostly Spanish-language)
    "Director Financiero",
    "Controller Financiero",
    "Responsable Financiero",
    "Director de Finanzas",
]

# Only fetch jobs posted within the last N days (weekly digest)
MAX_DAYS_OLD = 8

# Location filter
LOCATION = "Barcelona"

# Max results per query — Adzuna API hard limit is 50 per page
RESULTS_PER_PAGE = 50

# Intern / junior pre-screen — drop these titles before they ever reach scoring.
# Word-boundary match so "intern" never swallows "International"/"Internal" finance roles.
EXCLUDED_TITLE_KEYWORDS = [
    "intern", "internship", "prácticas", "practicas", "becario", "becaria",
    "trainee", "working student", "graduate program", "apprentice",
]
_EXCLUDED_TITLE_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in EXCLUDED_TITLE_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def is_excluded_title(title):
    """True if the title is an intern/junior/trainee role we never want to surface."""
    return bool(_EXCLUDED_TITLE_RE.search(title or ""))


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


def normalize_company_name(name):
    """Normalize company name for fuzzy matching."""
    if not name:
        return ""
    # Remove common suffixes and lowercase
    name = name.lower().strip()
    for suffix in [" s.a.", " s.l.", " sa", " sl", " inc.", " inc", " ltd", " gmbh",
                   " iberia", " spain", " españa", " barcelona", " bcn", " europe",
                   " emea", " global"]:
        name = name.replace(suffix, "")
    return name.strip()


def match_company(job_company, target_companies):
    """Check if a job's company matches any target company. Returns match or None."""
    if not job_company:
        return None

    job_normalized = normalize_company_name(job_company)

    for target in target_companies:
        target_normalized = normalize_company_name(target["name"])

        # Direct match
        if target_normalized in job_normalized or job_normalized in target_normalized:
            return target

        # Check individual words for multi-word companies (e.g. "Coty" in "Coty Inc.")
        target_words = target_normalized.split()
        if len(target_words) >= 1:
            primary_word = target_words[0]
            if len(primary_word) >= 4 and primary_word in job_normalized:
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

            # Intern / junior pre-screen — drop before scoring spend
            if is_excluded_title(title):
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

    # Combine matched + unmatched (up to 20 unmatched)
    matched_list = list(all_jobs.values())
    unmatched_list = unmatched_jobs[:20]

    result = {
        "fetch_date": datetime.utcnow().isoformat(),
        "total_matched": len(matched_list),
        "total_unmatched_sampled": len(unmatched_list),
        "matched_jobs": matched_list,
        "unmatched_jobs": unmatched_list,
    }

    # Save to output file
    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "fetched_jobs.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"✅ Fetch complete!")
    print(f"   Matched jobs: {len(matched_list)}")
    print(f"   Unmatched sample: {len(unmatched_list)}")
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
