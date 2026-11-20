"""Ownership for finite filesystem workers shared by imports and deletions."""

from collections.abc import Callable
from threading import Lock, Thread, current_thread


class WorkerGroup:
    def __init__(self, name: str):
        self.name = name
        self._lock = Lock()
        self._threads: dict[str, Thread] = {}
        self._closed = False

    def start(self, key: str, operation: Callable, *args) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Worker group is shutting down")
            previous = self._threads.get(key)
            if previous and previous.is_alive():
                return
            worker = Thread(target=operation, args=args, name=f"{self.name}-{key[:8]}", daemon=True)
            self._threads[key] = worker
            worker.start()

    def wait(self, key: str) -> None:
        with self._lock:
            worker = self._threads.get(key)
        if worker and worker is not current_thread():
            worker.join()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            workers = list(self._threads.values())
        for worker in workers:
            if worker is not current_thread():
                worker.join()
