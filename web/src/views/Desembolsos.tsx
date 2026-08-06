import { useState } from "react";
import { api, type DisbursementBreakdown } from "../api";
import { Amount, Async, Drawer, Tag, useAsync } from "../ui";

/**
 * Desembolsos y su cierre.
 *
 * Acá se verifica el invariante del canal: `Σ (bruto − descuentos) == |giro|`.
 *
 * Los que NO cierran no son un error: son los desembolsos cuyos descuentos
 * todavía no están declarados (hay CSV de 4 de 56 días). `residual` muestra
 * exactamente cuánto falta — que es lo que la Fase 2 va a inferir. La vista lo
 * expone en vez de esconderlo.
 */
export function Desembolsos({ ledgerId }: { ledgerId: string }) {
  const state = useAsync(() => api.disbursements(ledgerId), [ledgerId]);
  const [abierto, setAbierto] = useState<DisbursementBreakdown | null>(null);
  const [soloAbiertos, setSoloAbiertos] = useState(false);

  return (
    <>
      <h1>Desembolsos</h1>
      <p className="sub">
        Cada giro al banco con las transacciones que lo componen. El agrupamiento usa el{" "}
        <code>disbursement_id</code> que declara Wompi: no es una inferencia, así que la
        ambigüedad de subconjuntos no aplica por esta vía.
      </p>

      <Async state={state}>
        {(data) => {
          const items = soloAbiertos ? data.items.filter((d) => !d.closes_to_zero) : data.items;
          return (
            <>
              <div className="cards" style={{ marginBottom: 18 }}>
                <div className="card">
                  <div className="meta">Desembolsos</div>
                  <div className="kpi">{data.total}</div>
                </div>
                <div className="card">
                  <div className="meta">Cierran en cero</div>
                  <div className="kpi pos">{data.cierran_en_cero}</div>
                  <div className="meta">tienen el desglose completo</div>
                </div>
                <div className="card">
                  <div className="meta">Sin desglose declarado</div>
                  <div className="kpi">{data.total - data.con_desglose_completo}</div>
                  <div className="meta">las comisiones habrá que inferirlas</div>
                </div>
              </div>

              <div className="toolbar">
                <label>
                  <input
                    type="checkbox"
                    checked={soloAbiertos}
                    onChange={(e) => setSoloAbiertos(e.target.checked)}
                  />{" "}
                  Solo los que no cierran
                </label>
                <span className="dim">{items.length} de {data.total}</span>
              </div>

              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Desembolso</th>
                      <th style={{ width: 100 }}>Acreditado</th>
                      <th style={{ width: 50 }}>Tx</th>
                      <th style={{ width: 150 }}>Bruto</th>
                      <th style={{ width: 140 }}>Descuentos</th>
                      <th style={{ width: 150 }}>Neto esperado</th>
                      <th style={{ width: 150 }}>Giro real</th>
                      <th style={{ width: 140 }}>Residual</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((d) => (
                      <tr key={d.disbursement_id} className="clickable" onClick={() => setAbierto(d)}>
                        <td>
                          <code>{d.disbursement_id}</code>{" "}
                          {d.closes_to_zero ? <Tag kind="ok">cierra</Tag> : <Tag kind="warn">falta desglose</Tag>}
                        </td>
                        <td className="num dim">{d.settled_on}</td>
                        <td className="num dim">{d.transaction_count}</td>
                        <td><Amount value={d.gross_total} colored={false} /></td>
                        <td><Amount value={d.declared_deductions} colored={false} /></td>
                        <td><Amount value={d.net_expected} colored={false} /></td>
                        <td><Amount value={d.settlement} colored={false} /></td>
                        <td className={`num ${d.closes_to_zero ? "pos" : "warn"}`}>
                          {d.residual.formatted}
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
        <Drawer title={`Desembolso ${abierto.disbursement_id}`} onClose={() => setAbierto(null)}>
          <div className="ladder" style={{ marginBottom: 18 }}>
            <div><span>Bruto de {abierto.transaction_count} transaccion(es)</span><span>{abierto.gross_total.formatted}</span></div>
            <div><span className="dim">− descuentos declarados</span><span className="dim">{abierto.declared_deductions.formatted}</span></div>
            <div className="total"><span>Neto esperado</span><span>{abierto.net_expected.formatted}</span></div>
            <div><span className="dim">Giro que hizo Wompi</span><span className="dim">{abierto.settlement.formatted}</span></div>
            <div className="total">
              <span>Residual</span>
              <span className={abierto.closes_to_zero ? "pos" : "warn"}>{abierto.residual.formatted}</span>
            </div>
          </div>

          {!abierto.closes_to_zero && (
            <p className="sub">
              El residual son las comisiones e impuestos que esta fuente todavía no
              declara. No es plata faltante: es desglose faltante. Inferirlo es trabajo
              de la conciliación de flujo.
            </p>
          )}

          <h3>Transacciones</h3>
          <ul style={{ paddingLeft: 18, margin: 0 }}>
            {abierto.transaction_ids.map((t) => (
              <li key={t}><code style={{ fontSize: 12 }}>{t}</code></li>
            ))}
          </ul>
        </Drawer>
      )}
    </>
  );
}
