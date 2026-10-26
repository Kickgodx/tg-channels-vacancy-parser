import { useQuery } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api, getToken, retryIfRetryable } from "./api/client";
import CompanyPage from "./pages/CompanyPage";
import DashboardPage from "./pages/DashboardPage";
import HhPage from "./pages/HhPage";
import ImportPage from "./pages/ImportPage";
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

/** Парсинг, импорт и HH — только admin (backend отвечает 403); остальных — на вакансии. */
function RequireAdmin({ children }: { children: JSX.Element }) {
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: retryIfRetryable });
  if (meQ.isLoading) return <p className="muted">Загрузка…</p>;
  if (!meQ.data?.is_admin) return <Navigate to="/vacancies" replace />;
  return children;
}

function UserBadge() {
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: retryIfRetryable });
  return (
    <span className="nav-user">
      <span className="muted">{meQ.data?.email ?? "…"}</span>
      <button type="button" className="btn-link secondary" onClick={() => void api.logout()}>
        Выйти
      </button>
    </span>
  );
}

function AdminNavLink({ to, end, children }: { to: string; end?: boolean; children: string }) {
  const meQ = useQuery({ queryKey: ["me"], queryFn: () => api.me(), retry: retryIfRetryable });
  if (!meQ.data?.is_admin) return null;
  return (
    <NavLink to={to} end={end}>
      {children}
    </NavLink>
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
          <AdminNavLink to="/" end>
            Парсинг
          </AdminNavLink>
          <AdminNavLink to="/import">Импорт</AdminNavLink>
          <AdminNavLink to="/hh">HH</AdminNavLink>
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
              <RequireAdmin>
                <DashboardPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route
          path="/runs/:runId"
          element={
            <RequireAuth>
              <RequireAdmin>
                <RunPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route
          path="/import"
          element={
            <RequireAuth>
              <RequireAdmin>
                <ImportPage />
              </RequireAdmin>
            </RequireAuth>
          }
        />
        <Route
          path="/hh"
          element={
            <RequireAuth>
              <RequireAdmin>
                <HhPage />
              </RequireAdmin>
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
          path="/company"
          element={
            <RequireAuth>
              <CompanyPage />
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
