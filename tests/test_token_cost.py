"""Оценка стоимости токенов по каталогу."""

import pytest

from tg_jobs_parser.ai.models_catalog import LlmModelEntry
from tg_jobs_parser.ai.token_cost import (
    cost_usd_for_tokens,
    estimate_run_cost,
    parse_cost_per_million,
)


def test_parse_cost_per_million():
    assert parse_cost_per_million("0.25/1.5") == (0.25, 1.5)
    assert parse_cost_per_million("") is None


def test_cost_usd_for_tokens():
    usd_in, usd_out, total = cost_usd_for_tokens(
        1_000_000,
        500_000,
        input_per_million=0.1,
        output_per_million=0.4,
    )
    assert usd_in == pytest.approx(0.1)
    assert usd_out == pytest.approx(0.2)
    assert total == pytest.approx(0.3)


def test_estimate_run_cost_per_model():
    catalog = [
        LlmModelEntry(id="m/a", name="A", cost="1.0/2.0"),
        LlmModelEntry(id="m/b", name="B", cost="0.5/0.5"),
    ]
    est = estimate_run_cost(
        tokens_by_model={
            "m/a": (1_000_000, 0),
            "m/b": (0, 2_000_000),
        },
        tokens_in=1_000_000,
        tokens_out=2_000_000,
        catalog=catalog,
        primary_model="m/a",
    )
    assert est is not None
    assert est.usd_input == 1.0
    assert est.usd_output == 1.0
    assert est.usd_total == 2.0
