"""Пул потоков, не блокирующий выход процесса (Ctrl+C / остановка Streamlit)."""

from __future__ import annotations

import threading
import weakref

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures.thread import _threads_queues, _worker


class DaemonThreadPoolExecutor(ThreadPoolExecutor):
    """``ThreadPoolExecutor`` с daemon-потоками — процесс не ждёт зависший HTTP."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_ref, q=self._work_queue) -> None:
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
            )
            t.daemon = True
            t.start()
            self._threads.add(t)
            _threads_queues[t] = self._work_queue
