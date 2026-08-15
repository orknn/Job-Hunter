"""
score_jobs.py — bulunan ilanları 10 boyutlu rubric'e göre puanlar.
Her ilanı A-D arasında skorlar ve "neden Orkun'a uyuyor" açıklaması yazar.
"""

import os
import re
import sys
import json

import llm

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
# Provider and model live in llm.py; nothing here names either.

# The rubric stays in the user message with the postings it applies to; this
# only carries the role and the output contract. JSON mode additionally
# requires the word "json" to appear in the conversation.
SCORING_SYSTEM_PROMPT = (
    "You are an expert career advisor evaluating job listings for a specific "
    "candidate against a fixed rubric. You reply with a single JSON object and "
    "nothing else — no prose, no markdown fences."
)

SCORING_PROMPT = """You are an expert career advisor evaluating job listings for a specific candidate.

## Candidate Profile
- **Name:** Orkun Biçen
- **Current Role:** Zone Euro MCS Controller, Nestlé Barcelona
- **Target:** Finance Director / Head of FP&A in Barcelona
- **Previous:** Schneider Electric (production finance)
- **Languages:** English (fluent), Turkish (native), limited Spanish
- **Visa:** Requires HQP sponsorship (Spain)
- **Timeline:** Moving within 6 months
- **Side projects:** nocashflow.net (crypto/macro newsletter)

## 10-Dimension Scoring Rubric (A=best, D=lowest)

1. **Total Comp** — A: €180k+, B: €140-180k, C: €110-140k, D: <€110k
2. **HQP Visa Ease** — A: in-house immigration team, B: has sponsored before, C: possible but unverified, D: unlikely/never sponsored
3. **English-First Ops** — A: English only, B: mostly English, C: English + Spanish needed, D: Spanish primary
4. **Career Trajectory** — A: clear VP Finance/CFO path in 3-5y, B: good growth, C: lateral, D: dead-end
5. **Industry Pull** — A: high personal interest, B: moderate, C: neutral, D: irrelevant
6. **Scope** — A: €500M+ P&L / 10+ team, B: €100-500M / 5-10 team, C: €20-100M / small team, D: <€20M
7. **Working Model** — A: remote or 1-2 day hybrid, B: 3 day hybrid, C: 4 day office, D: 5 days mandatory
8. **Stability** — A: profitable/cash-positive, B: well-funded, C: restructuring, D: <12mo runway
9. **Brand Value** — A: top-tier MNC or unicorn, B: well-known regional, C: niche, D: unknown
10. **Side Hustle Compatibility** — A: explicitly OK, B: standard policy, C: unclear, D: strict moonlighting ban

## Special Rules
- **Language rule (critical):** Each job has a "language_fit" tag. The candidate has B1 Spanish only — he CANNOT work in a Spanish-primary environment.
  - "SPANISH_LOCAL" or "SPANISH_LOCAL_NATIVE_REQ": score English-First Ops = D and apply_priority = SKIP, regardless of other dimensions.
  - "SPANISH_AD_TARGET_MNC" or "SPANISH_AD_ENGLISH_SIGNALS": evaluate carefully — recruiters often post English-first MNC roles in Spanish. If the description demands high Spanish proficiency for the role itself (not just the ad language), score English-First Ops = D and SKIP.
  - A Spanish ad asking for "nivel alto de inglés" usually means a Spanish-primary team wanting English as a secondary skill → that is a C at best for this candidate.
- **Seniority hard rule (critical — check FIRST, before any other dimension):** The candidate targets Director / Head-level roles (8-10+ years). Before scoring anything else, scan the description for the stated experience requirement. If the highest years figure the ad asks for is below 8 — "+4 years", "4+ years", "3-5 years", "4-6 años de experiencia" all mean a minimum BELOW 8 — the job is "IRRELEVANT". This is absolute: no other strength (brand, location, stability, comp) can rescue it, and you must NOT grade it B/C "despite" the requirement. The same applies to Analyst / Junior / Associate / Specialist / Technician-level titles: a "Senior Financial Analyst" or "Controlling Specialist" is still below the bar. When the ad states no years and the title is ambiguous, judge the role's level from scope (team leadership, board exposure, group-wide remit = senior; "support the team", single-process ownership = junior → IRRELEVANT).
- **Target band ≠ job salary (critical):** target_match.tc_min/tc_max is the candidate's target compensation band for a DIRECTOR-level role at that company — it is NOT the salary of this specific job. NEVER copy it into tc_estimate. Estimate tc from the actual role level and description. If the description states a concrete salary figure (e.g. "€61,000"), anchor tc_estimate to that figure and score Total Comp accordingly.
- **Coty:** Always cap at Tier B, TC ceiling €80-90k (despite being a global MNC)
- **Location hard rule:** If the job location is NOT in Spain and NOT explicitly remote-from-Europe/EMEA, the overall_score is capped at C and apply_priority MUST be SKIP or WATCH — NEVER "APPLY NOW". US/India/Canada/UK-office-only roles cannot be A or B regardless of other merits.
- **Description overrides location field:** If the description reveals the position is actually based outside Spain (e.g. salary quoted for an Italian office, "based in Milan"), apply the location hard rule regardless of what the location field says. If the role is in Spain but NOT Barcelona metro (e.g. Madrid) and not remote-friendly, cap at C / WATCH and note the relocation mismatch in fit_summary.
- **HQP note:** HQP is a Spanish residence permit. For non-Spain roles, the visa dimension should reflect that HQP is irrelevant — do not output "HQP Risk: LOW" for roles outside Spain.
- If the job posting is clearly NOT a finance/FP&A/controller/CFO role, score it "IRRELEVANT" and skip

## Your Task
For each job below, return a JSON object with:
- "job_id": the job ID
- "overall_score": "A", "B", "C", or "D" (or "IRRELEVANT")
- "dimension_scores": object with scores for each of the 10 dimensions
- "tc_estimate": estimated total comp range string (e.g. "€130-160k")
- "fit_summary": 2-3 sentence explanation of why this job fits or doesn't fit Orkun
- "hqp_risk": "LOW", "MEDIUM", or "HIGH" (risk of NOT getting HQP sponsorship)
- "apply_priority": "APPLY NOW", "WATCH", or "SKIP"

Return a JSON object of the form {"scores": [ ...one entry per job... ]}.
No markdown, no explanation outside JSON.

## Jobs to Evaluate
{jobs_json}
"""


def load_fetched_jobs():
    """Load fetched jobs from JSON file."""
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "fetched_jobs.json")
    if not os.path.exists(data_path):
        print("ERROR: No fetched_jobs.json found. Run fetch_jobs.py first.")
        sys.exit(1)

    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def score_batch(jobs):
    """Send a batch of jobs to the model for scoring."""
    jobs_summary = []
    for job in jobs:
        summary = {
            "job_id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            # 3000 chars, matching the ATS enrichment cap: experience lines sit
            # deep in the ad — a 2000 cut once hid Puig's "+4 years" at char
            # 2059 and the role scored B because the model never saw it
            "description": job["description"][:3000],
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "target_match": job.get("target_match"),
            "language_fit": job.get("language_fit", "UNKNOWN"),
        }
        jobs_summary.append(summary)

    prompt = SCORING_PROMPT.replace("{jobs_json}", json.dumps(jobs_summary, indent=2, ensure_ascii=False))

    try:
        # JSON mode cannot return a bare array, so the prompt asks for a
        # {"scores": [...]} wrapper and llm.call_json lifts the list back out.
        # It also removes the fenced-code-block stripping this used to need.
        results = llm.call_json(
            system=SCORING_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=4096,
            unwrap="scores",
        )
        if not isinstance(results, list):
            print(f"  ⚠ Expected a list of scores, got {type(results).__name__}")
            return None
        return results

    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"  ⚠ API error: {e}")
        return None


# Deterministic seniority gate on the posting text. The prompt has the same
# rule, but Haiku has ignored it when the role otherwise looked attractive:
# Puig's "+4 years of experience" scored B even with the line in context.
# Parse "N(-M)(+) years/años of experience" mentions; if the ad states at
# least one requirement and none reaches MIN_YEARS, it never reaches scoring.
MIN_YEARS = 8
# High-precision on purpose: only "N years of experience"-shaped phrases and
# "Experience: N years" headers count. Vaguer mentions ("5 years in FP&A")
# fall through to the prompt rule rather than risk cutting a senior ad on a
# stray "3 years in a row"-style phrase.
_YEARS_REQ_RE = re.compile(
    r"[+]?(\d{1,2})\s*(?:\+|plus)?\s*(?:[-–—]|to\s+|a\s+)?\s*\d{0,2}\s*"
    r"(?:years?|años?|anys?)['’]?\s*(?:of\s+|de\s+|d['’])"
    r"(?:relevant\s+|professional\s+|work(?:ing)?\s+|solid\s+|proven\s+)?experien"
    r"|experien[a-z]*\s*:\s*[+]?(\d{1,2})\s*(?:\+)?\s*(?:[-–—]|to\s+|a\s+)?\s*\d{0,2}\s*(?:years?|años?|anys?)",
    re.IGNORECASE,
)


def years_requirement_below_bar(description):
    """True when the ad names experience requirement(s) and all are < MIN_YEARS."""
    mins = [int(m.group(1) or m.group(2))
            for m in _YEARS_REQ_RE.finditer(description or "")]
    mins = [n for n in mins if 0 < n <= 40]
    return bool(mins) and max(mins) < MIN_YEARS


def score_all_jobs():
    """Score all fetched jobs against the rubric."""
    if not llm.api_key_present():
        print("ERROR: OPENAI_API_KEY environment variable required.")
        sys.exit(1)
    data = load_fetched_jobs()

    all_jobs = data.get("matched_jobs", []) + data.get("unmatched_jobs", [])

    below_bar = [j for j in all_jobs if years_requirement_below_bar(j.get("description", ""))]
    if below_bar:
        print(f"⏭  Seniority gate: {len(below_bar)} jobs ask for <{MIN_YEARS} years — skipped:")
        for j in below_bar:
            print(f"   · {j['title'][:60]} | {j['company']}")
        all_jobs = [j for j in all_jobs if not years_requirement_below_bar(j.get("description", ""))]

    print(f"📊 Scoring {len(all_jobs)} jobs with {llm.MODEL}...")

    if not all_jobs:
        print("No jobs to score. Creating empty result.")
        scored = []
    else:
        # Process in batches of 10
        scored = []
        failed_batches = 0
        total_batches = 0
        batch_size = 10
        for i in range(0, len(all_jobs), batch_size):
            batch = all_jobs[i:i + batch_size]
            total_batches += 1
            print(f"\n  Batch {i // batch_size + 1}: scoring {len(batch)} jobs...")
            results = score_batch(batch)
            if results is None:
                failed_batches += 1
                continue
            scored.extend(results)
            print(f"  ✅ Got {len(results)} scores")

        if total_batches and failed_batches == total_batches:
            print(f"\n❌ ALL {total_batches} scoring batches failed - aborting.")
            sys.exit(1)
        elif failed_batches:
            print(f"\n⚠ {failed_batches}/{total_batches} batches failed - digest will be partial.")

    # Filter out IRRELEVANT
    relevant = [s for s in scored if s.get("overall_score") != "IRRELEVANT"]
    irrelevant = [s for s in scored if s.get("overall_score") == "IRRELEVANT"]

    # Sort by score (A first)
    score_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    relevant.sort(key=lambda x: score_order.get(x.get("overall_score", "D"), 4))

    # Merge scored data back with original job info
    jobs_by_id = {j["id"]: j for j in all_jobs}
    enriched = []
    dropped_unmatched = 0
    for score in relevant:
        job_id = str(score.get("job_id", ""))
        original = jobs_by_id.get(job_id)
        # The model occasionally returns a job_id that matches no original record
        # (truncation, hallucinated id). Drop it rather than emit a blank
        # "Unknown / no title" card into the digest.
        if original is None:
            dropped_unmatched += 1
            continue

        # Salary provenance — keep the scoring tc_estimate as the model's guess, but
        # tag where the headline number actually comes from so the email can be honest.
        if str(original.get("salary_is_predicted") or "") == "1":
            salary_source = "adzuna_predicted"
        elif original.get("salary_min") or original.get("salary_max"):
            salary_source = "posting"
        else:
            salary_source = "model_estimate"

        enriched.append({
            **original,
            "salary_source": salary_source,
            "scoring": score,
        })

    if dropped_unmatched:
        print(f"⚠ Dropped {dropped_unmatched} score(s) with no matching job record (would have rendered as 'Unknown').")

    result = {
        "score_date": data.get("fetch_date", ""),
        "total_scored": len(scored),
        "total_relevant": len(relevant),
        "total_irrelevant": len(irrelevant),
        "scored_jobs": enriched,
    }

    # Save scored results
    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "scored_jobs.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*50}")
    print(f"✅ Scoring complete!")
    print(f"   Relevant jobs: {len(relevant)}")
    print(f"   Irrelevant filtered: {len(irrelevant)}")

    # Quick summary
    for grade in ["A", "B", "C", "D"]:
        count = len([s for s in relevant if s.get("overall_score") == grade])
        if count:
            print(f"   Grade {grade}: {count} jobs")

    return result


if __name__ == "__main__":
    score_all_jobs()
