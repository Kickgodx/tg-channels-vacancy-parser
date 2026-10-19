import { useMemo, useState } from "react";
import type { VacancyListItem } from "../types/api";
import type { VacancySortField, VacancySortOrder, VacancyThenSortField } from "../types/sort";
import { SORTABLE_COLUMNS, thenSortPartner } from "../types/sort";
import { cleanPostUrl } from "../utils/cleanPostUrl";
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
  { key: "match_score", label: "Match", sortable: true },
  { key: "preview", label: "Текст", sortable: false },
] as const;
type ColKey = (typeof ALL_COLUMNS)[number]["key"];

/** Первый клик по числовой колонке — «больше сверху»: так же лежат индексы даты и зарплаты. */
const DESC_FIRST: ReadonlySet<VacancySortField> = new Set([
  "post_date",
  "detected_salary_avg",
  "detected_salary_avg_rub",
  "vacancy_score",
  "match_score",
]);

const flip = (o: VacancySortOrder): VacancySortOrder => (o === "asc" ? "desc" : "asc");
const arrow = (o: VacancySortOrder) => (o === "asc" ? "↑" : "↓");

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

function sortVacancies(
  items: VacancyListItem[],
  sortBy: VacancySortField,
  sortOrder: VacancySortOrder,
): VacancyListItem[] {
  return [...items].sort((a, b) => {
    const av = (a as unknown as Record<string, unknown>)[sortBy];
    const bv = (b as unknown as Record<string, unknown>)[sortBy];
    return compareValues(av, bv, sortOrder);
  });
}

interface Props {
  items: VacancyListItem[];
  title?: string;
  sortBy?: VacancySortField;
  sortOrder?: VacancySortOrder;
  /** Вторая ступень серверной сортировки (только при `multiSort`). */
  thenBy?: VacancyThenSortField | null;
  thenOrder?: VacancySortOrder;
  onSortChange?: (
    sortBy: VacancySortField,
    sortOrder: VacancySortOrder,
    thenBy: VacancyThenSortField | null,
    thenOrder: VacancySortOrder,
  ) => void;
  /** Shift+клик по «Дата» / «Зарплата, ₽» добавляет вторую ступень (сортирует backend). */
  multiSort?: boolean;
  /** Выбранное резюме — включает матчинг в карточке вакансии. */
  resumeId?: number | null;
}
export default function VacancyTable({
  items,
  title,
  sortBy: sortByProp = "post_date",
  sortOrder: sortOrderProp = "desc",
  thenBy: thenByProp = null,
  thenOrder = "desc",
  onSortChange,
  multiSort = false,
  resumeId,
}: Props) {
  const [localSortBy, setLocalSortBy] = useState<VacancySortField>(sortByProp);
  const [localSortOrder, setLocalSortOrder] = useState<VacancySortOrder>(sortOrderProp);

  const sortBy = onSortChange ? sortByProp : localSortBy;
  const sortOrder = onSortChange ? sortOrderProp : localSortOrder;
  const canMultiSort = multiSort && onSortChange != null;
  const thenPartner = canMultiSort ? thenSortPartner(sortBy) : null;
  const thenBy = thenPartner != null && thenByProp === thenPartner ? thenByProp : null;

  const [cols, setCols] = useState<ColKey[]>([
    "post_url",
    "source_channel",
    "post_date",
    "detected_salary_avg",
    "detected_salary_avg_rub",
    "salary_currency",
    "work_format",
    "detected_grade",
    "detected_specializations",
    "vacancy_score",
    "preview",
  ]);
  const [matchScoreHidden, setMatchScoreHidden] = useState(false);
  const [selected, setSelected] = useState<{ v: VacancyListItem; idx: number } | null>(null);

  const hasMatchScore = items.some((v) => v.match_score != null);
  const shownCols = useMemo(() => {
    if (cols.includes("match_score") || matchScoreHidden || !hasMatchScore) return cols;
    return [...cols, "match_score"];
  }, [cols, hasMatchScore, matchScoreHidden]);

  const sortedItems = useMemo(() => {
    const base = onSortChange ? items : sortVacancies(items, sortBy, sortOrder);
    // Live SSE иногда присылал кандидатов с одним content-id до дедупа.
    const seen = new Set<string>();
    return base.filter((v) => {
      if (seen.has(v.id)) return false;
      seen.add(v.id);
      return true;
    });
  }, [items, sortBy, sortOrder, onSortChange]);

  const handleSort = (key: ColKey, additive: boolean) => {
    if (!SORTABLE_COLUMNS.includes(key as VacancySortField)) return;
    const field = key as VacancySortField;
    if (onSortChange && canMultiSort && additive) {
      if (field === sortBy) {
        onSortChange(sortBy, flip(sortOrder), thenBy, thenOrder);
        return;
      }
      if (field === thenPartner) {
        onSortChange(sortBy, sortOrder, thenPartner, thenBy === field ? flip(thenOrder) : "desc");
        return;
      }
    }
    const nextOrder: VacancySortOrder =
      sortBy === field ? flip(sortOrder) : DESC_FIRST.has(field) ? "desc" : "asc";
    if (onSortChange) {
      onSortChange(field, nextOrder, null, "desc");
    } else {
      setLocalSortBy(field);
      setLocalSortOrder(nextOrder);
    }
  };

  const renderCell = (v: VacancyListItem, key: ColKey) => {
    switch (key) {
      case "post_url":
        return v.post_url ? (
          <a
            href={cleanPostUrl(v.post_url)}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
          >
            Открыть
          </a>
        ) : (
          "—"
        );
      case "source_channel":
        return `@${v.source_channel}`;
      case "post_date": {
        // День — по UTC, как фильтры периода и сортировка «по дню»; точное время — в подсказке.
        const date = v.post_date ? (
          <span title={new Date(v.post_date).toLocaleString()}>
            {new Date(v.post_date).toLocaleDateString(undefined, { timeZone: "UTC" })}
          </span>
        ) : (
          "—"
        );
        if (!v.archived_at) return date;
        return (
          <>
            {date}{" "}
            <span className="muted" title={`В архиве с ${new Date(v.archived_at).toLocaleDateString()}`}>
              · архив
            </span>
          </>
        );
      }
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
      case "match_score":
        return v.match_score != null ? (
          <span title={v.match_explanation ?? undefined}>{Math.round(v.match_score)}</span>
        ) : (
          "—"
        );
      case "detected_specializations":
        return (v.detected_specializations || []).join(", ") || "—";
      case "preview":
        return v.preview || "—";
      default: {
        const val = (v as unknown as Record<string, unknown>)[key];
        return val != null ? String(val) : "—";
      }
    }
  };

  const visibleCols = useMemo(() => ALL_COLUMNS.filter((c) => shownCols.includes(c.key)), [shownCols]);

  const sortIndicator = (key: ColKey) => {
    if (sortBy === key) {
      return thenBy ? (
        <>
          {" "}
          {arrow(sortOrder)}
          <sup>1</sup>
        </>
      ) : (
        ` ${arrow(sortOrder)}`
      );
    }
    if (thenBy === key) {
      return (
        <>
          {" "}
          {arrow(thenOrder)}
          <sup>2</sup>
        </>
      );
    }
    return "";
  };

  const sortTitle = (key: ColKey) =>
    canMultiSort && (key === "post_date" || key === "detected_salary_avg_rub")
      ? "Клик — сортировка, Shift+клик — второй уровень (дата + зарплата ₽)"
      : "Сортировка: клик — смена направления";

  const sortSummary = () => {
    if (!onSortChange || thenPartner == null) return null;
    if (thenBy == null) {
      return (
        <p className="caption sort-hint">
          <button
            type="button"
            className="btn-link"
            onClick={() => onSortChange(sortBy, sortOrder, thenPartner, "desc")}
          >
            {sortBy === "post_date"
              ? "+ внутри дня — по зарплате ₽"
              : "+ при равной зарплате — по дате"}
          </button>{" "}
          (или Shift+клик по заголовку)
        </p>
      );
    }
    return (
      <p className="caption sort-hint">
        {sortBy === "post_date"
          ? `Сортировка: по дню ${arrow(sortOrder)} (UTC), внутри дня — по зарплате ₽ ${arrow(thenOrder)}`
          : `Сортировка: по зарплате ₽ ${arrow(sortOrder)}, при равной — по дате ${arrow(thenOrder)}`}{" "}
        <button type="button" className="btn-link" onClick={() => onSortChange(sortBy, sortOrder, null, "desc")}>
          убрать второй уровень
        </button>
      </p>
    );
  };

  return (
    <>
      {title && <h3>{title}</h3>}
      <div className="col-picker">
        {ALL_COLUMNS.map((c) => (
          <label key={c.key} className="chip">
            <input
              type="checkbox"
              checked={shownCols.includes(c.key)}
              onChange={(e) => {
                if (c.key === "match_score") {
                  setMatchScoreHidden(!e.target.checked);
                  setCols((prev) =>
                    e.target.checked
                      ? prev.includes("match_score")
                        ? prev
                        : [...prev, "match_score"]
                      : prev.filter((k) => k !== "match_score"),
                  );
                  return;
                }
                setCols((prev) =>
                  e.target.checked ? [...prev, c.key] : prev.filter((k) => k !== c.key),
                );
              }}
            />
            {c.label}
          </label>
        ))}
      </div>
      {sortSummary()}
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
                    onClick={(e) => handleSort(c.key, e.shiftKey)}
                    title={sortTitle(c.key)}
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
        <VacancyModal
          vacancy={selected.v}
          index={selected.idx}
          resumeId={resumeId}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
