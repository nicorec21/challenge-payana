import { useState } from "react";
import { api, type Money, type PanoramaItem } from "../api";
import {
  Amount,
  Async,
  Badge,
  Drawer,
  fechaLarga,
  Origen,
  Paginacion,
  useAsync,
} from "../ui";

/**
 * Panorama de cada fuente de datos.
 *
 * Responde tres preguntas por fila y ninguna más: **qué trae la fuente**,
 * **de dónde salió el dato** y **si está conciliado**.
 *
 * El detalle de la conciliación tiene su propia vista. Acá el estado es un
 * aviso de una palabra: mezclar exploración con reporte hace que la vista no
 * sirva para ninguna de las dos cosas.
 */

type FuenteId = "wompi" | "bancolombia" | "odoo";

const FUENTES: { id: FuenteId; nombre: string; detalle: string }[] = [
  { id: "wompi", nombre: "Wompi", detalle: "Canal de cobro · API REST + reportes CSV" },
  { id: "bancolombia", nombre: "Bancolombia", detalle: "Cuenta bancaria · extractos PDF" },
  { id: "odoo", nombre: "Odoo", detalle: "Libro contable · XML-RPC" },
];

/**
 * Cómo se nombra cada estado.
 *
 * Las etiquetas anteriores («cierra», «falta desglose») describían el mecanismo
 * interno. Estas dicen qué le pasa al dato desde el punto de vista de quien
 * mira: si está verificado contra la otra punta, si falta verificarlo, si no
 * corresponde verificarlo, o si no hay con qué.
 */
const ESTADO: Record<
  string,
  { texto: string; tono?: "ok" | "warn" | "bad"; ayuda: string }
> = {
  conciliado: {
    texto: "Verificado",
    tono: "ok",
    ayuda: "Se encontró la contraparte en la otra fuente, con el mismo monto.",
  },
  sin_conciliar: {
    texto: "Sin verificar",
    tono: "bad",
    ayuda: "Debería tener contraparte y no se encontró. Requiere revisión.",
  },
  no_aplica: {
    texto: "No corresponde",
    ayuda:
      "Este dato queda fuera de lo que el sistema concilia: ventas rechazadas, " +
      "movimientos bancarios ajenos al canal, o asientos sin confirmar.",
  },
  sin_datos: {
    texto: "Falta información",
    tono: "warn",
    ayuda:
      "Cae fuera del período que cubre alguna de las fuentes necesarias. " +
      "No es un faltante de plata.",
  },
};

export function Fuentes() {
  const [fuente, setFuente] = useState<FuenteId>("wompi");
  const [page, setPage] = useState(1);
  const [estado, setEstado] = useState<string>("");
  const [q, setQ] = useState("");

  const state = useAsync(
    () => api.panorama(fuente, { page, size: 25, estado, q }),
    [fuente, page, estado, q],
  );
  const [abierto, setAbierto] = useState<PanoramaItem | null>(null);
  //: Qué desembolso está desplegado. Solo uno a la vez: con 25 filas por
  //: página, varios abiertos convierten la tabla en un scroll otra vez.
  const [expandido, setExpandido] = useState<string | null>(null);
  const [venta, setVenta] = useState<VentaResumen | null>(null);

  const cambiar = (f: FuenteId) => {
    setFuente(f);
    setPage(1);
    setEstado("");
    setQ("");
    setExpandido(null);
  };

  return (
    <>
      <h1>Fuentes de datos</h1>
      <p className="sub">
        Qué trae cada origen, de dónde salió cada dato y si está verificado contra
        la otra punta.
      </p>

      <div className="picker">
        {FUENTES.map((f) => (
          <button
            key={f.id}
            className={fuente === f.id ? "on" : ""}
            onClick={() => cambiar(f.id)}
          >
            <div className="nombre">{f.nombre}</div>
            <div className="detalle">{f.detalle}</div>
            {fuente === f.id && state.data && (
              <div className="conteo">
                {state.data.total} {state.data.unit}
              </div>
            )}
          </button>
        ))}
      </div>

      <Async state={state}>
        {(data) => (
          <>
            <div className="toolbar">
              <input
                type="search"
                placeholder="Buscar…"
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
              />
              <button
                className={`chip ${estado === "" ? "on" : ""}`}
                onClick={() => {
                  setEstado("");
                  setPage(1);
                }}
              >
                Todos ({data.total})
              </button>
              {Object.entries(data.counts).map(([k, n]) => (
                <button
                  key={k}
                  className={`chip ${estado === k ? "on" : ""}`}
                  onClick={() => {
                    setEstado(k);
                    setPage(1);
                  }}
                >
                  {ESTADO[k]?.texto ?? k} ({n})
                </button>
              ))}
            </div>

            <div className="tabla">
              <table>
                {/*
                  Anchos fijos para que el header y el contenido no se corran.
                  MONTO lleva 20%: un importe en pesos colombianos con miles y
                  moneda no entra en menos.
                */}
                <colgroup>
                  <col style={{ width: "36%" }} />
                  <col style={{ width: "11%" }} />
                  <col style={{ width: "20%" }} />
                  <col style={{ width: "18%" }} />
                  <col style={{ width: "15%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>{TITULO_COL[data.source_id] ?? "Registro"}</th>
                    <th>Fecha</th>
                    <th className="num">Monto</th>
                    <th>Origen del dato</th>
                    <th>Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((i) => (
                    <Fila
                      key={i.id}
                      item={i}
                      expandido={expandido === i.id}
                      onToggle={() =>
                        setExpandido(expandido === i.id ? null : i.id)
                      }
                      onAbrir={setAbierto}
                      onVenta={setVenta}
                    />
                  ))}
                  {data.items.length === 0 && (
                    <tr>
                      <td colSpan={5} className="empty">
                        No hay registros con ese filtro.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>

              <Paginacion
                page={data.page}
                pages={data.pages}
                total={data.filtered}
                unit={data.unit}
                onPage={setPage}
              />
            </div>
          </>
        )}
      </Async>

      {abierto && (
        <Detalle item={abierto} fuente={fuente} onClose={() => setAbierto(null)} />
      )}
      {venta && <DetalleVenta venta={venta} onClose={() => setVenta(null)} />}
    </>
  );
}

/** Una venta, como la devuelve el panorama dentro de su desembolso. */
interface VentaResumen {
  transaction_id: string;
  occurred_on: string;
  status: string;
  gross: Money | null;
  total_deductions: Money;
  net_expected: Money;
  has_declared_deductions: boolean;
  disbursement_id: string | number | null;
  deductions: { id: string; description: string; amount: Money }[];
  movements: { id: string; kind: string; amount: Money; source_id: string; raw_ref: string | null }[];
}

/**
 * Una fila del panorama. Para Wompi se despliega en sus ventas.
 *
 * Las filas hijas usan **las mismas columnas con el mismo significado**: la
 * fecha de la venta va bajo «Fecha», su monto bajo «Monto». La versión
 * anterior las reusaba con otro sentido y por eso todo se veía corrido.
 */
function Fila({
  item,
  expandido,
  onToggle,
  onAbrir,
  onVenta,
}: {
  item: PanoramaItem;
  expandido: boolean;
  onToggle: () => void;
  onAbrir: (i: PanoramaItem) => void;
  onVenta: (v: VentaResumen) => void;
}) {
  const ventas = (item.extra.transactions ?? []) as VentaResumen[];
  const desplegable = ventas.length > 0;
  const bruto = item.extra.gross_total as Money | undefined;
  const descuentos = item.extra.declared_deductions as Money | undefined;
  const completo = Boolean(item.extra.deductions_complete);

  return (
    <>
      <tr
        className={`clickable padre ${expandido ? "abierto" : ""}`}
        onClick={() => (desplegable ? onToggle() : onAbrir(item))}
      >
        <td>
          <div className="celda-principal">
            {desplegable && (
              <span className={`chevron ${expandido ? "abierto" : ""}`}>▶</span>
            )}
            {item.label}
          </div>
          {item.sublabel && <div className="celda-sub">{item.sublabel}</div>}
        </td>
        <td className="dim">{item.date ? fechaLarga(item.date) : "—"}</td>
        <td className="num"><Amount value={item.amount} /></td>
        <td>
          {item.origins.map((o) => (
            <Origen key={o} source={o} />
          ))}
        </td>
        <td>
          <Badge tono={ESTADO[item.reconciliation]?.tono}>
            {ESTADO[item.reconciliation]?.texto ?? item.reconciliation}
          </Badge>
        </td>
      </tr>

      {expandido &&
        ventas.map((v) => (
          <tr key={v.transaction_id} className="clickable hijo" onClick={() => onVenta(v)}>
            <td>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {v.transaction_id}
              </div>
              <div className="celda-sub">
                {v.has_declared_deductions
                  ? `comisiones ${v.total_deductions.formatted}`
                  : "comisiones no declaradas"}
              </div>
            </td>
            <td className="dim">{fechaLarga(v.occurred_on)}</td>
            <td className="num"><Amount value={v.gross} colored={false} /></td>
            <td>
              {[...new Set(v.movements.map((m) => m.source_id))].map((s) => (
                <Origen key={s} source={s} />
              ))}
            </td>
            <td>
              <Badge tono={ESTADO_VENTA[v.status]?.tono} plain>
                {ESTADO_VENTA[v.status]?.texto ?? v.status}
              </Badge>
            </td>
          </tr>
        ))}

      {expandido && bruto && (
        <tr className="resumen">
          {/*
            El total va bajo MONTO, que es su columna. Repartir la cuenta entre
            «Origen» y «Estado» —como estaba— es volver a usar columnas con un
            significado que no es el suyo.
          */}
          <td colSpan={2}>
            Bruto {bruto.formatted} menos comisiones e impuestos
            {completo ? ` (${descuentos?.formatted})` : " (estimados)"}
          </td>
          <td className="num total-fila">{item.amount.formatted}</td>
          <td colSpan={2} className="dim">acreditado en el banco</td>
        </tr>
      )}
    </>
  );
}

/** Estado de la venta en el canal. Distinto del estado de conciliación. */
const ESTADO_VENTA: Record<string, { texto: string; tono?: "ok" | "warn" | "bad" }> = {
  approved: { texto: "Cobrada" },
  declined: { texto: "Rechazada", tono: "bad" },
  error: { texto: "Con error", tono: "warn" },
  pending: { texto: "Pendiente", tono: "warn" },
};

function DetalleVenta({
  venta,
  onClose,
}: {
  venta: VentaResumen;
  onClose: () => void;
}) {
  const estado = ESTADO_VENTA[venta.status];
  return (
    <Drawer title="Venta" onClose={onClose}>
      <div style={{ display: "flex", gap: 8, marginBottom: 22, flexWrap: "wrap" }}>
        <Badge tono={estado?.tono}>{estado?.texto ?? venta.status}</Badge>
        {[...new Set(venta.movements.map((m) => m.source_id))].map((s) => (
          <Origen key={s} source={s} />
        ))}
      </div>

      <dl className="kv">
        <dt>Transacción</dt><dd>{venta.transaction_id}</dd>
        <dt>Fecha</dt><dd>{fechaLarga(venta.occurred_on)}</dd>
        <dt>Desembolso</dt>
        <dd>{venta.disbursement_id ?? "— no se liquidó —"}</dd>
      </dl>

      <div className="ladder" style={{ margin: "24px 0" }}>
        <div>
          <span>Bruto cobrado</span>
          <span>{venta.gross?.formatted ?? "—"}</span>
        </div>
        {venta.deductions.map((d) => (
          <div key={d.id}>
            <span className="dim">{d.description}</span>
            <span className="neg">{d.amount.formatted}</span>
          </div>
        ))}
        {!venta.has_declared_deductions && (
          <div>
            <span className="dim">comisiones e impuestos</span>
            <span className="dim">no declarados por el canal</span>
          </div>
        )}
        <div className="total">
          <span>Neto que aporta al giro</span>
          <span>{venta.net_expected.formatted}</span>
        </div>
      </div>

      {venta.disbursement_id == null && (
        <p className="sub">
          Esta venta no llegó al banco. Está en el sistema justamente para poder
          responder por qué.
        </p>
      )}

      <h3 style={{ marginTop: 26 }}>De dónde sale cada dato</h3>
      <p className="sub" style={{ margin: "0 0 10px" }}>
        Las piezas vienen de fuentes distintas y ninguna se cuenta dos veces.
      </p>
      <div className="tabla">
        <table>
          <colgroup>
            <col style={{ width: "30%" }} />
            <col style={{ width: "34%" }} />
            <col style={{ width: "36%" }} />
          </colgroup>
          <thead>
            <tr><th>Tipo</th><th className="num">Monto</th><th>Origen</th></tr>
          </thead>
          <tbody>
            {venta.movements.map((m) => (
              <tr key={m.id}>
                <td><code>{m.kind}</code></td>
                <td><Amount value={m.amount} /></td>
                <td><Origen source={m.source_id} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ marginTop: 26 }}>Evidencia</h3>
      <pre>
        {venta.movements.map((m) => m.raw_ref).filter(Boolean).join("\n") || "—"}
      </pre>
    </Drawer>
  );
}

const TITULO_COL: Record<string, string> = {
  wompi: "Desembolso",
  bancolombia: "Movimiento del extracto",
  odoo: "Asiento contable",
};

function Detalle({
  item,
  fuente,
  onClose,
}: {
  item: PanoramaItem;
  fuente: FuenteId;
  onClose: () => void;
}) {
  const estado = ESTADO[item.reconciliation];
  // `extra` viaja como Record<string, unknown> porque su forma depende de la
  // fuente. Se estrecha acá, en el único lugar que lo consume.
  const bruto = item.extra.gross_total as Money | undefined;
  const descuentos = item.extra.declared_deductions as Money | undefined;
  const desgloseCompleto = Boolean(item.extra.deductions_complete);
  const evidencia = item.extra.raw_ref ? String(item.extra.raw_ref) : null;

  return (
    <Drawer title={item.label} onClose={onClose}>
      <div style={{ display: "flex", gap: 8, marginBottom: 22, flexWrap: "wrap" }}>
        <Badge tono={estado?.tono}>{estado?.texto ?? item.reconciliation}</Badge>
        {item.origins.map((o) => (
          <Origen key={o} source={o} />
        ))}
      </div>

      <p className="sub" style={{ marginBottom: 22 }}>{estado?.ayuda}</p>

      <dl className="kv">
        {item.date && (<><dt>Fecha</dt><dd>{fechaLarga(item.date)}</dd></>)}
        <dt>Monto</dt><dd>{item.amount.formatted}</dd>
        {item.child_count != null && (
          <><dt>Ventas que agrupa</dt><dd>{item.child_count}</dd></>
        )}
        {Object.entries(item.extra)
          .filter(([k, v]) => k !== "transactions" && v != null && typeof v !== "object")
          .map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}>
              <dt>{ETIQUETA_EXTRA[k] ?? k}</dt>
              <dd>{String(v)}</dd>
            </div>
          ))}
      </dl>

      {fuente === "wompi" && bruto && (
        <div className="ladder" style={{ margin: "24px 0" }}>
          <div>
            <span>Bruto cobrado</span>
            <span>{bruto.formatted}</span>
          </div>
          <div>
            <span className="dim">
              Comisiones e impuestos
              {!desgloseCompleto && " (estimados)"}
            </span>
            <span className="neg">
              {descuentos?.formatted ?? "—"}
            </span>
          </div>
          <div className="total">
            <span>Acreditado en el banco</span>
            <span>{item.amount.formatted}</span>
          </div>
        </div>
      )}

      {evidencia && (
        <>
          <h3 style={{ marginTop: 26 }}>Evidencia</h3>
          <p className="sub" style={{ margin: 0 }}>
            De qué byte salió este dato.
          </p>
          <pre>{evidencia}</pre>
        </>
      )}
    </Drawer>
  );
}

const ETIQUETA_EXTRA: Record<string, string> = {
  saldo: "Saldo acumulado",
  periodo: "Período del extracto",
  kind: "Tipo",
  debit: "Debe",
  credit: "Haber",
  state: "Estado del asiento",
  reference: "Referencia",
  erp_line_id: "Línea en Odoo",
  deductions_complete: "Desglose declarado",
};
