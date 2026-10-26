import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** Флаги вакансии по правилам: предупреждения — с «!», справка — с «i»; цвет не единственный признак. */
export default function VacancyFlagsBlock({ vacancyId }: { vacancyId: string }) {
  const q = useQuery({
    queryKey: ["vacancy-flags", vacancyId],
    queryFn: () => api.getVacancyFlags(vacancyId),
    staleTime: 60_000,
  });
  const flags = q.data?.flags ?? [];
  if (flags.length === 0) return null;
  return (
    <ul className="vacancy-flags">
      {flags.map((f) => (
        <li key={f.code} className={`vacancy-flag ${f.severity}`}>
          <span className="vacancy-flag-icon" aria-hidden="true">
            {f.severity === "warning" ? "!" : "i"}
          </span>
          {f.message}
        </li>
      ))}
    </ul>
  );
}
