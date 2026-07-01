"""Отмена прогона парсинга."""

import asyncio
import threading
import time

import pytest

from tg_jobs_parser.parse_cancel import ParseCancellationToken, ParseCancelled


def test_token_not_cancelled_by_default():
    tok = ParseCancellationToken()
    assert not tok.is_cancelled()
    tok.check()


def test_request_cancel_sets_flag():
    tok = ParseCancellationToken()
    tok.request_cancel()
    assert tok.is_cancelled()
    with pytest.raises(ParseCancelled):
        tok.check()


def test_check_from_worker_thread():
    tok = ParseCancellationToken()
    seen: list[bool] = []

    def worker() -> None:
        time.sleep(0.05)
        tok.request_cancel()
        time.sleep(0.05)
        try:
            tok.check()
            seen.append(False)
        except ParseCancelled:
            seen.append(True)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)
    assert seen == [True]


def test_request_cancel_cancels_bound_async_tasks():
    async def _main() -> None:
        tok = ParseCancellationToken()
        loop = asyncio.get_running_loop()

        async def slow() -> None:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise

        tasks = [asyncio.create_task(slow()) for _ in range(2)]
        tok.bind_async_tasks(loop, tasks)

        def _request() -> None:
            time.sleep(0.05)
            tok.request_cancel()

        threading.Thread(target=_request, daemon=True).start()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert all(isinstance(r, asyncio.CancelledError) for r in results)
        tok.clear_async_tasks()

    asyncio.run(_main())
