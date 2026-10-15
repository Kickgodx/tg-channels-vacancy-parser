import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import VacancyModal from "../components/VacancyModal";
import VacancyTable from "../components/VacancyTable";
import type {
  Vacancy,
  VacancyImportItem,
  VacancyImportResponse,
  VacancyImportStatus,
} from "../types/api";
import { useUnmountSignal } from "../utils/useUnmountSignal";

const STATUS_LABEL: Record<VacancyImportStatus, string> = {
  created: "Сохранена",
  exists: "Уже в базе",
  not_vacancy: "Не вакансия",
  error: "Ошибка",
};

const STATUS_CLASS: Record<VacancyImportStatus, string> = {
  created: "success",
  exists: "muted",
  not_vacancy: "warning",
  error: "error",
};

/** Несколько вакансий в одном поле разделяются строкой из трёх и более дефисов. */
function splitTexts(text: string): string[] {
  return text
    .split(/\r?\n\s*-{3,}\s*\r?\n/)
    .map((t) => t.trim())
    .filter(Boolean);
}

function vacancyBrief(v: Vacancy): string {
  const parts: string[] = [];
  if (v.company) parts.push(v.company);
  if (v.detected_grade && v.detected_grade !== "unknown") parts.push(v.detected_grade);
  if (v.detected_salary_avg != null) {
    parts.push(
      `~${Math.round(v.detected_salary_avg).toLocaleString()} ${v.salary_currency ?? ""}`.trim(),
    );
  }
  if (v.work_format) parts.push(v.work_format);
  if (v.detected_specializations?.length) parts.push(v.detected_specializations.join(", "));
  return parts.join(" · ");
}

export default function ImportPage() {
  const queryClient = useQueryClient();
  const unmountSignal = useUnmountSignal();
  const [text, setText] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [selected, setSelected] = useState<{ v: Vacancy; idx: number } | null>(null);
  // Частичный итог фоновой джобы (импорт с LLM), пока она идёт.
  const [progress, setProgress] = useState<VacancyImportResponse | null>(null);

  const recentQ = useQuery({
    queryKey: ["imported-vacancies"],
    queryFn: () =>
      api.listVacancies({
        source_channel: "manual",
        page_size: 20,
        sort_by: "post_date",
        sort_order: "desc",
      }),
  });

  const importM = useMutation({
    mutationFn: () => {
      setProgress(null);
      return api.importVacancies(
        splitTexts(text),
        sourceUrl.trim() || undefined,
        setProgress,
        unmountSignal(),
      );
    },
    onSuccess: () => {
      setText("");
      void queryClient.invalidateQueries({ queryKey: ["imported-vacancies"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  const result = importM.data ?? (importM.isPending ? progress : null);
  const items: VacancyImportItem[] = result?.items ?? [];
  const canSubmit = splitTexts(text).length > 0 && !importM.isPending;
  const pendingLabel = progress
    ? `Разбор… ${progress.items.length} из ${progress.total}`
    : "Разбор…";

  return (
    <>
      <div className="card">
        <h2>Импорт вакансии из текста</h2>
        <p className="caption">
          Вставьте текст вакансии (например, скопированный с hh.ru) — она пройдёт тот же
          разбор, что и посты из Telegram: rules-извлечение + LLM, затем сохранится в общую
          базу и появится на странице «Вакансии». Несколько вакансий разделяйте строкой{" "}
          <code>---</code>.
        </p>
        <label className="field">
          Текст вакансии
          <textarea
            rows={12}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Вакансия: Python-разработчик…"
          />
        </label>
        <label className="field">
          Ссылка на оригинал (необязательно)
          <input
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            placeholder="https://hh.ru/vacancy/…"
          />
        </label>
        <div className="modal-actions">
          <button type="button" disabled={!canSubmit} onClick={() => importM.mutate()}>
            {importM.isPending ? pendingLabel : "Разобрать и сохранить"}
          </button>
        </div>
        {importM.isPending && progress && (
          <p className="caption">
            LLM-разбор идёт в фоне: со страницы можно уйти — вакансии всё равно сохранятся.
          </p>
        )}
        {importM.isError && <p className="error">{(importM.error as Error).message}</p>}
        {result?.warning && <p className="warning">{result.warning}</p>}
        {importM.data && (
          <p className="caption">
            Итого: сохранено {importM.data.created}, уже были {importM.data.existing},
            отклонено {importM.data.rejected}
            {importM.data.ai_enabled ? "" : " (LLM выключен — только rules-разбор)"}
          </p>
        )}
        {items.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Статус</th>
                <th>Вакансия</th>
                <th>Комментарий</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr
                  key={it.index}
                  className={it.vacancy ? "clickable" : undefined}
                  onClick={() =>
                    it.vacancy && setSelected({ v: it.vacancy, idx: it.index + 1 })
                  }
                >
                  <td>{it.index + 1}</td>
                  <td>
                    <span className={STATUS_CLASS[it.status]}>{STATUS_LABEL[it.status]}</span>
                  </td>
                  <td>{it.vacancy ? vacancyBrief(it.vacancy) || "—" : "—"}</td>
                  <td className="muted">{it.message ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card">
        <h3>Недавно импортированные</h3>
        {recentQ.isLoading && <p>Загрузка…</p>}
        {recentQ.error && <p className="error">{(recentQ.error as Error).message}</p>}
        {recentQ.data && recentQ.data.items.length === 0 && (
          <p className="muted">Пока ничего не импортировано.</p>
        )}
        {recentQ.data && recentQ.data.items.length > 0 && (
          <VacancyTable items={recentQ.data.items} />
        )}
      </div>

      {selected && (
        <VacancyModal
          vacancy={selected.v}
          index={selected.idx}
          onClose={() => setSelected(null)}
        />
      )}
    </>
  );
}
