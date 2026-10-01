"""
track_seen.py — remembers which postings earlier digests already showed.

Without it every weekly mail re-lists each role that is still open and there is
no way to tell what appeared this week. Runs after scoring: stamps each scored
job with `first_seen` / `is_new`, then updates data/seen_jobs.json, which the
workflow commits back so the next run can read it.

The repo is public, so the state file holds hashes and dates, not job titles.

DRY_RUN=1 annotates the jobs but leaves the state untouched — a test run must
not make next Saturday's real digest think it has already shown everything.
"""

import os
import json
import hashlib
from datetime import date, timedelta

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SEEN_PATH = os.path.join(DATA_DIR, "seen_jobs.json")
SCORED_PATH = os.path.join(DATA_DIR, "scored_jobs.json")

# A posting that has not come back for this long is forgotten; if the same
# title reopens later it is news again.
FORGET_AFTER_DAYS = 60


def job_key(job):
    """Stable across sources: the same role found via Adzuna, the company's
    own board and LinkedIn carries three different ids and urls."""
    pair = f"{(job.get('title') or '').lower().strip()}|{(job.get('company') or '').lower().strip()}"
    return hashlib.sha1(pair.encode("utf-8")).hexdigest()[:16]


def annotate(jobs, seen, today):
    """Stamp jobs with first_seen/is_new and return the updated state."""
    seen = dict(seen)
    for job in jobs:
        key = job_key(job)
        entry = seen.get(key)
        job["is_new"] = entry is None
        job["first_seen"] = entry["first"] if entry else today.isoformat()
        seen[key] = {"first": job["first_seen"], "last": today.isoformat()}

    cutoff = (today - timedelta(days=FORGET_AFTER_DAYS)).isoformat()
    return {k: v for k, v in seen.items() if v["last"] >= cutoff}


def main():
    if not os.path.exists(SCORED_PATH):
        print("No scored_jobs.json found — nothing to track.")
        return

    with open(SCORED_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    seen = {}
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, "r", encoding="utf-8") as f:
            seen = json.load(f)

    jobs = data.get("scored_jobs", [])
    updated = annotate(jobs, seen, date.today())

    with open(SCORED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    new = sum(1 for j in jobs if j["is_new"])
    print(f"🆕 {new} new this run, {len(jobs) - new} already shown before")

    if os.environ.get("DRY_RUN") == "1":
        print("   DRY_RUN — seen_jobs.json left untouched")
        return
    with open(SEEN_PATH, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=1, sort_keys=True)
    print(f"   seen_jobs.json: {len(updated)} postings remembered")


if __name__ == "__main__":
    main()
