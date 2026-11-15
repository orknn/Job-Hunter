"""
analyze_gaps.py — bu haftaki ilanların istediği ama adayda olmayan şeyleri
sayar ve mailin başına gelen "gelişim alanları" paragrafını üretir.

Sayım kodda yapılır (fit_notes.json'daki etiketler), model yalnızca paragrafı
yazar: rakamları model üretirse haftadan haftaya oynar ve denetlenemez.

Bu repo public ve Actions logları herkese açık — bu script içerik basmaz,
yalnızca sayı basar.
"""

import os
import json

import llm
from score_jobs import GAP_TAGS, FIT_NOTES_PATH

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SCORED_PATH = os.path.join(DATA_DIR, "scored_jobs.json")
REPORT_PATH = os.path.join(DATA_DIR, "gap_report.json")

# Below this many comparable ads a "pattern" is two postings agreeing.
MIN_JOBS_FOR_PARAGRAPH = 5
TOP_N = 5

GAP_SYSTEM_PROMPT = (
    "You are a career strategist for a senior finance professional. You reply "
    "with a single JSON object and nothing else — no prose, no markdown fences."
)

GAP_PROMPT = """Below is a tally of what this week's job ads asked for that the candidate does not \
(fully) have. The candidate is a Senior Manager-level controller in Barcelona targeting \
Finance Director / Head of FP&A roles.

{jobs_compared} ads listed concrete requirements. For each gap: in how many of those ads it \
appeared, in how many it was a stated must-have, and how the ads worded it.

{tally_json}

Write ONE paragraph (4-6 sentences, plain text, addressed to the candidate as "you") on what \
to develop, based on this tally:
- Lead with the gap that blocks the most ads; cite counts exactly as given ("9 of 28 ads"). \
Never invent a number or a requirement that is not in the tally.
- For gaps that can be closed in 3-12 months (a tool, a certification, a language level, \
visible project ownership) name one concrete next step.
- Gaps that only a different job history would close (industry background, a Big 4 past) get \
at most one clause — say they are not worth chasing.
- Rank must-haves above nice-to-haves. No preamble, no sign-off.

Return JSON: {{"paragraph": "..."}}
"""


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def tally_gaps(job_ids, fit_notes):
    """Count, per gap tag, the ads in this digest that raise it.

    One ad counts once per tag however many of its gap lines share the tag.
    Returns (ads with visible requirements, rows sorted most frequent first).
    """
    compared = 0
    rows = {}
    for job_id in job_ids:
        note = fit_notes.get(str(job_id))
        if not note or not note.get("requirements_visible"):
            continue
        compared += 1
        seen_here = {}
        for gap in note.get("gaps", []):
            tag = gap.get("tag") if gap.get("tag") in GAP_TAGS else "other"
            entry = seen_here.setdefault(tag, {"must": False, "labels": []})
            entry["must"] = entry["must"] or gap.get("need") == "must"
            entry["labels"].append(gap.get("label", ""))
        for tag, entry in seen_here.items():
            row = rows.setdefault(tag, {"tag": tag, "name": GAP_TAGS[tag],
                                        "count": 0, "must_count": 0, "examples": []})
            row["count"] += 1
            row["must_count"] += entry["must"]
            for label in entry["labels"]:
                if label and label not in row["examples"] and len(row["examples"]) < 4:
                    row["examples"].append(label)

    # "other" is a bucket of unrelated one-offs, not a development area.
    ranked = sorted((r for r in rows.values() if r["tag"] != "other"),
                    key=lambda r: (-r["must_count"], -r["count"], r["name"]))
    return compared, ranked


def analyze_gaps():
    jobs = _load(SCORED_PATH, {}).get("scored_jobs", [])
    fit_notes = _load(FIT_NOTES_PATH, {})

    if os.path.exists(REPORT_PATH):
        os.remove(REPORT_PATH)  # never let last week's report ride along

    if not isinstance(fit_notes, dict) or not fit_notes:
        print("No fit notes this run (no skill profile configured?) — skipping.")
        return None

    compared, ranked = tally_gaps([j.get("id") for j in jobs], fit_notes)
    top = ranked[:TOP_N]
    print(f"🧭 {compared} of {len(jobs)} ads listed comparable requirements; "
          f"{len(ranked)} distinct gap area(s).")
    if not top:
        return None

    report = {"jobs_total": len(jobs), "jobs_compared": compared,
              "top_gaps": top, "paragraph": ""}

    if compared >= MIN_JOBS_FOR_PARAGRAPH and llm.api_key_present():
        prompt = GAP_PROMPT.format(
            jobs_compared=compared,
            tally_json=json.dumps(
                [{"gap": r["name"], "ads": r["count"], "must_have_in": r["must_count"],
                  "worded_as": r["examples"]} for r in top],
                indent=2, ensure_ascii=False))
        try:
            answer = llm.call_json(system=GAP_SYSTEM_PROMPT, user=prompt, max_tokens=2048)
            report["paragraph"] = str(answer.get("paragraph", "")).strip()
        except Exception as e:
            # The counts still go out; only the prose is lost.
            print(f"⚠ Development paragraph failed ({type(e).__name__}) — sending the tally alone.")
    else:
        print("Too few comparable ads for a paragraph — sending the tally alone.")

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("✅ Development focus written.")
    return report


if __name__ == "__main__":
    analyze_gaps()
