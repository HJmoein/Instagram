import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadJob:
    user_id: int
    url: str
    callback: Callable[["DownloadJob"], Awaitable[None]]


class DownloadQueue:
    def __init__(self, workers: int) -> None:
        self._queue: asyncio.Queue[DownloadJob] = asyncio.Queue()
        self._workers = workers
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        self._tasks = [asyncio.create_task(self._worker(i), name=f"download-worker-{i}") for i in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def put(self, job: DownloadJob) -> None:
        await self._queue.put(job)

    def size(self) -> int:
        return self._queue.qsize()

    async def _worker(self, worker_id: int) -> None:
        while True:
            job = await self._queue.get()
            try:
                await job.callback(job)
            except Exception:
                logger.exception("Unhandled queue job failure in worker %s", worker_id)
            finally:
                self._queue.task_done()
