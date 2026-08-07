/** Piezas compartidas. Nada acá calcula plata: solo renderiza `formatted`. */

import { useEffect, useState } from "react";
import type { Money } from "./api";

/**
 * Un monto. `formatted` viene del backend; `cents` solo decide el color.
 * Formatear en el front haría que la web y el CLI puedan mostrar el mismo
 * monto distinto.
 */
export function Amount({ value, colored = true }: { value: Money | null; colored?: boolean }) {
  if (!value) return <span className="dim">—</span>;
  const cls = colored && value.cents !== 0 ? (value.cents > 0 ? "pos" : "neg") : "";
  return <span className={`num ${cls}`}>{value.formatted}</span>;
}

/**
 * `2026-04-01` → `01/04`.
 *
 * Día/mes, no mes/día: en Colombia `04-01` se lee "4 de enero", y la
 * ambigüedad DD-MM vs MM-DD ya es una trampa documentada de este dominio.
 */
export function diaMes(iso: string): string {
  const [, mes, dia] = iso.split("-");
  return `${dia}/${mes}`;
}

export function fechaLarga(iso: string): string {
  const [a, m, d] = iso.split("-");
  return `${d}/${m}/${a}`;
}

export function Badge({
  tono,
  plain,
  children,
}: {
  tono?: "ok" | "warn" | "bad";
  plain?: boolean;
  children: React.ReactNode;
}) {
  return <span className={`badge ${tono ?? ""} ${plain ? "plain" : ""}`}>{children}</span>;
}

/** De qué adapter salió un dato. Responde «¿de dónde viene esto?». */
export function Origen({ source }: { source: string }) {
  return <span className="origen">{NOMBRE_FUENTE[source] ?? source}</span>;
}

const NOMBRE_FUENTE: Record<string, string> = {
  wompi_api_transactions: "API · ventas",
  wompi_api_disbursements: "API · giros",
  wompi_disbursement_csv: "CSV · comisiones",
  bancolombia_pdf: "PDF · extracto",
  odoo_account_1110001: "Odoo · 1110001",
  odoo_account_111001: "Odoo · 111001",
  pos_asobancaria_2001: "POS · Asobancaria",
};

export function Banner({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <div className={`banner ${ok ? "ok" : "bad"}`}>{children}</div>;
}

/** Carga asíncrona con estados explícitos. Un error de la API se muestra. */
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
  if (state.error) return <div className="err">{state.error}</div>;
  if (!state.data) return <div className="empty">{state.loading ? "Cargando…" : "Sin datos."}</div>;
  return <>{children(state.data)}</>;
}

export function Drawer({
  onClose,
  title,
  children,
}: {
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

/**
 * Paginación del lado del servidor.
 *
 * Muestra una ventana de páginas alrededor de la actual: con 86 páginas,
 * listarlas todas es peor que no tener paginación.
 */
export function Paginacion({
  page,
  pages,
  total,
  unit,
  onPage,
}: {
  page: number;
  pages: number;
  total: number;
  unit: string;
  onPage: (p: number) => void;
}) {
  if (pages <= 1) {
    return (
      <div className="paginacion">
        <span>{total} {unit}</span>
      </div>
    );
  }

  const ventana: number[] = [];
  const desde = Math.max(1, Math.min(page - 2, pages - 4));
  for (let p = desde; p <= Math.min(pages, desde + 4); p++) ventana.push(p);

  return (
    <div className="paginacion">
      <span>
        {total} {unit} · página {page} de {pages}
      </span>
      <div className="pasos">
        <button onClick={() => onPage(page - 1)} disabled={page <= 1}>‹</button>
        {desde > 1 && (
          <>
            <button onClick={() => onPage(1)}>1</button>
            {desde > 2 && <span className="dim">…</span>}
          </>
        )}
        {ventana.map((p) => (
          <button key={p} className={p === page ? "on" : ""} onClick={() => onPage(p)}>
            {p}
          </button>
        ))}
        {desde + 4 < pages && (
          <>
            {desde + 5 < pages && <span className="dim">…</span>}
            <button onClick={() => onPage(pages)}>{pages}</button>
          </>
        )}
        <button onClick={() => onPage(page + 1)} disabled={page >= pages}>›</button>
      </div>
    </div>
  );
}
