# agentboom-sdk

The shared runtime SDK used by [agentboom](https://github.com/NeuralChowder/agentboom)
agents. Mini-apps and platform services import it instead of hand-rolling
the machinery:

```python
from agentboom_sdk import db, events
from agentboom_sdk.llm import complete_json
from agentboom_sdk.services.scheduler import scheduler
```

| Module | Purpose |
|---|---|
| `config` | env parsing (`env`, `env_int`, `env_bool`, `require`) |
| `log` | logging setup (`get_logger`) |
| `db` | data layer — SQLite by default, PostgreSQL when `DATABASE_URI` is set; unified API (`execute`, `fetchone`/`fetchrow`, `fetchall`, `fetchval`, `acquire`, `transaction`), `$n`/`?` placeholder interop, migration runner |
| `agent` | run agent turns via the `qwen serve` HTTP API (`ask`, `ask_json`) |
| `llm` | one-shot completions (`complete`, `complete_json`); serializes through the task queue when it runs |
| `cron` | 5-field cron parsing + `next_cron_time` (tz-aware, default UTC, dow 7 = Sunday) |
| `task_queue` | bounded priority queue serializing LLM-bound traffic |
| `events` | in-process pub/sub bus (`publish`, `subscribe`) |
| `untrusted` | fence external content + injection scoring (`wrap`, `scan`, `risk_score`) |
| `accepted` | one canonical "started, not finished" response envelope |
| `idle` | let scheduled jobs prove there is nothing to do |
| `services.scheduler` | SQLite-backed scheduler for manifest jobs (`http`/`agent`) |


## Install

Released wheels are attached to agentboom's GitHub releases — match the
version to the agentboom release you use (one base, one version):

```
agentboom_sdk @ https://github.com/NeuralChowder/agentboom/releases/download/v0.7.0/agentboom_sdk-0.7.0-py3-none-any.whl
```

## Versioning

The SDK is versioned in lockstep with the agentboom CLI and templates
(one base, one version). Generated agents pin the wheel URL in
`platform/requirements.txt`; bumping the pin + rebuilding is the upgrade.
