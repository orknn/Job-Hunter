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
    # Catalan metro area — where many target HQs actually sit
    "sant cugat", "hospitalet", "sant feliu", "cornella", "cornellà",
    "esplugues", "sant joan despi", "sant joan despí", "el prat", "viladecans",
]

# Seniority hint — used only for soft prioritization, not exclusion
SENIOR_HINTS = ["director", "head", "lead", "senior", "vp", "manager", "chief", "responsable"]


# ──────────────────────────────────────────────
# ATS Adapters — each returns a list of raw job dicts:
# {title, location, url, description, posted}
# ──────────────────────────────────────────────

def _workday_query(tenant, wd, site, search_text, offset):
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    r = requests.post(
        url,
        json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": search_text},
        headers={**HEADERS, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    if r.status_code != 200:
        return None
    return r.json().get("jobPostings", [])


def fetch_workday(cfg, **_):
    """Workday CXS public API. Location goes INTO searchText (Workday matches
    location text in search) and we paginate — top-20 global relevance alone
    rarely surfaces Spain postings for an MNC."""
    tenant = cfg["tenant"]
    wd_candidates = cfg.get("wd_candidates", ["wd3", "wd1", "wd5"])
    site_candidates = cfg.get(
        "site_candidates",
        ["Careers", "External", f"{tenant}careers", f"{tenant.capitalize()}_Careers", tenant],
    )
    search_texts = cfg.get("search_texts", ["finance Spain", "finance Barcelona"])

    # Resolve working (wd, site) combo with a cheap probe
    resolved = None
    for wd in wd_candidates:
        for site in site_candidates:
            try:
                if _workday_query(tenant, wd, site, "finance", 0) is not None:
                    resolved = (wd, site)
                    break
            except requests.exceptions.RequestException:
                continue
        if resolved:
            break
    if not resolved:
        return None

    wd, site = resolved
    cfg["_resolved"] = {"wd": wd, "site": site}
    base = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}"

    seen, out = set(), []
    for st in search_texts:
        for offset in (0, 20, 40):
            try:
                postings = _workday_query(tenant, wd, site, st, offset)
            except requests.exceptions.RequestException:
                break
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append({
                    "title": p.get("title", ""),
                    "location": p.get("locationsText", ""),
                    "url": base + path,
                    "description": p.get("title", ""),
                    "posted": p.get("postedOn", ""),
                })
            time.sleep(0.3)
    return out


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


def fetch_ashby(cfg, **_):
    board = cfg["board"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return [
            {
                "title": j.get("title", ""),
                "location": j.get("location", "") or "",
                "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                "description": (j.get("descriptionPlain") or j.get("title") or "")[:2000],
                "posted": j.get("publishedAt", ""),
            }
            for j in r.json().get("jobs", [])
        ]
    except requests.exceptions.RequestException:
        return None


ADAPTERS = {
    "workday": fetch_workday,
    "ashby": fetch_ashby,
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
        elif len(raw) == 0:
            failed.append(company["name"])
            print(f"  ⚠️ {company['name']:35s} {ats['type']} — 0 postings (config suspect — run discover)")
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





# ──────────────────────────────────────────────
# Discovery mode — probe ATS platforms for companies with broken configs
# ──────────────────────────────────────────────

DISCOVER_ALIASES = {
    "Bayer Iberia": ["bayer", "bayerag"],
    "Microsoft Spain": [],  # custom API, handled separately
    "Coty Inc. BCN": ["coty", "cotyinc"],
    "Grifols": ["grifols", "grifolssa"],
    "Werfen": ["werfen", "werfenlife"],
    "Reckitt Iberia": ["reckitt", "rb", "reckittbenckiser"],
    "TravelPerk": ["travelperk"],
    "Glovo": ["glovo", "glovoapp", "glovoapp23"],
    "Wallbox": ["wallbox", "wallboxchargers"],
    "Zurich Insurance Iberia": ["zurich", "zurichinsurance", "zurichinsurancegroup"],
    "Seedtag": ["seedtag"],
    "Holaluz": ["holaluz"],
    "Genially": ["genially", "geniallyweb"],
    "Snowflake": ["snowflake", "snowflakecomputing", "snowflakeinc"],
    "Puig": ["puig", "puigbrands", "Puig", "PuigBrands"],
    "Cellnex Telecom": ["cellnex", "cellnextelecom", "CellnexTelecom"],
    "Almirall": ["almirall", "Almirall"],
    "eDreams ODIGEO": ["edreams", "edreamsodigeo", "eDreamsODIGEO", "odigeo"],
    "Adevinta": ["adevinta", "Adevinta", "adevintaspain"],
    "Affinity Petcare": ["affinity", "affinitypetcare", "AffinityPetcare"],
    "Privalia / Veepee Iberia": ["veepee", "vptech", "privalia", "Veepee", "vpTech"],
    # Revision adds (June 2026)
    "HP Inc.": ["hp", "hpinc", "hp1"],
    "Danone Iberia": ["danone"],
    "Schneider Electric": ["schneiderelectric", "schneider", "se"],
    "Galderma": ["galderma"],
    "PepsiCo Iberia": ["pepsico"],
    "Vueling (IAG)": ["vueling", "iairgroup", "iag", "InternationalAirlinesGroup"],
    "Fluidra": ["fluidra"],
    "Ferrer": ["ferrer", "ferrerinternacional", "FerrerInternacional"],
    "ISDIN": ["isdin", "Isdin"],
    "Typeform": ["typeform"],
    "TheFork (Tripadvisor)": ["thefork", "lafourchette", "tripadvisor"],
}

WD_SITE_GRID = ["Careers", "External", "Career", "Jobs", "{t}careers", "{T}_Careers",
                "{t}-ext", "{t}", "External_Careers", "{T}Careers", "{t}_jobs"]
WD_DC_GRID = ["wd1", "wd2", "wd3", "wd5", "wd10", "wd12"]


def _slug_variants(name):
    base = name.lower().split("/")[0].strip()
    flat = "".join(ch for ch in base if ch.isalnum())
    first = base.split()[0]
    return list(dict.fromkeys([flat, first] + DISCOVER_ALIASES.get(name, [])))


def _probe(kind, slug):
    try:
        if kind == "greenhouse":
            r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                             headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return len(r.json().get("jobs", []))
        elif kind == "lever":
            r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json",
                             headers=HEADERS, timeout=10)
            if r.status_code == 200 and isinstance(r.json(), list):
                return len(r.json())
        elif kind == "ashby":
            r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
                             headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return len(r.json().get("jobs", []))
        elif kind == "smartrecruiters":
            r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=10",
                             headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return r.json().get("totalFound", 0)
    except (requests.exceptions.RequestException, ValueError):
        pass
    return None


def run_discover():
    """For companies whose endpoint failed or returned 0, probe all ATS
    platforms with name variants and print working configs as JSON."""
    companies = load_companies()
    suggestions = {}

    print("🕵️ Discovery mode — probing ATS platforms for broken/empty configs...\n")
    for company in companies:
        ats = company.get("ats") or {}
        if ats.get("type") in ("none",):
            continue
        # Re-check current config; skip companies that already work with >0 postings
        if ats.get("type") in ADAPTERS:
            raw = ADAPTERS[ats["type"]](dict(ats))
            if raw:
                continue

        name = company["name"]
        print(f"  🔎 {name}")
        found = []
        for slug in _slug_variants(name):
            for kind in ("greenhouse", "lever", "ashby", "smartrecruiters"):
                n = _probe(kind, slug)
                if n is not None and n > 0:
                    found.append((kind, slug, n))
                    print(f"      ✅ {kind}:{slug} → {n} postings")
                time.sleep(0.2)

        # Workday grid probe (only if nothing found yet and tenant-ish name)
        if not found:
            for tenant in _slug_variants(name)[:2]:
                for wd in WD_DC_GRID:
                    for site_t in WD_SITE_GRID:
                        site = site_t.replace("{t}", tenant).replace("{T}", tenant.capitalize())
                        try:
                            res = _workday_query(tenant, wd, site, "finance", 0)
                        except requests.exceptions.RequestException:
                            res = None
                        if res is not None:
                            found.append(("workday", f"{tenant}|{wd}|{site}", len(res)))
                            print(f"      ✅ workday: {tenant} {wd} {site} → {len(res)} postings")
                            break
                        time.sleep(0.1)
                    if found:
                        break
                if found:
                    break

        if found:
            kind, slug, n = found[0]
            if kind == "workday":
                tenant, wd, site = slug.split("|")
                suggestions[name] = {"type": "workday", "tenant": tenant,
                                     "wd_candidates": [wd], "site_candidates": [site]}
            elif kind in ("greenhouse", "ashby"):
                suggestions[name] = {"type": kind, "board": slug}
            else:
                suggestions[name] = {"type": kind, "company": slug}
        else:
            print(f"      ❌ nothing found")

    print(f"\n{'='*50}")
    print(f"📋 Discovered configs (paste into target_companies.json 'ats' fields):\n")
    print(json.dumps(suggestions, indent=2, ensure_ascii=False))


if "_discover_hook" not in dir():
    pass


if __name__ == "__main__":
    if "--verify" in sys.argv:
        run_verify()
    elif "--discover" in sys.argv:
        run_discover()
    else:
        run_fetch()
