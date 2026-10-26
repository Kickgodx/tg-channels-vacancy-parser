import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type VacancyQueryParams } from "../api/client";
import type { TechnologyMomentum, TechnologyPremium } from "../types/api";

interface Props {
  params: VacancyQueryParams;
}

// Категориальный порядок, проверенный на различимость при дальтонизме (dataviz, палитра по умолчанию).
const TREND_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#6250d6",
  "#e34948",
];
// Расходящаяся пара: плюс — синий, минус — красный, ноль — нейтральная линия.
const POSITIVE = "#2a78d6";
const NEGATIVE = "#e34948";
const AXIS_INK = "#868e96";
// Порядок грейдов по старшинству — как GRADE_ORDER в AnalyticsPanel.
const GRADE_ORDER = [
  "intern",
  "junior",
  "junior+",
  "middle",
  "senior",
  "lead",
  "staff",
  "principal",
];

function gradeRank(g: string): number {
  const i = GRADE_ORDER.indexOf(g);
  return i >= 0 ? i : GRADE_ORDER.length;
}
const LEGEND_INK = "#1a1a2e";

function fmtRub(v?: number | null): string {
  if (v == null) return "—";
  return Math.round(v).toLocaleString("ru-RU");
}

function fmtSigned(v: number, digits = 0): string {
  const s = v.toFixed(digits);
  return v > 0 ? `+${s}` : s;
}

interface PremiumRow extends TechnologyPremium {
  pct: number;
}

interface PremiumTooltipProps {
  active?: boolean;
  payload?: { payload?: PremiumRow }[];
}

function PremiumTooltip({ active, payload }: PremiumTooltipProps) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  return (
    <div className="bar-tooltip">
      <div className="bar-tooltip-title">{row.technology}</div>
      <div>
        Премия: <strong>{fmtSigned(row.pct)}%</strong>
      </div>
      <div>
        Медиана с технологией: {fmtRub(row.median)} (n={row.vacancies})
      </div>
      <table className="tooltip-table">
        <thead>
          <tr>
            <th>Грейд</th>
            <th>с</th>
            <th>без</th>
          </tr>
        </thead>
        <tbody>
          {[...row.by_grade]
            .sort((a, b) => gradeRank(a.grade) - gradeRank(b.grade))
            .map((g) => (
              <tr key={g.grade} className={g.compared ? undefined : "muted"}>
                <td>{g.grade}</td>
                <td>
                  {fmtRub(g.with_median)} <span className="muted">({g.with_count})</span>
                </td>
                <td>
                  {fmtRub(g.without_median)} <span className="muted">({g.without_count})</span>
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      <div className="muted">
        В сравнении: {row.compared_vacancies} вакансий с технологией; серым — грейды, где с ней или
        без неё меньше 5 вакансий
      </div>
    </div>
  );
}

function PremiumChart({ items, suffix }: { items: TechnologyPremium[]; suffix: string }) {
  const rows: PremiumRow[] = useMemo(
    () =>
      items
        .filter((p) => p.premium != null)
        .map((p) => ({ ...p, pct: Math.round(p.premium! * 1000) / 10 }))
        .sort((a, b) => b.pct - a.pct),
    [items],
  );
  const skipped = items.length - rows.length;
  if (rows.length === 0) return null;
  return (
    <div className="card chart-card">
      <h4>Зарплатная премия технологии{suffix}</h4>
      <p className="muted" style={{ margin: "0 0 0.5rem" }}>
        Медиана с технологией против медианы без неё внутри одного грейда, средневзвешенно по
        грейдам. Фильтр по технологии здесь не применяется.
        {skipped > 0 && ` Мало данных для сравнения: ${skipped}.`}
      </p>
      <ResponsiveContainer width="100%" height={40 + rows.length * 24}>
        <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 24 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis
            type="number"
            tickFormatter={(v: number) => `${fmtSigned(v)}%`}
            tick={{ fontSize: 11 }}
          />
          <YAxis type="category" dataKey="technology" width={110} tick={{ fontSize: 11 }} />
          <ReferenceLine x={0} stroke={AXIS_INK} />
          <Tooltip cursor={{ fill: "#f1f3f5" }} content={<PremiumTooltip />} />
          <Bar dataKey="pct" name="Премия, %" radius={[0, 4, 4, 0]} maxBarSize={16}>
            {rows.map((r) => (
              <Cell key={r.technology} fill={r.pct >= 0 ? POSITIVE : NEGATIVE} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function TrendChart({
  months,
  totals,
  series,
}: {
  months: string[];
  totals: number[];
  series: { technology: string; counts: number[] }[];
}) {
  const data = useMemo(
    () =>
      months.map((m, i) => {
        const row: Record<string, string | number | null> = { period: m };
        for (const s of series) {
          row[s.technology] = totals[i] ? Math.round((s.counts[i] / totals[i]) * 1000) / 10 : null;
        }
        return row;
      }),
    [months, totals, series],
  );
  if (months.length === 0 || series.length === 0) return null;
  return (
    <div className="card chart-card">
      <h4>Спрос на технологии: доля вакансий по месяцам</h4>
      <p className="muted" style={{ margin: "0 0 0.5rem" }}>
        Доля зависит и от набора каналов: новый канал с фронтендом поднимет React.
      </p>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="period" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v: number) => `${v}%`} tick={{ fontSize: 11 }} />
          <Tooltip formatter={(v: number) => `${v}%`} />
          <Legend
            formatter={(value: string) => <span style={{ color: LEGEND_INK }}>{value}</span>}
          />
          {series.map((s, i) => (
            <Line
              key={s.technology}
              type="linear"
              dataKey={s.technology}
              stroke={TREND_COLORS[i % TREND_COLORS.length]}
              strokeWidth={2}
              dot={{ r: 4, strokeWidth: 2, fill: "#fff" }}
              activeDot={{ r: 5 }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function MomentumTable({ title, rows }: { title: string; rows: TechnologyMomentum[] }) {
  if (rows.length === 0) return null;
  return (
    <>
      <h5 className="momentum-title">{title}</h5>
      <table>
        <thead>
          <tr>
            <th>Технология</th>
            <th>Доля сейчас</th>
            <th>Была</th>
            <th>Изменение</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.technology}>
              <td>{m.technology}</td>
              <td>
                {(m.recent_share * 100).toFixed(1)}% <span className="muted">({m.recent})</span>
              </td>
              <td>
                {(m.previous_share * 100).toFixed(1)}% <span className="muted">({m.previous})</span>
              </td>
              <td>
                <strong>
                  {m.delta_pp > 0 ? "▲" : "▼"} {fmtSigned(m.delta_pp, 1)} п.п.
                </strong>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

const MOMENTUM_SHOWN = 6;

/** Блок «Технологии» во вкладке аналитики: премия, спрос по месяцам, рост и падение. */
export default function TechnologyAnalyticsSection({ params }: Props) {
  const q = useQuery({
    queryKey: ["analytics-technologies", params],
    queryFn: () => api.getTechnologyAnalytics(params),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const data = q.data;
  if (q.error) return <p className="error">{(q.error as Error).message}</p>;
  if (!data) return null;
  const sd = data.salary_display;
  const suffix = sd?.display_currency === "RUB" ? ", ₽" : "";
  const up = data.momentum.filter((m) => m.delta_pp > 0).slice(0, MOMENTUM_SHOWN);
  const down = data.momentum
    .filter((m) => m.delta_pp < 0)
    .slice(-MOMENTUM_SHOWN)
    .reverse();
  return (
    <>
      <PremiumChart items={data.premium} suffix={suffix} />
      <TrendChart
        months={data.trend.months}
        totals={data.trend.totals}
        series={data.trend.series}
      />
      {(up.length > 0 || down.length > 0) && (
        <div className="card chart-card chart-wide">
          <h4>
            Растут и падают: последние {data.momentum_window_days} дней против предыдущих
            {data.momentum_anchor && ` (по ${data.momentum_anchor})`}
          </h4>
          <div className="table-wrap">
            <MomentumTable title="Растут" rows={up} />
            <MomentumTable title="Падают" rows={down} />
          </div>
        </div>
      )}
    </>
  );
}
