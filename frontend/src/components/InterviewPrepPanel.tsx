import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { InterviewPrep, InterviewQuestion, InterviewQuestionKind } from "../types/api";
import { useUnmountSignal } from "../utils/useUnmountSignal";

interface Props {
  resumeId: number;
  vacancyId: string;
}

const KIND_LABEL: Record<InterviewQuestionKind, string> = {
  technical: "техника",
  experience: "опыт",
  behavioral: "поведение",
  gap: "пробел",
};

function Question({ q }: { q: InterviewQuestion }) {
  return (
    <li className="prep-item">
      <p>
        <span className={`prep-kind ${q.kind === "gap" ? "gap" : ""}`}>{KIND_LABEL[q.kind]}</span>
        {q.topic && <span className="muted">{q.topic} · </span>}
        <strong>{q.question}</strong>
      </p>
      {q.why && <p className="caption">{q.why}</p>}
      {q.answer_points.length > 0 && (
        <ul>
          {q.answer_points.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

/** «Подготовка к собеседованию»: вероятные вопросы и тезисы ответов из резюме (LLM-джоба). */
export default function InterviewPrepPanel({ resumeId, vacancyId }: Props) {
  const unmountSignal = useUnmountSignal();
  const [generated, setGenerated] = useState<InterviewPrep | null>(null);
  const savedQ = useQuery({
    queryKey: ["interview-prep", resumeId, vacancyId],
    queryFn: () => api.getInterviewPrep(resumeId, vacancyId).catch(() => null),
    staleTime: Infinity,
    retry: false,
  });
  const result = generated ?? savedQ.data ?? null;
  const prepM = useMutation({
    mutationFn: ({ force }: { force: boolean }) =>
      api.prepareInterview(resumeId, vacancyId, force, unmountSignal()),
    onSuccess: setGenerated,
  });

  return (
    <aside className="cover-letter">
      <div className="vacancy-match-header">
        <strong>Подготовка к собеседованию</strong>
        {result?.model && <span className="caption">{result.model}</span>}
        <button
          type="button"
          disabled={prepM.isPending}
          title="LLM соберёт вероятные вопросы по этой вакансии и тезисы ответов из вашего резюме"
          onClick={() => prepM.mutate({ force: result != null })}
        >
          {prepM.isPending ? "Готовим…" : result != null ? "Пересчитать" : "Подготовиться"}
        </button>
      </div>
      {prepM.isError && <p className="error">{(prepM.error as Error).message}</p>}
      {!result && !prepM.isPending && (
        <p className="caption">
          Вопросы, которые вероятнее всего зададут на этой вакансии, и что ответить — по опыту из
          резюме, без выдуманного.
        </p>
      )}
      {result && (
        <>
          {result.summary && <p>{result.summary}</p>}
          {result.questions.length > 0 && (
            <ol className="prep-list">
              {result.questions.map((q, i) => (
                <Question key={i} q={q} />
              ))}
            </ol>
          )}
          {result.gaps.length > 0 && (
            <>
              <p>
                <strong className="warning">Подтянуть до собеседования:</strong>
              </p>
              <ul>
                {result.gaps.map((g) => (
                  <li key={g.skill}>
                    <strong>{g.skill}</strong>
                    {g.advice && <> — {g.advice}</>}
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.questions_to_ask.length > 0 && (
            <>
              <p>
                <strong>Что спросить у работодателя:</strong>
              </p>
              <ul>
                {result.questions_to_ask.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </>
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
