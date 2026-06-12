"""
score_jobs.py — Claude AI ile bulunan ilanları 10 boyutlu rubric'e göre puanlar.
Her ilanı A-D arasında skorlar ve "neden Orkun'a uyuyor" açıklaması yazar.
"""

import os
import sys
import json
import anthropic

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
MODEL = "claude-haiku-4-5-20251001"  # Fast + cheap (Haiku 4.5)

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
- **Coty:** Always cap at Tier B, TC ceiling €80-90k (despite being a global MNC)
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

Return ONLY valid JSON array. No markdown, no explanation outside JSON.

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


def score_batch(client, jobs):
    """Send a batch of jobs to Claude for scoring."""
    jobs_summary = []
    for job in jobs:
        summary = {
            "job_id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "location": job["location"],
            "description": job["description"][:1000],  # Truncate long descriptions
            "salary_min": job.get("salary_min"),
            "salary_max": job.get("salary_max"),
            "target_match": job.get("target_match"),
            "language_fit": job.get("language_fit", "UNKNOWN"),
        }
        jobs_summary.append(summary)

    prompt = SCORING_PROMPT.replace("{jobs_json}", json.dumps(jobs_summary, indent=2, ensure_ascii=False))

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        response_text = message.content[0].text

        # Parse JSON from response
        # Sometimes Claude wraps in ```json ... ```
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            response_text = response_text.split("```")[1].split("```")[0]

        return json.loads(response_text.strip())

    except json.JSONDecodeError as e:
        print(f"  ⚠ JSON parse error: {e}")
        print(f"  Raw response: {response_text[:500]}")
        return None
    except Exception as e:
        print(f"  ⚠ API error: {e}")
        return None


def score_all_jobs():
    """Score all fetched jobs using Claude."""
    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY environment variable required.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    data = load_fetched_jobs()

    all_jobs = data.get("matched_jobs", []) + data.get("unmatched_jobs", [])
    print(f"📊 Scoring {len(all_jobs)} jobs with Claude AI...")

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
            results = score_batch(client, batch)
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
    for score in relevant:
        job_id = str(score.get("job_id", ""))
        original = jobs_by_id.get(job_id, {})
        enriched.append({
            **original,
            "scoring": score,
        })

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
