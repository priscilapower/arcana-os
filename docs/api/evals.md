# Evals

`arcana.evals` ships with `arcana-core` so you can evaluate your own agents
without any extra install.

```python
from arcana.evals import EvalHarness

harness = EvalHarness(use_llm=False)   # rules-only, no cost
summary = await harness.run(suite="cards")
print(f"Pass rate: {summary.pass_rate:.1%}")
```

## Harness

::: arcana.evals.harness.EvalHarness

## Judges

::: arcana.evals.judge.CompositeJudge

::: arcana.evals.judge.RuleJudge

::: arcana.evals.judge.LLMJudge

## Types

::: arcana.evals.types.EvalCase

::: arcana.evals.types.EvalResult

::: arcana.evals.types.EvalRubric

::: arcana.evals.types.EvalDimension
