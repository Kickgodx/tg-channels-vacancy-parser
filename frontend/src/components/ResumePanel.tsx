import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

interface Props {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}

export default function ResumePanel({ selectedId, onSelect }: Props) {
  const queryClient = useQueryClient();
  const resumesQ = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });

  const [resumeName, setResumeName] = useState("");
  const [resumeText, setResumeText] = useState("");

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
      <span className="caption">
        {selectedId
          ? "Откройте вакансию и нажмите «Оценить резюме» в её карточке."
          : "Выберите резюме, чтобы оценивать вакансии по одной."}
      </span>
      <button
        type="button"
        className="btn-secondary"
        disabled={!selectedId || deleteM.isPending}
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
      {(createM.isError || deleteM.isError) && (
        <span className="error">
          {(createM.error as Error | null)?.message ??
            (deleteM.error as Error | null)?.message}
        </span>
      )}
    </div>
  );
}
