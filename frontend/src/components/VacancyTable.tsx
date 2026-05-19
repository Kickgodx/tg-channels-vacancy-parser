import { useMemo, useState } from "react";
import type { Vacancy } from "../types/api";
import type { VacancySortField, VacancySortOrder } from "../types/sort";
import { SORTABLE_COLUMNS } from "../types/sort";
import VacancyModal from "./VacancyModal";

const ALL_COLUMNS = [
  { key: "post_url", label: "Пост", sortable: false },
  { key: "source_channel", label: "Канал", sortable: false },
  { key: "post_date", label: "Дата", sortable: true },
  { key: "detected_salary_avg", label: "Зарплата", sortable: true },
  { key: "detected_salary_avg_rub", label: "Зарплата, ₽", sortable: true },
  { key: "salary_currency", label: "Валюта", sortable: true },
  { key: "work_format", label: "Формат", sortable: true },
  { key: "detected_grade", label: "Грейд", sortable: true },
  { key: "detected_specializations", label: "Специализации", sortable: false },
  { key: "vacancy_score", label: "Score", sortable: true },
  { key: "cleaned_text", label: "Текст", sortable: false },
] as const;

type ColKey = (typeof ALL_COLUMNS)[number]["key"];

function compareValues(a: unknown, b: unknown, order: VacancySortOrder): number {
  const mul = order === "asc" ? 1 : -1;
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  if (typeof a === "number" && typeof b === "number") return (a - b) * mul;
  if (typeof a === "string" && typeof b === "string") {
    return a.localeCompare(b, "ru", { sensitivity: "base" }) * mul;
  }
  return String(a).localeCompare(String(b), "ru") * mul;
}

function sortVacancies(items: Vacancy[], sortBy: VacancySortField, sortOrder: VacancySortOrder): Vacancy[] {
  return [...items].sort((a, b) => {
    const av = (a as unknown as Record<string, unknown>)[sortBy];
    const bv = (b as unknown as Record<string, unknown>)[sortBy];
    return compareValues(av, bv, sortOrder);
  });
}

interface Props {
  items: Vacancy[];
  title?: string;
  sortBy?: VacancySortField;
  sortOrder?: VacancySortOrder;
  onSortChange?: (sortBy: VacancySortField, sortOrder: VacancySortOrder) => void;
}

export default function VacancyTable({
  items,
  title,
  sortBy: sortByProp = "post_date",
  sortOrder: sortOrderProp = "desc",
  onSortChange,
}: Props) {
  const [localSortBy, setLocalSortBy] = useState<VacancySortField>(sortByProp);
  const [localSortOrder, setLocalSortOrder] = useState<VacancySortOrder>(sortOrderProp);

  const sortBy = onSortChange ? sortByProp : localSortBy;
  const sortOrder = onSortChange ? sortOrderProp : localSortOrder;

  const [cols, setCols] = useState<ColKey[]>([
    "post_url",
    "source_channel",
    "post_date",
    "detected_salary_avg",
    "detected_salary_avg_rub",
    "salary_currency",
    "work_format",
    "detected_grade",
    "vacancy_score",
    "cleaned_text",
  ]);
  const [selected, setSelected] = useState<{ v: Vacancy; idx: number } | null>(null);

  const sortedItems = useMemo(
    () => (onSortChange ? items : sortVacancies(items, sortBy, sortOrder)),
    [items, sortBy, sortOrder, onSortChange],
  );

  const handleSort = (key: ColKey) => {
    if (!SORTABLE_COLUMNS.includes(key as VacancySortField)) return;
    const field = key as VacancySortField;
    let nextBy = field;
    let nextOrder: VacancySortOrder = "desc";
    if (sortBy === field) {
      nextOrder = sortOrder === "asc" ? "desc" : "asc";
    } else {
      nextOrder = field === "post_date" ? "desc" : "asc";
    }
    if (onSortChange) {
      onSortChange(nextBy, nextOrder);
    } else {
      setLocalSortBy(nextBy);
      setLocalSortOrder(nextOrder);
    }
  };

  const renderCell = (v: Vacancy, key: ColKey) => {
    switch (key) {
      case "post_url":
        return v.post_url ? (
          <a href={v.post_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
            Открыть
          </a>
        ) : (
          "—"
        );
      case "source_channel":
        return `@${v.source_channel}`;
      case "post_date":
        return v.post_date ? new Date(v.post_date).toLocaleDateString() : "—";
      case "detected_salary_avg": {
        if (v.detected_salary_avg == null) return "—";
        const cur = (v.salary_currency || "").trim();
        const curLabel = cur.replace(/\/HOUR$/i, "/ч");
        return `${Math.round(v.detected_salary_avg).toLocaleString()} ${curLabel}`.trim();
      }
      case "detected_salary_avg_rub":
        return v.detected_salary_avg_rub != null
          ? `${Math.round(v.detected_salary_avg_rub).toLocaleString()} ₽`
          : "—";
      case "detected_specializations":
        return (v.detected_specializations || []).join(", ") || "—";
      case "cleaned_text":
        return (v.cleaned_text || "").slice(0, 120) + ((v.cleaned_text?.length ?? 0) > 120 ? "…" : "");
      default: {
        const val = (v as unknown as Record<string, unknown>)[key];
        return val != null ? String(val) : "—";
      }
    }
  };

  const visibleCols = useMemo(() => ALL_COLUMNS.filter((c) => cols.includes(c.key)), [cols]);

  const sortIndicator = (key: ColKey) => {
    if (sortBy !== key) return "";
    return sortOrder === "asc" ? " ↑" : " ↓";
  };

  return (
    <>
      {title && <h3>{title}</h3>}
      <div className="col-picker">
        {ALL_COLUMNS.map((c) => (
          <label key={c.key} className="chip">
            <input
              type="checkbox"
              checked={cols.includes(c.key)}
              onChange={(e) => {
                if (e.target.checked) setCols([...cols, c.key]);
                else setCols(cols.filter((k) => k !== c.key));
              }}
            />
            {c.label}
          </label>
        ))}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              {visibleCols.map((c) =>
                c.sortable ? (
                  <th
                    key={c.key}
                    className="sortable-th"
                    onClick={() => handleSort(c.key)}
                    title="Сортировка: клик — смена направления"
                  >
                    {c.label}
                    {sortIndicator(c.key)}
                  </th>
                ) : (
                  <th key={c.key}>{c.label}</th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {sortedItems.map((v, i) => (
              <tr key={v.id} className="clickable" onClick={() => setSelected({ v, idx: i + 1 })}>
                <td>{i + 1}</td>
                {visibleCols.map((c) => (
                  <td key={c.key}>{renderCell(v, c.key)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {selected && (
        <VacancyModal vacancy={selected.v} index={selected.idx} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
