"""
Tests for the search widening of 1 Oct 2026: the shared title gate, whole-word
company matching, the uncapped Adzuna pool, the two seniority lanes, the
seen-jobs memory and the LinkedIn card parser.

No test here makes a network call.

Run: python3 -m unittest discover -s tests -t .
"""

import os
import sys
import unittest
from datetime import date
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import fetch_jobs  # noqa: E402
import fetch_linkedin  # noqa: E402
import generate_email  # noqa: E402
import score_jobs  # noqa: E402
import track_seen  # noqa: E402
from titles import title_gate, title_lane  # noqa: E402


class TitleGateTests(unittest.TestCase):

    def test_director_and_step_down_titles_pass(self):
        for title in ["Finance Director", "VP Finance", "Head of FP&A", "Chief Financial Officer",
                      "Senior Manager Finance", "Senior Finance Business Partner",
                      "Senior Manager, Strategic Finance - B2C Marketing", "FP&A Manager",
                      "Associate Director, FP&A", "Director/A Operacions I Finances",
                      "Responsable Financiero", "Global Tax Director - HQ"]:
            self.assertIsNone(title_gate(title), title)

    def test_analyst_and_specialist_titles_are_cut(self):
        for title in ["Senior Financial Analyst", "Lead Finance Analyst", "Treasury Senior Associate",
                      "Performance Controlling Specialist", "Senior Accountant", "Finance Trainee"]:
            self.assertIsNotNone(title_gate(title), title)

    def test_controllers_are_cut_unless_the_remit_is_the_group(self):
        for title in ["Financial Controller (MFX)", "Business Controller", "Controller Financiero",
                      "Plant Controller"]:
            self.assertEqual(title_gate(title), "controller without group remit", title)
        self.assertIsNone(title_gate("Group Financial Controller"))
        self.assertIsNone(title_gate("Director, Plant Controlling"))

    def test_a_finance_word_does_not_make_another_function_a_finance_job(self):
        for title in ["Product Manager - Digital Finance", "Senior Solutions Architect (EPM/CPM/FP&A)",
                      "Customer Care Specialist - Finance and Accounting Platform"]:
            self.assertIsNotNone(title_gate(title), title)
        self.assertEqual(title_gate("Senior Software Engineer"), "not a finance title")

    def test_a_title_naming_no_level_is_cut(self):
        self.assertEqual(title_gate("Finance Business Partner – Global IT"), "no seniority marker")

    def test_lane(self):
        self.assertEqual(title_lane("Head of Finance"), "target")
        self.assertEqual(title_lane("Senior Finance Manager"), "step_down")


class CompanyMatchTests(unittest.TestCase):
    TARGETS = [
        {"name": "ABB"}, {"name": "King"}, {"name": "Alan"}, {"name": "Deutsche Bank"},
        {"name": "TravelPerk", "aliases": ["Perk"]}, {"name": "Cellnex Telecom"},
        {"name": "SEAT / CUPRA"}, {"name": "Novartis Iberia"},
    ]

    def _match(self, company):
        hit = fetch_jobs.match_company(company, self.TARGETS)
        return hit["name"] if hit else None

    def test_short_names_match_whole_words_only(self):
        self.assertIsNone(self._match("AbbVie"))
        self.assertIsNone(self._match("Booking.com"))
        self.assertIsNone(self._match("Catalana Occidente"))
        self.assertEqual(self._match("ABB"), "ABB")
        self.assertEqual(self._match("King"), "King")

    def test_generic_first_word_does_not_claim_another_company(self):
        self.assertIsNone(self._match("Deutsche Telekom"))
        self.assertEqual(self._match("Deutsche Bank AG"), "Deutsche Bank")

    def test_aliases_split_names_and_suffixes(self):
        self.assertEqual(self._match("Perk"), "TravelPerk")
        self.assertEqual(self._match("Cellnex"), "Cellnex Telecom")
        self.assertEqual(self._match("CUPRA"), "SEAT / CUPRA")
        self.assertEqual(self._match("Novartis Farmacéutica S.A."), "Novartis Iberia")


class AdzunaPoolTests(unittest.TestCase):

    def test_later_queries_are_not_starved_by_the_first_ones(self):
        """The old cap of 20 was filled by the first queries in order."""
        def fake_search(query, page=1):
            return [{"id": f"{query}-{i}", "title": f"Finance Director {query} {i}",
                     "company": {"display_name": f"Co {query} {i}"},
                     "description": "English ad", "location": {"display_name": "Barcelona"}}
                    for i in range(10)], None

        with patch.object(fetch_jobs, "search_adzuna", side_effect=fake_search), \
                patch.object(fetch_jobs, "load_target_companies", return_value=[]), \
                patch("builtins.open", unittest.mock.mock_open()), \
                patch.object(fetch_jobs.json, "dump"):
            result = fetch_jobs.fetch_all_jobs()

        queries_seen = {j["matched_query"] for j in result["unmatched_jobs"]}
        self.assertEqual(len(result["unmatched_jobs"]), fetch_jobs.MAX_UNMATCHED)
        self.assertGreater(len(queries_seen), 3)

    def test_junk_titles_never_reach_the_pool_even_from_a_target_company(self):
        def fake_search(query, page=1):
            return [{"id": "1", "title": "Senior Software Engineer",
                     "company": {"display_name": "Perk"}, "description": "x"},
                    {"id": "2", "title": "Senior Financial Analyst",
                     "company": {"display_name": "Perk"}, "description": "x"}], None

        with patch.object(fetch_jobs, "search_adzuna", side_effect=fake_search), \
                patch.object(fetch_jobs, "load_target_companies",
                             return_value=[{"name": "TravelPerk", "aliases": ["Perk"], "tier": "B",
                                            "tc_min": 1, "tc_max": 2}]), \
                patch("builtins.open", unittest.mock.mock_open()), \
                patch.object(fetch_jobs.json, "dump"):
            result = fetch_jobs.fetch_all_jobs()

        self.assertEqual(result["matched_jobs"], [])
        # Only the finance-titled one is worth an audit line.
        self.assertEqual([d["title"] for d in result["dropped"]], ["Senior Financial Analyst"])


class SeniorityTests(unittest.TestCase):

    def test_hard_gate_is_five_years(self):
        self.assertTrue(score_jobs.years_requirement_below_bar("+4 years of experience in finance"))
        self.assertFalse(score_jobs.years_requirement_below_bar("5+ years of experience"))
        self.assertFalse(score_jobs.years_requirement_below_bar("no figure stated"))

    def test_stated_years_takes_the_highest_requirement(self):
        self.assertEqual(score_jobs.stated_years("3 years of experience ... 10+ years of experience"), 10)
        self.assertIsNone(score_jobs.stated_years("senior leader"))

    def test_dimension_keys_are_normalized(self):
        got = score_jobs.normalize_dimension_keys(
            {"Total Comp": "A", "english_first_ops": "B", "Side Hustle Compatibility": "C"})
        self.assertEqual(got, {"total_comp": "A", "english_first": "B", "side_hustle": "C"})

    def test_prompt_names_both_lanes_and_asks_for_a_reason(self):
        for needle in ("STEP_DOWN", "TARGET", "irrelevant_reason"):
            self.assertIn(needle, score_jobs.SCORING_PROMPT)


class SeenTrackingTests(unittest.TestCase):

    def test_first_sighting_is_new_and_the_second_is_not(self):
        job = {"title": "Finance Director", "company": "Acme"}
        seen = track_seen.annotate([job], {}, date(2026, 10, 3))
        self.assertTrue(job["is_new"])

        again = {"title": "finance director ", "company": "ACME"}  # another source, same role
        track_seen.annotate([again], seen, date(2026, 10, 10))
        self.assertFalse(again["is_new"])
        self.assertEqual(again["first_seen"], "2026-10-03")

    def test_state_holds_no_job_titles(self):
        seen = track_seen.annotate([{"title": "Finance Director", "company": "Acme"}], {},
                                   date(2026, 10, 3))
        self.assertNotIn("Acme", str(seen))
        self.assertNotIn("Finance", str(seen))

    def test_postings_that_stop_appearing_are_forgotten(self):
        seen = track_seen.annotate([{"title": "A", "company": "B"}], {}, date(2026, 7, 1))
        seen = track_seen.annotate([], seen, date(2026, 10, 3))
        self.assertEqual(seen, {})


class LinkedInParsingTests(unittest.TestCase):
    CARD = '''<li><div class="base-card" data-entity-urn="urn:li:jobPosting:4123456789">
      <h3 class="base-search-card__title"> Head of Finance </h3>
      <h4 class="base-search-card__subtitle"><a href="#"> Acme &amp; Co </a></h4>
      <span class="job-search-card__location"> Barcelona, Catalonia, Spain </span>
      <time class="job-search-card__listdate" datetime="2026-09-28">3 days ago</time></div></li>'''

    def test_card_fields(self):
        (card,) = fetch_linkedin.parse_cards(self.CARD)
        self.assertEqual(card, {"id": "4123456789", "title": "Head of Finance",
                                "company": "Acme & Co",
                                "location": "Barcelona, Catalonia, Spain", "posted": "2026-09-28"})

    def test_the_region_name_alone_is_not_barcelona(self):
        self.assertTrue(fetch_linkedin.in_barcelona_area("Greater Barcelona Metropolitan Area"))
        self.assertTrue(fetch_linkedin.in_barcelona_area("Sant Cugat del Vallès, Catalonia, Spain"))
        self.assertFalse(fetch_linkedin.in_barcelona_area("Valls, Catalonia, Spain"))
        self.assertFalse(fetch_linkedin.in_barcelona_area("Madrid, Community of Madrid, Spain"))

    def test_description_stops_at_its_own_div(self):
        page = ('<div class="show-more-less-html__markup"><p>Lead FP&amp;A.</p>'
                '<div>8+ years</div></div><div class="criteria">Seniority</div>')
        self.assertEqual(fetch_linkedin.parse_description(page), "Lead FP&A. 8+ years")

    def test_closed_banner_only_counts_above_the_description(self):
        body = '<div class="show-more-less-html__markup">no longer accepting applications</div>'
        self.assertFalse(fetch_linkedin.is_closed(body))
        self.assertTrue(fetch_linkedin.is_closed('<span class="closed-job__flavor"></span>' + body))


class EmailTests(unittest.TestCase):

    def test_step_down_roles_get_their_own_section_and_cuts_are_listed(self):
        def job(title, level, new):
            return {"title": title, "company": "Acme", "location": "Barcelona", "url": "#",
                    "is_new": new, "first_seen": "2026-09-26",
                    "scoring": {"overall_score": "B", "level": level, "apply_priority": "WATCH",
                                "hqp_risk": "LOW", "dimension_scores": {}}}

        data = {"score_date": "2026-10-03T09:30:00",
                "scored_jobs": [job("Finance Director", "TARGET", True),
                                job("Senior Finance Manager", "STEP_DOWN", False)],
                "rejected": [{"title": "Financial <Controller>", "company": "Acme",
                              "reason": "controller without group remit", "stage": "title gate"}]}
        with patch.object(generate_email, "load_scored_jobs", return_value=data), \
                patch.object(generate_email, "load_gap_report", return_value=None), \
                patch("builtins.open", unittest.mock.mock_open(read_data="[]")):
            page, total = generate_email.generate_email()

        self.assertEqual(total, 2)
        self.assertLess(page.index("TIER B"), page.index("ONE RUNG BELOW"))
        self.assertLess(page.index("ONE RUNG BELOW"), page.index("Senior Finance Manager"))
        self.assertIn("NEW", page)
        self.assertIn("first seen 26 Sep", page)
        self.assertIn("Financial &lt;Controller&gt;", page)
        self.assertIn("controller without group remit", page)


if __name__ == "__main__":
    unittest.main()
