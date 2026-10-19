import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { VacancyReextractResponse } from "../types/api";
import { useUnmountSignal } from "../utils/useUnmountSignal";

/**
 * Переразбор LLM вакансий без специализации (admin): после выноса языков в технологии
 * у вакансий вида «Go-разработчик» роли не осталось — назначить её по тексту может только модель.
 */
export default function ReextractPanel() {
  const queryClient = useQueryClient();
  const unmountSignal = useUnmountSignal();
  const [progress, setProgress] = useState<VacancyReextractResponse | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);

  const infoQ = useQuery({ queryKey: ["reextract-info"], queryFn: () => api.getReextractInfo() });

  const cancelM = useMutation({ mutationFn: (id: string) => api.cancelLlmJob(id) });

  const runM = useMutation({
    mutationFn: () => {
      setProgress(null);
      setJobId(null);
      cancelM.reset();
      return api.reextractVacancies(setProgress, unmountSignal(), setJobId);
    },
    onSettled: () => {
      setJobId(null);
      void queryClient.invalidateQueries({ queryKey: ["reextract-info"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
      void queryClient.invalidateQueries({ queryKey: ["vacancy-metrics"] });
    },
  });

  const info = infoQ.data;
  const done = runM.data?.result ?? null;
  const result = done ?? (runM.isPending ? progress : null);
  const active = Boolean(info?.active_job_id);
  const canRun = !runM.isPending && info != null && info.ai_enabled && (active || info.missing_role > 0);

  return (
    <div className="card">
      <h3>Переразбор вакансий без специализации</h3>
      <p className="caption">
        Языки и фреймворки (Python, Go, Java…) теперь технологии, а не специализации. У вакансий, где
        специализацией был только язык, роли не осталось — LLM переразберёт их текст и назначит роль
        (Backend, AI, Automation QA…). Вакансии HH и архивные не трогаются; за раз — до{" "}
        {info?.max_per_job ?? "…"} вакансий.
      </p>
      {infoQ.error && <p className="error">{(infoQ.error as Error).message}</p>}
      {info && (
        <p>
          Вакансий без специализации: <strong>{info.missing_role}</strong>
          {!info.ai_enabled && <span className="warning"> — AI выключен, переразбор недоступен</span>}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" disabled={!canRun} onClick={() => runM.mutate()}>
          {runM.isPending
            ? progress
              ? `Разбор… ${progress.items.length} из ${progress.total}`
              : "Разбор…"
            : active
              ? "Следить за идущим переразбором"
              : "Переразобрать LLM"}
        </button>
        {runM.isPending && jobId && (
          <button
            type="button"
            className="secondary"
            disabled={cancelM.isPending || cancelM.isSuccess}
            onClick={() => cancelM.mutate(jobId)}
          >
            {cancelM.isPending || cancelM.isSuccess ? "Отмена…" : "Отменить"}
          </button>
        )}
      </div>
      {cancelM.isError && <p className="error">{(cancelM.error as Error).message}</p>}
      {runM.isError && <p className="error">{(runM.error as Error).message}</p>}
      {result?.warning && <p className="warning">{result.warning}</p>}
      {runM.data?.cancelled && done && (
        <p className="warning">
          Переразбор отменён: обработано {done.items.length} из {done.total}.
        </p>
      )}
      {result && (
        <p className="caption">
          Обновлено {result.updated}, не похожи на вакансию (не изменены) {result.kept}, ошибок{" "}
          {result.errors}
          {runM.isPending ? ` — обработано ${result.items.length} из ${result.total}` : ""}
        </p>
      )}
    </div>
  );
}
