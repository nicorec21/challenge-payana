import { useState } from "react";
import { api, type MovementView } from "../api";
import { Amount, diaMes, Async, Drawer, Tag, useAsync } from "../ui";

/**
 * Explorador de movimientos con trazabilidad.
 *
 * El panel de detalle muestra `raw_ref`: el puntero al byte del que salió el
 * movimiento. Es lo que permite verificar cualquier afirmación del sistema
 * contra el archivo original.
 */
export function Movimientos({ ledgerId }: { ledgerId: string }) {
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [abierto, setAbierto] = useState<MovementView | null>(null);

  const state = useAsync(
    () => api.movements(ledgerId, { kind, status, q, limit: 500 }),
    [ledgerId, kind, status, q],
  );
  const kinds = useAsync(
    () => fetch("/api/kinds").then((r) => r.json() as Promise<{ kinds: string[]; statuses: string[] }>),
    [],
  );

  return (
    <>
      <h1>Movimientos</h1>
      <p className="sub">
        El modelo canónico. Los rechazados están acá a propósito: explicar por qué una
        venta <em>no</em> llegó al banco requiere tenerla.
      </p>

      <div className="toolbar">
        <input
          type="search"
          placeholder="Buscar en descripción o referencia…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">todos los tipos</option>
          {kinds.data?.kinds.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">todos los estados</option>
          {kinds.data?.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        {state.data && (
          <span className="dim">
            {state.data.items.length} de {state.data.total}
          </span>
        )}
      </div>

      <Async state={state}>
        {(data) => (
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th style={{ width: 92 }}>Fecha</th>
                  <th>Descripción</th>
                  <th style={{ width: 110 }}>Tipo</th>
                  <th style={{ width: 90 }}>Estado</th>
                  <th style={{ width: 160 }}>Monto</th>
                  <th style={{ width: 200 }}>Fuente</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((m) => (
                  <tr key={m.id} className="clickable" onClick={() => setAbierto(m)}>
                    <td className="num dim">{diaMes(m.occurred_on)}</td>
                    <td>{m.description || <span className="dim">—</span>}</td>
                    <td><code className="dim">{m.kind}</code></td>
                    <td>
                      {m.counts_for_reconciliation
                        ? <span className="dim">{m.status}</span>
                        : <Tag kind="warn">{m.status}</Tag>}
                    </td>
                    <td>
                      <Amount value={m.amount} colored={m.counts_for_reconciliation} />
                    </td>
                    <td><code className="dim" style={{ fontSize: 11 }}>{m.source_id}</code></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>

      {abierto && (
        <Drawer title={abierto.description || abierto.kind} onClose={() => setAbierto(null)}>
          <dl className="kv">
            <dt>ID canónico</dt><dd>{abierto.id}</dd>
            <dt>ID en la fuente</dt><dd>{abierto.external_id}</dd>
            <dt>Fuente</dt><dd>{abierto.source_id}</dd>
            <dt>Fecha contable</dt><dd>{abierto.occurred_on}</dd>
            {abierto.occurred_at && (<><dt>Momento exacto</dt><dd>{abierto.occurred_at}</dd></>)}
            <dt>Monto</dt><dd>{abierto.amount.formatted}</dd>
            <dt>Tipo · estado</dt><dd>{abierto.kind} · {abierto.status}</dd>
            {abierto.reference && (<><dt>Referencia</dt><dd>{abierto.reference}</dd></>)}
            <dt>Concilia</dt>
            <dd>{abierto.counts_for_reconciliation ? "sí" : "no — no movió plata"}</dd>
          </dl>

          <h3 style={{ marginTop: 22 }}>Evidencia</h3>
          <p className="sub" style={{ margin: 0 }}>
            De qué byte salió este movimiento. Se puede abrir y verificar.
          </p>
          <pre>{abierto.raw_ref ?? "sin referencia"}</pre>

          <h3 style={{ marginTop: 22 }}>Metadata de la fuente</h3>
          <p className="sub" style={{ margin: 0 }}>
            Lo que la fuente declaró y no entra en el modelo canónico. Se preserva para
            poder explicar.
          </p>
          <pre>{JSON.stringify(abierto.metadata, null, 2)}</pre>
        </Drawer>
      )}
    </>
  );
}
