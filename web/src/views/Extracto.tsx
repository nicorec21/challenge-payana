import { useState } from "react";
import { api } from "../api";
import { Amount, diaMes, Async, Banner, Tag, useAsync } from "../ui";

/**
 * El extracto bancario reconstruido en ORDEN DE DOCUMENTO.
 *
 * Es la vista que valida el parser: si esto se ve igual que el PDF, el parseo
 * está bien. El ledger ordena por (fecha, id) —determinista— y ese no es el
 * orden del extracto; la cadena de saldos solo existe en orden de documento.
 *
 * Se muestran las DOS columnas de saldo: la que declara el banco y la que
 * calcula el sistema acumulando movimientos. Que coincidan es el invariante de
 * ADR-0007, y mostrarlas permite verlo en vez de confiar.
 */
export function Extracto({ ledgerId }: { ledgerId: string }) {
  const periodos = useAsync(() => api.statementPeriods(ledgerId), [ledgerId]);
  const [periodo, setPeriodo] = useState<string | null>(null);
  const elegido = periodo ?? periodos.data?.periods.at(-1) ?? null;
  const extracto = useAsync(
    () => (elegido ? api.statement(ledgerId, elegido) : Promise.resolve(null)),
    [ledgerId, elegido],
  );

  return (
    <>
      <h1>Extracto bancario</h1>
      <p className="sub">
        En orden de documento, como lo emite el banco. Si esto coincide con el PDF,
        el parser está bien.
      </p>

      <Async state={periodos}>
        {(p) => (
          <div className="toolbar">
            <label className="dim">Período</label>
            <select value={elegido ?? ""} onChange={(e) => setPeriodo(e.target.value)}>
              {p.periods.map((x) => (
                <option key={x} value={x}>{x}</option>
              ))}
            </select>
          </div>
        )}
      </Async>

      {extracto.data && (
        <>
          <Banner ok={extracto.data.chain_intact}>
            {extracto.data.chain_intact ? (
              <>
                <strong>Cadena de saldos intacta.</strong> Las {extracto.data.line_count}{" "}
                líneas encadenan: cada saldo declarado por el banco coincide con el que
                calcula el sistema. Cuenta <code>{extracto.data.account_number}</code>.
              </>
            ) : (
              <>
                <strong>La cadena de saldos se rompe.</strong> Alguna línea se perdió o se
                leyó mal. Las filas marcadas en rojo son las que no encadenan.
              </>
            )}
          </Banner>

          <div className="cards" style={{ marginBottom: 18 }}>
            <div className="card">
              <div className="meta">Saldo anterior</div>
              <div className="kpi">{extracto.data.opening_balance.formatted}</div>
            </div>
            <div className="card">
              <div className="meta">Saldo actual</div>
              <div className="kpi">{extracto.data.closing_balance.formatted}</div>
            </div>
            <div className="card">
              <div className="meta">Líneas</div>
              <div className="kpi">{extracto.data.line_count}</div>
            </div>
          </div>
        </>
      )}

      <Async state={extracto}>
        {(data) =>
          data ? (
            <div className="scroll">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 44 }}>#</th>
                    <th style={{ width: 92 }}>Fecha</th>
                    <th>Descripción</th>
                    <th style={{ width: 150 }}>Valor</th>
                    <th style={{ width: 165 }}>Saldo (banco)</th>
                    <th style={{ width: 165 }}>Saldo (calculado)</th>
                    <th style={{ width: 60 }}>Pág.</th>
                  </tr>
                </thead>
                <tbody>
                  {data.lines.map((l) => (
                    <tr key={l.movement_id} style={l.chain_ok ? undefined : { background: "rgba(247,118,142,.12)" }}>
                      <td className="num dim">{l.position}</td>
                      <td className="num dim">{diaMes(l.occurred_on)}</td>
                      <td>
                        {l.description}
                        {/(WOMPI)/i.test(l.description) && (
                          <> <Tag kind="ok">wompi</Tag></>
                        )}
                      </td>
                      <td><Amount value={l.amount} /></td>
                      <td className="num dim">{l.running_balance.formatted}</td>
                      <td className={`num ${l.chain_ok ? "dim" : "neg"}`}>
                        {l.chain_ok ? "✓" : l.expected_balance.formatted}
                      </td>
                      <td className="num dim">{l.page ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">Elegí un período.</div>
          )
        }
      </Async>
    </>
  );
}
