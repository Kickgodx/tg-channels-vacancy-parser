import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { MetricsSummary } from "../types/api";

/** Семантические цвета формата работы — контрастные, в тон общему UI (#3b5bdb, #f0f2f8). */
const WORK_FORMAT_COLORS: Record<string, string> = {
  remote: "#12b886",
  hybrid: "#7950f2",
  office: "#fd7e14",
  relocation: "#e64980",
  exclude_office: "#868e96",
  unknown: "#adb5bd",
  any: "#ced4da",
};

const WORK_FORMAT_FALLBACK = [
  "#3b5bdb",
  "#12b886",
  "#fd7e14",
  "#7950f2",
  "#e64980",
  "#15aabf",
  "#fab005",
  "#fa5252",
];

function workFormatColor(name: string, index: number): string {
  const key = name.toLowerCase().trim();
  if (WORK_FORMAT_COLORS[key]) return WORK_FORMAT_COLORS[key];
  return WORK_FORMAT_FALLBACK[index % WORK_FORMAT_FALLBACK.length];
}

function dictToBarData(d: Record<string, number>, limit = 25) {
  return Object.entries(d)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, limit);
}

interface Props {
  metrics: MetricsSummary;
}

function salaryChartSuffix(metrics: MetricsSummary): string {
  const sd = metrics.salary_display;
  if (!sd || sd.display_currency !== "RUB") return "";
  return sd.rates_as_of ? `, ₽ (курс ЦБ ${sd.rates_as_of})` : ", ₽";
}

export default function MetricsCharts({ metrics }: Props) {
  const salarySuffix = salaryChartSuffix(metrics);
  const specData = dictToBarData(metrics.vacancies_by_specialization ?? {}, 20);
  const gradeData = dictToBarData(metrics.vacancies_by_grade ?? {});
  const wfData = dictToBarData(metrics.vacancies_by_work_format ?? {});
  const dailyData = (metrics.daily ?? []).map((b) => ({ period: b.period, count: b.count }));
  const salarySpec = Object.entries(metrics.salary_by_specialization ?? {})
    .filter(([, st]) => st.count_with_salary > 0 && st.mean != null)
    .map(([name, st]) => ({ name, mean: Math.round(st.mean!) }))
    .sort((a, b) => b.mean - a.mean)
    .slice(0, 20);
  const salaryGrade = Object.entries(metrics.salary_by_grade ?? {})
    .filter(([, st]) => st.count_with_salary > 0 && st.mean != null)
    .map(([name, st]) => ({ name, mean: Math.round(st.mean!) }))
    .sort((a, b) => b.mean - a.mean);

  return (
    <div className="charts-grid">
      <div className="card chart-card">
        <h4>Вакансии по специализациям</h4>
        {specData.length === 0 ? (
          <p className="muted">Нет данных</p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={specData} margin={{ bottom: 80 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" angle={-35} textAnchor="end" interval={0} height={90} tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip />
              <Bar dataKey="count" fill="#3b5bdb" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="card chart-card">
        <h4>Вакансии по грейдам</h4>
        {gradeData.length === 0 ? (
          <p className="muted">Нет данных</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={gradeData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="count" fill="#5c7cfa" />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="card chart-card">
        <h4>Динамика по дням</h4>
        {dailyData.length === 0 ? (
          <p className="muted">Нет данных</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={dailyData}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip />
              <Line type="monotone" dataKey="count" stroke="#3b5bdb" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="card chart-card">
        <h4>Формат работы</h4>
        {wfData.length === 0 ? (
          <p className="muted">Нет данных</p>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <PieChart>
              <Pie data={wfData} dataKey="count" nameKey="name" cx="50%" cy="50%" outerRadius={90} label>
                {wfData.map((entry, i) => (
                  <Cell key={entry.name} fill={workFormatColor(entry.name, i)} />
                ))}
              </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        )}
      </div>

      {salaryGrade.length > 0 && (
        <div className="card chart-card">
          <h4>Средняя зарплата по грейдам{salarySuffix}</h4>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={salaryGrade}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" />
              <YAxis />
              <Tooltip />
              <Bar dataKey="mean" fill="#7950f2" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {salarySpec.length > 0 && (
        <div className="card chart-card chart-wide">
          <h4>Средняя зарплата по специализациям{salarySuffix}</h4>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={salarySpec} margin={{ bottom: 80 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="name" angle={-35} textAnchor="end" interval={0} height={90} tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip />
              <Bar dataKey="mean" fill="#12b886" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
