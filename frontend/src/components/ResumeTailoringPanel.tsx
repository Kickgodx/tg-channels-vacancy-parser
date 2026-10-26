import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { ResumeTailoring, ResumeTailoringSuggestion } from "../types/api";
import { useUnmountSignal } from "../utils/useUnmountSignal";

interface Props {
  resumeId: number;
  vacancyId: string;
}

const PRIORITY_LABEL: Record<ResumeTailoringSuggestion["priority"], string> = {
  high: "важно",
  medium: "полезно",
  low: "по желанию",
};

function Suggestion({ s }: { s: ResumeTailoringSuggestion }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(s.suggested);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <li className="tailor-item">
      <div className="tailor-item-head">
        <strong>{s.section || "Резюме"}</strong>
        <span className={`match-skill-importance ${s.priority === "high" ? "critical" : ""}`}>
          {PRIORITY_LABEL[s.priority]}
        </span>
        <button type="button" className="btn-link secondary" onClick={copy}>
          {copied ? "Скопировано" : "Копировать"}
        </button>
      </div>
      {s.current && (
        <p className="tailor-current">
          <span className="muted">Было:</span> {s.current}
        </p>
      )}
      <p className="tailor-suggested">
        <span className="muted">{s.current ? "Стало:" : "Добавить:"}</span> {s.suggested}
      </p>
      {s.reason && <p className="caption">{s.reason}</p>}
    </li>
  );
}

/** «Адаптировать резюме»: правки реального опыта под требования вакансии (LLM-джоба). */
export default function ResumeTailoringPanel({ resumeId, vacancyId }: Props) {
  const unmountSignal = useUnmountSignal();
  const [generated, setGenerated] = useState<ResumeTailoring | null>(null);
  const savedQ = useQuery({
    queryKey: ["resume-tailoring", resumeId, vacancyId],
    queryFn: () => api.getResumeTailoring(resumeId, vacancyId).catch(() => null),
    staleTime: Infinity,
    retry: false,
  });
  const result = generated ?? savedQ.data ?? null;
  const tailorM = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      api.tailorResume(resumeId, vacancyId, force, unmountSignal()),
    onSuccess: setGenerated,
  });

  return (
    <aside className="cover-letter">
      <div className="vacancy-match-header">
        <strong>Адаптация резюме</strong>
        {result?.model && <span className="caption">{result.model}</span>}
        <button
          type="button"
          disabled={tailorM.isPending}
          title="LLM подскажет, как переписать реальный опыт из резюме под требования вакансии"
          onClick={() => tailorM.mutate({ force: result != null })}
        >
          {tailorM.isPending ? "Анализ…" : result != null ? "Пересчитать" : "Адаптировать резюме"}
        </button>
      </div>
      {tailorM.isError && <p className="error">{(tailorM.error as Error).message}</p>}
      {!result && !tailorM.isPending && (
        <p className="caption">
          Подскажет, какие формулировки поменять и что поднять выше, — только из того, что уже есть
          в резюме.
        </p>
      )}
      {result && (
        <>
          {result.summary && <p>{result.summary}</p>}
          {result.suggestions.length > 0 && (
            <ul className="tailor-list">
              {result.suggestions.map((s, i) => (
                <Suggestion key={i} s={s} />
              ))}
            </ul>
          )}
          {result.keywords.length > 0 && (
            <p>
              <strong>Термины вакансии, которые резюме подтверждает:</strong>{" "}
              {result.keywords.join(", ")}
            </p>
          )}
          {result.do_not_claim.length > 0 && (
            <p>
              <strong className="warning">Не добавлять без реального опыта:</strong>{" "}
              {result.do_not_claim.join(", ")}
            </p>
          )}
          {result.stale && (
            <p className="caption">
              <span className="warning">
                Получено другой моделью или версией промпта — пересчитайте.
              </span>
            </p>
          )}
        </>
      )}
    </aside>
  );
}
