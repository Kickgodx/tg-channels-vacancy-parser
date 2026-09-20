import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";

interface Props {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}

export default function ResumePanel({ selectedId, onSelect }: Props) {
  const resumesQ = useQuery({ queryKey: ["resumes"], queryFn: () => api.listResumes() });
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
      <Link to="/resumes" className="btn-link secondary">
        Управление резюме
      </Link>
    </div>
  );
}
