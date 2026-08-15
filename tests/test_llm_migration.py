"""
Tests for the Anthropic → OpenAI migration (15 Aug 2026).

No test here makes a network call. The provider is stubbed, which is the only
way to check the migration without spending on the account that had just run
dry — and the request shape is asserted rather than assumed, because that is
the part nobody can eyeball: the call either uses the right argument names or
it fails at 09:50 next Saturday with nothing sent.

Run: python3 -m unittest discover -s tests -t .
"""

import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import llm  # noqa: E402
import score_jobs  # noqa: E402


def _completion(content, finish_reason="stop"):
    """A stand-in for the SDK's response object."""
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=content), finish_reason=finish_reason)])


class LlmClientTests(unittest.TestCase):

    def _patched(self, completion):
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        return patch.object(llm, "_get_client", return_value=client), client

    def test_request_shape_matches_the_provider(self):
        """The argument names this provider accepts, locked down.

        `max_tokens` is rejected in favour of `max_completion_tokens`, and
        `temperature=0` is rejected outright — both learned from job-hunter-core,
        neither visible without a live call.
        """
        ctx, client = self._patched(_completion('{"ok": true}'))
        with ctx:
            llm.call_json("sys", "user", max_tokens=1024)

        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", kwargs)
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in kwargs["messages"]],
                         ["system", "user"])

    def test_unwrap_lifts_the_list_out(self):
        """JSON mode cannot return a bare array, so scoring wraps its answer."""
        ctx, _ = self._patched(_completion('{"scores": [{"job_id": "a"}]}'))
        with ctx:
            out = llm.call_json("s", "u", 100, unwrap="scores")

        self.assertEqual(out, [{"job_id": "a"}])

    def test_missing_wrapper_key_is_an_error_not_an_empty_result(self):
        ctx, _ = self._patched(_completion('{"results": []}'))
        with ctx, self.assertRaises(ValueError):
            llm.call_json("s", "u", 100, unwrap="scores")

    def test_truncated_answer_is_rejected(self):
        """A partial batch of scores silently drops jobs from the digest."""
        ctx, _ = self._patched(
            _completion('{"scores": [{"job_id": "a"}]}', finish_reason="length"))
        with ctx, self.assertRaises(ValueError):
            llm.call_json("s", "u", 100, unwrap="scores")

    def test_empty_response_is_rejected(self):
        ctx, _ = self._patched(_completion(""))
        with ctx, self.assertRaises(ValueError):
            llm.call_json("s", "u", 100)

    def test_importing_does_not_demand_a_key(self):
        """Scripts import this module for MODEL alone."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(llm.api_key_present())
            self.assertEqual(llm.MODEL, "gpt-5.6-luna")


class ScoringCallTests(unittest.TestCase):

    JOBS = [{"id": "j1", "title": "Finance Director", "company": "ACME",
             "location": "Barcelona", "description": "x" * 100}]

    def test_a_good_response_comes_back_as_a_list(self):
        payload = [{"job_id": "j1", "overall_score": "A"}]
        with patch.object(score_jobs.llm, "call_json", return_value=payload):
            self.assertEqual(score_jobs.score_batch(self.JOBS), payload)

    def test_api_failure_returns_none_so_the_batch_counter_sees_it(self):
        """score_all_jobs aborts only when every batch fails; it counts Nones."""
        with patch.object(score_jobs.llm, "call_json",
                          side_effect=RuntimeError("credit balance is too low")):
            self.assertIsNone(score_jobs.score_batch(self.JOBS))

    def test_a_wrapper_holding_the_wrong_type_is_refused(self):
        with patch.object(score_jobs.llm, "call_json",
                          return_value={"unexpected": "shape"}):
            self.assertIsNone(score_jobs.score_batch(self.JOBS))

    def test_the_prompt_asks_for_the_wrapper_it_unwraps(self):
        """Prompt and parser have to agree, and they are edited separately."""
        self.assertIn('{"scores"', score_jobs.SCORING_PROMPT)
        # JSON mode requires the word in the conversation.
        self.assertIn("JSON", score_jobs.SCORING_SYSTEM_PROMPT)

    def test_description_cap_survived_the_migration(self):
        """A 2000-char cut once hid Puig's '+4 years' and it scored B."""
        captured = {}

        def fake(system, user, max_tokens, unwrap=None):
            captured["user"] = user
            return []

        long_job = [dict(self.JOBS[0], description="y" * 5000)]
        with patch.object(score_jobs.llm, "call_json", side_effect=fake):
            score_jobs.score_batch(long_job)

        sent = json.loads(captured["user"].split("## Jobs to Evaluate")[1].strip())
        self.assertEqual(len(sent[0]["description"]), 3000)


class NoAnthropicLeftTests(unittest.TestCase):
    """The migration is only done when nothing reaches for the old provider."""

    SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")

    def test_no_script_imports_anthropic(self):
        for name in os.listdir(self.SCRIPTS):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(self.SCRIPTS, name), encoding="utf-8") as f:
                body = f.read()
            self.assertNotIn("import anthropic", body, f"{name} still imports it")
            self.assertNotIn("ANTHROPIC_API_KEY", body, f"{name} still reads it")

    def test_workflow_passes_the_openai_key(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            ".github", "workflows", "job_digest.yml")
        with open(path, encoding="utf-8") as f:
            body = f.read()

        self.assertIn("OPENAI_API_KEY", body)
        self.assertNotIn("ANTHROPIC_API_KEY", body)
        # Both LLM steps, not just the first one.
        self.assertEqual(body.count("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}"), 2)

    def test_requirements_ship_the_right_sdk(self):
        path = os.path.join(os.path.dirname(__file__), "..", "requirements.txt")
        with open(path, encoding="utf-8") as f:
            body = f.read()

        self.assertIn("openai", body)
        self.assertNotIn("anthropic", body)


if __name__ == "__main__":
    unittest.main()
