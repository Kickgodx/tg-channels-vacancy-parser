"""Отмена прогона парсинга."""

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
