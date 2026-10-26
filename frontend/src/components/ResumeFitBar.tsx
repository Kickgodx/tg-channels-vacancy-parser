import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { MatchBatchResponse, VacancyListItem } from "../types/api";
import { useUnmountSignal } from "../utils/useUnmountSignal";

/** Сколько вакансий страницы оценивать за раз — как MAX_BATCH_MATCH в backend. */
const TOP_N = 10;

interface Props {
  resumeId: number;
  /** Список сейчас отсортирован по близости к резюме (sort_by=resume_fit). */
  active: boolean;
  items: VacancyListItem[];
  onToggle: (on: boolean) => void;
}

/** «Подбор под резюме»: сортировка по смыслу + LLM-оценка верхних вакансий одной задачей. */
export default function ResumeFitBar({ resumeId, active, items, onToggle }: Props) {
  const queryClient = useQueryClient();
  const unmountSignal = useUnmountSignal();
  const [progress, setProgress] = useState<MatchBatchResponse | null>(null);
  const targets = items.filter((v) => v.match_score == null).slice(0, TOP_N);

  const batchM = useMutation({
    mutationFn: () =>
      api.matchBatch(
        resumeId,
        targets.map((v) => v.id),
        setProgress,
        unmountSignal(),
      ),
    onMutate: () => setProgress(null),
    onSuccess: (r) => setProgress(r),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
      void queryClient.invalidateQueries({ queryKey: ["skill-gaps"] });
    },
  });

  return (
    <div className="filters-panel resume-fit-bar">
      <label className="checkbox-row">
        <input type="checkbox" checked={active} onChange={(e) => onToggle(e.target.checked)} />
        Подбор под резюме — сначала самые близкие по смыслу
      </label>
      {active && (
        <button
          type="button"
          className="btn-secondary"
          disabled={batchM.isPending || targets.length === 0}
          title="LLM оценит верхние вакансии страницы, у которых ещё нет оценки (квота — за каждую)"
          onClick={() => batchM.mutate()}
        >
          {batchM.isPending
            ? `Оценка… ${progress ? progress.items.length : 0} / ${targets.length}`
            : targets.length > 0
              ? `Оценить топ-${targets.length}`
              : "Верхние вакансии уже оценены"}
        </button>
      )}
      {progress && !batchM.isPending && (
        <span className="caption">
          Оценено {progress.scored}
          {progress.skipped > 0 && `, уже были ${progress.skipped}`}
          {progress.errors > 0 && `, ошибок ${progress.errors}`}
          {progress.warning && ` — ${progress.warning}`}
        </span>
      )}
      {batchM.isError && <span className="error">{(batchM.error as Error).message}</span>}
    </div>
  );
}
