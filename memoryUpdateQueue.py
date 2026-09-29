from dataclasses import dataclass
from typing import Callable, Optional

from .aiModelWorker import AiModelWorker, createModelWorker
from .memory import (
    MAX_MEMORY_POINTS,
    MEMORY_GENERATION_CONFIG,
    getDeckMemory,
    isInDeckTree,
    parseMemoryResponse,
    saveDeckMemory,
)


@dataclass(frozen = True)
class MemoryUpdateTask:
    # Rewrites one deck's memory. buildPrompt is called as buildPrompt(currentMemory = points)
    # when the task starts, so it always sees the result of the task before it.
    deckName: str
    buildPrompt: Callable[..., str]
    modelId: str
    geminiApiKey: str
    claudeApiKey: str


class MemoryUpdateQueue:
    # Runs learning-memory updates one at a time, so two updates of the same deck never read
    # the same memory and overwrite each other's result. Everything here runs on the main
    # thread (worker signals are delivered there). Failures are silent: the memory is left as
    # it was and the next task starts.

    def __init__(self) -> None:
        self._pendingTasks: list[MemoryUpdateTask] = []
        self._runningTask: Optional[MemoryUpdateTask] = None
        self._runningWorker: Optional[AiModelWorker] = None
        self._idleWaiters: list[tuple[str, Callable[[], None]]] = []

    def enqueue(self, task: MemoryUpdateTask) -> None:
        self._pendingTasks.append(task)
        self._startNextTask()

    def isDeckTreeBusy(self, deckName: str) -> bool:
        tasks = self._pendingTasks + ([self._runningTask] if self._runningTask else [])
        return any(isInDeckTree(task.deckName, deckName) for task in tasks)

    def callWhenDeckTreeIdle(self, deckName: str, callback: Callable[[], None]) -> None:
        # Calls back once no queued or running task updates the deck or its subdecks.
        if self.isDeckTreeBusy(deckName):
            self._idleWaiters.append((deckName, callback))
        else:
            callback()

    def _startNextTask(self) -> None:
        while self._runningWorker is None and self._pendingTasks:
            task = self._pendingTasks.pop(0)
            worker = self._createWorker(task)
            if worker is None:
                continue
            self._runningTask = task
            self._runningWorker = worker
            worker.success.connect(
                lambda text, deckName = task.deckName: self._onWorkerSuccess(text, deckName)
            )
            worker.finished.connect(lambda w = worker: self._onWorkerFinished(w))
            worker.start()
        self._notifyIdleWaiters()

    @staticmethod
    def _createWorker(task: MemoryUpdateTask) -> Optional[AiModelWorker]:
        # None (unsupported model, missing API key, unbuildable prompt) skips the task.
        try:
            prompt = task.buildPrompt(currentMemory = getDeckMemory(task.deckName))
        except Exception:
            return None
        return createModelWorker(
            modelId = task.modelId,
            geminiApiKey = task.geminiApiKey,
            claudeApiKey = task.claudeApiKey,
            prompt = prompt,
            generationConfig = MEMORY_GENERATION_CONFIG,
        )

    @staticmethod
    def _onWorkerSuccess(text: str, deckName: str) -> None:
        points = parseMemoryResponse(text, MAX_MEMORY_POINTS)
        if points is not None:
            saveDeckMemory(deckName, points)

    def _onWorkerFinished(self, worker: AiModelWorker) -> None:
        # Emitted after success/error (whose handlers have already run), whatever the outcome.
        worker.deleteLater()
        if worker is self._runningWorker:
            self._runningWorker = None
            self._runningTask = None
        self._startNextTask()

    def _notifyIdleWaiters(self) -> None:
        readyWaiters = [waiter for waiter in self._idleWaiters if not self.isDeckTreeBusy(waiter[0])]
        self._idleWaiters = [waiter for waiter in self._idleWaiters if waiter not in readyWaiters]
        for _, callback in readyWaiters:
            callback()
