# The 22 Major Arcana

Each agent is configured by a Major Arcana card. The card generates the system
prompt, sets the default temperature, and seeds the memory and decay profile.

| # | Card | Archetype | Temp |
|---|------|-----------|------|
| 0 | The Fool | Explorer / Autonomous Agent | 0.95 |
| I | The Magician | Executor / Tool Master | 0.50 |
| II | The High Priestess | Archivist / Pattern Reader | 0.40 |
| III | The Empress | Creator / Generative Agent | 0.85 |
| IV | The Emperor | Orchestrator / System Agent | 0.30 |
| V | The Hierophant | Advisor / Domain Expert | 0.30 |
| VI | The Lovers | Collaborator / Communication | 0.70 |
| VII | The Chariot | Driver / Goal Agent | 0.40 |
| VIII | Strength | Coach / Long-Game Agent | 0.60 |
| IX | The Hermit | Researcher / Deep Analyst | 0.35 |
| X | Wheel of Fortune | Scheduler / Probabilistic | 0.65 |
| XI | Justice | Auditor / Evaluation Agent | 0.20 |
| XII | The Hanged Man | Reframer / Perspective | 0.80 |
| XIII | Death | Transformer / Refactor Agent | 0.40 |
| XIV | Temperance | Integrator / Synthesis | 0.55 |
| XV | The Devil | Shadow / Constraint Breaker | 0.75 |
| XVI | The Tower | Disruptor / Breakthrough | 0.85 |
| XVII | The Star | Companion / Wellbeing Agent | 0.70 |
| XVIII | The Moon | Interpreter / Ambiguity | 0.80 |
| XIX | The Sun | Amplifier / Output Agent | 0.75 |
| XX | Judgement | Reviewer / Reflection | 0.45 |
| XXI | The World | Meta-Agent *(reserved)* | 0.50 |

The card enum and registry are documented under [Cards](api/cards.md) in the API
reference.
