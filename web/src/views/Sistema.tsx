import { api } from "../api";
import { Amount, Async, Tag, useAsync } from "../ui";

/** Vista general: qué ledgers hay, con qué saldo, y de qué fuentes salen. */
export function Sistema() {
  const state = useAsync(() => api.system(), []);

  return (
    <>
      <h1>Sistema</h1>
      <p className="sub">Ledgers normalizados y fuentes que los alimentan.</p>

      <Async state={state}>
        {(data) => (
          <>
            <div className="cards">
              {data.ledgers.map((l) => (
                <div className="card" key={l.id}>
                  <h3>{l.name}</h3>
                  <div className="meta">
                    <code>{l.id}</code> · {l.role === "bank" ? "cuenta bancaria" : "canal"}
                  </div>

                  {/*
                    `balance()` suma los movimientos ingeridos. Eso es el saldo
                    real solo si se ingirió desde la apertura de la cuenta.
                    Para el banco tenemos una tajada de 4 meses, así que es
                    FLUJO NETO del período, no saldo. Llamarlo "saldo" invitaría
                    a compararlo contra el extracto y no coincide.
                  */}
                  <div className={`kpi ${l.balance.cents < 0 ? "neg" : "pos"}`}>
                    {l.balance.formatted}
                  </div>
                  <div className="meta">
                    {l.role === "bank"
                      ? "flujo neto del período ingerido"
                      : "saldo — plata cobrada y todavía no girada"}
                  </div>
                  <div className="meta">
                    {l.movement_count} movimientos
                    {l.date_range && ` · ${l.date_range[0]} → ${l.date_range[1]}`}
                  </div>

                  <table style={{ marginTop: 12 }}>
                    <tbody>
                      {l.breakdown.map((b) => (
                        <tr key={`${b.kind}-${b.status}`}>
                          <td style={{ paddingLeft: 0 }}>
                            <code>{b.kind}</code>{" "}
                            {b.status !== "approved" && <Tag kind="warn">{b.status}</Tag>}
                          </td>
                          <td className="num dim">{b.count}</td>
                          <td style={{ paddingRight: 0 }}>
                            <Amount value={b.total} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>

            <h2>Fuentes</h2>
            <p className="sub">
              Cada fuente es un <code>Connector</code> (cómo llegan los bytes) más los{" "}
              <code>Adapter</code> que los interpretan. Sumar una fuente nueva es agregar
              una fila acá y un adapter.
            </p>
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th>Fuente</th>
                    <th>Ledger</th>
                    <th>Connector</th>
                    <th>Adapters</th>
                    <th>Transporte</th>
                  </tr>
                </thead>
                <tbody>
                  {data.sources.map((s) => (
                    <tr key={s.name}>
                      <td><code>{s.name}</code></td>
                      <td className="dim">{s.ledger_id}</td>
                      <td><code className="dim">{s.connector_id}</code></td>
                      <td>
                        {s.adapters.map((a) => (
                          <code key={a} style={{ marginRight: 8 }}>{a}</code>
                        ))}
                      </td>
                      <td>
                        {s.requires_network ? <Tag kind="warn">red</Tag> : <Tag>archivo local</Tag>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <h2>Cobertura</h2>
            <p className="sub">
              Qué período tiene datos cada ledger. Es lo que permite distinguir{" "}
              <em>“no llegó la plata”</em> de <em>“no tengo datos de ese período”</em>: se
              ven idénticos y significan lo contrario.
            </p>
            <div className="scroll">
              <table>
                <thead>
                  <tr><th>Ledger</th><th>Desde</th><th>Hasta</th></tr>
                </thead>
                <tbody>
                  {Object.entries(data.coverage).map(([id, c]) => (
                    <tr key={id}>
                      <td><code>{id}</code></td>
                      <td className="num">{c.from ?? "—"}</td>
                      <td className="num">{c.to ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="sub" style={{ marginTop: 20 }}>
              contrato v{data.contract_version} · generado {data.generated_at.slice(0, 19).replace("T", " ")} UTC
            </p>
          </>
        )}
      </Async>
    </>
  );
}
