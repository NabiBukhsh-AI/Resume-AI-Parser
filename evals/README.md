# Evaluation harness

Unit tests prove the pipeline's deterministic parts are correct. They say nothing about
whether the *model* is extracting well - and that is the part most likely to regress when
you change a prompt, switch models, or raise/lower reasoning effort.

This harness scores the whole pipeline against labelled documents so those changes can be
judged on numbers.

## Running

```bash
# Needs real credentials - every run calls a provider and costs money.
export ANTHROPIC_API_KEY=sk-ant-...

python evals/run_eval.py --dataset evals/datasets/sample.jsonl
```

Save a baseline before you change anything, then compare after:

```bash
python evals/run_eval.py --output evals/results/baseline.json
# ... edit a prompt, change a model, adjust effort ...
python evals/run_eval.py --baseline evals/results/baseline.json
```

The exit code is non-zero when a quality metric moved backwards, so this drops into a
scheduled job. Latency and cost are reported but never fail a run on their own - they move
with provider load.

## Dataset format

JSONL, one example per line. Document paths resolve relative to the dataset file.

```json
{
  "document": "documents/candidate.pdf",
  "expected": {
    "full_name": "Ada Lovelace",
    "email": "ada@example.com",
    "companies": ["Analytical Engines"],
    "titles": ["Senior Machine Learning Engineer"],
    "skills": ["Python", "PyTorch"],
    "total_years_of_experience": 7.8
  }
}
```

Every `expected` key is optional - omit one and that check is skipped, so you can label
only the fields you care about.

## Metrics

| Metric | What it tells you |
| --- | --- |
| `field_accuracy` | Exact match on identity fields. These are what an ATS import gets visibly wrong. |
| `company_recall` / `title_recall` | Did we find the work history? Recall is what matters for sourcing. |
| `skill_recall` / `skill_precision` | Both, so a change that lifts recall by hallucinating is visible. |
| `mean_years_error` | Should be ~0. This number is computed in Python, so drift means *date extraction* regressed. |
| `completeness` | The pipeline's own confidence signal, averaged. |
| `p50_latency_ms`, `total_cost_usd` | Operating cost of the change. |

## A note on data

Do not commit real candidate resumes to a public repository. Resumes are personal data.
Use synthetic documents (like the bundled one), or keep a private dataset directory
outside version control and point `--dataset` at it.
