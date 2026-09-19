import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type VacancyQueryParams } from "../api/client";
import type { MatchProgress } from "../types/api";

interface Props {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  filterParams: VacancyQueryParams;
}

function toVacancyQuery(params: VacancyQueryParams): Record<string, unknown> {
  const filters: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(params)) {
    filters[k === "specialization" ? "specializations" : k] = v;
  }
  return filters;
}

export default function ResumePanel({ selectedId, onSelect, filterParams }: Props) {
  const queryClient = useQueryClient();
  const resumesQ = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });

  const [limit, setLimit] = useState(100);
  const [progress, setProgress] = useState<MatchProgress | null>(null);
  const [sseError, setSseError] = useState<string | null>(null);
  const [resumeName, setResumeName] = useState("");
  const [resumeText, setResumeText] = useState("");
  const unsubscribeRef = useRef<(() => void) | null>(null);

  useEffect(() => () => unsubscribeRef.current?.(), []);

  const createM = useMutation({
    mutationFn: ({ name, text }: { name: string; text: string }) =>
      api.createResume(name, text),
    onSuccess: (r) => {
      setResumeName("");
      setResumeText("");
      onSelect(r.id);
      void queryClient.invalidateQueries({ queryKey: ["resumes"] });
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => api.deleteResume(id),
    onSuccess: () => {
      onSelect(null);
      void queryClient.invalidateQueries({ queryKey: ["resumes"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    },
  });

  const matchM = useMutation({
    mutationFn: (resumeId: number) =>
      api.startMatch(resumeId, limit, toVacancyQuery(filterParams)),
    onSuccess: (d) => {
      setSseError(null);
      unsubscribeRef.current?.();
      unsubscribeRef.current = api.subscribeMatchEvents(
        d.match_id,
        (p) => setProgress(p),
        () => {
          void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
        },
        (err) => setSseError(err.message),
      );
    },
  });

  const running = progress?.status === "running";
  const resumes = resumesQ.data?.items ?? [];

  return (
    <div className="filters-panel resume-bar">
      <select
        value={selectedId ?? ""}
        onChange={(e) => onSelect(e.target.value ? Number(e.target.value) : null)}
        title="Резюме для оценки совпадения"
      >
        <option value="">Резюме…</option>
        {resumes.map((r) => (
          <option key={r.id} value={r.id}>
            {r.name}
          </option>
        ))}
      </select>
      <label className="caption">
        Вакансий:
        <input
          type="number"
          min={1}
          max={500}
          value={limit}
          style={{ width: "5rem", marginLeft: "0.4rem" }}
          onChange={(e) => setLimit(Math.max(1, Math.min(500, Number(e.target.value) || 1)))}
        />
      </label>
      <button
        type="button"
        className="btn-secondary"
        disabled={!selectedId || running || matchM.isPending}
        onClick={() => selectedId && matchM.mutate(selectedId)}
        title="LLM оценит соответствие резюме топ-N вакансий по текущим фильтрам"
      >
        {running ? "Оценка…" : "Оценить совпадение"}
      </button>
      {running && (
        <button
          type="button"
          className="btn-secondary"
          onClick={() => progress && api.cancelMatch(progress.match_id)}
        >
          Отменить
        </button>
      )}
      <button
        type="button"
        className="btn-secondary"
        disabled={!selectedId || deleteM.isPending || running}
        onClick={() => selectedId && deleteM.mutate(selectedId)}
      >
        Удалить
      </button>
      <details>
        <summary className="btn-secondary">Добавить резюме</summary>
        <div className="resume-add-form">
          <input
            placeholder="Имя резюме"
            value={resumeName}
            onChange={(e) => setResumeName(e.target.value)}
          />
          <textarea
            rows={6}
            placeholder="Текст резюме (опыт, стек, проекты)…"
            value={resumeText}
            onChange={(e) => setResumeText(e.target.value)}
          />
          <button
            type="button"
            className="btn-secondary"
            disabled={!resumeName.trim() || !resumeText.trim() || createM.isPending}
            onClick={() => createM.mutate({ name: resumeName.trim(), text: resumeText })}
          >
            {createM.isPending ? "Сохранение…" : "Сохранить"}
          </button>
        </div>
      </details>
      {progress && (
        <span className="caption">
          {progress.status === "running" &&
            `Оценено ${progress.processed}/${progress.total} (токены: ${progress.tokens_in}+${progress.tokens_out})`}
          {progress.status === "completed" &&
            `Готово: ${progress.matched} оценено, ${progress.skipped_cached} уже было, ошибок ${progress.errors}`}
          {progress.status === "cancelled" &&
            `Отменено: ${progress.matched} оценено${progress.error ? ` (${progress.error})` : ""}`}
          {progress.status === "failed" && (
            <span className="error">Ошибка: {progress.error}</span>
          )}
        </span>
      )}
      {(matchM.isError || sseError || createM.isError || deleteM.isError) && (
        <span className="error">
          {sseError ??
            (matchM.error as Error | null)?.message ??
            (createM.error as Error | null)?.message ??
            (deleteM.error as Error | null)?.message}
        </span>
      )}
    </div>
  );
}
