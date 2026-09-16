# decision-engine (คน 7)

The last safety boundary. Deterministic policy picks and **locks** one of `NORMAL | CHANGE_ROUTE | DELAY | AVOID`;
only then does an LLM write the explanation, which is post-validated and replaced by a fixed template on any violation.

```text
DecisionRequest{travel_request, evidence_package}
  -> consistency gate (request/revision/snapshot/route ids, feature schema, expired citations)
  -> derive_facts (gate, closure, usable alternative, materially safer, time-dependent, conflicts, coverage)
  -> first-match rule from policies/v1/decision-table.yaml (13 rules, unique priorities, numeric thresholds)
  -> confidence v1 (uncertainty, quality, coverage, rule specificity, alternative quality, conflict penalty)
  -> OpenAI Responses API (strict json_schema, temperature 0, store=false, bounded tokens/time)
     -> validator: locked action, citation allowlist, numbers ⊆ facts, banned phrases, no url/phone/html
     -> one feedback retry -> deterministic Thai/English fallback
  -> DecisionResult + audit trace (hashes, versions, rules, validator results; no PII / chain-of-thought)
```

API (`/internal/v1`, service token): `POST decisions`, `POST decisions/validate`, `GET policies/current`,
`POST decisions/preview-consistency`.

Config: `OPENAI_API_KEY` + `OPENAI_EXPLAINER_MODEL` enable the LLM; without them every decision uses the fallback
template and reports `decision-engine: explanation fallback (llm_disabled)` in `degraded_services`.

Tests (`uv run pytest -q`, 28): golden scenarios, threshold boundaries, consistency gate, Hypothesis monotonic
safety (200 cases), validator red-team cases, stubbed OpenAI client (valid / action change / hallucinated number /
refusal / incomplete / timeout / error), API + audit.
