import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { refreshSession } from "./api/client";
import "./index.css";

const queryClient = new QueryClient();

// Access-токен только в памяти: до первого рендера восстановить сессию по
// refresh-cookie, иначе RequireAuth отправит на /login уже залогиненного.
void refreshSession().finally(() => {
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </React.StrictMode>,
  );
});
