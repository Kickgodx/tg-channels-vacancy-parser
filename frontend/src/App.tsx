import { useQuery } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, getToken } from "./api/client";
import DashboardPage from "./pages/DashboardPage";
import LoginPage from "./pages/LoginPage";
import ResumesPage from "./pages/ResumesPage";
import VacanciesPage from "./pages/VacanciesPage";
import RunPage from "./pages/RunPage";

function RequireAuth({ children }: { children: JSX.Element }) {
  const location = useLocation();
  if (!getToken()) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return children;
}

function UserBadge() {
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: false });
  return (
    <span className="nav-user">
      <span className="muted">{meQ.data?.email ?? "…"}</span>
      <button type="button" className="btn-link secondary" onClick={() => api.logout()}>
        Выйти
      </button>
    </span>
  );
}

export default function App() {
  useLocation(); // перерисовка при навигации — nav появляется сразу после логина
  const authed = Boolean(getToken());
  return (
    <div className="app-shell">
      <h1>Telegram Jobs Parser</h1>
      {authed && (
        <nav>
          <NavLink to="/" end>
            Парсинг
          </NavLink>
          <NavLink to="/vacancies">Вакансии</NavLink>
          <NavLink to="/resumes">Резюме</NavLink>
          <UserBadge />
        </nav>
      )}
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <DashboardPage />
            </RequireAuth>
          }
        />
        <Route
          path="/runs/:runId"
          element={
            <RequireAuth>
              <RunPage />
            </RequireAuth>
          }
        />
        <Route
          path="/vacancies"
          element={
            <RequireAuth>
              <VacanciesPage />
            </RequireAuth>
          }
        />
        <Route
          path="/resumes"
          element={
            <RequireAuth>
              <ResumesPage />
            </RequireAuth>
          }
        />
      </Routes>
    </div>
  );
}
