"""
fetch_jobs.py — Adzuna API üzerinden Barcelona'da Finance Director / Head of FP&A ilanlarını arar.
Hedef şirket listesiyle eşleştirir ve JSON olarak kaydeder.
"""

import os
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
    "Finance Director",
    "Head of FP&A",
    "FP&A Director",
    "CFO",
    "Finance Controller",
    "Head of Finance",
    "Senior Finance Manager",
    "VP Finance",
]

# Location filter
LOCATION = "Barcelona"

# Max results per query (increased to get all open roles)
RESULTS_PER_PAGE = 100


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
        "content-type": "application/json",
        "sort_by": "date",
        "page": page,
    }

    try:
        response = requests.get(f"{BASE_URL}/{page}", params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ API error for query '{query}': {e}")
        return []


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


def fetch_all_jobs():
    """Run all search queries and collect matching jobs."""
    target_companies = load_target_companies()
    print(f"📋 Loaded {len(target_companies)} target companies")

    all_jobs = {}  # Use dict to deduplicate by job ID
    unmatched_jobs = []  # Jobs that don't match target list but are relevant

    for query in SEARCH_QUERIES:
        print(f"\n🔍 Searching: '{query}' in {LOCATION}...")
        results = search_adzuna(query)
        print(f"   Found {len(results)} results")

        for job in results:
            job_id = job.get("id", "")
            if job_id in all_jobs:
                continue  # Skip duplicates

            company_name = job.get("company", {}).get("display_name", "Unknown")
            matched_target = match_company(company_name, target_companies)

            job_entry = {
                "id": str(job_id),
                "title": job.get("title", ""),
                "company": company_name,
                "location": job.get("location", {}).get("display_name", "Barcelona"),
                "description": job.get("description", ""),
                "salary_min": job.get("salary_min"),
                "salary_max": job.get("salary_max"),
                "url": job.get("redirect_url", ""),
                "created": job.get("created", ""),
                "contract_type": job.get("contract_type", ""),
                "category": job.get("category", {}).get("label", ""),
                "matched_query": query,
            }

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

    return result


if __name__ == "__main__":
    fetch_all_jobs()
