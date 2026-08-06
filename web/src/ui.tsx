/** Piezas compartidas. Nada acá calcula plata: solo renderiza `formatted`. */

import { useEffect, useState } from "react";
import type { Money } from "./api";

/**
 * Un monto. `formatted` viene del backend; `cents` solo se usa para decidir el
 * color. Formatear en el front haría que la web y el CLI puedan mostrar el
 * mismo monto distinto.
 */
export function Amount({ value, colored = true }: { value: Money | null; colored?: boolean }) {
  if (!value) return <span className="dim">—</span>;
  const cls = colored && value.cents !== 0 ? (value.cents > 0 ? "pos" : "neg") : "";
  return <span className={`num ${cls}`}>{value.formatted}</span>;
}

/**
 * `2026-04-01` → `01/04`.
 *
 * Se escribe día/mes, no mes/día: en Colombia `04-01` se lee "4 de enero", y la
 * ambigüedad DD-MM vs MM-DD ya es una trampa documentada de este dominio (el
 * CSV de Wompi usa DD-MM-YYYY). Mostrarla al revés en la interfaz sería
 * reintroducirla justo donde alguien valida a ojo.
 */
export function diaMes(iso: string): string {
  const [, mes, dia] = iso.split("-");
  return `${dia}/${mes}`;
}

export function Tag({ kind, children }: { kind?: "ok" | "bad" | "warn"; children: React.ReactNode }) {
  return <span className={`tag ${kind ?? ""}`}>{children}</span>;
}

export function Banner({
  ok,
  children,
}: {
  ok: boolean;
  children: React.ReactNode;
}) {
  return <div className={`banner ${ok ? "ok" : "bad"}`}>{children}</div>;
}

/** Carga asíncrona con estados explícitos. Un error de la API se muestra, no se traga. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<{
    data: T | null;
    error: string | null;
    loading: boolean;
  }>({ data: null, error: null, loading: true });

  useEffect(() => {
    let vivo = true;
    setState((s) => ({ ...s, loading: true, error: null }));
    fn()
      .then((data) => vivo && setState({ data, error: null, loading: false }))
      .catch((e: Error) => vivo && setState({ data: null, error: e.message, loading: false }));
    return () => {
      vivo = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

export function Async<T>({
  state,
  children,
}: {
  state: { data: T | null; error: string | null; loading: boolean };
  children: (data: T) => React.ReactNode;
}) {
  if (state.loading) return <div className="empty">Cargando…</div>;
  if (state.error) return <div className="err">{state.error}</div>;
  if (!state.data) return <div className="empty">Sin datos.</div>;
  return <>{children(state.data)}</>;
}

export function Drawer({ onClose, title, children }: {
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <aside className="drawer">
      <button className="close" onClick={onClose} aria-label="Cerrar">×</button>
      <h3>{title}</h3>
      {children}
    </aside>
  );
}
