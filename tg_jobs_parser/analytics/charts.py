"""Построение графиков Matplotlib (PNG в output/charts/)."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from tg_jobs_parser.models.metrics import MetricsSummary


def save_matplotlib_charts(metrics: MetricsSummary, out_dir: str | Path) -> list[Path]:
    """Сохраняет PNG в ``out_dir``."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    if metrics.vacancies_by_specialization:
        fig, ax = plt.subplots(figsize=(10, 5))
        s = pd.Series(metrics.vacancies_by_specialization).sort_values(ascending=False).head(20)
        s.plot(kind="bar", ax=ax)
        ax.set_title("Вакансии по специализациям")
        fig.tight_layout()
        p = out / "spec_bar.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

    if metrics.vacancies_by_grade:
        fig, ax = plt.subplots(figsize=(8, 4))
        s = pd.Series(metrics.vacancies_by_grade)
        s.plot(kind="bar", ax=ax)
        ax.set_title("Вакансии по грейдам")
        fig.tight_layout()
        p = out / "grade_bar.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

    if metrics.daily:
        fig, ax = plt.subplots(figsize=(10, 4))
        df = pd.DataFrame([b.model_dump() for b in metrics.daily])
        df.plot(x="period", y="count", kind="line", ax=ax, legend=False)
        ax.set_title("Динамика по дням")
        fig.tight_layout()
        p = out / "daily_line.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

    if metrics.salary_by_grade:
        rows = [
            (g, (st.mean or 0))
            for g, st in metrics.salary_by_grade.items()
            if st.mean is not None and st.count_with_salary > 0
        ]
        if rows:
            fig, ax = plt.subplots(figsize=(8, 4))
            df = pd.DataFrame(rows, columns=["grade", "mean_salary"])
            df.sort_values("mean_salary", ascending=False).plot(
                x="grade", y="mean_salary", kind="bar", ax=ax, legend=False
            )
            ax.set_title("Средняя зарплата по грейдам (условные единицы)")
            fig.tight_layout()
            p = out / "salary_grade_bar.png"
            fig.savefig(p)
            plt.close(fig)
            saved.append(p)

    if metrics.vacancies_by_work_format:
        fig, ax = plt.subplots(figsize=(6, 6))
        s = pd.Series(metrics.vacancies_by_work_format)
        ax.pie(s.values, labels=list(s.index), autopct="%1.0f%%")
        ax.set_ylabel("")
        ax.set_title("Формат работы")
        fig.tight_layout()
        p = out / "work_format_pie.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

    if metrics.technology_top:
        fig, ax = plt.subplots(figsize=(10, 5))
        s = pd.Series(metrics.technology_top).sort_values(ascending=False).head(20)
        s.plot(kind="barh", ax=ax)
        ax.set_title("Топ технологий")
        fig.tight_layout()
        p = out / "tech_top.png"
        fig.savefig(p)
        plt.close(fig)
        saved.append(p)

    return saved
