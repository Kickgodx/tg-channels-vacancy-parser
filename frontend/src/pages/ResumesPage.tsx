import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import ResumeMarketPanel from "../components/ResumeMarketPanel";
import SkillGapsPanel from "../components/SkillGapsPanel";
import type { Resume } from "../types/api";

export default function ResumesPage() {
  const queryClient = useQueryClient();
  const resumesQ = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const [fileNote, setFileNote] = useState<string | null>(null);

  const resumes = resumesQ.data?.items ?? [];
  const selected = resumes.find((r) => r.id === selectedId) ?? null;

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["resumes"] });
    void queryClient.invalidateQueries({ queryKey: ["vacancies"] });
    // Смена текста резюме удаляет на бэкенде его матчи и письма.
    void queryClient.invalidateQueries({ queryKey: ["cover-letter"] });
    void queryClient.invalidateQueries({ queryKey: ["skill-gaps"] });
    void queryClient.invalidateQueries({ queryKey: ["resume-tailoring"] });
    void queryClient.invalidateQueries({ queryKey: ["resume-market"] });
  };

  const closeForm = () => {
    setFormOpen(false);
    setEditingId(null);
    setFileNote(null);
  };

  const openCreate = () => {
    setEditingId(null);
    setName("");
    setText("");
    setFormOpen(true);
  };

  const openEdit = (r: Resume) => {
    setEditingId(r.id);
    setName(r.name);
    setText(r.text);
    setFormOpen(true);
  };

  const saveM = useMutation({
    mutationFn: () =>
      editingId != null
        ? api.updateResume(editingId, name.trim(), text)
        : api.createResume(name.trim(), text),
    onSuccess: (r) => {
      closeForm();
      setSelectedId(r.id);
      invalidate();
    },
  });

  const deleteM = useMutation({
    mutationFn: (id: number) => api.deleteResume(id),
    onSuccess: () => {
      setSelectedId(null);
      invalidate();
    },
  });

  const fileM = useMutation({
    mutationFn: (file: File) => api.parseResumeFile(file),
    onSuccess: (r, file) => {
      setText(r.text);
      if (!name.trim()) setName(file.name.replace(/\.(pdf|docx)$/i, ""));
      setFileNote(
        `Текст из ${r.format.toUpperCase()}${r.pages ? `, ${r.pages} стр.` : ""}: ${r.chars} символов` +
          (r.truncated ? " — обрезан до 50 000" : "") +
          ". Проверьте форматирование перед сохранением.",
      );
    },
  });

  const saving = saveM.isPending;

  return (
    <>
      <div className="card">
        <h2>Резюме</h2>
        <p className="caption">
          Резюме используются для оценки совпадения с вакансией: выберите резюме на странице
          «Вакансии» и нажмите «Оценить резюме» в карточке вакансии.
        </p>
        <div className="filters-panel">
          <button type="button" onClick={openCreate}>
            Новое резюме
          </button>
        </div>
        {resumesQ.isLoading && <p>Загрузка…</p>}
        {resumesQ.error && <p className="error">{(resumesQ.error as Error).message}</p>}
        {resumesQ.data && resumes.length === 0 && <p className="muted">Резюме пока нет.</p>}
        {resumes.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Имя</th>
                <th>Создано</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {resumes.map((r) => (
                <tr
                  key={r.id}
                  className="clickable"
                  onClick={() => setSelectedId(r.id)}
                >
                  <td>{r.id === selectedId ? <strong>{r.name}</strong> : r.name}</td>
                  <td className="muted">
                    {new Date(r.created_at).toLocaleDateString("ru-RU")}
                  </td>
                  <td onClick={(e) => e.stopPropagation()}>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        setSelectedId(r.id);
                        openEdit(r);
                      }}
                    >
                      Редактировать
                    </button>{" "}
                    <button
                      type="button"
                      className="secondary danger"
                      disabled={deleteM.isPending}
                      onClick={() => {
                        if (window.confirm(`Удалить резюме «${r.name}»?`)) {
                          deleteM.mutate(r.id);
                        }
                      }}
                    >
                      Удалить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {deleteM.isError && <p className="error">{(deleteM.error as Error).message}</p>}
      </div>

      {selected && !formOpen && (
        <div className="card">
          <h3>{selected.name}</h3>
          <p className="caption">
            Создано {new Date(selected.created_at).toLocaleDateString("ru-RU")}
          </p>
          <p className="vacancy-full-text">{selected.text}</p>
          <div className="modal-actions">
            <button type="button" className="secondary" onClick={() => openEdit(selected)}>
              Редактировать
            </button>
          </div>
        </div>
      )}

      {selected && !formOpen && <ResumeMarketPanel resumeId={selected.id} />}
      {selected && !formOpen && <SkillGapsPanel resumeId={selected.id} />}

      {formOpen && (
        <div className="card">
          <h3>{editingId != null ? "Редактировать резюме" : "Новое резюме"}</h3>
          <label className="field">
            Имя
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например: Python backend, 5 лет"
            />
          </label>
          <label className="field">
            Загрузить из файла (PDF или DOCX, до 5 МБ)
            <input
              type="file"
              accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              disabled={fileM.isPending}
              onChange={(e) => {
                const file = e.target.files?.[0];
                setFileNote(null);
                if (file) fileM.mutate(file);
                e.target.value = "";
              }}
            />
          </label>
          {fileM.isPending && <p className="caption">Извлекаем текст…</p>}
          {fileM.isError && <p className="error">{(fileM.error as Error).message}</p>}
          {fileNote && <p className="caption">{fileNote}</p>}
          <label className="field">
            Текст резюме
            <textarea
              rows={12}
              value={text}
              maxLength={50000}
              onChange={(e) => setText(e.target.value)}
              placeholder="Опыт, стек, проекты…"
            />
          </label>
          <div className="modal-actions">
            <button
              type="button"
              disabled={!name.trim() || !text.trim() || saving}
              onClick={() => saveM.mutate()}
            >
              {saving ? "Сохранение…" : "Сохранить"}
            </button>
            <button type="button" className="secondary" onClick={closeForm}>
              Отмена
            </button>
          </div>
          {saveM.isError && <p className="error">{(saveM.error as Error).message}</p>}
        </div>
      )}
    </>
  );
}
