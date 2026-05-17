"""Прогресс и partial-батчи."""

from pathlib import Path

from tg_jobs_parser.parse_progress import clear_partial_batches


def test_clear_partial_batches_removes_only_partial_json(tmp_path: Path):
    partial = tmp_path / "partial"
    partial.mkdir()
    (partial / "partial_run_00010.json").write_text("{}", encoding="utf-8")
    (partial / "partial_run_00020.json").write_text("{}", encoding="utf-8")
    (partial / "vacancies.json").write_text("{}", encoding="utf-8")

    assert clear_partial_batches(partial) == 2
    assert list(partial.glob("partial_*.json")) == []
    assert (partial / "vacancies.json").is_file()


def test_clear_partial_batches_missing_dir():
    assert clear_partial_batches(Path("/nonexistent/partial")) == 0
