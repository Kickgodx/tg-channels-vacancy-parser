import { useCallback, useEffect, useRef } from "react";

/**
 * AbortSignal, который срабатывает при размонтировании компонента: долгие
 * LLM-ожидания не продолжаются после закрытия модалки / ухода со страницы.
 * Контроллер создаётся в эффекте — StrictMode-перемонтирование не оставляет
 * сигнал навсегда отменённым. Возвращает геттер: вызывать в обработчике, не при рендере.
 */
export function useUnmountSignal(): () => AbortSignal {
  const ref = useRef<AbortController | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    ref.current = ctrl;
    return () => ctrl.abort();
  }, []);
  return useCallback(() => {
    if (!ref.current) ref.current = new AbortController();
    return ref.current.signal;
  }, []);
}
