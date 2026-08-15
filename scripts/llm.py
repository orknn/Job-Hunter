"""
llm.py — the single place this repo talks to a model.

Migrated from Anthropic to OpenAI on 15 Aug 2026. The bulletin repo moved on
8 Aug when the Anthropic balance ran out; this one was left behind, so the
weekly digest failed on 8 and 15 Aug with "credit balance is too low" and no
job list went out either week. Two call sites shared one provider and one
retired-model risk, so they now share one module instead of duplicating the
client, the model name and the markdown-fence stripping three times over.

The call shape follows job-hunter-core's reasoner, which has been running
against this provider already:

  * `max_completion_tokens`, not `max_tokens` — the older name is rejected.
  * No `temperature`. This model rejects `temperature=0`, and leaving it unset
    is not the same as setting it to the default.
  * `seed` for repeatability. Best-effort on the provider's side, so it narrows
    the gap between two runs over the same postings rather than closing it.
  * JSON mode, which guarantees the response parses. It does not guarantee the
    shape, so callers still validate what they get.
"""

import json
import os

from openai import OpenAI

# Same tier the bulletin's daily edition runs on: cheap, and this is scoring
# against a rubric rather than open-ended writing. Named once, here, so a model
# retirement is a one-line change instead of a hunt — the last one took the
# digest down until someone noticed (commit ccbcc2a).
MODEL = "gpt-5.6-luna"

SEED = 7

# Deliberately not a module-level client. Constructing one demands the key the
# moment it exists, and importing this module should not require credentials —
# scripts that never score anything import it for MODEL alone.
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI()  # reads OPENAI_API_KEY
    return _client


def api_key_present():
    """Whether the environment can make a call at all."""
    return bool(os.environ.get("OPENAI_API_KEY"))


def call_json(system, user, max_tokens, unwrap=None):
    """One JSON call. Returns parsed output, or raises.

    `unwrap` names a key to lift out of the response. JSON mode can only return
    an object, but the scoring prompt's natural answer is a list, so it asks for
    {"scores": [...]} and unwraps here rather than leaving every caller to
    remember the wrapper.

    Raises on anything that is not a usable answer — a truncated response, a
    missing wrapper key, unparseable text. The callers decide what a failure
    costs; this does not paper over one by returning something empty that reads
    like a real result.
    """
    completion = _get_client().chat.completions.create(
        model=MODEL,
        max_completion_tokens=max_tokens,
        seed=SEED,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    choice = completion.choices[0]
    # A response cut off at the token ceiling is still valid JSON under JSON
    # mode on some providers and truncated garbage on others. Either way it is
    # a partial answer, and a partial batch of scores silently drops jobs.
    if getattr(choice, "finish_reason", None) == "length":
        raise ValueError(
            f"response hit the {max_tokens} token ceiling — answer is partial")

    text = (choice.message.content or "").strip()
    if not text:
        raise ValueError("model returned an empty response")

    data = json.loads(text)

    if unwrap is None:
        return data
    if not isinstance(data, dict) or unwrap not in data:
        raise ValueError(
            f"expected a {unwrap!r} key in the response, got: {list(data)[:5]}")
    return data[unwrap]
