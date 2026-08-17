# agentloom:managed — upgraded by `agentloom upgrade`; local edits become drift.
"""Bounded async task queue for LLM-bound work.

Prevents model-gateway bursting by serializing requests through a bounded
priority queue + semaphore. Agent asks (sdk.agent) and one-shot completions
(sdk.llm) share this queue, so all LLM traffic waits its turn — default
parallelism is 1.
"""
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Coroutine, Dict, Optional

log = logging.getLogger("sdk.task_queue")


class TaskPriority(Enum):
    CRITICAL = 0   # Immediate investigation
    HIGH = 1       # User-facing interactive work
    NORMAL = 2     # Scheduled jobs
    LOW = 3        # Periodic housekeeping


@dataclass(order=True)
class QueuedTask:
    """A task waiting in the queue. Ordered by priority then enqueue order."""
    sort_key: tuple = field(init=False, repr=False)
    task_id: str = field(compare=False)
    coroutine: Coroutine[Any, Any, Any] = field(compare=False, repr=False)
    priority: TaskPriority = TaskPriority.NORMAL
    enqueue_time: float = field(default_factory=time.monotonic, compare=False)
    _order: int = field(default=0, compare=False)

    def __post_init__(self):
        object.__setattr__(self, "sort_key", (self.priority.value, self._order))


_MAX_CONCURRENT = int(os.environ.get("AGENT_MAX_CONCURRENT", "1"))
_QUEUE_DEPTH = int(os.environ.get("AGENT_MAX_QUEUE", "20"))


class AgentTaskQueue:
    """Bounded async queue with priority scheduling."""

    def __init__(self, max_concurrent: int = _MAX_CONCURRENT, max_queue: int = _QUEUE_DEPTH):
        self.max_concurrent = max_concurrent
        self.max_queue = max_queue
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running = 0
        self._worker_task: Optional[asyncio.Task] = None
        self._counter = 0
        self._active_tasks: Dict[str, asyncio.Task] = {}
        self._stats = {"enqueued": 0, "processed": 0, "rejected": 0}

    async def start(self):
        if self._worker_task and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._worker(), name="task_queue_worker")
        log.info("Task queue started (max_concurrent=%d, max_queue=%d)",
                 self.max_concurrent, self.max_queue)

    async def stop(self):
        if self._worker_task:
            # Drain queued tasks before cancelling so work is not silently
            # dropped at shutdown.
            queued = self.queue.qsize()
            if queued > 0:
                log.warning("Task queue stopping with %d tasks still queued", queued)
                while not self.queue.empty():
                    try:
                        qt = self.queue.get_nowait()
                        await qt.coroutine
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        log.exception("Error draining queued task '%s'", qt.task_id)
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            if self._active_tasks:
                await asyncio.gather(*self._active_tasks.values(), return_exceptions=True)
            log.info("Task queue stopped (%d queued tasks drained)", queued)

    async def enqueue(self, coro: Coroutine, task_id: str,
                      priority: TaskPriority = TaskPriority.NORMAL) -> bool:
        if self.queue.qsize() >= self.max_queue:
            self._stats["rejected"] += 1
            log.warning("Task '%s' rejected — queue full (%d/%d)",
                        task_id, self.queue.qsize(), self.max_queue)
            return False
        self._counter += 1
        qt = QueuedTask(task_id=task_id, priority=priority, coroutine=coro, _order=self._counter)
        await self.queue.put(qt)
        self._stats["enqueued"] += 1
        return True

    async def run_with_queue(self, coro: Coroutine, task_id: str,
                             priority: TaskPriority = TaskPriority.NORMAL,
                             timeout: Optional[float] = None) -> Any:
        """Submit a task and wait for its result."""
        done = asyncio.Event()
        result_holder: Dict[str, Any] = {"result": None, "error": None}

        async def wrapped():
            try:
                if timeout:
                    result_holder["result"] = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    result_holder["result"] = await coro
            except Exception as e:  # noqa: BLE001 — surfaced via holder
                result_holder["error"] = e
            finally:
                done.set()

        ok = await self.enqueue(wrapped(), task_id, priority)
        if not ok:
            raise RuntimeError(f"Task '{task_id}' rejected — queue full")

        await done.wait()
        if result_holder["error"]:
            raise result_holder["error"]
        return result_holder["result"]

    async def _worker(self):
        while True:
            try:
                qt = await self.queue.get()
                await self._execute(qt)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Task queue worker error")

    async def _execute(self, qt: QueuedTask):
        async with self.semaphore:
            self._running += 1
            try:
                task = asyncio.create_task(qt.coroutine, name=qt.task_id)
                self._active_tasks[qt.task_id] = task
                await task
            finally:
                self._running -= 1
                self._active_tasks.pop(qt.task_id, None)
                self._stats["processed"] += 1

    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "active": self._running,
            "queued": self.queue.qsize(),
            "max_concurrent": self.max_concurrent,
            "max_queue": self.max_queue,
        }


queue = AgentTaskQueue()
