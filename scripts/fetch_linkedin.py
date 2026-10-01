"""
fetch_linkedin.py — keyword search over LinkedIn's public `jobs-guest` endpoints.

The other two sources are blind in opposite ways: the ATS fetcher only sees
companies already on the target list, and Adzuna's Spanish inventory is thin.
This one answers a query across the whole board, so it is how a company nobody
thought to list — or a role posted through a search firm — gets found.

**No account is involved.** The endpoints serve logged-out visitors: no cookie,
session or credential is sent. Coverage is therefore the guest view; login-gated
postings never appear.

Automated access is against LinkedIn's Terms of Service. This is one person's
own job search at low volume — a handful of queries, two pages each, once a
week, with a pause between requests. Do not raise MAX_PAGES to crawl the board.

A failure here never fails the digest: LinkedIn rate-limits datacenter IPs, and
a blocked run must still send what the other sources found.

Ported from job-hunter-core's `src/connectors/linkedin.py`.
"""

import os
import re
import sys
import html
import json
import time
import random
import requests

sys.path.insert(0, os.path.dirname(__file__))
from fetch_jobs import classify_language_fit, load_target_companies, match_company  # noqa: E402
from titles import is_finance_title, title_gate  # noqa: E402

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Job-Hunter weekly digest; personal use)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    # The guest endpoints serve the infinite-scroll fragment; without this
    # header they redirect to the consent-walled React page instead.
    "X-Requested-With": "XMLHttpRequest",
}
TIMEOUT = 20
MAX_RETRIES = 3
# Between requests, not just after a 429. The point is to never reach one.
PAUSE_SECONDS = 1.5

LOCATION = "Barcelona, Catalonia, Spain"
QUERIES = [
    "finance director",
    "head of finance",
    "head of fp&a",
    "cfo",
    "senior finance manager",
    "senior finance business partner",
    "director financiero",
]
PAGE_SIZE = 10  # fixed by the endpoint
MAX_PAGES = 2
JOBAGE_DAYS = 8
# Detail requests are the expensive part; only postings that passed the title
# gate get one, and never more than this many in a run.
MAX_DETAILS = 40

# The search location is the Barcelona area, but LinkedIn pads thin result
# pages with the rest of Catalonia and Spain ("Valls, Catalonia" is 100 km out),
# so the region name alone does not count.
_BCN_AREA = ("barcelona", "sant cugat", "hospitalet", "cornell", "esplugues",
             "viladecans", "el prat", "badalona", "sabadell", "terrassa", "rubí",
             "martorell", "vallès", "valles", "sant joan desp", "sant just",
             "sant boi", "sant feliu", "sant adri", "sant quirze", "granollers",
             "cerdanyola", "montcada", "gavà", "castelldefels", "mollet",
             "palau-solit", "santa perp", "barberà", "parets")


class LinkedInError(Exception):
    pass


def _get(url, params=None):
    """One request, retrying 429 and 5xx with jittered exponential backoff."""
    delay = 2.0
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
        except requests.exceptions.RequestException as error:
            raise LinkedInError(f"request failed: {error}") from error
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == MAX_RETRIES:
                raise LinkedInError(f"HTTP {response.status_code} after {MAX_RETRIES} retries")
            time.sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 16.0)
            continue
        return response
    raise LinkedInError("request failed after max retries")


# --- parsing -------------------------------------------------------------
# Regex rather than a DOM parser, deliberately: the guest fragment is a flat
# list of <li> cards, and splitting on the posting URN keeps one malformed card
# from taking the rest of the page with it.

_URN = 'data-entity-urn="urn:li:jobPosting:'


def _strip(text):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip()


def _tag(chunk, class_name, tag):
    match = re.search(
        rf'class="[^"]*{re.escape(class_name)}[^"]*"[^>]*>(.*?)</{tag}>', chunk, re.DOTALL)
    return _strip(match.group(1)) if match else ""


def parse_cards(html_text):
    cards = []
    for chunk in html_text.split(_URN)[1:]:
        match = re.match(r"^(\d+)", chunk)
        if not match:
            continue
        title = _tag(chunk, "base-search-card__title", "h3")
        if not title:
            continue
        posted = re.search(r'class="job-search-card__listdate[^"]*"[^>]*datetime="([^"]+)"', chunk)
        cards.append({
            "id": match.group(1),
            "title": title,
            "company": _tag(chunk, "base-search-card__subtitle", "h4"),
            "location": _tag(chunk, "job-search-card__location", "span"),
            "posted": posted.group(1) if posted else "",
        })
    return cards


def _extract_div(html_text, class_name):
    """Inner HTML of a <div> with this class, tracking nesting depth."""
    opening = re.search(
        rf'<div[^>]*class="[^"]*{re.escape(class_name)}[^"]*"[^>]*>', html_text, re.I)
    if not opening:
        return None
    index, depth = opening.end(), 1
    while depth and index < len(html_text):
        next_open = html_text.find("<div", index)
        next_close = html_text.find("</div>", index)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            depth += 1
            index = next_open + 4
        else:
            depth -= 1
            index = next_close + 6
    return html_text[opening.end():index - 6]


def parse_description(html_text):
    fragment = (_extract_div(html_text, "show-more-less-html__markup")
                or _extract_div(html_text, "description__text"))
    return _strip(fragment) if fragment else ""


def is_closed(html_text):
    """A closed posting carries a banner in its top card — scoped to the markup
    before the description, because recruiter boilerplate quotes the phrase."""
    start = re.search(r'class="[^"]*(?:show-more-less-html__markup|description__text)', html_text)
    topcard = html_text[: start.start()] if start else html_text
    return bool(re.search(r"closed-job__flavor|no longer accepting applications", topcard, re.I))


# --- pipeline ------------------------------------------------------------

def search():
    """All cards for the configured queries, deduplicated by posting id."""
    cards, requested = {}, 0
    for query in QUERIES:
        for page in range(MAX_PAGES):
            if requested:
                time.sleep(PAUSE_SECONDS)
            requested += 1
            response = _get(SEARCH_URL, {
                "keywords": query, "location": LOCATION,
                "f_TPR": f"r{JOBAGE_DAYS * 86400}", "start": page * PAGE_SIZE,
            })
            if response.status_code != 200:
                if page == 0 and response.status_code != 404:
                    raise LinkedInError(f"search HTTP {response.status_code}")
                break
            found = parse_cards(response.text)
            for card in found:
                cards.setdefault(card["id"], card)
            if len(found) < PAGE_SIZE:
                break
        print(f"   '{query}': {len(cards)} unique cards so far")
    return list(cards.values())


def fetch_detail(job_id):
    """Posting body, or '' when the posting is gone, closed or unreadable."""
    time.sleep(PAUSE_SECONDS)
    response = _get(f"{DETAIL_URL}/{job_id}")
    if response.status_code != 200 or is_closed(response.text):
        return ""
    return parse_description(response.text)


def in_barcelona_area(location):
    loc = (location or "").lower()
    return any(k in loc for k in _BCN_AREA)


def run():
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "fetched_jobs.json")
    if os.path.exists(data_path):
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {"matched_jobs": [], "unmatched_jobs": []}
    data.setdefault("matched_jobs", [])
    data.setdefault("unmatched_jobs", [])
    dropped = data.setdefault("dropped", [])

    targets = load_target_companies()
    pool = data["matched_jobs"] + data["unmatched_jobs"]
    existing_pairs = {((j.get("title") or "").lower().strip(),
                       (j.get("company") or "").lower().strip()) for j in pool}

    print(f"🔗 Searching LinkedIn ({len(QUERIES)} queries, {LOCATION})...")
    cards = search()

    survivors = []
    for card in cards:
        if not in_barcelona_area(card["location"]):
            continue
        reason = title_gate(card["title"])
        if reason:
            if is_finance_title(card["title"]):
                dropped.append({"title": card["title"], "company": card["company"],
                                "reason": reason, "source": "linkedin"})
            continue
        pair = (card["title"].lower().strip(), card["company"].lower().strip())
        if pair in existing_pairs:
            continue  # Adzuna or the company's own board already has it
        existing_pairs.add(pair)
        survivors.append(card)

    print(f"   {len(cards)} cards → {len(survivors)} new after location/title/dedupe")

    added = 0
    for card in survivors[:MAX_DETAILS]:
        description = fetch_detail(card["id"])
        if not description:
            continue  # closed or gone — a title alone is not worth a scoring call
        target = match_company(card["company"], targets)
        entry = {
            "id": f"li-{card['id']}",
            "title": card["title"],
            "company": card["company"] or "Confidential",
            "location": card["location"],
            "description": description[:3000],
            "salary_min": None,
            "salary_max": None,
            "url": f"https://www.linkedin.com/jobs/view/{card['id']}",
            "created": card["posted"],
            "contract_type": "",
            "category": "Finance (LinkedIn)",
            "matched_query": "linkedin",
            "target_match": {
                "name": target["name"], "tier": target["tier"],
                "tc_min": target["tc_min"], "tc_max": target["tc_max"],
                "notes": target.get("notes", ""),
            } if target else None,
        }
        tag, keep = classify_language_fit(entry, target is not None)
        entry["language_fit"] = tag
        if not keep:
            continue
        data["matched_jobs" if target else "unmatched_jobs"].append(entry)
        added += 1
        print(f"       → {entry['title']} | {entry['company']}")

    data["total_matched"] = len(data["matched_jobs"])
    data["linkedin_jobs_added"] = added
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"✅ LinkedIn fetch complete — {added} new jobs merged")


if __name__ == "__main__":
    try:
        run()
    except LinkedInError as error:
        # Never fail the digest over this source.
        print(f"⚠️ LinkedIn unavailable this run ({error}) — continuing without it")
