"""agentloom-sdk — shared runtime SDK for agentloom-based agents.

Modules
-------
agentloom_sdk.config      env parsing helpers
agentloom_sdk.log         logging setup (one format, one level knob: LOG_LEVEL)
agentloom_sdk.db          data layer: SQLite by default, PostgreSQL when
                          DATABASE_URI is set (unified API, placeholder interop)
agentloom_sdk.agent       Qwen Code `qwen serve` HTTP client (simple turns)
agentloom_sdk.llm         one-shot OpenAI-compatible completions
agentloom_sdk.cron        5-field cron parser, tz-aware (default UTC)
agentloom_sdk.task_queue  bounded priority queue serializing LLM-bound traffic
agentloom_sdk.events      in-process pub/sub event bus
agentloom_sdk.untrusted   fencing + injection scoring for external content
agentloom_sdk.accepted    one canonical 'started, not finished' envelope
agentloom_sdk.idle        let scheduled jobs prove there is nothing to do
agentloom_sdk.services.scheduler
                          SQLite-backed scheduler for manifest-declared jobs

Doctrine: deterministic first. LLM calls are expensive and serialized —
use them for judgement, never for work a script or SQL query can do.
"""

__version__ = "0.4.1"
