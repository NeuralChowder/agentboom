# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""Platform SDK — the only import root for mini-apps and services.

Modules
-------
sdk.config      env parsing helpers
sdk.log         logging setup (one format, one level knob: LOG_LEVEL)
sdk.db          SQLite (WAL) data layer + SQL migration runner
sdk.agent       Qwen Code `qwen serve` HTTP client (multi-step agent turns)
sdk.llm         one-shot OpenAI-compatible completions (classify/extract/draft)
sdk.cron        5-field cron parser and next-fire computation
sdk.task_queue  bounded priority queue serializing all LLM-bound traffic
sdk.events      in-process pub/sub event bus
sdk.untrusted   fencing for external content before it reaches a model

Doctrine: deterministic first. LLM calls are expensive and serialized —
use them for judgement, never for work a script or SQL query can do.
"""

__version__ = "{{BASE_VERSION}}"
