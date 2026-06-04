# Eval harness

A golden-set regression gate for the generation pipeline — it runs like a test, not a notebook.

```bash
python -m app.eval.harness            # offline smoke (free, hermetic) — for CI
python -m app.eval.harness --live      # real Gemini quality run (costs money)
python evals/run_eval.py --live        # same thing, from the repo root
```

- **offline (default):** injects a deterministic, template-aware stand-in LLM. Exercises the real
  machinery (draft → map+repair → constraint report → faithfulness → confidence) and asserts the
  pipeline still yields in-limit, cited output. Exits non-zero on regression.
- **`--live`:** uses the real Gemini path to measure *quality* (faithfulness, confidence, cost) on the
  golden prompts. Run before changing a model id or a prompt.

It aggregates **pass-rate · mean-faithfulness · mean-confidence · total-cost** and fails below threshold
(`min_faithfulness`, `min_pass_rate` in `golden_cases.json`, override with `--min-faithfulness` /
`--min-pass-rate`).

**Add a case:** append to `golden_cases.json` — `id`, `template_id`, `prompt`, and a `sender`/`receiver`
`CompanyBrief` (the same shape the parser produces). Facts need stable ids so citations can resolve.
