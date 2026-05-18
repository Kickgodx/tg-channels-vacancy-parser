import { NavLink, Route, Routes } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import VacanciesPage from "./pages/VacanciesPage";
import RunPage from "./pages/RunPage";

export default function App() {
  return (
    <div className="app-shell">
      <h1>Telegram Jobs Parser</h1>
      <nav>
        <NavLink to="/" end>
          Запуск
        </NavLink>
        <NavLink to="/vacancies">Вакансии</NavLink>
      </nav>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/runs/:runId" element={<RunPage />} />
        <Route path="/vacancies" element={<VacanciesPage />} />
      </Routes>
    </div>
  );
}
