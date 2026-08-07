import { api, type ErpReport, type FlowReport, type SystemView } from "../api";
import { Async, Badge, Banner, useAsync } from "../ui";

/**
 * Panorama general.
 *
 * Responde la pregunta del enunciado de arriba hacia abajo: qué esperábamos
 * recibir, qué llegó, qué falta, y qué dice el ERP.
 *
 * Maqueta: los números y su jerarquía son los definitivos; el diseño se pule
 * después.
 */
export function Dashboard() {
  const sistema = useAsync(() => api.system(), []);
  const flujo = useAsync(() => api.flow("wompi", "bancolombia"), []);
  const erpWompi = useAsync(() => api.erp("wompi"), []);
  const erpBanco = useAsync(() => api.erp("bancolombia"), []);

  return (
    <>
      <h1>Panorama</h1>
      <p className="sub">
        Qué plata esperábamos recibir, qué llegó al banco, qué falta y qué dice el ERP.
      </p>

      <Async state={flujo}>{(f) => <Veredicto flujo={f} />}</Async>

      <h2>Conciliación de flujo · canal → banco</h2>
      <Async state={flujo}>{(f) => <FlujoKpis flujo={f} />}</Async>

      <h2>Conciliación contra el ERP</h2>
      <p className="sub">¿El libro contable de Odoo refleja lo que pasó?</p>
      <div className="cards">
        <Async state={erpWompi}>{(e) => <ErpKpi erp={e} />}</Async>
        <Async state={erpBanco}>{(e) => <ErpKpi erp={e} />}</Async>
      </div>

      <h2>Fuentes</h2>
      <Async state={sistema}>{(s) => <Fuentes sistema={s} />}</Async>
    </>
  );
}

function Veredicto({ flujo }: { flujo: FlowReport }) {
  const fuera = flujo.counts.out_of_coverage ?? 0;
  return (
    <Banner ok={flujo.problem_count === 0}>
      {flujo.problem_count === 0 ? (
        <>
          <strong>Todo el dinero del período está explicado.</strong> Se conciliaron{" "}
          {flujo.counts.matched ?? 0} giros por {flujo.matched_amount.formatted}.
        </>
      ) : (
        <>
          <strong>{flujo.problem_count} caso(s) requieren revisión</strong> por{" "}
          {flujo.disputed_amount.formatted}. Se conciliaron {flujo.counts.matched ?? 0} giros
          por {flujo.matched_amount.formatted}.
        </>
      )}
      {fuera > 0 && (
        <>
          {" "}
          Hay {fuera} movimiento(s) fuera del período que cubren los datos:{" "}
          <strong>falta información, no plata.</strong>
        </>
      )}
    </Banner>
  );
}

function FlujoKpis({ flujo }: { flujo: FlowReport }) {
  const exactos = flujo.by_confidence.exact ?? 0;
  const altos = flujo.by_confidence.high ?? 0;
  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="meta">Coinciden exacto</div>
          <div className="kpi pos">{exactos}</div>
          <div className="meta">cada peso respaldado por un dato declarado</div>
        </div>
        <div className="card">
          <div className="meta">Coinciden con estimación</div>
          <div className="kpi">{altos}</div>
          <div className="meta">comisiones inferidas, ±1 centavo por venta</div>
        </div>
        <div className="card">
          <div className="meta">A revisar</div>
          <div className={`kpi ${flujo.problem_count ? "neg" : "pos"}`}>
            {flujo.problem_count}
          </div>
          <div className="meta">{flujo.disputed_amount.formatted}</div>
        </div>
        <div className="card">
          <div className="meta">Conciliado</div>
          <div className="kpi">{flujo.matched_amount.formatted}</div>
          <div className="meta">
            {flujo.coverage.overlap
              ? `${flujo.coverage.overlap.from} → ${flujo.coverage.overlap.to}`
              : "sin período en común"}
          </div>
        </div>
      </div>

      <div className="tabla" style={{ marginTop: 16 }}>
        <table>
          <thead>
            <tr>
              <th>Estado</th>
              <th style={{ width: 90 }}>Casos</th>
              <th>Qué significa</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(flujo.counts).map(([k, n]) => (
              <tr key={k}>
                <td>
                  <Badge tono={k === "matched" ? "ok" : k === "out_of_coverage" ? undefined : "bad"}>
                    {ESTADO[k] ?? k}
                  </Badge>
                </td>
                <td className="num dim">{n}</td>
                <td className="dim" style={{ fontSize: 12 }}>{SIGNIFICA[k] ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

function ErpKpi({ erp }: { erp: ErpReport }) {
  const pct = Math.round(erp.coverage_ratio * 100);
  return (
    <div className="card">
      <h3>{erp.ledger_id}</h3>
      <div className="meta">cuenta {erp.account_code} en Odoo</div>
      <div className={`kpi ${pct >= 99 ? "pos" : pct >= 50 ? "" : "neg"}`}>{pct}%</div>
      <div className="meta">de los movimientos está registrado</div>
      <table style={{ marginTop: 10 }}>
        <tbody>
          <tr>
            <td style={{ paddingLeft: 0 }} className="dim">coinciden</td>
            <td className="num">{erp.counts.matched ?? 0}</td>
          </tr>
          <tr>
            <td style={{ paddingLeft: 0 }} className="dim">faltan en el ERP</td>
            <td className="num">{erp.counts.missing_in_erp ?? 0}</td>
          </tr>
          <tr>
            <td style={{ paddingLeft: 0 }} className="dim">sin respaldo</td>
            <td className="num">{erp.counts.missing_in_ledger ?? 0}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function Fuentes({ sistema }: { sistema: SystemView }) {
  return (
    <>
      <div className="cards" style={{ marginBottom: 14 }}>
        {sistema.ledgers.map((l) => (
          <div className="card" key={l.id}>
            <h3>{l.name}</h3>
            <div className="meta">
              <code>{l.id}</code> · {ROL[l.role] ?? l.role}
            </div>
            <div className={`kpi ${l.balance.cents < 0 ? "neg" : "pos"}`}>
              {l.balance.formatted}
            </div>
            <div className="meta">
              {l.role === "bank" ? "flujo neto del período" : "saldo"}
            </div>
            <div className="meta">
              {l.movement_count} movimientos
              {l.date_range && ` · ${l.date_range[0]} → ${l.date_range[1]}`}
            </div>
          </div>
        ))}
      </div>

      <div className="tabla">
        <table>
          <thead>
            <tr>
              <th>Fuente</th>
              <th>Ledger</th>
              <th>Connector</th>
              <th>Adapters</th>
              <th style={{ width: 120 }}>Transporte</th>
            </tr>
          </thead>
          <tbody>
            {sistema.sources.map((s) => (
              <tr key={s.name}>
                <td><code>{s.name}</code></td>
                <td className="dim">{s.ledger_id}</td>
                <td><code className="dim">{s.connector_id}</code></td>
                <td className="dim" style={{ fontSize: 12 }}>{s.adapters.join(", ")}</td>
                <td>
                  {s.requires_network ? <Badge tono="warn">red</Badge> : <Badge>archivo local</Badge>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

const ROL: Record<string, string> = {
  channel: "canal de cobro",
  bank: "cuenta bancaria",
  erp: "libro contable",
};

const ESTADO: Record<string, string> = {
  matched: "conciliado",
  unmatched_settlement: "giro sin crédito",
  unmatched_bank: "crédito sin giro",
  out_of_coverage: "fuera de cobertura",
  ambiguous: "ambiguo",
};

const SIGNIFICA: Record<string, string> = {
  matched: "el giro del canal apareció en el extracto bancario",
  unmatched_settlement: "salió plata del canal y no se encontró en el banco",
  unmatched_bank: "entró plata al banco sin origen identificado",
  out_of_coverage: "no se puede juzgar: falta data de una de las dos fuentes",
  ambiguous: "más de un crédito podría corresponder al giro",
};
