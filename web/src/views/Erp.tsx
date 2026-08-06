import { useState } from "react";
import { api, type ErpFinding } from "../api";
import { Amount, Async, Banner, diaMes, Drawer, Tag, useAsync } from "../ui";

/**
 * Conciliación contra el libro contable de Odoo.
 *
 * Pregunta **contable**, distinta de la de flujo: no interesa de qué canal vino
 * la plata —eso ya lo resolvió la Fase 2— sino si el system of record refleja
 * lo que pasó.
 *
 * La vista se organiza por gravedad, no por volumen:
 *
 *   1. la cobertura, que es el titular
 *   2. lo registrado con otro monto: el ERP tiene el asiento, mal
 *   3. lo que falta registrar, AGRUPADO por tipo
 *   4. lo que el ERP registra sin respaldo, y lo que está sin confirmar
 *   5. las coincidencias, con los dos identificadores
 *
 * El punto 3 se agrupa a propósito: «el ERP no registra ninguna comisión» es
 * una conclusión; 27 filas de comisión suelta son la misma información
 * convertida en ruido.
 */

const ETIQUETA: Record<string, string> = {
  matched: "coincide",
  amount_mismatch: "otro monto",
  missing_in_erp: "falta en el ERP",
  missing_in_ledger: "sin respaldo",
  not_posted: "sin confirmar",
  out_of_coverage: "fuera de cobertura",
};

const TIPO: Record<string, string> = {
  payment: "Ventas cobradas",
  fee: "Comisiones",
  tax: "IVA y retenciones",
  settlement: "Giros al banco",
  bank_credit: "Créditos bancarios",
  bank_debit: "Débitos bancarios",
  asiento: "Asientos",
};

const TONO: Record<string, "ok" | "warn" | "bad" | undefined> = {
  matched: "ok",
  amount_mismatch: "bad",
  missing_in_erp: "warn",
  missing_in_ledger: "bad",
  not_posted: "warn",
  out_of_coverage: undefined,
};

export function Erp({ ledger }: { ledger: string }) {
  const state = useAsync(() => api.erp(ledger), [ledger]);
  const [abierto, setAbierto] = useState<ErpFinding | null>(null);
  const [filtro, setFiltro] = useState("");

  return (
    <>
      <h1>Conciliación contra el ERP</h1>
      <p className="sub">
        ¿El libro contable de Odoo refleja lo que realmente pasó en {ledger}?
      </p>

      <Async state={state}>
        {(data) => {
          const items = filtro ? data.findings.filter((f) => f.status === filtro) : data.findings;
          const pct = Math.round(data.coverage_ratio * 100);
          const delLedger = data.findings.filter((f) => f.ledger_movement_id).length;

          return (
            <>
              <Banner ok={data.coverage_ratio >= 0.99}>
                {data.coverage_ratio >= 0.99 ? (
                  <>
                    <strong>El ERP refleja lo que pasó.</strong> Registra los{" "}
                    {delLedger} movimientos del período.
                  </>
                ) : (
                  <>
                    <strong>El ERP registra el {pct}% de los movimientos.</strong>{" "}
                    De {delLedger} hechos que ocurrieron, el libro contable de la
                    cuenta <code>{data.account_code}</code> tiene {data.counts.matched ?? 0}
                    {data.counts.missing_in_ledger ? (
                      <>
                        {" "}— y {data.counts.missing_in_ledger} asiento(s) que
                        registra <strong>sin que nada los respalde</strong>
                      </>
                    ) : null}
                    .
                  </>
                )}
              </Banner>

              <div className="cards" style={{ marginBottom: 18 }}>
                <div className="card">
                  <div className="meta">Cobertura del ERP</div>
                  <div className={`kpi ${pct >= 99 ? "pos" : pct >= 50 ? "" : "neg"}`}>
                    {pct}%
                  </div>
                  <div className="meta">
                    {data.counts.matched ?? 0} de {delLedger} movimientos
                  </div>
                </div>
                <div className="card">
                  <div className="meta">Conciliado</div>
                  <div className="kpi">{data.matched_amount.formatted}</div>
                  <div className="meta">cuenta {data.account_code} en Odoo</div>
                </div>
                <div className="card">
                  <div className="meta">A revisar</div>
                  <div className={`kpi ${data.problem_count ? "neg" : "pos"}`}>
                    {data.problem_count}
                  </div>
                  <div className="meta">discrepancias de cualquier tipo</div>
                </div>
              </div>

              {data.missing_in_erp_by_kind.length > 0 && (
                <>
                  <h2>Qué clase de hecho no se está contabilizando</h2>
                  <p className="sub">
                    Agrupado por tipo: importa más <em>qué</em> falta que la lista de casos.
                  </p>
                  <div className="scroll" style={{ marginBottom: 22 }}>
                    <table>
                      <thead>
                        <tr>
                          <th>Tipo</th>
                          <th style={{ width: 110 }}>Cantidad</th>
                          <th style={{ width: 200 }}>Monto sin registrar</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.missing_in_erp_by_kind.map((g) => (
                          <tr key={g.kind}>
                            <td>{TIPO[g.kind] ?? g.kind}</td>
                            <td className="num dim">{g.count}</td>
                            <td><Amount value={g.total} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              <h2>Línea por línea</h2>
              <div className="toolbar">
                {["", ...Object.keys(data.counts)].map((k) => (
                  <button
                    key={k || "todos"}
                    onClick={() => setFiltro(k)}
                    style={{
                      background: filtro === k ? "var(--panel-2)" : "transparent",
                      border: "1px solid var(--line)", borderRadius: 6,
                      padding: "5px 11px",
                      color: filtro === k ? "var(--accent)" : "var(--dim)",
                    }}
                  >
                    {k ? `${ETIQUETA[k] ?? k} (${data.counts[k]})` : "todos"}
                  </button>
                ))}
              </div>

              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th style={{ width: 74 }}>Fecha</th>
                      <th style={{ width: 130 }}>Estado</th>
                      <th style={{ width: 120 }}>Tipo</th>
                      <th style={{ width: 155 }}>Real</th>
                      <th style={{ width: 155 }}>En el ERP</th>
                      <th style={{ width: 145 }}>Asiento</th>
                      <th>Emparejado por</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((f) => (
                      <tr key={f.id} className="clickable" onClick={() => setAbierto(f)}>
                        <td className="num dim">
                          {f.occurred_on ? diaMes(f.occurred_on) : "—"}
                        </td>
                        <td>
                          <Tag kind={TONO[f.status]}>{ETIQUETA[f.status] ?? f.status}</Tag>
                        </td>
                        <td className="dim">{TIPO[f.kind ?? ""] ?? f.kind ?? "—"}</td>
                        <td><Amount value={f.ledger_amount} colored={false} /></td>
                        <td><Amount value={f.book_amount} colored={false} /></td>
                        <td className="dim" style={{ fontSize: 12 }}>
                          {f.erp_move_name ?? "—"}
                        </td>
                        <td className="dim" style={{ fontSize: 12 }}>
                          {f.status === "matched"
                            ? f.explanation.rule_id.includes("reference")
                              ? "referencia"
                              : "monto y fecha"
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          );
        }}
      </Async>

      {abierto && (
        <Drawer
          title={ETIQUETA[abierto.status] ?? abierto.status}
          onClose={() => setAbierto(null)}
        >
          <p style={{ marginTop: 0 }}>{abierto.explanation.summary}</p>

          {abierto.ledger_amount && abierto.book_amount && (
            <div className="ladder" style={{ margin: "18px 0" }}>
              <div>
                <span>Lo que realmente ocurrió</span>
                <span>{abierto.ledger_amount.formatted}</span>
              </div>
              <div>
                <span className="dim">Lo que registra el ERP</span>
                <span className="dim">{abierto.book_amount.formatted}</span>
              </div>
              <div className="total">
                <span>Diferencia</span>
                <span className={abierto.difference?.cents ? "neg" : "pos"}>
                  {abierto.difference?.formatted ?? "—"}
                </span>
              </div>
            </div>
          )}

          {/*
            Los dos identificadores juntos. Es lo que el enunciado pide
            explícitamente: dejar claro que representan la misma cosa, y poder
            abrir cada uno en su sistema.
          */}
          <dl className="kv">
            <dt>Movimiento</dt>
            <dd>{abierto.ledger_movement_id ?? "— no existe —"}</dd>
            <dt>Línea contable</dt>
            <dd>{abierto.book_movement_id ?? "— no existe —"}</dd>
            {abierto.erp_line_id && (
              <>
                <dt>Línea en Odoo</dt>
                <dd>{abierto.erp_line_id}</dd>
              </>
            )}
            {abierto.erp_move_name && (
              <>
                <dt>Asiento</dt>
                <dd>{abierto.erp_move_name}</dd>
              </>
            )}
            <dt>Regla aplicada</dt>
            <dd>{abierto.explanation.rule_id}</dd>
            <dt>Confianza</dt>
            <dd>{abierto.explanation.confidence}</dd>
          </dl>

          {abierto.explanation.window && (
            <>
              <h3 style={{ marginTop: 22 }}>Ventana considerada</h3>
              <p className="sub" style={{ margin: 0 }}>
                {abierto.explanation.window.start} → {abierto.explanation.window.end}{" "}
                ({abierto.explanation.window.rule})
              </p>
            </>
          )}

          {abierto.status === "not_posted" && (
            <p className="sub" style={{ marginTop: 18 }}>
              Un asiento en borrador o anulado no forma parte del libro formal, así
              que no se compara contra nada. Alguien tiene que confirmarlo o
              descartarlo.
            </p>
          )}
          {abierto.status === "missing_in_ledger" && (
            <p className="sub" style={{ marginTop: 18 }}>
              El ERP registra este hecho pero ninguna fuente observada lo respalda.
              Un libro que registra algo que no pasó es tan problema como uno al que
              le falta un registro.
            </p>
          )}
        </Drawer>
      )}
    </>
  );
}
