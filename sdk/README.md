# agentloom-sdk

The shared runtime SDK used by [agentloom](https://github.com/ejbp/agentloom)
agents. Mini-apps and platform services import it instead of hand-rolling
the machinery:

```python
from agentloom_sdk import db, events
from agentloom_sdk.llm import complete_json
from agentloom_sdk.services.scheduler import scheduler
```

| Module | Purpose |
|---|---|
| `config` | env parsing (`env`, `env_int`, `env_bool`, `require`) |
| `log` | logging setup (`get_logger`) |
| `db` | SQLite (WAL, busy_timeout=30000) + migration runner |
| `agent` | run agent turns via the `qwen serve` HTTP API (`ask`, `ask_json`) |
| `llm` | one-shot OpenAI-compatible completions (`complete`, `complete_json`) |
| `cron` | 5-field cron parsing + `next_cron_time` |
| `task_queue` | bounded priority queue serializing all LLM-bound traffic |
| `events` | in-process pub/sub bus (`publish`, `subscribe`) |
| `untrusted` | fence external content before any model sees it |
| `services.scheduler` | SQLite-backed scheduler for manifest jobs (`http`/`agent`) |

## Install

Released wheels are attached to agentloom's GitHub releases:

```
agentloom_sdk @ https://github.com/ejbp/agentloom/releases/download/v0.3.0/agentloom_sdk-0.3.0-py3-none-any.whl
```

## Versioning

The SDK is versioned in lockstep with the agentloom CLI and templates
(one base, one version). Generated agents pin the wheel URL in
`platform/requirements.txt`; bumping the pin + rebuilding is the upgrade.
