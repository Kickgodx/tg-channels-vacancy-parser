"""setup_logging: запись через очередь (фоновый поток) и ротация app.log в .gz."""

from __future__ import annotations

import gzip
import logging
import threading
from logging.handlers import QueueHandler

import pytest

from jobdesk.utils import logger as logger_mod


@pytest.fixture
def isolated_logging(monkeypatch, tmp_path):
    monkeypatch.delenv("LOG_DIR", raising=False)
    yield tmp_path
    logger_mod.setup_logging(log_dir=None)


def test_records_are_written_by_listener_thread(isolated_logging):
    logger_mod.setup_logging(log_dir=isolated_logging)
    root = logging.getLogger()
    assert [type(h) for h in root.handlers] == [QueueHandler]

    emit_threads: list[str] = []
    file_handler = next(h for h in logger_mod._listener.handlers if isinstance(h, logging.FileHandler))
    original_emit = file_handler.emit

    def spy(record):
        emit_threads.append(threading.current_thread().name)
        original_emit(record)

    file_handler.emit = spy
    logging.getLogger("backend.test").info("hello from queue")
    logger_mod.stop_logging()

    text = (isolated_logging / "app.log").read_text(encoding="utf-8")
    assert "| INFO | backend.test | hello from queue" in text
    assert emit_threads and threading.current_thread().name not in emit_threads


def test_request_id_prefix_survives_queue(isolated_logging):
    logger_mod.setup_logging(log_dir=isolated_logging)
    token = logger_mod.request_id_var.set("rid-123")
    try:
        logging.getLogger("backend.test").info("inside")
    finally:
        logger_mod.request_id_var.reset(token)
    logging.getLogger("backend.test").info("outside")
    logger_mod.stop_logging()

    lines = (isolated_logging / "app.log").read_text(encoding="utf-8").splitlines()
    assert lines[0].endswith("| backend.test | request_id=rid-123 | inside")
    assert lines[1].endswith("| backend.test | outside")


def test_file_rotates_into_gzip_parts(isolated_logging):
    logger_mod.setup_logging(log_dir=isolated_logging, max_bytes=300, backup_count=2)
    log = logging.getLogger("backend.test")
    for i in range(30):
        log.info("line %03d %s", i, "x" * 40)
    logger_mod.stop_logging()

    names = sorted(p.name for p in isolated_logging.iterdir())
    assert names == ["app.log", "app.log.1.gz", "app.log.2.gz"]
    with gzip.open(isolated_logging / "app.log.1.gz", "rt", encoding="utf-8") as f:
        assert "backend.test | line" in f.read()
