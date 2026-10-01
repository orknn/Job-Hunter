"""
fetch_ats.py — Hedef şirketlerin kariyer sayfalarını (ATS) doğrudan sorgular.

Adzuna'nın görmediği ilanları kaynağından çeker: Workday, Greenhouse, Lever,
SmartRecruiters, Amazon Jobs ve Microsoft Careers public JSON endpoint'leri;
ayrıca SuccessFactors CSB (HTML/sitemap), iCIMS (Jibe JSON + klasik portal),
Teamtailor/SF RSS feed'leri ve Workable public API.

Kullanım:
  python scripts/fetch_ats.py            # fetch + fetched_jobs.json'a merge
  python scripts/fetch_ats.py --verify   # her şirketin endpoint'ini test et, rapor bas
"""

import os
import re
import sys
import html
import json
import time
import requests
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from fetch_jobs import classify_language_fit  # noqa: E402
from titles import is_finance_title, title_gate  # noqa: E402

HEADERS = {"User-Agent": "Mozilla/5.0 (Job-Hunter weekly digest; personal use)"}
TIMEOUT = 20

# Location must match at least one (lowercase substring match)
LOCATION_KEYWORDS = [
    "barcelona", "catalonia", "cataluña", "catalunya", "spain", "españa",
    "espana", "remote - emea", "emea remote", "remote, spain",
    # Catalan metro area — where many target HQs actually sit
    "sant cugat", "hospitalet", "sant feliu", "cornella", "cornellà",
    "esplugues", "sant joan despi", "sant joan despí", "el prat", "viladecans",
]

# Barcelona-focused digest: a location naming another Spanish city passes only
# if Barcelona/Catalonia is ALSO listed (multi-location postings). "Madrid,
# Spain" alone used to slip through because "spain" matched.
BCN_KEYWORDS = [
    "barcelona", "catalonia", "cataluña", "catalunya", "sant cugat",
    "hospitalet", "sant feliu", "cornella", "cornellà", "esplugues",
    "sant joan despi", "sant joan despí", "el prat", "viladecans",
]
NON_BCN_SPAIN_CITIES = [
    "madrid", "valencia", "sevilla", "seville", "bilbao", "zaragoza",
    "malaga", "málaga", "alicante", "murcia", "vigo", "gijon", "gijón",
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
    # NOTE: the facet param must be the array form "normalized_country_code[]" —
    # the bare "normalized_country_code" is silently ignored and returns
    # GLOBAL results (the digest once surfaced India/US roles because of this).
    url = ("https://www.amazon.jobs/en/search.json"
           "?base_query=finance&normalized_country_code%5B%5D=ESP&result_limit=50&sort=recent")
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
                # A posting open in "London" with Barcelona as a secondary
                # location is still a Barcelona role.
                "location": " / ".join(filter(None, [j.get("location", "")] + [
                    x.get("location", "") for x in j.get("secondaryLocations") or []])),
                "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
                "description": (j.get("descriptionPlain") or j.get("title") or "")[:2000],
                "posted": j.get("publishedAt", ""),
            }
            for j in r.json().get("jobs", [])
        ]
    except requests.exceptions.RequestException:
        return None


def _strip_tags(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def fetch_successfactors(cfg, **_):
    """SAP SuccessFactors 'Career Site Builder' sites (jobs.<company>.com).
    No public JSON API, but the job list is plain server-rendered HTML with a
    stable jobTitle-link markup shared across deployments:
      - mode 'table' (default): /search/?q=<kw>[&locationsearch=..]&startrow=N
      - mode 'tile': /tile-search-results/?q= — for sites whose /search/ page
        renders results client-side (Boehringer, ISDIN); returns ALL postings
        regardless of q, so downstream title/location filters do the work.
    Location sits in a jobLocation span (table) or a 'section-field location'
    div (tile), always between one title anchor and the next."""
    host = cfg["host"]
    mode = cfg.get("mode", "table")
    queries = cfg.get("queries", ["finance"])
    locsearch = cfg.get("locationsearch", "")

    anchor_re = re.compile(
        r'<a[^>]*class="jobTitle-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    loc_re = re.compile(
        r'class="jobLocation"[^>]*>\s*(.*?)\s*</span>'
        r'|class="[^"]*section-field\s+location[^"]*"[^>]*>(.*?)</div>', re.S)

    def parse_page(page_html, seen, out):
        matches = list(anchor_re.finditer(page_html))
        added = 0
        for i, m in enumerate(matches):
            path, title = m.group(1), _strip_tags(m.group(2))
            if path in seen or not title:
                continue
            seen.add(path)
            window = page_html[m.end():matches[i + 1].start() if i + 1 < len(matches) else m.end() + 3000]
            lm = loc_re.search(window)
            loc = _strip_tags(lm.group(1) or lm.group(2)) if lm else ""
            # tile location divs carry an sr-only "Location" label — drop it
            loc = re.sub(r"^Location:?\s*", "", loc, flags=re.I)
            if not loc:
                # Some tile sites (ISDIN) configure no location field at all,
                # but CSB job paths lead with the city: /job/Barcelona-Treasury-
                # Technician/123/ — use the slug so the location gate still works.
                sm = re.search(r"/job/([^/]+)/", path)
                if sm:
                    loc = re.sub(r"-", " ", sm.group(1))
            path = html.unescape(path)  # hrefs carry &amp; entities
            url = path if path.startswith("http") else f"https://{host}{path}"
            out.append({"title": title, "location": loc, "url": url,
                        "description": title, "posted": ""})
            added += 1
        return added

    seen, out = set(), []
    try:
        if mode == "tile":
            r = requests.get(f"https://{host}/tile-search-results/?q=",
                             headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                return None
            parse_page(r.text, seen, out)
        else:
            for q in queries:
                for startrow in (0, 25, 50):
                    params = {"q": q, "startrow": startrow}
                    if locsearch:
                        params["locationsearch"] = locsearch
                    r = requests.get(f"https://{host}/search/", params=params,
                                     headers=HEADERS, timeout=TIMEOUT)
                    if r.status_code != 200:
                        return None if not out else out
                    if parse_page(r.text, seen, out) == 0:
                        break  # past the last page (or 0 results)
                    time.sleep(0.3)
    except requests.exceptions.RequestException:
        return None if not out else out
    return out


def fetch_sf_sitemap(cfg, **_):
    """Fallback for SuccessFactors CSB sites that render everything client-side
    (Fluidra): sitemap.xml still lists every posting URL, and CSB slugs lead
    with the city followed by the title (/job/Sant-Cugat-del-Valles-Accounts-
    Payable-Specialist-B/123/). City and title can't be split reliably, so the
    whole slug serves as both — the title/location keyword filters still work,
    and scoring reads the real posting via the URL."""
    host = cfg["host"]
    try:
        r = requests.get(f"https://{host}/sitemap.xml", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.exceptions.RequestException:
        return None
    out = []
    for url in re.findall(r"<loc>(.*?)</loc>", r.text):
        m = re.search(r"/job/([^/]+)/\d+/?$", html.unescape(url))
        if not m:
            continue
        slug_text = re.sub(r"-+", " ", m.group(1)).strip()
        out.append({"title": slug_text, "location": slug_text,
                    "url": html.unescape(url), "description": slug_text, "posted": ""})
    return out


def fetch_jibe(cfg, **_):
    """iCIMS Talent Cloud / Jibe career sites (careers.se.com etc.) — clean
    public JSON at /api/jobs. Job objects live under each item's 'data' key."""
    host = cfg["host"]
    params = {"keywords": cfg.get("keywords", "finance"), "page": 1, "limit": 50}
    if cfg.get("location"):
        params["location"] = cfg["location"]
    try:
        r = requests.get(f"https://{host}/api/jobs", params=params,
                         headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        jobs = r.json().get("jobs", [])
    except (requests.exceptions.RequestException, ValueError):
        return None
    out = []
    for j in jobs:
        d = j.get("data", j)
        out.append({
            "title": d.get("title", ""),
            "location": d.get("full_location") or ", ".join(
                filter(None, [d.get("city", ""), d.get("country", "")])),
            "url": f"https://{host}/jobs/{d.get('slug', '')}",
            "description": _strip_tags(d.get("description", ""))[:2000],
            "posted": d.get("create_date", "") or d.get("posted_date", ""),
        })
    return out


def fetch_icims(cfg, **_):
    """Classic iCIMS career portals (careers-<company>.icims.com). The
    in_iframe=1 variant returns lightweight server-rendered job cards."""
    host = cfg["host"]
    queries = cfg.get("queries", ["finance"])
    card_re = re.compile(r'iCIMS_JobCardItem(.*?)(?=iCIMS_JobCardItem|</ul>)', re.S)
    seen, out = set(), []
    for q in queries:
        try:
            r = requests.get(
                f"https://{host}/jobs/search",
                params={"ss": 1, "searchKeyword": q, "in_iframe": 1},
                headers=HEADERS, timeout=TIMEOUT)
        except requests.exceptions.RequestException:
            return None if not out else out
        if r.status_code != 200:
            return None if not out else out
        for card in card_re.findall(r.text):
            a = re.search(r'<a href="(https?://[^"]+/jobs/\d+/[^"]+)"[^>]*title="([^"]+)"', card)
            if not a or a.group(1) in seen:
                continue
            seen.add(a.group(1))
            title = re.sub(r"^\d+\s*-\s*", "", html.unescape(a.group(2)))
            lm = re.search(r'Job Locations?</span>\s*<span[^>]*>\s*([^<]+)', card)
            out.append({
                "title": title,
                "location": _strip_tags(lm.group(1)) if lm else "",
                "url": a.group(1).split("?")[0],
                "description": title,
                "posted": "",
            })
        time.sleep(0.3)
    return out


def fetch_rss(cfg, **_):
    """Generic job-feed RSS adapter (Teamtailor /jobs.rss, SuccessFactors
    /services/rss/job/, ...). Location comes from Teamtailor's tt:* tags when
    present, else from a trailing '(City, CC, zip)' suffix in the title (the
    SuccessFactors RSS convention). link_contains scopes multi-brand feeds
    (e.g. VW Group's — SEAT postings only)."""
    def tag(item, name):
        m = re.search(rf"<{name}[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</{name}>", item, re.S)
        return _strip_tags(m.group(1)) if m else ""

    try:
        r = requests.get(cfg["url"], headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.exceptions.RequestException:
        return None
    must = cfg.get("link_contains", "")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", r.text, re.S):
        link = tag(item, "link")
        if not link or (must and must not in link):
            continue
        title = tag(item, "title")
        loc = tag(item, "tt:location") or ", ".join(
            filter(None, [tag(item, "tt:city"), tag(item, "tt:country")]))
        if not loc:
            m = re.search(r"\(([^()]+,[^()]+)\)\s*$", title)
            if m:
                loc = m.group(1).strip()
                title = title[:m.start()].strip()
        out.append({
            "title": title,
            "location": loc,
            "url": link,
            "description": tag(item, "description")[:2000] or title,
            "posted": tag(item, "pubDate"),
        })
    return out


def fetch_workable(cfg, **_):
    """Workable hosted career pages (apply.workable.com/<account>) — public
    v3 jobs endpoint, POST with an empty query returns all published roles."""
    account = cfg["account"]
    try:
        r = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{account}/jobs",
            json={"query": cfg.get("query", ""), "location": [], "department": [],
                  "worktype": [], "remote": []},
            headers={**HEADERS, "Content-Type": "application/json"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
    except (requests.exceptions.RequestException, ValueError):
        return None
    return [
        {
            "title": j.get("title", ""),
            "location": ", ".join(filter(None, [
                (j.get("location") or {}).get("city", ""),
                (j.get("location") or {}).get("country", ""),
            ])),
            "url": f"https://apply.workable.com/{account}/j/{j.get('shortcode', '')}/",
            "description": j.get("title", ""),
            "posted": j.get("published", ""),
        }
        for j in results
    ]


ADAPTERS = {
    "workday": fetch_workday,
    "ashby": fetch_ashby,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
    "amazon": fetch_amazon,
    "microsoft": fetch_microsoft,
    "successfactors": fetch_successfactors,
    "sf_sitemap": fetch_sf_sitemap,
    "jibe": fetch_jibe,
    "icims": fetch_icims,
    "rss": fetch_rss,
    "workable": fetch_workable,
}


# ──────────────────────────────────────────────
# Job-detail enrichment — most list endpoints (Workday CXS list, SF CSB pages,
# iCIMS cards) carry no description, so entries land with description == title
# and Claude scores blind: a "+4 years" controller role once reached the digest
# as grade D because the scorer never saw the experience requirement. For the
# few jobs that survive the filters, fetch the posting itself.
# ──────────────────────────────────────────────

MAX_ENRICH_PER_COMPANY = 8

_WORKDAY_URL = re.compile(
    r"https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/[^/]+/([^/]+)(/job/.+)$")
# CSB detail pages mark the body with schema.org itemprop="description"
_CSB_DESC = re.compile(
    r'itemprop="description"[^>]*>(.*?)'
    r'(?=<(?:div|section|footer)[^>]*(?:class="job|id="similar|itemprop=)|data-careersite-propertyid)',
    re.S)
_META_DESC = re.compile(
    r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]+)"')


def _workday_posting_info(url):
    """jobPostingInfo dict for a Workday job URL, or {} when unavailable."""
    m = _WORKDAY_URL.match(url)
    if not m:
        return {}
    tenant, wd, site, path = m.groups()
    try:
        r = requests.get(
            f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{path}",
            headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {}
        return r.json().get("jobPostingInfo") or {}
    except (requests.exceptions.RequestException, ValueError):
        return {}


# Workday's list endpoint collapses multi-location postings to "2 Locations",
# which names no city — the location gate then drops a Barcelona role unseen
# (Rockwell's "Senior Finance Business Partner", Barcelona + Katowice).
_MULTI_LOCATION_RE = re.compile(r"^\s*\d+\s+locations?\s*$", re.IGNORECASE)


def resolve_workday_locations(url):
    """Real location list for a multi-location Workday posting, or ''."""
    info = _workday_posting_info(url)
    locations = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    return " / ".join(loc for loc in locations if loc)


def fetch_detail_description(url, ats_type):
    """Return the real posting text for a job URL, or '' when unavailable."""
    try:
        if ats_type == "workday" and _WORKDAY_URL.match(url):
            return _strip_tags(_workday_posting_info(url).get("jobDescription", ""))
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return ""
        m = _CSB_DESC.search(r.text)
        if m:
            return _strip_tags(m.group(1))
        m = _META_DESC.search(r.text)
        return _strip_tags(m.group(1)) if m else ""
    except (requests.exceptions.RequestException, ValueError):
        return ""


# ──────────────────────────────────────────────
# Filtering & pipeline
# ──────────────────────────────────────────────

def is_target_location(location):
    loc = (location or "").lower()
    # Workday MNCs sometimes list "Spain" only or multi-location strings
    return any(k in loc for k in LOCATION_KEYWORDS)


# Remote roles only pass if anchored to Europe/EMEA/Spain — keeps "Remote - US"
# and "Remote - India" out of a Barcelona-focused digest.
REMOTE_EU_ANCHORS = ("emea", "europe", "spain", "europa")


def passes_location_filter(location):
    """Universal location gate applied to EVERY adapter (greenhouse/lever/ashby
    included — these were previously unfiltered and flooded the digest with
    Stripe's US/India roles). A job passes when:
      - the location names a Spain/Catalonia target city/region, OR
      - it is remote AND anchored to Europe/EMEA/Spain, OR
      - the location is blank (unknown → leave it for scoring to judge)."""
    loc = (location or "").strip().lower()
    if not loc:
        return True
    # Another Spanish city named without Barcelona/Catalonia → out (e.g.
    # "Madrid, Spain" — the generic "spain" keyword would otherwise pass it)
    if any(c in loc for c in NON_BCN_SPAIN_CITIES) and not any(k in loc for k in BCN_KEYWORDS):
        return False
    if any(k in loc for k in LOCATION_KEYWORDS):
        return True
    if "remote" in loc and any(a in loc for a in REMOTE_EU_ANCHORS):
        return True
    return False


def load_companies():
    path = os.path.join(os.path.dirname(__file__), "..", "data", "target_companies.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_company(company, dropped=None):
    """Fetch + filter jobs for a single company. Returns (jobs, status).

    `dropped` collects finance-titled postings the title gate cut, for audit."""
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
    enriched = 0
    for r in raw:
        # Title gate (same rules as the Adzuna and LinkedIn fetchers)
        reason = title_gate(r["title"])
        if reason:
            if (dropped is not None and is_finance_title(r["title"])
                    and passes_location_filter(r["location"])):
                dropped.append({"title": r["title"], "company": company["name"],
                                "reason": reason, "source": f"ats:{ats['type']}"})
            continue
        if ats["type"] == "workday" and _MULTI_LOCATION_RE.match(r["location"] or ""):
            r["location"] = resolve_workday_locations(r["url"]) or r["location"]
            time.sleep(0.4)
        # Location gate now applies to ALL adapters, greenhouse/lever/ashby
        # included — they used to slip US/India roles straight into the digest.
        if not passes_location_filter(r["location"]):
            continue
        # Thin description = the scorer would judge from the title alone.
        # Fetch the actual posting for the handful of jobs that got this far.
        if len(r["description"]) < 200 and enriched < MAX_ENRICH_PER_COMPANY:
            enriched += 1
            detail = fetch_detail_description(r["url"], ats["type"])
            if detail:
                r["description"] = detail[:3000]
            time.sleep(0.4)
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
    # Normalized (title, company) pairs already in the pool (incl. Adzuna results) —
    # stops ATS re-adding a role Adzuna already surfaced, on top of URL dedupe.
    existing_pairs = {
        ((j.get("title") or "").lower().strip(), (j.get("company") or "").lower().strip())
        for j in data.get("matched_jobs", [])
    }
    added = 0
    dropped = data.setdefault("dropped", [])

    print(f"🏢 Polling {len(companies)} target company career sites...\n")
    for company in companies:
        jobs, status = fetch_company(company, dropped)
        marker = "✅" if jobs else ("⚠️" if "FAILED" in status else "·")
        print(f"  {marker} {company['name']:35s} {status}")
        for j in jobs:
            pair = (j["title"].lower().strip(), j["company"].lower().strip())
            if j["url"] not in existing_urls and pair not in existing_pairs:
                data["matched_jobs"].append(j)
                existing_urls.add(j["url"])
                existing_pairs.add(pair)
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
