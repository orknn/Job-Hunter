"""
titles.py — the title gate every fetcher (Adzuna, ATS, LinkedIn) applies before
a posting is allowed to cost a scoring call or a line in the digest.

What the candidate wants to see:
  - target lane:    Director / Head / VP / CFO-level finance roles
  - step-down lane: Senior Manager, Senior Finance Business Partner, Finance
                    Manager / Lead — one rung below, shown in their own section
What must never arrive: analysts, specialists, accountants, and controller
roles (Financial / Business / Plant Controller) without a group-level remit.

The gate is deliberately title-only and deterministic. Anything it lets through
is still judged on its description by the years gate and the scorer.
"""

import re

# Substring match (lowercase). "finance" also covers Catalan "finances".
FINANCE_KEYWORDS = [
    "finance", "financial", "fp&a", "fpa", "fp & a", "controller", "controlling",
    "cfo", "treasury", "accounting", "financiero", "financiera", "finanzas",
    "contabilidad", "tesorería", "tesoreria",
]
# Whole-word only — "tax" must not match "taxonomy".
_FINANCE_WORD_RE = re.compile(r"\b(tax|audit|auditor[ií]a|fiscal)\b", re.IGNORECASE)

_INTERN_RE = re.compile(
    r"\b(intern|internship|pr[áa]cticas|becari[oa]|trainee|working student|"
    r"graduate program|apprentice)\b",
    re.IGNORECASE,
)

# Finance word in the title, but the job is another function ("Product Manager
# - Digital Finance", "Solutions Architect (FP&A)", "Customer Care - Finance").
_OTHER_FUNCTION_RE = re.compile(
    r"\b(product\s+(manager|owner|lead)|engineer(ing)?|developer|architect|"
    r"recruiter|customer\s+(care|success|support|service)|sales\s+development|"
    r"account\s+executive|consultant|consultor[a/]*)\b",
    re.IGNORECASE,
)

_TOP_RE = re.compile(
    r"\b(director|directora|head|vp|vice\s+president|chief|cfo)\b", re.IGNORECASE)

# Below the bar unless a top marker overrides ("Associate Director" survives).
_JUNIOR_RE = re.compile(
    r"\b(analyst|analista|junior|jr|entry\s+level|graduate|associate|asociad[oa]|"
    r"auxiliar|assistant|asistente|specialist|especialista|accountant|contable|"
    r"technician|t[ée]cnic[oa]|clerk|administrativ[oa]|administrator|coordinator|"
    r"bookkeeper|payroll|collections?|receivable|payable|billing|officer)\b",
    re.IGNORECASE,
)

# Controller roles are mid-level unless the remit is the whole group.
_CONTROLLER_RE = re.compile(r"\bcontroll?er\b", re.IGNORECASE)
_GROUP_REMIT_RE = re.compile(r"\b(group|corporate|global|emea|europe)\b", re.IGNORECASE)

# Without any of these the title names no level at all ("Finance Business
# Partner") and reads as an individual-contributor mid-level role.
_LEVEL_RE = re.compile(
    r"\b(manager|gerente|lead|principal|responsable|jefe|jefa|senior|sr)\b",
    re.IGNORECASE,
)


def is_finance_title(title):
    t = (title or "").lower()
    return any(k in t for k in FINANCE_KEYWORDS) or bool(_FINANCE_WORD_RE.search(t))


def title_gate(title):
    """None when the title may go on to scoring, else the reason it is dropped."""
    t = title or ""
    if _INTERN_RE.search(t):
        return "intern/trainee"
    if not is_finance_title(t):
        return "not a finance title"
    if _OTHER_FUNCTION_RE.search(t):
        return "other function"
    top = bool(_TOP_RE.search(t))
    if top:
        return None
    if _JUNIOR_RE.search(t):
        return "analyst/specialist level"
    if _CONTROLLER_RE.search(t):
        return None if _GROUP_REMIT_RE.search(t) else "controller without group remit"
    if not _LEVEL_RE.search(t):
        return "no seniority marker"
    return None


def title_lane(title):
    """'target' for Director/Head/VP/CFO titles, 'step_down' for the rest that pass."""
    return "target" if _TOP_RE.search(title or "") else "step_down"
