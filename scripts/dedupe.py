"""
dedupe.py — one card per role, however many sources found it.

The fetchers dedupe on the exact (title, company) pair, which misses the common
case: Adzuna lists "Novartis España", the company's Workday board "Novartis
Iberia", LinkedIn plain "Novartis". The 7 Nov 2026 digest showed the same
DD&IT Global FP&A Manager three times, and two Glovo roles twice more under
the parent's name (Delivery Hero) — 5 of 32 cards were repeats, each paid for
with its own scoring call.

Here the company is resolved to its target-list name first (aliases included),
the title is normalised, and copies of one role collapse into the copy the
candidate should click: the company's own board over a job site, a posted
salary over none, the fuller description over the thinner. The other sources
are kept on the survivor as `also_on` so nothing is hidden.

Also flags postings placed by recruitment agencies (`via_recruiter`): the
hiring company is unnamed, so brand, sponsorship and stability are guesses.
"""

import re

from fetch_jobs import match_company, normalize_company_name


def _norm_title(title):
    t = (title or "").lower().replace("&", " and ")
    t = re.sub(r"[^\w]+", " ", t)
    return " ".join(t.split())


def canonical_company(job, targets):
    target = job.get("target_match") or match_company(job.get("company"), targets)
    if target:
        return target["name"].lower()
    return normalize_company_name(job.get("company"))


def source_label(job):
    q = job.get("matched_query") or ""
    if q.startswith("ats:"):
        return "company site"
    if q == "linkedin":
        return "LinkedIn"
    return "Adzuna"


def _preference(job):
    return (
        source_label(job) == "company site",
        bool(job.get("salary_min") or job.get("salary_max")),
        len(job.get("description") or ""),
    )


def dedupe_jobs(jobs, targets):
    """Return (unique_jobs, removed_count). Order follows first appearance."""
    groups = {}
    order = []
    for job in jobs:
        key = (_norm_title(job.get("title")), canonical_company(job, targets))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(job)

    unique = []
    for key in order:
        copies = groups[key]
        best = max(copies, key=_preference)
        keep = dict(best)
        others = [c for c in copies if c is not best]
        if not keep.get("target_match"):
            keep["target_match"] = next((c["target_match"] for c in others if c.get("target_match")), None)
        if not (keep.get("salary_min") or keep.get("salary_max")):
            paid = next((c for c in others if c.get("salary_min") or c.get("salary_max")), None)
            if paid:
                for field in ("salary_min", "salary_max", "salary_is_predicted"):
                    if field in paid:
                        keep[field] = paid[field]
        also = sorted({source_label(c) for c in others} - {source_label(keep)})
        if also:
            keep["also_on"] = also
        unique.append(keep)
    return unique, len(jobs) - len(unique)


# ──────────────────────────────────────────────
# Recruitment agencies
# ──────────────────────────────────────────────

# Agencies and executive-search firms that post Barcelona finance roles. Matched
# as whole words on the normalised company name.
RECRUITERS = [
    "finatal", "mindquest", "mur&partners", "mur & partners", "michael page",
    "page executive", "page personnel", "hays", "robert walters", "walters people",
    "robert half", "randstad", "adecco", "spring professional", "hudson",
    "morgan philips", "badenoch", "experis", "harvey nash", "selby jennings",
    "kellan", "korn ferry", "spencer stuart", "egon zehnder", "russell reynolds",
    "heidrick", "boyden", "grupo crit", "talent search people", "eurofirms",
    "nexus group", "fargo executive", "catenon", "pedersen & partners",
]
_RECRUITER_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(r) for r in RECRUITERS) + r")(?!\w)", re.IGNORECASE)

# Agency phrasing in the ad itself, for agencies not on the list.
_CLIENT_PHRASE_RE = re.compile(
    # Singular only: an employer serving customers writes "our clients"
    # (Amesto Global did, and was flagged until this lookahead).
    r"\b(our client(?!s)|nuestro cliente(?!s)|on behalf of (?:a|our|an)\b|"
    r"para (?:importante|una importante|un importante) (?:empresa|compañía|grupo|multinacional)|"
    r"we are recruiting for|estamos seleccionando para)",
    re.IGNORECASE)


def is_recruiter(job):
    if _RECRUITER_RE.search(job.get("company") or ""):
        return True
    return bool(_CLIENT_PHRASE_RE.search((job.get("description") or "")[:1500]))
