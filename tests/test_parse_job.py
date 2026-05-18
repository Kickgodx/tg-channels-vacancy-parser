"""Фоновое состояние парсинга для Streamlit."""

import threading

from tg_jobs_parser.parse_cancel import ParseCancellationToken
from tg_jobs_parser.web.parse_job import ParseJobState


def test_finish_success_clears_running():
    job = ParseJobState(cancel_token=ParseCancellationToken())
    job.finish_success(
        result=object(),  # type: ignore[arg-type]
        xlsx_path=__import__("pathlib").Path("a.xlsx"),
        json_path=__import__("pathlib").Path("a.json"),
    )
    assert not job.is_running()
    running, *_ = job.snapshot()
    assert not running


def test_mark_finished_if_thread_dead():
    job = ParseJobState(cancel_token=ParseCancellationToken())

    def noop() -> None:
        pass

    t = threading.Thread(target=noop)
    t.start()
    t.join()
    job.thread = t
    job.mark_finished_if_thread_dead()
    assert not job.is_running()
