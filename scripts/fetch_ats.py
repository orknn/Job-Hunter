"""
fetch_ats.py — Hedef şirketlerin kariyer sayfalarını (ATS) doğrudan sorgular.

Adzuna'nın görmediği ilanları kaynağından çeker: Workday, Greenhouse, Lever,
SmartRecruiters, Amazon Jobs ve Microsoft Careers public JSON endpoint'leri.

Kullanım:
  python scripts/fetch_ats.py            # fetch + fetched_jobs.json'a merge
  python scripts/fetch_ats.py --verify   # her şirketin endpoint'ini test et, rapor bas
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

# Reuse language classification from the Adzuna fetcher
sys.path.insert(0, os.path.dirname(__file__))
from fetch_jobs import classify_language_fit  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0 (Job-Hunter weekly digest; personal use)"}
TIMEOUT = 20

# Finance-role title keywords (EN + ES). A job must match at least one.
TITLE_KEYWORDS = [
    "finance", "financial", "fp&a", "fpa", "controller", "controlling",
    "cfo", "treasury", "financiero", "financiera", "finanzas", "contabilidad",
]

# Location must match at least one (lowercase substring match)
LOCATION_KEYWORDS = [
    "barcelona", "catalonia", "cataluña", "catalunya", "spain", "españa",
    "espana", "madrid", "remote - emea", "emea remote", "remote, spain",
]

# Seniority hint — used only for soft prioritization, not exclusion
SENIOR_HINTS = ["director", "head", "lead", "senior", "vp", "manager", "chief", "responsable"]


# ──────────────────────────────────────────────
# ATS Adapters — each returns a list of raw job dicts:
# {title, location, url, description, posted}
# ──────────────────────────────────────────────

def fetch_workday(cfg, search_text="finance"):
    """Workday CXS public API. Tries candidate (wd, site) combos until one works."""
    tenant = cfg["tenant"]
    wd_candidates = cfg.get("wd_candidates", ["wd3", "wd1", "wd5"])
    site_candidates = cfg.get(
        "site_candidates",
        ["Careers", "External", f"{tenant}careers", f"{tenant.capitalize()}_Careers", tenant],
    )

    for wd in wd_candidates:
        for site in site_candidates:
            url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
            try:
                r = requests.post(
                    url,
                    json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": search_text},
                    headers={**HEADERS, "Content-Type": "application/json"},
                    timeout=TIMEOUT,
                )
                if r.status_code != 200:
                    continue
                data = r.json()
                postings = data.get("jobPostings", [])
                # Cache the working combo so subsequent queries skip discovery
                cfg["_resolved"] = {"wd": wd, "site": site}
                base = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}"
                return [
                    {
                        "title": p.get("title", ""),
                        "location": p.get("locationsText", ""),
                        "url": base + p.get("externalPath", ""),
                        "description": p.get("title", ""),  # listing has no body; title only
                        "posted": p.get("postedOn", ""),
                    }
                    for p in postings
                ]
            except requests.exceptions.RequestException:
                continue
    return None  # signals: endpoint not found


def fetch_greenhouse(cfg, **_):
    board = cfg["board"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        jobs = r.json().get("jobs", [])
        return [
            {
                "title": j.get("title", ""),
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "description": (j.get("content") or "")[:2000],
                "posted": j.get("updated_at", ""),
            }
            for j in jobs
        ]
    except requests.exceptions.RequestException:
        return None


def fetch_lever(cfg, **_):
    company = cfg["company"]
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return [
            {
                "title": j.get("text", ""),
                "location": (j.get("categories") or {}).get("location", "") or "",
                "url": j.get("hostedUrl", ""),
                "description": (j.get("descriptionPlain") or "")[:2000],
                "posted": "",
            }
            for j in r.json()
        ]
    except requests.exceptions.RequestException:
        return None


def fetch_smartrecruiters(cfg, **_):
    company = cfg["company"]
    url = f"https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        content = r.json().get("content", [])
        return [
            {
                "title": j.get("name", ""),
                "location": ", ".join(filter(None, [
                    (j.get("location") or {}).get("city", ""),
                    (j.get("location") or {}).get("country", ""),
                ])),
                "url": f"https://jobs.smartrecruiters.com/{company}/{j.get('id','')}",
                "description": j.get("name", ""),
                "posted": j.get("releasedDate", ""),
            }
            for j in content
        ]
    except requests.exceptions.RequestException:
        return None


def fetch_amazon(cfg, **_):
    url = ("https://www.amazon.jobs/en/search.json"
           "?base_query=finance&normalized_country_code=ESP&result_limit=50&sort=recent")
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return [
            {
                "title": j.get("title", ""),
                "location": j.get("normalized_location", "") or j.get("location", ""),
                "url": "https://www.amazon.jobs" + j.get("job_path", ""),
                "description": (j.get("description_short") or j.get("description") or "")[:2000],
                "posted": j.get("posted_date", ""),
            }
            for j in r.json().get("jobs", [])
        ]
    except requests.exceptions.RequestException:
        return None


def fetch_microsoft(cfg, **_):
    url = ("https://gcsservices.careers.microsoft.com/search/api/v1/search"
           "?q=finance&lc=Barcelona%2C%20Spain&l=en_us&pg=1&pgSz=20")
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        jobs = (((r.json().get("operationResult") or {}).get("result") or {}).get("jobs")) or []
        return [
            {
                "title": j.get("title", ""),
                "location": ", ".join(j.get("properties", {}).get("locations", []) or []),
                "url": f"https://jobs.careers.microsoft.com/global/en/job/{j.get('jobId','')}",
                "description": (j.get("properties", {}).get("description") or "")[:2000],
                "posted": j.get("postingDate", ""),
            }
            for j in jobs
        ]
    except requests.exceptions.RequestException:
        return None


ADAPTERS = {
    "workday": fetch_workday,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
    "amazon": fetch_amazon,
    "microsoft": fetch_microsoft,
}


# ──────────────────────────────────────────────
# Filtering & pipeline
# ──────────────────────────────────────────────

def is_finance_role(title):
    t = title.lower()
    return any(k in t for k in TITLE_KEYWORDS)


def is_target_location(location):
    loc = (location or "").lower()
    # Workday MNCs sometimes list "Spain" only or multi-location strings
    return any(k in loc for k in LOCATION_KEYWORDS)


def load_companies():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "target_companies.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_company(company):
    """Fetch + filter jobs for a single company. Returns (jobs, status)."""
    ats = company.get("ats")
    if not ats or ats.get("type") in (None, "none", "todo"):
        return [], "SKIPPED (no ATS config)"

    adapter = ADAPTERS.get(ats["type"])
    if not adapter:
        return [], f"SKIPPED (unknown type {ats['type']})"

    raw = adapter(ats)
    if raw is None:
        return [], "ENDPOINT FAILED"

    jobs = []
    for r in raw:
        if not is_finance_role(r["title"]):
            continue
        # Greenhouse/Lever scaleups are Barcelona-based → looser location check
        if ats["type"] in ("workday", "smartrecruiters", "microsoft", "amazon"):
            if not is_target_location(r["location"]):
                continue
        entry = {
            "id": f"ats-{company['name'][:12].replace(' ','')}-{abs(hash(r['url'])) % 10**8}",
            "title": r["title"],
            "company": company["name"],
            "location": r["location"] or "Spain",
            "description": r["description"],
            "salary_min": None,
            "salary_max": None,
            "url": r["url"],
            "created": r["posted"],
            "contract_type": "",
            "category": "Finance (ATS direct)",
            "matched_query": f"ats:{ats['type']}",
            "target_match": {
                "name": company["name"],
                "tier": company["tier"],
                "tc_min": company["tc_min"],
                "tc_max": company["tc_max"],
                "notes": company.get("notes", ""),
            },
        }
        tag, keep = classify_language_fit(entry, is_target_match=True)
        entry["language_fit"] = tag
        if keep:
            jobs.append(entry)
    return jobs, f"OK ({len(raw)} raw → {len(jobs)} finance/ES)"


def run_fetch():
    companies = load_companies()
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "fetched_jobs.json")

    # Load existing Adzuna results to merge into
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"fetch_date": datetime.utcnow().isoformat(),
                "matched_jobs": [], "unmatched_jobs": []}

    existing_urls = {j.get("url") for j in data.get("matched_jobs", [])}
    added = 0

    print(f"🏢 Polling {len(companies)} target company career sites...\n")
    for company in companies:
        jobs, status = fetch_company(company)
        marker = "✅" if jobs else ("⚠️" if "FAILED" in status else "·")
        print(f"  {marker} {company['name']:35s} {status}")
        for j in jobs:
            if j["url"] not in existing_urls:
                data["matched_jobs"].append(j)
                existing_urls.add(j["url"])
                added += 1
                print(f"       → {j['title']} | {j['location']}")
        time.sleep(0.5)  # be polite

    data["total_matched"] = len(data.get("matched_jobs", []))
    data["ats_jobs_added"] = added

    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"✅ ATS fetch complete — {added} new jobs merged (total matched: {data['total_matched']})")


def run_verify():
    """Test every configured endpoint and print a report. No data is written."""
    companies = load_companies()
    ok, failed, skipped = [], [], []
    print(f"🔬 Verifying ATS endpoints for {len(companies)} companies...\n")
    for company in companies:
        ats = company.get("ats")
        if not ats or ats.get("type") in (None, "none", "todo"):
            skipped.append(company["name"])
            print(f"  · {company['name']:35s} SKIPPED")
            continue
        adapter = ADAPTERS[ats["type"]]
        raw = adapter(ats)
        if raw is None:
            failed.append(company["name"])
            print(f"  ❌ {company['name']:35s} {ats['type']} — ENDPOINT FAILED")
        else:
            resolved = ats.get("_resolved", "")
            ok.append(company["name"])
            print(f"  ✅ {company['name']:35s} {ats['type']} — {len(raw)} postings {resolved}")
        time.sleep(0.5)

    print(f"\n{'='*50}")
    print(f"✅ Working: {len(ok)}  ❌ Failed: {len(failed)}  · Skipped: {len(skipped)}")
    if failed:
        print(f"\nFailed endpoints (fix config or mark as 'none'):")
        for name in failed:
            print(f"  - {name}")


if __name__ == "__main__":
    if "--verify" in sys.argv:
        run_verify()
    else:
        run_fetch()
