# What agents spend their tool calls on

A descriptive analysis of completed batches t2 (synthetic tasks) and t3 (natural tasks), from the preserved tool events ([agent-behavior-t2-t3.json](../results/agent-behavior-t2-t3.json), produced by `benchmark/agent_behavior.py t2 t3`). Batch t4 is excluded until it completes, so that none of its outcomes are looked at early. These numbers are not a test of any arm.

| Batch / arm | Runs | Turns | Tool calls | Calls before first edit | Repeated read lines | Repeated read tokens per run | Total tokens per run |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| t2 graf_floor | 100 | 8.0 | 7.0 | 3.5 | 1.9% | 23 | 41,713 |
| t2 Graphify | 100 | 8.2 | 7.2 | 3.5 | 2.1% | 27 | 58,464 |
| t2 search_only | 100 | 7.4 | 6.4 | 3.0 | 1.8% | 22 | 38,280 |
| t3 graf_floor | 90 | 11.2 | 10.2 | 5.6 | 1.4% | 36 | 93,770 |
| t3 Graphify | 90 | 10.4 | 9.4 | 5.1 | 1.4% | 32 | 87,983 |
| t3 search_only | 90 | 10.7 | 9.7 | 5.0 | 1.5% | 41 | 85,133 |

- **Re-reading is negligible in this harness.** Under 2.1% of read lines had already been delivered earlier in the same run, worth about 22–41 tokens of a run's 38,000–94,000. Agents do return to a file about once per run, but for a different region. A "you already have this" feature would not pay off here. This harness's `read` returns at most 200 lines; agents whose read tool returns whole files may behave differently.
- **Orientation is about half of every run.** On natural tasks, agents made about 5 of their roughly 10 tool calls before the first edit, in every arm. Since about 85% of tokens are context re-sent each turn, removing orientation turns is the main lever, not smaller payloads. Batch t5's up-front `graf triage` targets exactly this.
