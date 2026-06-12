"""
generate_email.py — Puanlanan ilanlardan premium HTML email oluşturur.
Dark theme, tier bazlı gruplandırma, detaylı kartlar.
"""

import os
import json
from datetime import datetime


def load_scored_jobs():
    """Load scored jobs from JSON file."""
    data_path = os.path.join(os.path.dirname(__file__), "..", "data", "scored_jobs.json")
    with open(data_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_score_color(score):
    """Return color for score badge."""
    colors = {
        "A": "#10b981",  # Emerald green
        "B": "#3b82f6",  # Blue
        "C": "#f59e0b",  # Amber
        "D": "#ef4444",  # Red
    }
    return colors.get(score, "#6b7280")


def get_priority_badge(priority):
    """Return HTML badge for apply priority."""
    styles = {
        "APPLY NOW": ("🎯", "#10b981", "#052e16"),
        "WATCH": ("👀", "#f59e0b", "#451a03"),
        "SKIP": ("⏭", "#6b7280", "#1f2937"),
    }
    icon, bg, text = styles.get(priority, ("", "#6b7280", "#1f2937"))
    return f'<span style="background:{bg}20;color:{bg};padding:3px 10px;border-radius:12px;font-size:12px;font-weight:600;">{icon} {priority}</span>'


def get_hqp_badge(risk):
    """Return HTML badge for HQP risk."""
    styles = {
        "LOW": ("✅", "#10b981"),
        "MEDIUM": ("⚠️", "#f59e0b"),
        "HIGH": ("🚨", "#ef4444"),
    }
    icon, color = styles.get(risk, ("❓", "#6b7280"))
    return f'<span style="color:{color};font-size:12px;">{icon} HQP Risk: {risk}</span>'


def _fmt_eur(n):
    """Format a euro amount compactly: 90000 → €90k, 1500 → €1.5k, 800 → €800."""
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n >= 1000:
        k = n / 1000
        return f"€{k:.0f}k" if k == int(k) else f"€{k:.1f}k"
    return f"€{int(n)}"


def _salary_range(smin, smax):
    """Build an '€X-Y' string from whatever bounds are present."""
    lo, hi = _fmt_eur(smin), _fmt_eur(smax)
    if lo and hi:
        return f"{lo}-{hi}" if lo != hi else lo
    return lo or hi


def format_salary(job):
    """Render the headline salary honestly, per its source:
      posting          → 💰 €X-Y                    (real numbers from the ad)
      adzuna_predicted → 💰 €X-Y (Adzuna tahmini)   (Adzuna's ML guess)
      claude_estimate  → 💰 ~€X-Y (tahmini)          (Claude's rubric estimate)"""
    source = job.get("salary_source", "claude_estimate")
    rng = _salary_range(job.get("salary_min"), job.get("salary_max"))

    if source == "posting" and rng:
        return f"💰 {rng}"
    if source == "adzuna_predicted" and rng:
        return f"💰 {rng} (Adzuna tahmini)"
    tc = job.get("scoring", {}).get("tc_estimate", "N/A")
    return f"💰 ~{tc} (tahmini)"


def build_job_card(job):
    """Build HTML card for a single job."""
    scoring = job.get("scoring", {})
    score = scoring.get("overall_score", "?")
    score_color = get_score_color(score)
    salary_html = format_salary(job)
    fit_summary = scoring.get("fit_summary", "")
    priority = scoring.get("apply_priority", "WATCH")
    hqp_risk = scoring.get("hqp_risk", "MEDIUM")
    target = job.get("target_match", {})
    tier = target.get("tier", "?") if target else "New"
    url = job.get("url", "#")

    # Dimension scores as mini badges
    dim_scores = scoring.get("dimension_scores", {})
    dim_html = ""
    dim_labels = {
        "total_comp": "💰 Comp",
        "hqp_visa_ease": "🛂 Visa",
        "english_first": "🇬🇧 Eng",
        "career_trajectory": "📈 Career",
        "industry_pull": "🏭 Industry",
        "scope": "📊 Scope",
        "working_model": "🏠 WFH",
        "stability": "🏛 Stable",
        "brand_value": "⭐ Brand",
        "side_hustle": "🌙 Side",
    }
    for key, label in dim_labels.items():
        dim_val = dim_scores.get(key, "?")
        dim_color = get_score_color(dim_val)
        dim_html += f'<span style="display:inline-block;margin:2px 3px;padding:2px 6px;background:{dim_color}15;color:{dim_color};border-radius:4px;font-size:10px;font-weight:600;">{label}: {dim_val}</span>'

    return f"""
    <div style="background:#1a1a2e;border:1px solid #2d2d44;border-left:4px solid {score_color};border-radius:12px;padding:20px;margin-bottom:16px;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
        <div>
          <div style="font-size:18px;font-weight:700;color:#e2e8f0;margin-bottom:4px;">{job.get('company', 'Unknown')}</div>
          <div style="font-size:14px;color:#94a3b8;">{job.get('title', '')}</div>
          <div style="font-size:12px;color:#64748b;margin-top:4px;">📍 {job.get('location', 'Barcelona')}</div>
        </div>
        <div style="text-align:right;">
          <div style="background:{score_color};color:#000;font-size:24px;font-weight:800;width:44px;height:44px;border-radius:10px;display:inline-flex;align-items:center;justify-content:center;">{score}</div>
          <div style="font-size:11px;color:#64748b;margin-top:4px;">Tier {tier}</div>
        </div>
      </div>

      <div style="display:flex;gap:12px;align-items:center;margin-bottom:12px;flex-wrap:wrap;">
        <span style="color:#e2e8f0;font-weight:600;font-size:14px;">{salary_html}</span>
        {get_priority_badge(priority)}
        {get_hqp_badge(hqp_risk)}
      </div>

      <div style="color:#cbd5e1;font-size:13px;line-height:1.6;margin-bottom:12px;padding:12px;background:#16162a;border-radius:8px;">
        {fit_summary}
      </div>

      <div style="margin-bottom:12px;">{dim_html}</div>

      <a href="{url}" style="display:inline-block;background:{score_color};color:#000;text-decoration:none;padding:8px 20px;border-radius:8px;font-weight:700;font-size:13px;">
        View Job ↗
      </a>
    </div>
    """


def generate_email():
    """Generate the full HTML email."""
    data = load_scored_jobs()
    jobs = data.get("scored_jobs", [])
    score_date = data.get("score_date", datetime.utcnow().isoformat())

    # Parse date for display
    try:
        dt = datetime.fromisoformat(score_date.replace("Z", "+00:00"))
        date_display = dt.strftime("%A, %d %B %Y")
    except Exception:
        date_display = score_date[:10]

    # Group by overall score
    groups = {"A": [], "B": [], "C": [], "D": []}
    for job in jobs:
        grade = job.get("scoring", {}).get("overall_score", "D")
        if grade in groups:
            groups[grade].append(job)

    # Count stats
    total = len(jobs)
    apply_now = len([j for j in jobs if j.get("scoring", {}).get("apply_priority") == "APPLY NOW"])

    # Build sections
    sections_html = ""
    tier_labels = {
        "A": ("🏆 TIER A — Top Matches", "#10b981"),
        "B": ("⭐ TIER B — Strong Matches", "#3b82f6"),
        "C": ("📋 TIER C — Worth Watching", "#f59e0b"),
        "D": ("📌 TIER D — Lower Priority", "#6b7280"),
    }

    for grade in ["A", "B", "C", "D"]:
        grade_jobs = groups[grade]
        if not grade_jobs:
            continue
        label, color = tier_labels[grade]
        cards = "".join(build_job_card(j) for j in grade_jobs)
        sections_html += f"""
        <div style="margin-top:32px;">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
            <h2 style="color:{color};font-size:18px;font-weight:700;margin:0;">{label}</h2>
            <span style="background:{color}20;color:{color};padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;">{len(grade_jobs)} position{'s' if len(grade_jobs) != 1 else ''}</span>
          </div>
          {cards}
        </div>
        """

    # No jobs fallback
    if total == 0:
        sections_html = """
        <div style="text-align:center;padding:60px 20px;">
          <div style="font-size:48px;margin-bottom:16px;">🔍</div>
          <div style="color:#94a3b8;font-size:16px;">No matching positions found this week.</div>
          <div style="color:#64748b;font-size:13px;margin-top:8px;">The search covered all 42 target companies. Keep watching — new roles get posted regularly.</div>
        </div>
        """

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Weekly Job Digest</title>
</head>
<body style="margin:0;padding:0;background:#0f0f23;font-family:'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="max-width:680px;margin:0 auto;padding:24px 16px;">

    <!-- Header -->
    <div style="text-align:center;padding:32px 20px;background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);border-radius:16px;margin-bottom:24px;border:1px solid #2d2d44;">
      <div style="font-size:32px;margin-bottom:8px;">🎯</div>
      <h1 style="color:#e2e8f0;font-size:24px;font-weight:800;margin:0 0 8px 0;">Weekly Job Digest</h1>
      <div style="color:#94a3b8;font-size:14px;">{date_display}</div>

      <!-- Stats bar -->
      <div style="display:flex;justify-content:center;gap:24px;margin-top:20px;">
        <div style="text-align:center;">
          <div style="color:#10b981;font-size:28px;font-weight:800;">{total}</div>
          <div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px;">Matches</div>
        </div>
        <div style="text-align:center;">
          <div style="color:#f59e0b;font-size:28px;font-weight:800;">{apply_now}</div>
          <div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px;">Apply Now</div>
        </div>
        <div style="text-align:center;">
          <div style="color:#3b82f6;font-size:28px;font-weight:800;">42</div>
          <div style="color:#64748b;font-size:11px;text-transform:uppercase;letter-spacing:1px;">Companies</div>
        </div>
      </div>
    </div>

    <!-- Job Sections -->
    {sections_html}

    <!-- Footer -->
    <div style="text-align:center;padding:32px 20px;margin-top:32px;border-top:1px solid #2d2d44;">
      <div style="color:#64748b;font-size:12px;">
        Job Hunter · Automated weekly digest<br>
        Powered by Adzuna + Claude AI<br>
        <span style="color:#475569;">42 target companies · Barcelona & Remote</span>
      </div>
    </div>

  </div>
</body>
</html>"""

    # Save HTML
    output_path = os.path.join(os.path.dirname(__file__), "..", "data", "email_digest.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Email HTML generated: {output_path}")
    print(f"   Total jobs: {total} | Apply Now: {apply_now}")

    return html, total


if __name__ == "__main__":
    generate_email()
