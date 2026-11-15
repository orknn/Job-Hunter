"""
Requirement fit: the + / − lines on each card and the development paragraph.

The skill profile is private (secret / git-ignored file), so these tests pin
two things: the comparison only happens when a profile exists, and nothing the
candidate lacks ends up in a file the public repo archives.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import analyze_gaps  # noqa: E402
import generate_email  # noqa: E402
import score_jobs  # noqa: E402


class PromptTests(unittest.TestCase):

    def test_without_a_profile_the_fit_fields_are_not_requested(self):
        prompt = score_jobs.build_prompt("[]", "")
        self.assertNotIn("strengths", prompt)
        self.assertNotIn("{skills_block}", prompt)
        self.assertNotIn("{fit_task}", prompt)

    def test_with_a_profile_the_prompt_carries_it_and_the_tag_list(self):
        prompt = score_jobs.build_prompt("[]", "- LACKS: Python")
        self.assertIn("- LACKS: Python", prompt)
        self.assertIn('"gaps"', prompt)
        for tag in score_jobs.GAP_TAGS:
            self.assertIn(f'"{tag}"', prompt)

    def test_env_secret_wins_over_the_local_file(self):
        with patch.dict(os.environ, {"CANDIDATE_PROFILE": "from secret"}):
            self.assertEqual(score_jobs.load_candidate_profile(), "from secret")

    def test_the_profile_is_not_in_the_repo(self):
        root = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
            ignored = f.read()
        for private in ("data/candidate_profile.md", "data/fit_notes.json", "data/gap_report.json"):
            self.assertIn(private, ignored)
        with open(os.path.join(root, ".github", "workflows", "job_digest.yml"), encoding="utf-8") as f:
            archive = f.read().split("Archive results")[1].split("path: |")[1]
        for private in ("fit_notes", "gap_report", "email_digest"):
            self.assertNotIn(private, archive)


class ExtractFitTests(unittest.TestCase):

    def test_fit_fields_leave_the_score_that_gets_archived(self):
        score = {"overall_score": "B", "requirements_visible": True,
                 "strengths": ["SQL reporting"],
                 "gaps": [{"tag": "sap", "label": "S/4HANA rollout", "need": "must"}]}
        fit = score_jobs.extract_fit(score)
        self.assertEqual(score, {"overall_score": "B"})
        self.assertEqual(fit["gaps"], [{"tag": "sap", "label": "S/4HANA rollout", "need": "must"}])

    def test_a_score_made_without_a_profile_has_no_fit(self):
        self.assertIsNone(score_jobs.extract_fit({"overall_score": "B"}))

    def test_model_noise_is_normalised(self):
        fit = score_jobs.extract_fit({
            "requirements_visible": True,
            "strengths": ["a", "b", "c", "d", 7, ""],
            "gaps": [{"tag": "made_up", "label": "x" * 200, "need": "essential"},
                     {"tag": "sap"}, "not a dict"]})
        self.assertEqual(fit["strengths"], ["a", "b", "c"])
        self.assertEqual(len(fit["gaps"]), 1)
        self.assertEqual(fit["gaps"][0]["tag"], "other")
        self.assertEqual(fit["gaps"][0]["need"], "nice")
        self.assertLessEqual(len(fit["gaps"][0]["label"]), 60)

    def test_a_teaser_length_ad_never_claims_a_comparison(self):
        """Adzuna's 500-char snippets got strengths and an empty gap list."""
        fit = score_jobs.extract_fit(
            {"requirements_visible": True, "strengths": ["Forecasting"], "gaps": []},
            description_chars=500)
        self.assertEqual(fit, {"requirements_visible": False, "strengths": [], "gaps": []})

    def test_invisible_requirements_with_no_items_stay_invisible(self):
        fit = score_jobs.extract_fit({"requirements_visible": False, "strengths": [], "gaps": []})
        self.assertFalse(fit["requirements_visible"])


class TallyTests(unittest.TestCase):

    NOTES = {
        "1": {"requirements_visible": True, "gaps": [
            {"tag": "sap", "label": "S/4HANA", "need": "must"},
            {"tag": "sap", "label": "SAP FI/CO", "need": "nice"},
            {"tag": "other", "label": "odd one", "need": "must"}]},
        "2": {"requirements_visible": True, "gaps": [
            {"tag": "sap", "label": "S/4HANA", "need": "nice"},
            {"tag": "certification", "label": "ACCA", "need": "must"}]},
        "3": {"requirements_visible": False, "gaps": []},
        "4": {"requirements_visible": True, "gaps": []},
    }

    def test_an_ad_counts_once_per_tag_and_teasers_are_not_in_the_base(self):
        compared, ranked = analyze_gaps.tally_gaps([1, 2, 3, 4, 99], self.NOTES)
        self.assertEqual(compared, 3)
        by_tag = {r["tag"]: r for r in ranked}
        self.assertEqual((by_tag["sap"]["count"], by_tag["sap"]["must_count"]), (2, 1))
        self.assertEqual(by_tag["sap"]["examples"], ["S/4HANA", "SAP FI/CO"])
        self.assertNotIn("other", by_tag)

    def test_the_run_log_never_prints_gap_content(self):
        """Actions logs of a public repo are public."""
        jobs = {"scored_jobs": [{"id": i} for i in range(1, 7)]}
        notes = {str(i): {"requirements_visible": True,
                          "gaps": [{"tag": "sap", "label": "SECRETLABEL", "need": "must"}]}
                 for i in range(1, 7)}
        written = {}

        def fake_load(path, default):
            return jobs if path == analyze_gaps.SCORED_PATH else notes

        with patch.object(analyze_gaps, "_load", side_effect=fake_load), \
                patch.object(analyze_gaps.os.path, "exists", return_value=False), \
                patch.object(analyze_gaps.llm, "api_key_present", return_value=True), \
                patch.object(analyze_gaps.llm, "call_json",
                             return_value={"paragraph": "PARAGRAPHTEXT"}) as call, \
                patch("builtins.open", unittest.mock.mock_open()), \
                patch.object(analyze_gaps.json, "dump",
                             side_effect=lambda obj, f, **k: written.update(obj)), \
                patch("builtins.print") as printed:
            analyze_gaps.analyze_gaps()

        log = " ".join(str(c) for c in printed.call_args_list)
        self.assertNotIn("SECRETLABEL", log)
        self.assertNotIn("PARAGRAPHTEXT", log)
        self.assertNotIn("SAP", log)
        self.assertEqual(written["paragraph"], "PARAGRAPHTEXT")
        self.assertEqual(written["top_gaps"][0]["count"], 6)
        # The model is handed counts, not asked to produce them.
        self.assertIn('"ads": 6', call.call_args.kwargs["user"])


class CardTests(unittest.TestCase):

    def test_plus_and_minus_lines_render_escaped(self):
        out = generate_email.build_fit_html({
            "requirements_visible": True, "strengths": ["P&L <owner>"],
            "gaps": [{"tag": "sap", "label": "S/4HANA", "need": "must"},
                     {"tag": "certification", "label": "ACCA", "need": "nice"}]})
        self.assertIn("P&amp;L &lt;owner&gt;", out)
        self.assertIn("S/4HANA <b>(must)</b>", out)
        self.assertNotIn("ACCA <b>(must)</b>", out)

    def test_a_teaser_ad_says_so_instead_of_claiming_no_gaps(self):
        out = generate_email.build_fit_html(
            {"requirements_visible": False, "strengths": [], "gaps": []})
        self.assertIn("Requirements not listed", out)
        self.assertNotIn("no gap", out)

    def test_no_profile_no_lines(self):
        self.assertEqual(generate_email.build_fit_html(None), "")

    def test_development_block_shows_paragraph_and_counts(self):
        out = generate_email.build_gap_report_html({
            "jobs_total": 30, "jobs_compared": 24, "paragraph": "Learn <SAP>.",
            "top_gaps": [{"tag": "sap", "name": "SAP depth", "count": 9,
                          "must_count": 4, "examples": ["S/4HANA"]}]})
        self.assertIn("Learn &lt;SAP&gt;.", out)
        self.assertIn("9 of 24 ads · must-have in 4", out)
        self.assertEqual(generate_email.build_gap_report_html(None), "")


if __name__ == "__main__":
    unittest.main()
