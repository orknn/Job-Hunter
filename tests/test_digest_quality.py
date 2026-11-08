"""
Tests for the digest-quality pass of 8 Nov 2026: cross-source dedupe, the
APPLY NOW calibration, the recruiter flag, the audit/tax title cut, the
coverage note and the two replaced career-site adapters (Microsoft, Jobvite).

No test here makes a network call.

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import dedupe  # noqa: E402
import fetch_ats  # noqa: E402
import generate_email  # noqa: E402
import score_jobs  # noqa: E402
from titles import title_gate  # noqa: E402

TARGETS = [
    {"name": "Novartis Iberia", "tier": "A", "tc_min": 1, "tc_max": 2},
    {"name": "Glovo", "aliases": ["Delivery Hero"], "tier": "B", "tc_min": 1, "tc_max": 2},
]


def posting(title, company, query, description="x", **extra):
    return {"id": f"{company}-{query}", "title": title, "company": company,
            "matched_query": query, "description": description, "url": f"https://{query}",
            "salary_min": None, "salary_max": None, "target_match": None, **extra}


class DedupeTests(unittest.TestCase):

    def test_one_role_from_three_sources_becomes_one_card(self):
        # The 7 Nov 2026 digest: same Novartis role via Adzuna, Workday, LinkedIn.
        jobs = [
            posting("DD&IT Global FP&A Manager", "Novartis España", "FP&A Manager",
                    salary_min=59100, salary_max=109700),
            posting("DD&IT Global FP&A Manager", "Novartis Iberia", "ats:workday", "long " * 100),
            posting("DD&IT Global FP&A Manager", "Novartis", "linkedin"),
        ]
        unique, removed = dedupe.dedupe_jobs(jobs, TARGETS)
        self.assertEqual((len(unique), removed), (1, 2))
        kept = unique[0]
        self.assertEqual(kept["matched_query"], "ats:workday")   # company board wins
        self.assertEqual(kept["salary_min"], 59100)             # salary carried over
        self.assertEqual(kept["also_on"], ["Adzuna", "LinkedIn"])

    def test_parent_company_alias_collapses_glovo_and_delivery_hero(self):
        jobs = [posting("Finance Insights Manager", "Glovo", "Strategic Finance"),
                posting("Finance Insights Manager", "Delivery Hero", "Strategic Finance")]
        unique, removed = dedupe.dedupe_jobs(jobs, TARGETS)
        self.assertEqual(removed, 1)

    def test_different_roles_and_unrelated_companies_stay_apart(self):
        jobs = [posting("Head of Finance", "Exoticca", "Head of Finance"),
                posting("Head of Finance", "Arbolus", "Head of Finance"),
                posting("FP&A Manager", "Novartis Iberia", "ats:workday")]
        unique, removed = dedupe.dedupe_jobs(jobs, TARGETS)
        self.assertEqual(removed, 0)
        self.assertNotIn("also_on", unique[0])

    def test_title_punctuation_does_not_split_a_role(self):
        jobs = [posting("Senior Manager, Strategic Finance - B2C", "Glovo", "linkedin"),
                posting("Senior Manager Strategic Finance B2C", "Glovo", "Adzuna")]
        self.assertEqual(dedupe.dedupe_jobs(jobs, TARGETS)[1], 1)


class RecruiterTests(unittest.TestCase):

    def test_known_agencies_and_client_phrasing_are_flagged(self):
        self.assertTrue(dedupe.is_recruiter({"company": "Finatal"}))
        self.assertTrue(dedupe.is_recruiter({"company": "Mur&Partners"}))
        self.assertTrue(dedupe.is_recruiter({"company": "Michael Page España"}))
        self.assertTrue(dedupe.is_recruiter(
            {"company": "Confidential", "description": "Our client, a leading SaaS scale-up, ..."}))

    def test_employers_are_not_flagged(self):
        for company in ["Glovo", "Schneider Electric", "Hayes Logistics", "Novartis"]:
            self.assertFalse(dedupe.is_recruiter({"company": company, "description": "We are Glovo."}),
                             company)
        # A service firm talking about its own customers is not an agency.
        self.assertFalse(dedupe.is_recruiter(
            {"company": "Amesto Global", "description": "to deliver timely information to our clients."}))


class PriorityCalibrationTests(unittest.TestCase):

    def check(self, grade, level, risk, given, expected):
        score = {"overall_score": grade, "level": level, "hqp_risk": risk, "apply_priority": given}
        self.assertEqual(score_jobs.calibrate_priority(score)["apply_priority"], expected,
                         (grade, level, risk, given))

    def test_apply_now_is_kept_only_where_earned(self):
        self.check("A", "STEP_DOWN", "HIGH", "APPLY NOW", "APPLY NOW")
        self.check("B", "TARGET", "MEDIUM", "APPLY NOW", "APPLY NOW")
        self.check("B", "TARGET", "HIGH", "APPLY NOW", "WATCH")
        self.check("B", "STEP_DOWN", "LOW", "APPLY NOW", "WATCH")
        self.check("C", "TARGET", "LOW", "APPLY NOW", "WATCH")

    def test_never_upgrades(self):
        self.check("A", "TARGET", "LOW", "WATCH", "WATCH")
        self.check("A", "TARGET", "LOW", "SKIP", "SKIP")


class SpecialistTitleTests(unittest.TestCase):

    def test_audit_and_tax_compliance_tracks_are_cut_before_scoring(self):
        for title in ["Senior Manager, Internal Audit", "Head of Internal Audit",
                      "Robotics Global Analytics Audit Manager",
                      "Tax Compliance Manager, EMEA VAT compliance", "Indirect Tax Lead"]:
            self.assertIsNotNone(title_gate(title), title)

    def test_tax_and_controlling_leadership_still_pass(self):
        for title in ["Global Tax Director - HQ", "Global Controlling & Tax Director",
                      "Finance Director"]:
            self.assertIsNone(title_gate(title), title)


class AdapterTests(unittest.TestCase):

    def response(self, status=200, json_data=None, text=""):
        r = MagicMock(status_code=status, text=text)
        r.json.return_value = json_data
        return r

    def test_microsoft_pages_through_the_new_api(self):
        def page(start):
            positions = [{"id": start + i, "name": f"Role {start + i}",
                          "locations": ["Spain, Barcelona, Barcelona"], "postedTs": 1793982873}
                         for i in range(10 if start == 0 else 5)]
            return self.response(json_data={"data": {"positions": positions, "count": 15}})

        with patch.object(fetch_ats.requests, "get",
                          side_effect=lambda url, **kw: page(kw["params"]["start"])):
            jobs = fetch_ats.fetch_microsoft({})
        self.assertEqual(len(jobs), 15)
        self.assertEqual(jobs[0]["url"], "https://apply.careers.microsoft.com/careers/job/0")
        self.assertIn("Barcelona", jobs[0]["location"])

    def test_jobvite_rows_are_parsed(self):
        page = """<tr>
            <td class="jv-job-list-name">
                <a href="/affinitypetcare/job/oSDszfwg">Digital Channels Business Partner</a>
            </td>
            <td class="jv-job-list-location">
            Barcelona,
            Barcelona
            </td>
        </tr>"""
        with patch.object(fetch_ats.requests, "get", return_value=self.response(text=page)):
            jobs = fetch_ats.fetch_jobvite({"company": "affinitypetcare"})
        self.assertEqual(jobs, [{
            "title": "Digital Channels Business Partner", "location": "Barcelona, Barcelona",
            "url": "https://jobs.jobvite.com/affinitypetcare/job/oSDszfwg",
            "description": "Digital Channels Business Partner", "posted": ""}])

    def test_personio_feed_is_parsed_as_utf8(self):
        feed = ('<?xml version="1.0" encoding="UTF-8"?><workzag-jobs><position><id>2539117</id>'
                '<office>Barcelona</office><additionalOffice>Madrid</additionalOffice>'
                '<name>Head of Finance – Iberia</name><createdAt>2026-11-01</createdAt>'
                '<jobDescriptions><jobDescription><name>Mission</name><value><![CDATA[<p>Own the P&amp;L.</p>]]>'
                '</value></jobDescription></jobDescriptions></position></workzag-jobs>')
        r = self.response(text=feed.encode("utf-8").decode("latin-1"))  # what requests would guess
        r.content = feed.encode("utf-8")
        with patch.object(fetch_ats.requests, "get", return_value=r):
            jobs = fetch_ats.fetch_personio({"company": "ailylabs"})
        self.assertEqual(jobs[0]["title"], "Head of Finance – Iberia")
        self.assertEqual(jobs[0]["location"], "Barcelona / Madrid")
        self.assertEqual(jobs[0]["url"], "https://ailylabs.jobs.personio.de/job/2539117")
        self.assertEqual(jobs[0]["description"], "Own the P&L.")

    def test_a_failed_endpoint_gets_one_retry(self):
        calls = []

        def flaky(cfg):
            calls.append(1)
            return None if len(calls) == 1 else []

        company = {"name": "Puig", "tier": "A", "tc_min": 1, "tc_max": 2,
                   "ats": {"type": "flaky"}}
        with patch.dict(fetch_ats.ADAPTERS, {"flaky": flaky}), \
                patch.object(fetch_ats, "RETRY_PAUSE", 0):
            jobs, status = fetch_ats.fetch_company(company)
        self.assertEqual(len(calls), 2)
        self.assertTrue(status.startswith("OK"), status)


class CoverageNoteTests(unittest.TestCase):

    def test_failed_sources_are_named_in_the_mail(self):
        note = generate_email.build_coverage_html({
            "ats_failed": ["Puig"], "ats_unconfigured": ["Glovo", "Mango"],
            "linkedin_unavailable": "429",
            "adzuna_failed_queries": ["CFO", "VP Finance"], "adzuna_total_queries": 16})
        self.assertIn("Adzuna search failed for 2 of 16 queries", note)
        self.assertIn("Puig", note)
        self.assertIn("LinkedIn", note)
        self.assertIn("2 target companies", note)

    def test_nothing_to_report_renders_nothing(self):
        self.assertEqual(generate_email.build_coverage_html({}), "")
        self.assertEqual(generate_email.build_coverage_html(None), "")

    def test_card_shows_recruiter_and_other_sources(self):
        card = generate_email.build_job_card({
            "title": "EMEA CFO", "company": "Finatal", "via_recruiter": True,
            "also_on": ["Adzuna", "LinkedIn"],
            "scoring": {"overall_score": "A", "apply_priority": "APPLY NOW", "hqp_risk": "LOW"}})
        self.assertIn("via recruiter", card)
        self.assertIn("also on Adzuna, LinkedIn", card)


if __name__ == "__main__":
    unittest.main()
