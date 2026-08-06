import { useState } from "react";
import { api, type FlowFinding } from "../api";
import { Amount, Async, Banner, diaMes, Drawer, Tag, useAsync } from "../ui";

/**
 * Conciliación de flujo canal → banco.
 *
 * La vista está organizada por la pregunta que responde, no por la estructura
 * de los datos:
 *
 *   1. ¿falta plata?           → el veredicto, arriba de todo
 *   2. ¿qué hay que revisar?   → los problemas, separados
 *   3. ¿qué NO puedo saber?    → fuera de cobertura, explícitamente aparte
 *   4. ¿qué cerró?             → el detalle, al final
 *
 * El punto 3 es el que más importa: un giro sin crédito bancario y un giro
 * fuera del período de datos se ven idénticos en una tabla y significan lo
 * contrario. Mezclarlos haría que el CFO vea faltantes que no faltan.
 */

const ETIQUETA: Record<string, string> = {
  matched: "conciliado",
  unmatched_settlement: "giro sin crédito",
  unmatched_bank: "crédito sin giro",
  out_of_coverage: "fuera de cobertura",
  ambiguous: "ambiguo",
};

const CONFIANZA: Record<string, { texto: string; tono?: "ok" | "warn" | "bad" }> = {
  exact: { texto: "exacta", tono: "ok" },
  high: { texto: "alta", tono: "ok" },
  medium: { texto: "media", tono: "warn" },
  low: { texto: "baja", tono: "bad" },
};

export function Conciliacion({ canal, banco }: { canal: string; banco: string }) {
  const state = useAsync(() => api.flow(canal, banco), [canal, banco]);
  const [abierto, setAbierto] = useState<FlowFinding | null>(null);
  const [filtro, setFiltro] = useState<string>("");

  return (
    <>
      <h1>Conciliación de flujo</h1>
      <p className="sub">
        ¿La plata que {canal} declaró haber girado apareció en {banco}?
      </p>

      <Async state={state}>
        {(data) => {
          const items = filtro ? data.findings.filter((f) => f.status === filtro) : data.findings;
          const fuera = data.counts.out_of_coverage ?? 0;
          return (
            <>
              <Banner ok={data.problem_count === 0}>
                {data.problem_count === 0 ? (
                  <>
                    <strong>Todo el dinero del período está explicado.</strong> Se
                    conciliaron {data.counts.matched ?? 0} giros por{" "}
                    {data.matched_amount.formatted}.
                  </>
                ) : (
                  <>
                    <strong>
                      {data.problem_count} caso(s) requieren revisión
                    </strong>{" "}
                    por un total de {data.disputed_amount.formatted}. Se conciliaron {data.counts.matched ?? 0} giros por{" "}
                    {data.matched_amount.formatted}.
                  </>
                )}
                {fuera > 0 && (
                  <>
                    {" "}
                    Hay {fuera} movimiento(s) fuera del período que cubren los datos:{" "}
                    <strong>no son faltantes de plata, son faltantes de información.</strong>
                  </>
                )}
              </Banner>

              <div className="cards" style={{ marginBottom: 18 }}>
                <div className="card">
                  <div className="meta">Conciliado</div>
                  <div className="kpi pos">{data.matched_amount.formatted}</div>
                  <div className="meta">{data.counts.matched ?? 0} giros</div>
                </div>
                <div className="card">
                  <div className="meta">A revisar</div>
                  <div className={`kpi ${data.problem_count ? "neg" : "pos"}`}>
                    {data.problem_count}
                  </div>
                  <div className="meta">exigen acción de alguien</div>
                </div>
                <div className="card">
                  <div className="meta">Fuera de cobertura</div>
                  <div className="kpi">{fuera}</div>
                  <div className="meta">el sistema no puede opinar</div>
                </div>
                <div className="card">
                  <div className="meta">Confianza</div>
                  <div style={{ marginTop: 10, display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {Object.entries(data.by_confidence).map(([k, n]) => (
                      <Tag key={k} kind={CONFIANZA[k]?.tono}>
                        {CONFIANZA[k]?.texto ?? k}: {n}
                      </Tag>
                    ))}
                  </div>
                  <div className="meta" style={{ marginTop: 8 }}>
                    <em>exacta</em> = desglose declarado por el canal
                  </div>
                </div>
              </div>

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
                      <th style={{ width: 80 }}>Fecha</th>
                      <th style={{ width: 150 }}>Estado</th>
                      <th style={{ width: 155 }}>Giro del canal</th>
                      <th style={{ width: 155 }}>Acreditado</th>
                      <th style={{ width: 60 }}>Ventas</th>
                      <th style={{ width: 95 }}>Confianza</th>
                      <th>Explicación</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((f) => (
                      <tr key={f.id} className="clickable" onClick={() => setAbierto(f)}>
                        <td className="num dim">{f.occurred_on ? diaMes(f.occurred_on) : "—"}</td>
                        <td>
                          <Tag
                            kind={
                              f.status === "matched" ? "ok"
                              : f.status === "out_of_coverage" ? undefined
                              : "bad"
                            }
                          >
                            {ETIQUETA[f.status] ?? f.status}
                          </Tag>
                        </td>
                        <td><Amount value={f.settlement_amount} colored={false} /></td>
                        <td><Amount value={f.bank_amount} colored={false} /></td>
                        <td className="num dim">{f.transaction_ids.length || "—"}</td>
                        <td>
                          <Tag kind={CONFIANZA[f.explanation.confidence]?.tono}>
                            {CONFIANZA[f.explanation.confidence]?.texto}
                          </Tag>
                        </td>
                        <td className="dim" style={{ fontSize: 12 }}>
                          {f.explanation.summary.slice(0, 90)}…
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

          {abierto.explanation.gross && (
            <div className="ladder" style={{ margin: "18px 0" }}>
              <div>
                <span>Bruto cobrado</span>
                <span>{abierto.explanation.gross.formatted}</span>
              </div>
              {abierto.explanation.adjustments.map((a, i) => (
                <div key={i}>
                  <span className="dim">
                    {a.kind}{" "}
                    {a.source !== "declared" && <em style={{ fontSize: 11 }}>(estimado)</em>}
                  </span>
                  <span className="neg">−{a.amount.formatted}</span>
                </div>
              ))}
              <div className="total">
                <span>Acreditado en el banco</span>
                <span>{abierto.explanation.net?.formatted ?? "—"}</span>
              </div>
              {abierto.explanation.unexplained && (
                <div className="total">
                  <span>Sin explicar</span>
                  <span className="neg">{abierto.explanation.unexplained.formatted}</span>
                </div>
              )}
            </div>
          )}

          <dl className="kv">
            <dt>Regla aplicada</dt><dd>{abierto.explanation.rule_id}</dd>
            <dt>Confianza</dt>
            <dd>{CONFIANZA[abierto.explanation.confidence]?.texto}</dd>
            <dt>La explicación cierra</dt>
            <dd>{abierto.explanation.is_balanced ? "sí" : "no"}</dd>
            {abierto.explanation.window && (
              <>
                <dt>Ventana buscada</dt>
                <dd>
                  {abierto.explanation.window.start} → {abierto.explanation.window.end}
                </dd>
                <dt>Regla de ventana</dt>
                <dd style={{ fontFamily: "inherit" }}>{abierto.explanation.window.rule}</dd>
              </>
            )}
            <dt>Movimientos del canal</dt>
            <dd>{abierto.explanation.source_movement_ids.length}</dd>
            <dt>Movimientos del banco</dt>
            <dd>{abierto.explanation.target_movement_ids.length}</dd>
          </dl>

          {abierto.explanation.alternatives.length > 0 && (
            <>
              <h3 style={{ marginTop: 22 }}>Qué descartó el sistema</h3>
              <p className="sub" style={{ margin: 0 }}>
                Exponer lo descartado es lo que convierte un match en un argumento.
              </p>
              {abierto.explanation.alternatives.map((alt, i) => (
                <div key={i} style={{ marginTop: 10, fontSize: 13 }}>
                  <div>{alt.description}</div>
                  <div className="dim" style={{ marginTop: 3 }}>{alt.rejected_because}</div>
                </div>
              ))}
            </>
          )}

          <h3 style={{ marginTop: 22 }}>Movimientos relacionados</h3>
          <pre>
            {JSON.stringify(
              {
                canal: abierto.explanation.source_movement_ids,
                banco: abierto.explanation.target_movement_ids,
              },
              null,
              2,
            )}
          </pre>
        </Drawer>
      )}
    </>
  );
}
