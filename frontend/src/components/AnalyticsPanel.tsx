import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type VacancyQueryParams } from "../api/client";
import TechnologyAnalyticsSection from "./TechnologyAnalyticsSection";
import type {
  SalaryAnalytics,
  SalaryGroupStats,
  SalaryPeriodGroupBucket,
} from "../types/api";

interface Props {
  params: VacancyQueryParams;
}

const SERIES_COLORS = [
  "#7950f2",
  "#12b886",
  "#f59f00",
  "#e03131",
  "#228be6",
  "#ae3ec9",
  "#495057",
  "#15aabf",
  "#fd7e14",
  "#82c91e",
];

function fmtRub(v?: number | null): string {
  if (v == null) return "—";
  return Math.round(v).toLocaleString("ru-RU");
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(0)}%`;
}

function salarySuffix(data?: SalaryAnalytics): string {
  const sd = data?.salary_display;
  if (!sd || sd.display_currency !== "RUB") return "";
  return sd.rates_as_of ? `, ₽ (курс ЦБ ${sd.rates_as_of})` : ", ₽";
}

function QuantileTable({ title, rows }: { title: string; rows: SalaryGroupStats[] }) {
  if (rows.length === 0) return null;
  return (
    <div className="card chart-card">
      <h4>{title}</h4>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Группа</th>
              <th>n</th>
              <th>min</th>
              <th>p25</th>
              <th>Медиана</th>
              <th>p75</th>
              <th>max</th>
              <th>Среднее</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.group}>
                <td>{r.group}</td>
                <td>{r.count}</td>
                <td>{fmtRub(r.min)}</td>
                <td>{fmtRub(r.q1)}</td>
                <td>
                  <strong>{fmtRub(r.median)}</strong>
                </td>
                <td>{fmtRub(r.q3)}</td>
                <td>{fmtRub(r.max)}</td>
                <td>{fmtRub(r.mean)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Порядок грейдов по старшинству — как _ORDER в tg_jobs_parser/extraction/grade_extractor.py.
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
const UNKNOWN_GROUP = "unknown";
const UNKNOWN_COLOR = "#adb5bd";

function sortGroups(groups: string[], order?: string[]): string[] {
  const rank = (g: string) => {
    if (g === UNKNOWN_GROUP) return Number.MAX_SAFE_INTEGER;
    const i = order?.indexOf(g) ?? -1;
    return i >= 0 ? i : (order?.length ?? 0);
  };
  return [...groups].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

function bucketKey(period: string, group: string): string {
  return `${period}\u0000${group}`;
}

function pivotPeriodGroups(buckets: SalaryPeriodGroupBucket[], order?: string[]) {
  const periods = [...new Set(buckets.map((b) => b.period))].sort();
  const groups = sortGroups([...new Set(buckets.map((b) => b.group))], order);
  const byKey = new Map(buckets.map((b) => [bucketKey(b.period, b.group), b]));
  const data = periods.map((period) => {
    const row: Record<string, string | number | null> = { period };
    for (const g of groups) {
      const b = byKey.get(bucketKey(period, g));
      row[g] = b?.median != null ? Math.round(b.median) : null;
    }
    return row;
  });
  return { data, groups, byKey };
}

interface BarTooltipProps {
  active?: boolean;
  payload?: {
    dataKey?: string | number;
    color?: string;
    payload?: { period?: string };
  }[];
  byKey: Map<string, SalaryPeriodGroupBucket>;
}

/**
 * Подсказка по одному столбику (Tooltip shared={false}): группа, период, квантили, n.
 * Период берётся из строки наведённого столбика: `label` в item-режиме Recharts 2
 * при наведении не обновляется.
 */
function BarTooltip({ active, payload, byKey }: BarTooltipProps) {
  const item = payload?.[0];
  const label = item?.payload?.period;
  if (!active || !item || label == null) return null;
  const group = String(item.dataKey);
  const b = byKey.get(bucketKey(label, group));
  if (!b) return null;
  return (
    <div className="bar-tooltip">
      <div className="bar-tooltip-title">
        <span className="bar-tooltip-swatch" style={{ background: item.color }} />
        {group} · {label}
      </div>
      <div>
        Медиана: <strong>{fmtRub(b.median)}</strong>
      </div>
      <div>
        p25–p75: {fmtRub(b.q1)} – {fmtRub(b.q3)}
      </div>
      <div>
        min–max: {fmtRub(b.min)} – {fmtRub(b.max)}
      </div>
      <div>Среднее: {fmtRub(b.mean)}</div>
      <div className="muted">
        Вакансий: {b.count}
        {b.count < 5 && " — мало данных"}
      </div>
    </div>
  );
}

function PeriodGroupChart({
  title,
  buckets,
  order,
}: {
  title: string;
  buckets: SalaryPeriodGroupBucket[];
  order?: string[];
}) {
  const { data, groups, byKey } = useMemo(
    () => pivotPeriodGroups(buckets, order),
    [buckets, order],
  );
  if (data.length === 0 || groups.length === 0) return null;
  const known = groups.filter((g) => g !== UNKNOWN_GROUP);
  const colorOf = (g: string) =>
    g === UNKNOWN_GROUP ? UNKNOWN_COLOR : SERIES_COLORS[known.indexOf(g) % SERIES_COLORS.length];
  return (
    <div className="card chart-card">
      <h4>{title}</h4>
      <ResponsiveContainer width="100%" height={280}>
        <BarChart data={data} barCategoryGap="15%">
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="period" tick={{ fontSize: 11 }} />
          <YAxis tickFormatter={(v: number) => `${Math.round(v / 1000)}k`} />
          <Tooltip shared={false} cursor={false} content={<BarTooltip byKey={byKey} />} />
          <Legend />
          {groups.map((g) => (
            <Bar key={g} dataKey={g} name={g} fill={colorOf(g)} maxBarSize={28} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const BOX_FILL = "#d0bfff";
const BOX_STROKE = "#7950f2";
const MEDIAN_STROKE = "#5f3dc4";
const WHISKER_STROKE = "#868e96";
const AXIS_STEP = 50_000;

interface SpreadRow {
  group: string;
  label: string;
  range: [number, number];
  stats: SalaryGroupStats;
}

/** Строки box-plot: грейды по старшинству, усы — p10–p90 (без них — min–max). */
function spreadRows(rows: SalaryGroupStats[]): SpreadRow[] {
  const byGroup = new Map(rows.map((r) => [r.group, r]));
  const out: SpreadRow[] = [];
  for (const g of sortGroups([...byGroup.keys()], GRADE_ORDER)) {
    const s = byGroup.get(g)!;
    const lo = s.p10 ?? s.min;
    const hi = s.p90 ?? s.max;
    if (lo == null || hi == null || s.q1 == null || s.q3 == null || s.median == null) continue;
    out.push({ group: g, label: `${g} (n=${s.count})`, range: [lo, hi], stats: s });
  }
  return out;
}

interface BoxShapeProps {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: SpreadRow;
}

/**
 * Ящик с усами поверх range-бара [p10, p90]: Recharts отдаёт пиксели концов
 * диапазона, внутренние квантили переводим линейно в той же шкале.
 */
function BoxShape({ x = 0, y = 0, width = 0, height = 0, payload }: BoxShapeProps) {
  if (!payload) return null;
  const [lo, hi] = payload.range;
  const { q1, median, q3 } = payload.stats;
  const px = (v: number) => (hi > lo ? x + ((v - lo) / (hi - lo)) * width : x);
  const cy = y + height / 2;
  const boxH = Math.min(height, 20);
  const top = cy - boxH / 2;
  const capH = boxH * 0.6;
  const x1 = px(q1!);
  const x3 = px(q3!);
  const xm = px(median!);
  // Мало вакансий — квантили шумные: приглушаем ящик, цифры остаются в подсказке.
  const opacity = payload.stats.count < 5 ? 0.45 : 1;
  return (
    <g opacity={opacity}>
      <line x1={x} x2={x + width} y1={cy} y2={cy} stroke={WHISKER_STROKE} strokeWidth={1.5} />
      <line x1={x} x2={x} y1={cy - capH / 2} y2={cy + capH / 2} stroke={WHISKER_STROKE} strokeWidth={1.5} />
      <line
        x1={x + width}
        x2={x + width}
        y1={cy - capH / 2}
        y2={cy + capH / 2}
        stroke={WHISKER_STROKE}
        strokeWidth={1.5}
      />
      <rect
        x={x1}
        y={top}
        width={Math.max(x3 - x1, 1)}
        height={boxH}
        rx={3}
        fill={BOX_FILL}
        stroke={BOX_STROKE}
        strokeWidth={1}
      />
      <line x1={xm} x2={xm} y1={top} y2={top + boxH} stroke={MEDIAN_STROKE} strokeWidth={2.5} />
    </g>
  );
}

interface SpreadTooltipProps {
  active?: boolean;
  payload?: { payload?: SpreadRow }[];
}

function SpreadTooltip({ active, payload }: SpreadTooltipProps) {
  const row = payload?.[0]?.payload;
  if (!active || !row) return null;
  const s = row.stats;
  return (
    <div className="bar-tooltip">
      <div className="bar-tooltip-title">{row.group}</div>
      <div>
        Медиана: <strong>{fmtRub(s.median)}</strong>
      </div>
      <div>
        p25–p75: {fmtRub(s.q1)} – {fmtRub(s.q3)}
      </div>
      <div>
        p10–p90: {fmtRub(s.p10)} – {fmtRub(s.p90)}
      </div>
      <div>
        min–max: {fmtRub(s.min)} – {fmtRub(s.max)}
      </div>
      <div>Среднее: {fmtRub(s.mean)}</div>
      <div className="muted">
        Вакансий: {s.count}
        {s.count < 5 && " — мало данных"}
      </div>
    </div>
  );
}

function GradeSpreadChart({ title, rows }: { title: string; rows: SalaryGroupStats[] }) {
  const data = useMemo(() => spreadRows(rows), [rows]);
  if (data.length === 0) return null;
  const lo = Math.min(...data.map((d) => d.range[0]));
  const hi = Math.max(...data.map((d) => d.range[1]));
  const domain = [
    Math.max(0, Math.floor(lo / AXIS_STEP) * AXIS_STEP),
    Math.ceil(hi / AXIS_STEP) * AXIS_STEP || AXIS_STEP,
  ];
  return (
    <div className="card chart-card">
      <h4>{title}</h4>
      <p className="muted" style={{ margin: "0 0 0.5rem" }}>
        Ящик — p25–p75, черта — медиана, усы — p10–p90
      </p>
      <ResponsiveContainer width="100%" height={40 + data.length * 34}>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis
            type="number"
            domain={domain}
            tickFormatter={(v: number) => `${Math.round(v / 1000)}k`}
            tick={{ fontSize: 11 }}
          />
          <YAxis type="category" dataKey="label" width={110} tick={{ fontSize: 11 }} />
          <Tooltip cursor={{ fill: "#f1f3f5" }} content={<SpreadTooltip />} />
          <Bar dataKey="range" shape={<BoxShape />} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function AnalyticsPanel({ params }: Props) {
  const [analyticsYear, setAnalyticsYear] = useState<string | null>(null);
  // По умолчанию с архивом: иначе TTL обрезает прошлые годы и YoY пустеет.
  // Без него цифры совпадают со списком (он показывает только активные).
  const [includeArchived, setIncludeArchived] = useState(true);

  // Вся история без сужения дат списка — для YoY и селектора лет.
  const historyParams = useMemo(() => {
    const base: VacancyQueryParams = { ...params, include_archived: includeArchived };
    delete base.date_from;
    delete base.date_to;
    return base;
  }, [params, includeArchived]);

  const yearParams = useMemo(() => {
    if (!analyticsYear) return null;
    return {
      ...params,
      include_archived: includeArchived,
      date_from: `${analyticsYear}-01-01`,
      date_to: `${analyticsYear}-12-31`,
    } satisfies VacancyQueryParams;
  }, [params, analyticsYear, includeArchived]);

  const historyQ = useQuery({
    queryKey: ["analytics-salaries", historyParams],
    queryFn: () => api.getSalaryAnalytics(historyParams),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const yearQ = useQuery({
    queryKey: ["analytics-salaries", yearParams],
    queryFn: () => api.getSalaryAnalytics(yearParams!),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
    enabled: yearParams != null,
  });
  const panelParams = yearParams ?? historyParams;
  const sourcesQ = useQuery({
    queryKey: ["analytics-sources", panelParams],
    queryFn: () => api.getSourceStats(panelParams),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
  const companiesQ = useQuery({
    queryKey: ["analytics-companies", panelParams],
    queryFn: () => api.getCompanyStats(panelParams),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });

  const history = historyQ.data;
  const salaries = analyticsYear ? yearQ.data : history;
  const suffix = salarySuffix(salaries);
  const yearOptions = (history?.yearly ?? []).map((b) => b.period);
  // Без архива выбранный год может пропасть из истории — оставляем его в списке.
  if (analyticsYear && !yearOptions.includes(analyticsYear)) {
    yearOptions.push(analyticsYear);
    yearOptions.sort();
  }
  const monthlyData = (salaries?.monthly ?? []).map((b) => ({
    period: b.period,
    median: b.median != null ? Math.round(b.median) : null,
    mean: b.mean != null ? Math.round(b.mean) : null,
  }));
  const yearlyData = (history?.yearly ?? []).map((b) => ({
    period: b.period,
    median: b.median != null ? Math.round(b.median) : null,
    mean: b.mean != null ? Math.round(b.mean) : null,
  }));
  const distData = salaries?.distribution ?? [];
  const sources = sourcesQ.data?.items ?? [];
  const companies = companiesQ.data?.items ?? [];
  const anyError = historyQ.error ?? yearQ.error ?? sourcesQ.error ?? companiesQ.error;

  return (
    <>
      {anyError && <p className="error">{(anyError as Error).message}</p>}

      <div className="filters" style={{ marginBottom: "0.75rem", alignItems: "center" }}>
        <label>
          Период аналитики
          <select
            value={analyticsYear ?? ""}
            onChange={(e) => setAnalyticsYear(e.target.value || null)}
          >
            <option value="">Все годы (YoY)</option>
            {yearOptions.map((y) => (
              <option key={y} value={y}>
                {y} (месяц к месяцу)
              </option>
            ))}
          </select>
        </label>
        <label
          className="checkbox-row"
          style={{ marginBottom: 0 }}
          title="Список показывает только активные вакансии; с архивом цифры аналитики больше выборки, зато прошлые годы не обрезаны TTL"
        >
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(e) => setIncludeArchived(e.target.checked)}
          />
          Учитывать архивные вакансии
        </label>
      </div>

      <div className="charts-grid">
        {!analyticsYear && (
          <>
            {yearlyData.length > 0 && (
              <div className="card chart-card">
                <h4>Медианная зарплата по годам{suffix}</h4>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={yearlyData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                    <YAxis />
                    <Tooltip formatter={(v: number) => fmtRub(v)} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="median"
                      name="Медиана"
                      stroke="#7950f2"
                      strokeWidth={2}
                      dot
                    />
                    <Line
                      type="monotone"
                      dataKey="mean"
                      name="Среднее"
                      stroke="#adb5bd"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            <PeriodGroupChart
              title={`Медиана по грейдам (годы)${suffix}`}
              buckets={history?.by_grade_yearly ?? []}
              order={GRADE_ORDER}
            />
            <PeriodGroupChart
              title={`Медиана по формату (годы)${suffix}`}
              buckets={history?.by_work_format_yearly ?? []}
            />
          </>
        )}

        {analyticsYear && (
          <>
            {monthlyData.length > 0 && (
              <div className="card chart-card">
                <h4>
                  Медианная зарплата по месяцам {analyticsYear}
                  {suffix}
                </h4>
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={monthlyData}>
                    <CartesianGrid strokeDasharray="3 3" />
                    <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                    <YAxis />
                    <Tooltip formatter={(v: number) => fmtRub(v)} />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="median"
                      name="Медиана"
                      stroke="#7950f2"
                      strokeWidth={2}
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="mean"
                      name="Среднее"
                      stroke="#adb5bd"
                      strokeWidth={1.5}
                      strokeDasharray="4 4"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
            <PeriodGroupChart
              title={`Медиана по грейдам (месяцы ${analyticsYear})${suffix}`}
              buckets={salaries?.by_grade_monthly ?? []}
              order={GRADE_ORDER}
            />
            <PeriodGroupChart
              title={`Медиана по формату (месяцы ${analyticsYear})${suffix}`}
              buckets={salaries?.by_work_format_monthly ?? []}
            />
          </>
        )}

        <QuantileTable title={`Зарплата по грейдам${suffix}`} rows={salaries?.by_grade ?? []} />
        <QuantileTable
          title={`Зарплата по формату работы${suffix}`}
          rows={salaries?.by_work_format ?? []}
        />
        <QuantileTable
          title={`Зарплата по специализациям${suffix}`}
          rows={salaries?.by_specialization ?? []}
        />

        {!analyticsYear && monthlyData.length > 0 && (
          <div className="card chart-card">
            <h4>Медианная зарплата по месяцам (все годы){suffix}</h4>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={monthlyData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="period" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip formatter={(v: number) => fmtRub(v)} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="median"
                  name="Медиана"
                  stroke="#7950f2"
                  strokeWidth={2}
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="mean"
                  name="Среднее"
                  stroke="#adb5bd"
                  strokeWidth={1.5}
                  strokeDasharray="4 4"
                  dot={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {distData.some((b) => b.count > 0) && (
          <div className="card chart-card">
            <h4>Распределение зарплат{suffix}</h4>
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={distData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="range" tick={{ fontSize: 11 }} />
                <YAxis />
                <Tooltip />
                <Bar dataKey="count" name="Вакансий" fill="#12b886" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <GradeSpreadChart
          title={`Разброс зарплат по грейдам${suffix}`}
          rows={salaries?.by_grade ?? []}
        />

        <TechnologyAnalyticsSection params={panelParams} />

        {sources.length > 0 && (
          <div className="card chart-card chart-wide">
            <h4>Качество каналов</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Канал</th>
                    <th title="Вакансий, впервые найденных в этом канале">Вакансий</th>
                    <th title="Постов с уже встречавшимся текстом: чужим, своим перепостом или из прошлых прогонов">
                      Дублей
                    </th>
                    <th title="Доля постов канала с новым текстом">% уникальных</th>
                    <th>Ср. score</th>
                    <th>% с зарплатой</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((s) => (
                    <tr key={s.channel}>
                      <td>@{s.channel}</td>
                      <td>{s.vacancies}</td>
                      <td>{s.duplicates}</td>
                      <td>{fmtPct(s.unique_share)}</td>
                      <td>{s.avg_score != null ? s.avg_score.toFixed(1) : "—"}</td>
                      <td>{fmtPct(s.with_salary_share)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {companies.length > 0 && (
          <div className="card chart-card chart-wide">
            <h4>Топ компаний</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Компания</th>
                    <th>Вакансий</th>
                    <th>Ср. score</th>
                    <th>С зарплатой</th>
                    <th>Ср. зарплата{suffix}</th>
                  </tr>
                </thead>
                <tbody>
                  {companies.map((c) => (
                    <tr key={c.company}>
                      <td>
                        <Link to={`/company?name=${encodeURIComponent(c.company)}`}>{c.company}</Link>
                      </td>
                      <td>{c.vacancies}</td>
                      <td>{c.avg_score != null ? c.avg_score.toFixed(1) : "—"}</td>
                      <td>{c.with_salary}</td>
                      <td>{fmtRub(c.avg_salary_rub)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </>
  );
}
