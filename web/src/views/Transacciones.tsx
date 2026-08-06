import { useState } from "react";
import { api, type TransactionBreakdown } from "../api";
import { Amount, diaMes, Async, Drawer, Tag, useAsync } from "../ui";

/**
 * Transacciones del canal, descompuestas.
 *
 * Verificación visual del reparto disjunto entre fuentes (ADR-0008): el bruto
 * viene de la API de transacciones, los descuentos del CSV de desembolsos, y
 * la liquidación de otro endpoint. Son tres fuentes distintas para el mismo
 * hecho económico.
 *
 * `settlement_scope` distingue dos situaciones que se ven iguales:
 *   "transaction" → el canal liquida por venta (POS), y la venta cierra sola;
 *   "batch"       → el canal consolida (Wompi), y el cierre se verifica en la
 *                   vista de desembolsos, no acá;
 *   null          → la venta no se liquidó: rechazada o pendiente.
 */
export function Transacciones({ ledgerId }: { ledgerId: string }) {
  const state = useAsync(() => api.transactions(ledgerId), [ledgerId]);
  const [abierto, setAbierto] = useState<TransactionBreakdown | null>(null);
  const [filtro, setFiltro] = useState<"todas" | "con-desglose" | "sin-liquidar">("todas");

  return (
    <>
      <h1>Transacciones</h1>
      <p className="sub">
        Cada venta con su descomposición. Las piezas vienen de fuentes distintas y
        ninguna se cuenta dos veces.
      </p>

      <Async state={state}>
        {(data) => {
          const items = data.items.filter((t) =>
            filtro === "con-desglose" ? t.has_declared_deductions
            : filtro === "sin-liquidar" ? t.settlement_scope === null
            : true,
          );
          return (
            <>
              <div className="cards" style={{ marginBottom: 18 }}>
                <div className="card">
                  <div className="meta">Transacciones</div>
                  <div className="kpi">{data.total}</div>
                </div>
                <div className="card">
                  <div className="meta">Con desglose declarado</div>
                  <div className="kpi pos">{data.con_desglose_declarado}</div>
                  <div className="meta">el resto habrá que inferirlo</div>
                </div>
                <div className="card">
                  <div className="meta">Sin liquidar</div>
                  <div className="kpi">{data.sin_liquidar}</div>
                  <div className="meta">rechazadas o con error: nunca llegaron al banco</div>
                </div>
              </div>

              <div className="toolbar">
                {(["todas", "con-desglose", "sin-liquidar"] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFiltro(f)}
                    style={{
                      background: filtro === f ? "var(--panel-2)" : "transparent",
                      border: "1px solid var(--line)",
                      borderRadius: 6,
                      padding: "5px 11px",
                      color: filtro === f ? "var(--accent)" : "var(--dim)",
                    }}
                  >
                    {f}
                  </button>
                ))}
                <span className="dim">{items.length} de {data.total}</span>
              </div>

              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Transacción</th>
                      <th style={{ width: 92 }}>Fecha</th>
                      <th style={{ width: 90 }}>Estado</th>
                      <th style={{ width: 145 }}>Bruto</th>
                      <th style={{ width: 130 }}>Descuentos</th>
                      <th style={{ width: 145 }}>Neto esperado</th>
                      <th style={{ width: 130 }}>Liquidación</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((t) => (
                      <tr key={t.transaction_id} className="clickable" onClick={() => setAbierto(t)}>
                        <td><code style={{ fontSize: 11 }}>{t.transaction_id}</code></td>
                        <td className="num dim">{diaMes(t.occurred_on)}</td>
                        <td>
                          {t.status === "approved"
                            ? <span className="dim">{t.status}</span>
                            : <Tag kind="warn">{t.status}</Tag>}
                        </td>
                        <td><Amount value={t.gross} colored={false} /></td>
                        <td>
                          {t.has_declared_deductions
                            ? <Amount value={t.total_deductions} colored={false} />
                            : <span className="dim">a inferir</span>}
                        </td>
                        <td><Amount value={t.net_expected} colored={false} /></td>
                        <td>
                          {t.settlement_scope === "batch" ? (
                            <Tag>batch #{String(t.disbursement_id)}</Tag>
                          ) : t.settlement_scope === "transaction" ? (
                            <Tag kind={t.closes_to_zero ? "ok" : "bad"}>
                              {t.closes_to_zero ? "cierra" : "no cierra"}
                            </Tag>
                          ) : (
                            <Tag kind="warn">sin liquidar</Tag>
                          )}
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
        <Drawer title={abierto.transaction_id} onClose={() => setAbierto(null)}>
          <div className="ladder" style={{ marginBottom: 18 }}>
            <div>
              <span>Bruto cobrado</span>
              <span>{abierto.gross?.formatted ?? "—"}</span>
            </div>
            {abierto.deductions.map((d) => (
              <div key={d.id}>
                <span className="dim">{d.description}</span>
                <span className="neg">{d.amount.formatted}</span>
              </div>
            ))}
            {!abierto.has_declared_deductions && (
              <div><span className="dim">descuentos</span><span className="dim">no declarados</span></div>
            )}
            <div className="total">
              <span>Neto esperado</span>
              <span>{abierto.net_expected.formatted}</span>
            </div>
          </div>

          <dl className="kv">
            <dt>Estado</dt><dd>{abierto.status}</dd>
            <dt>Liquidación</dt>
            <dd>
              {abierto.settlement_scope === "batch"
                ? `batch — desembolso ${abierto.disbursement_id}`
                : abierto.settlement_scope === "transaction"
                  ? "por venta"
                  : "no liquidada"}
            </dd>
            {abierto.settlement && (<><dt>Giro</dt><dd>{abierto.settlement.formatted}</dd></>)}
          </dl>

          {abierto.settlement_scope === "batch" && (
            <p className="sub">
              Wompi consolida varias ventas en un solo giro, así que esta transacción no
              cierra sola: aporta su neto al desembolso{" "}
              <code>{String(abierto.disbursement_id)}</code>. El cierre se verifica ahí.
            </p>
          )}
          {abierto.settlement_scope === null && (
            <p className="sub">
              Esta venta no tiene desembolso asociado. Está en el ledger justamente para
              poder responder por qué no llegó al banco.
            </p>
          )}

          <h3 style={{ marginTop: 22 }}>Movimientos que la componen</h3>
          <div className="scroll" style={{ maxHeight: 260 }}>
            <table>
              <tbody>
                {abierto.movements.map((m) => (
                  <tr key={m.id}>
                    <td><code style={{ fontSize: 11 }}>{m.kind}</code></td>
                    <td className="dim" style={{ fontSize: 12 }}>{m.source_id}</td>
                    <td><Amount value={m.amount} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Drawer>
      )}
    </>
  );
}
