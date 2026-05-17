"""Оценка стоимости LLM по ценам из ``llm_models.yaml`` (USD / 1M токенов)."""

from __future__ import annotations

from dataclasses import dataclass

from tg_jobs_parser.ai.models_catalog import LlmModelEntry


def parse_cost_per_million(cost: str) -> tuple[float, float] | None:
    """Разобрать ``\"0.25/1.5\"`` → (input_usd_per_1m, output_usd_per_1m)."""
    raw = (cost or "").strip()
    if not raw:
        return None
    parts = raw.replace(",", ".").split("/")
    if len(parts) != 2:
        return None
    try:
        return float(parts[0].strip()), float(parts[1].strip())
    except ValueError:
        return None


def rates_for_model(catalog: list[LlmModelEntry], model_id: str) -> tuple[float, float] | None:
    mid = (model_id or "").strip()
    for entry in catalog:
        if entry.id == mid:
            return parse_cost_per_million(entry.cost)
    return None


def cost_usd_for_tokens(
    tokens_in: int,
    tokens_out: int,
    *,
    input_per_million: float,
    output_per_million: float,
) -> tuple[float, float, float]:
    usd_in = max(0, tokens_in) * input_per_million / 1_000_000
    usd_out = max(0, tokens_out) * output_per_million / 1_000_000
    return usd_in, usd_out, usd_in + usd_out


def format_usd(amount: float) -> str:
    if amount <= 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:.2f}"


@dataclass(frozen=True)
class TokenCostEstimate:
    usd_input: float
    usd_output: float
    usd_total: float
    unpriced_tokens_in: int = 0
    unpriced_tokens_out: int = 0

    def format_line(self) -> str:
        line = (
            f"Оценка стоимости LLM (USD): input={format_usd(self.usd_input)}, "
            f"output={format_usd(self.usd_output)}, всего={format_usd(self.usd_total)}"
        )
        if self.unpriced_tokens_in or self.unpriced_tokens_out:
            line += (
                f" (без тарифа в каталоге: in={self.unpriced_tokens_in}, "
                f"out={self.unpriced_tokens_out})"
            )
        return line

    def to_dict(self) -> dict[str, float | int]:
        return {
            "cost_usd_input": round(self.usd_input, 6),
            "cost_usd_output": round(self.usd_output, 6),
            "cost_usd_total": round(self.usd_total, 6),
            "unpriced_tokens_in": self.unpriced_tokens_in,
            "unpriced_tokens_out": self.unpriced_tokens_out,
        }


def estimate_run_cost(
    *,
    tokens_by_model: dict[str, tuple[int, int]],
    tokens_in: int,
    tokens_out: int,
    catalog: list[LlmModelEntry],
    primary_model: str,
) -> TokenCostEstimate | None:
    """``None`` если нет токенов API или пустой каталог."""
    if not catalog or (tokens_in == 0 and tokens_out == 0):
        return None

    usage = dict(tokens_by_model)
    if not usage and (tokens_in or tokens_out):
        pm = (primary_model or "").strip()
        if pm:
            usage = {pm: (tokens_in, tokens_out)}

    usd_in = 0.0
    usd_out = 0.0
    unpriced_in = 0
    unpriced_out = 0
    for model_id, (tin, tout) in usage.items():
        rates = rates_for_model(catalog, model_id)
        if rates is None:
            unpriced_in += tin
            unpriced_out += tout
            continue
        inp_rate, out_rate = rates
        ci, co, _ = cost_usd_for_tokens(tin, tout, input_per_million=inp_rate, output_per_million=out_rate)
        usd_in += ci
        usd_out += co

    return TokenCostEstimate(
        usd_input=usd_in,
        usd_output=usd_out,
        usd_total=usd_in + usd_out,
        unpriced_tokens_in=unpriced_in,
        unpriced_tokens_out=unpriced_out,
    )
