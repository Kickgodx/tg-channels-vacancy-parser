import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { MarketSalary } from "../types/api";

const LEVEL_LABEL: Record<NonNullable<MarketSalary["level"]>, string> = {
  grade_role_tech: "тот же грейд, роль и хотя бы одна технология",
  grade_role: "тот же грейд и роль",
  grade_tech: "тот же грейд и хотя бы одна технология",
  role: "та же роль, любой грейд",
};

function k(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${Math.round(v / 1000).toLocaleString("ru-RU")}k`;
}

function Strip({ m }: { m: MarketSalary }) {
  const { p10, p25, median, p75, p90, vacancy_salary: own } = m;
  if (p10 == null || p25 == null || median == null || p75 == null || p90 == null) return null;
  const lo = Math.min(p10, own ?? p10);
  const hi = Math.max(p90, own ?? p90);
  const span = hi - lo || 1;
  const x = (v: number) => `${((v - lo) / span) * 100}%`;
  return (
    <div className="market-strip" aria-hidden="true">
      <div
        className="market-whisker"
        style={{ left: x(p10), width: `calc(${x(p90)} - ${x(p10)})` }}
      />
      <div className="market-box" style={{ left: x(p25), width: `calc(${x(p75)} - ${x(p25)})` }} />
      <div className="market-median" style={{ left: x(median) }} />
      {own != null && <div className="market-own" style={{ left: x(own) }} title="Эта вакансия" />}
    </div>
  );
}

/** Рыночная вилка в карточке вакансии: квантили зарплаты похожих вакансий. */
export default function MarketSalaryBlock({ vacancyId }: { vacancyId: string }) {
  const q = useQuery({
    queryKey: ["market-salary", vacancyId],
    queryFn: () => api.getMarketSalary(vacancyId),
    staleTime: 60_000,
  });
  const m = q.data;
  if (!m) return null;
  if (m.level == null) {
    return (
      <aside className="market-salary">
        <strong>Рынок</strong>
        <p className="caption">Похожих вакансий с зарплатой за год мало — вилку не оценить.</p>
      </aside>
    );
  }
  const criteria = [
    m.grade,
    m.specializations.join(", "),
    m.technologies.length > 0 ? m.technologies.slice(0, 4).join(" / ") : null,
  ].filter(Boolean);
  return (
    <aside className="market-salary">
      <div className="vacancy-match-header">
        <strong>Рынок для похожих</strong>
        <span className="caption">
          {criteria.join(" · ")} — {m.count} вакансий за год
        </span>
      </div>
      <p>
        Обычно{" "}
        <strong>
          {k(m.p25)} – {k(m.p75)}
        </strong>
        , медиана <strong>{k(m.median)}</strong>
        <span className="muted">
          {" "}
          (p10–p90: {k(m.p10)} – {k(m.p90)}), ₽ в месяц
        </span>
      </p>
      <Strip m={m} />
      {m.vacancy_salary != null && m.percentile != null && (
        <p>
          Эта вакансия: <strong>{k(m.vacancy_salary)}</strong> — не ниже, чем у{" "}
          {Math.round(m.percentile * 100)}% похожих.
        </p>
      )}
      <p className="caption">Похожие: {LEVEL_LABEL[m.level]}.</p>
    </aside>
  );
}
