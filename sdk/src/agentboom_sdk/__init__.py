"""agentboom-sdk — shared runtime SDK for agentboom-based agents.

Modules
-------
agentboom_sdk.config      env parsing helpers
agentboom_sdk.log         logging setup (one format, one level knob: LOG_LEVEL)
agentboom_sdk.db          data layer: SQLite by default, PostgreSQL when
                          DATABASE_URI is set (unified API, placeholder interop)
agentboom_sdk.agent       Qwen Code `qwen serve` HTTP client (simple turns)
agentboom_sdk.llm         one-shot OpenAI-compatible completions
agentboom_sdk.cron        5-field cron parser, tz-aware (default UTC)
agentboom_sdk.task_queue  bounded priority queue serializing LLM-bound traffic
agentboom_sdk.events      in-process pub/sub event bus
agentboom_sdk.untrusted   fencing + injection scoring for external content
agentboom_sdk.accepted    one canonical 'started, not finished' envelope
agentboom_sdk.idle        let scheduled jobs prove there is nothing to do
agentboom_sdk.services.scheduler
                          SQLite-backed scheduler for manifest-declared jobs

Doctrine: deterministic first. LLM calls are expensive and serialized —
use them for judgement, never for work a script or SQL query can do.
"""

__version__ = "0.6.1"
