"""
analyze_gaps.py — B/C tier ilanlardaki tekrar eden zayıf boyutları toplayıp
haftalık bir "pozisyonlama açığı" raporu üretir: Orkun'u daha çok A-tier
ilana taşımak için hangi 1-3 alanda çalışması gerektiğini özetler.
"""

import os
import sys
import json
from collections import Counter

import llm

# Provider and model live in llm.py; nothing here names either.
GAP_SYSTEM_PROMPT = (
    "You are a career strategist reading a batch of near-miss job scorings. "
    "You reply with a single JSON object and nothing else — no prose, no "
    "markdown fences."
)

DIM_LABELS = {
    "total_comp": "Total Comp",
    "hqp_visa_ease": "HQP Visa Ease",
    "english_first": "English-First Ops",
    "career_trajectory": "Career Trajectory",
    "industry_pull": "Industry Pull",
    "scope": "Scope",
    "working_model": "Working Model",
    "stability": "Stability",
    "brand_value": "Brand Value",
    "side_hustle": "Side Hustle Compatibility",
}

GAP_PROMPT = """You are a career advisor. Below is an aggregate of a candidate's near-miss job \
evaluations from the past week (jobs scored B or C, not yet A-tier).

## Candidate
Orkun Biçen — Zone Euro MCS Controller at Nestlé Barcelona, targeting Finance Director / \
Head of FP&A roles in Barcelona, moving within 6 months, needs HQP visa sponsorship.

## Aggregate weak dimensions across this week's B/C tier matches
{weak_dims_json}

## Sample fit summaries from those jobs (for context)
{samples_json}

## Your Task
Identify the 1-3 dimensions that most often hold Orkun back from an A score, and for each one \
give a concrete, realistic action he could take in the next 1-3 months to close that gap \
(e.g. a certification, a positioning change for his CV/LinkedIn, a networking move, a skill to \
develop). Do NOT suggest actions for dimensions that are structural and not fixable by Orkun \
(e.g. a specific job's visa policy or location) — focus on things within his control.

Return ONLY valid JSON, no markdown:
{{
  "summary": "1-2 sentence overview of the main pattern this week",
  "weak_dimensions": [
    {{"dimension": "...", "frequency": N, "insight": "why this keeps capping the score", "action": "concrete next step"}}
  ]
}}
"""


def load_scored_jobs():
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "scored_jobs.json")
    if not os.path.exists(data_path):
        print("No scored_jobs.json found — skipping gap analysis.")
        sys.exit(0)
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_weak_dimensions(jobs):
    """Tally C/D dimension scores across B/C-tier (near-miss) jobs."""
    near_miss = [j for j in jobs if j.get("scoring", {}).get("overall_score") in ("B", "C")]

    dim_counter = Counter()
    samples_by_dim = {}
    for job in near_miss:
        dims = job.get("scoring", {}).get("dimension_scores", {})
        for dim_key, dim_val in dims.items():
            if dim_val in ("C", "D") and dim_key in DIM_LABELS:
                dim_counter[dim_key] += 1
                samples_by_dim.setdefault(dim_key, []).append({
                    "company": job.get("company"),
                    "title": job.get("title"),
                    "fit_summary": job.get("scoring", {}).get("fit_summary", ""),
                })

    return near_miss, dim_counter, samples_by_dim


def analyze_gaps():
    if not llm.api_key_present():
        print("ERROR: OPENAI_API_KEY environment variable required.")
        sys.exit(1)

    data = load_scored_jobs()
    jobs = data.get("scored_jobs", [])

    near_miss, dim_counter, samples_by_dim = collect_weak_dimensions(jobs)

    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "gap_report.json")

    if not near_miss or not dim_counter:
        print("No B/C tier jobs with weak dimensions this week — skipping gap report.")
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

    top_dims = dim_counter.most_common(3)
    weak_dims_summary = [
        {"dimension": DIM_LABELS[key], "frequency": count}
        for key, count in top_dims
    ]
    samples = []
    for key, _ in top_dims:
        samples.extend(samples_by_dim[key][:2])

    prompt = GAP_PROMPT.format(
        weak_dims_json=json.dumps(weak_dims_summary, indent=2, ensure_ascii=False),
        samples_json=json.dumps(samples, indent=2, ensure_ascii=False),
    )

    print(f"🧭 Analyzing positioning gaps across {len(near_miss)} near-miss jobs...")

    try:
        # The gap report is already a JSON object, so it needs no unwrapping —
        # unlike the scoring call, whose natural answer is a list.
        report = llm.call_json(
            system=GAP_SYSTEM_PROMPT, user=prompt, max_tokens=1024)
    except Exception as e:
        print(f"⚠ Gap analysis failed ({e}) — skipping gap report for this run.")
        if os.path.exists(output_path):
            os.remove(output_path)
        return None

    report["near_miss_count"] = len(near_miss)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"✅ Gap report generated: {output_path}")
    print(f"   {report.get('summary', '')}")

    return report


if __name__ == "__main__":
    analyze_gaps()
