import { useState } from "react";
import { api, type ErpFinding, type ErpReport } from "../api";
import {
  Amount,
  Async,
  Badge,
  Drawer,
  fechaLarga,
  Paginacion,
  useAsync,
} from "../ui";

/**
 * Conciliación contra el ERP: ¿el libro de Odoo refleja lo que pasó?
 *
 * Pregunta contable, distinta de la de flujo. Son **dos corridas**, no una
 * tabla con dos filtros: cada ledger se compara contra su propia cuenta del
 * plan, y la llave de match no es la misma en las dos.
 *
 * La vista lidera con la conclusión agrupada y no con la lista. Del lado banco
 * hay 431 hallazgos y 415 son el mismo: *«el ERP no registra la operación
 * corriente»*. Enumerarlos línea por línea tiene la misma información y ninguna
 * de la legibilidad.
 */

const TAMANO = 25;

const LEDGERS: { id: string; nombre: string; detalle: string; cuenta: string }[] = [
  {
    id: "wompi",
    nombre: "Wompi",
    detalle: "Canal de cobro contra su cuenta puente",
    cuenta: "1110001",
  },
  {
    id: "bancolombia",
    nombre: "Bancolombia",
    detalle: "Cuenta bancaria contra su cuenta de banco",
    cuenta: "111001",
  },
];

export function Erp() {
  const [ledger, setLedger] = useState("wompi");
  const [estado, setEstado] = useState("");
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [abierto, setAbierto] = useState<ErpFinding | null>(null);

  const reporte = useAsync(() => api.erp(ledger), [ledger]);

  const cambiar = (id: string) => {
    setLedger(id);
    setEstado("");
    setKind("");
    setQ("");
    setPage(1);
  };

  const limpiar = (fn: () => void) => {
    fn();
    setPage(1);
  };

  const filtrar = (f: ErpFinding) => {
    if (estado && f.status !== estado) return false;
    if (kind && f.kind !== kind) return false;
    if (q) {
      const aguja = q.toLowerCase();
      const paja = [
        f.erp_move_name,
        f.explanation.summary,
        f.ledger_amount?.formatted,
        f.book_amount?.formatted,
        f.occurred_on,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!paja.includes(aguja)) return false;
    }
    return true;
  };

  return (
    <>
      <h1>Conciliación contra el ERP</h1>
      <p className="sub">
        El libro de Odoo es el system of record. Esta vista lo compara línea por
        línea contra lo que realmente pasó, y dice de cada dato si coincide, si
        falta, o si el ERP registra algo que nadie respalda.
      </p>

      <div className="picker">
        {LEDGERS.map((l) => (
          <button
            key={l.id}
            className={ledger === l.id ? "on" : ""}
            onClick={() => cambiar(l.id)}
          >
            <div className="nombre">{l.nombre}</div>
            <div className="detalle">{l.detalle}</div>
            <div className="conteo">
              cuenta {l.cuenta}
              {ledger === l.id && reporte.data
                ? ` · ${Math.round(reporte.data.coverage_ratio * 100)}% registrado`
                : ""}
            </div>
          </button>
        ))}
      </div>

      <Async state={reporte}>
        {(r) => {
          const visibles = r.findings.filter(filtrar);
          const paginas = Math.max(1, Math.ceil(visibles.length / TAMANO));
          const actual = Math.min(page, paginas);
          const enPantalla = visibles.slice((actual - 1) * TAMANO, actual * TAMANO);
          const kinds = [...new Set(r.findings.map((f) => f.kind).filter(Boolean))];

          return (
            <>
              <Veredicto r={r} />
              <Kpis r={r} />

              {r.missing_in_erp_by_kind.length > 0 && (
                <>
                  <h2>Lo que el ERP no registra</h2>
                  <p className="sub">
                    Agrupado por tipo, porque es una conclusión y no{" "}
                    {r.counts.missing_in_erp} hallazgos sueltos con la misma
                    información. Click en una fila para ver el detalle. Los
                    marcados <strong>otra causa</strong> no faltan por descuido:
                    no existe la cuenta donde asentarlos.
                  </p>
                  <div className="tabla">
                    <table>
                      <colgroup>
                        <col style={{ width: "40%" }} />
                        <col style={{ width: "15%" }} />
                        <col style={{ width: "30%" }} />
                        <col style={{ width: "15%" }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <th>Tipo de movimiento</th>
                          <th className="num">Casos</th>
                          <th className="num">Monto sin registrar</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {r.missing_in_erp_by_kind.map((g) => {
                          //: Estos no faltan por descuido: no existe la cuenta.
                          //: Mezclarlos con los otros hace que el total sugiera
                          //: una acción —asentar— que no aplica.
                          const sinCuenta =
                            r.coverage.unrepresentable_kinds.includes(g.kind);
                          return (
                            <tr
                              key={g.kind}
                              className="clickable"
                              onClick={() =>
                                limpiar(() => {
                                  setEstado("missing_in_erp");
                                  setKind(g.kind);
                                })
                              }
                            >
                              <td className="celda-principal">
                                {KIND[g.kind] ?? g.kind}
                                {sinCuenta && (
                                  <div className="celda-sub">
                                    no hay cuenta en el plan donde asentarlo
                                  </div>
                                )}
                              </td>
                              <td className="num">{g.count}</td>
                              <td className="num">
                                <Amount value={g.total} />
                              </td>
                              <td style={{ fontSize: 12 }}>
                                {sinCuenta ? (
                                  <Badge tono="warn">otra causa</Badge>
                                ) : (
                                  <span className="dim">ver detalle →</span>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              <h2>Cómo se empareja cada línea</h2>
              <Llaves r={r} />

              <h2>Detalle · {r.findings.length} comparaciones</h2>
              <p className="sub">
                Cuando coinciden se muestran <strong>los dos identificadores</strong>,
                que es lo que pide el enunciado: el del movimiento real y el de la
                línea contable.
              </p>

              <div className="toolbar">
                <input
                  type="search"
                  placeholder="Buscar asiento, monto, fecha…"
                  value={q}
                  onChange={(e) => limpiar(() => setQ(e.target.value))}
                />
                <button
                  className={`chip ${!estado && !kind ? "on" : ""}`}
                  onClick={() =>
                    limpiar(() => {
                      setEstado("");
                      setKind("");
                    })
                  }
                >
                  Todo ({r.findings.length})
                </button>
                {Object.entries(r.counts).map(([k, n]) => (
                  <button
                    key={k}
                    className={`chip ${estado === k ? "on" : ""}`}
                    onClick={() => limpiar(() => setEstado(estado === k ? "" : k))}
                  >
                    {ESTADO[k]?.texto ?? k} ({n})
                  </button>
                ))}
                {kind && (
                  <button className="chip on" onClick={() => limpiar(() => setKind(""))}>
                    tipo: {KIND[kind] ?? kind} ✕
                  </button>
                )}
                {!kind && kinds.length > 1 && (
                  <select
                    value={kind}
                    onChange={(e) => limpiar(() => setKind(e.target.value))}
                  >
                    <option value="">todos los tipos</option>
                    {kinds.map((k) => (
                      <option key={k} value={k!}>
                        {KIND[k!] ?? k}
                      </option>
                    ))}
                  </select>
                )}
                <a className="chip" href={`#informe/erp/${ledger}`}>
                  Ver informe →
                </a>
              </div>

              <div className="tabla">
                <table>
                  {/*
                    «Ledger» y «Libro» son los dos lados de la misma afirmación.
                    Cuando una está vacía, ese hueco es exactamente la
                    discrepancia que el enunciado pide señalar.
                  */}
                  <colgroup>
                    <col style={{ width: "30%" }} />
                    <col style={{ width: "10%" }} />
                    <col style={{ width: "16%" }} />
                    <col style={{ width: "16%" }} />
                    <col style={{ width: "13%" }} />
                    <col style={{ width: "15%" }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Conclusión</th>
                      <th>Fecha</th>
                      <th className="num">Ledger</th>
                      <th className="num">Libro</th>
                      <th>Emparejado por</th>
                      <th>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {enPantalla.map((f) => (
                      <Fila key={f.id} f={f} onAbrir={() => setAbierto(f)} />
                    ))}
                    {enPantalla.length === 0 && (
                      <tr>
                        <td colSpan={6} className="empty">
                          Ninguna comparación con ese filtro.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>

                <Paginacion
                  page={actual}
                  pages={paginas}
                  total={visibles.length}
                  unit="comparaciones"
                  onPage={setPage}
                />
              </div>
            </>
          );
        }}
      </Async>

      {abierto && <Detalle f={abierto} onClose={() => setAbierto(null)} />}
    </>
  );
}

// ── encabezado ──────────────────────────────────────────────────────────────

function Veredicto({ r }: { r: ErpReport }) {
  const c = r.coverage;
  const pct = Math.round(c.ratio * 100);
  const faltan = (r.counts.missing_in_erp ?? 0) - c.unrepresentable;
  const sobran = r.counts.missing_in_ledger ?? 0;
  const borradores = r.counts.not_posted ?? 0;
  const discrepan = r.counts.amount_mismatch ?? 0;

  return (
    <div className={`banner ${pct >= 99 && r.problem_count === 0 ? "ok" : "bad"}`}>
      <strong>
        El ERP registra el {pct}% de lo que puede registrar de {r.ledger_id}.
      </strong>{" "}
      {c.matched} de {c.comparable} coinciden por {r.matched_amount.formatted}
      {discrepan > 0 && (
        <>
          , y <strong>{discrepan} coinciden con otro monto</strong>
        </>
      )}
      . Faltan {faltan} asientos que deberían estar.
      {c.unrepresentable > 0 && (
        <>
          {" "}
          Aparte hay {c.unrepresentable} movimiento(s) por{" "}
          {c.unrepresentable_total.formatted} que{" "}
          <strong>no tienen cuenta donde asentarse</strong>: eso no se arregla
          registrando, se arregla rediseñando el plan de cuentas.
        </>
      )}
      {sobran > 0 && (
        <>
          {" "}
          Hay {sobran} asiento(s) que ningún movimiento respalda.
        </>
      )}
      {borradores > 0 && (
        <>
          {" "}
          {borradores} asiento(s) en borrador o anulados: no son ni coincidencia
          ni ausencia, hay que confirmarlos o descartarlos.
        </>
      )}
    </div>
  );
}

/**
 * Los KPIs, con la cobertura **separada**.
 *
 * Un solo «25%» juntaba «debería estar asentado y no lo está» con «no existe la
 * cuenta donde asentarlo». Se arreglan de maneras opuestas —asentando vs.
 * rediseñando el plan de cuentas— así que un número que baja por las dos
 * razones no le dice a nadie qué hacer.
 */
function Kpis({ r }: { r: ErpReport }) {
  const c = r.coverage;
  const pct = Math.round(c.ratio * 100);
  return (
    <div className="cards">
      <div className="card">
        <div className="meta">Cobertura de lo comparable</div>
        <div className={`kpi ${pct >= 99 ? "pos" : pct >= 50 ? "" : "neg"}`}>
          {pct}%
        </div>
        <div className="meta">
          {c.matched} de {c.comparable} hechos que el plan de cuentas sí puede
          representar en la cuenta {r.account_code}
        </div>
      </div>

      {c.unrepresentable > 0 ? (
        <div className="card">
          <div className="meta">Sin cuenta donde asentarse</div>
          <div className="kpi warn">{c.unrepresentable}</div>
          <div className="meta">
            {c.unrepresentable_kinds.map((k) => KIND[k] ?? k).join(", ")} por{" "}
            {c.unrepresentable_total.formatted}. No hay cuenta de gasto ni de
            impuesto en estos diarios: no se arregla asentando
          </div>
        </div>
      ) : (
        <div className="card">
          <div className="meta">Coincide, con los dos ids</div>
          <div className="kpi pos">{r.counts.matched ?? 0}</div>
          <div className="meta">{r.matched_amount.formatted}</div>
        </div>
      )}

      <div className="card">
        <div className="meta">Falta el asiento</div>
        {/*
          Sin restar los que no tienen cuenta, esta tarjeta vuelve a mezclar lo
          mismo que el porcentaje de al lado: 149 sugiere «asentá 149 cosas», y
          27 de esas no se pueden asentar en ningún lado.
        */}
        <div className="kpi neg">
          {(r.counts.missing_in_erp ?? 0) - c.unrepresentable}
        </div>
        <div className="meta">
          hechos que sí van en la cuenta {r.account_code} y no están. Agrupados
          por tipo abajo
        </div>
      </div>
      <div className="card">
        <div className="meta">El ERP lo tiene y no pasó</div>
        <div className={`kpi ${r.counts.missing_in_ledger ? "neg" : "pos"}`}>
          {r.counts.missing_in_ledger ?? 0}
        </div>
        <div className="meta">
          un registro sin respaldo es tan problema como uno que falta
        </div>
      </div>
    </div>
  );
}

/**
 * Cómo se decidió la llave de match.
 *
 * Es la decisión defendible de la fase: no hay lista negra de referencias, el
 * motor cuenta. Una referencia que aparece más de una vez no identifica nada.
 */
function Llaves({ r }: { r: ErpReport }) {
  const porRef = r.findings.filter(
    (f) => f.explanation.rule_id === "erp.matched_by_reference",
  ).length;
  const porMonto = r.findings.filter(
    (f) => f.explanation.rule_id === "erp.matched_by_amount_and_date",
  ).length;

  return (
    <div className="cards">
      <div className="card">
        <div className="meta">Llave fuerte · {porRef} caso(s)</div>
        <h3 style={{ margin: "10px 0 6px" }}>Por referencia</h3>
        <p className="sub" style={{ margin: 0, fontSize: 13 }}>
          La <code>ref</code> del asiento identifica la transacción. Solo se usa
          cuando apunta a <strong>una sola</strong> línea del libro, y el motor lo
          decide contando cuántas veces aparece: no hay lista negra, así que si el
          contador cambia el texto la regla sigue valiendo.
          {porRef === 0 && (
            <>
              {" "}
              <strong>Ninguna línea de este libro calificó.</strong>
            </>
          )}
        </p>
      </div>
      <div className="card">
        <div className="meta">Llave débil · {porMonto} caso(s)</div>
        <h3 style={{ margin: "10px 0 6px" }}>Por monto y fecha</h3>
        <p className="sub" style={{ margin: 0, fontSize: 13 }}>
          Para las líneas cuya referencia se repite: monto exacto dentro de ±3
          días, porque el asiento puede llevar la fecha del hecho o la de
          registración. Es más débil y la vista lo marca en cada fila.
        </p>
      </div>
    </div>
  );
}

// ── fila ────────────────────────────────────────────────────────────────────

function Fila({ f, onAbrir }: { f: ErpFinding; onAbrir: () => void }) {
  const e = ESTADO[f.status];
  const llave = LLAVE[f.explanation.rule_id];

  return (
    <tr className="clickable" onClick={onAbrir}>
      <td>
        <div className="celda-principal">{e?.titulo ?? f.status}</div>
        <div className="celda-sub">
          {f.erp_move_name ? `${f.erp_move_name} · ` : ""}
          {KIND[f.kind ?? ""] ?? f.kind ?? ""}
        </div>
      </td>
      <td className="dim">{f.occurred_on ? fechaLarga(f.occurred_on) : "—"}</td>
      <td className="num">
        <Amount value={f.ledger_amount} colored={false} />
      </td>
      <td className="num">
        <Amount value={f.book_amount} colored={false} />
      </td>
      <td>
        {llave ? (
          <Badge tono={llave.tono} plain>
            {llave.texto}
          </Badge>
        ) : (
          <span className="dim">—</span>
        )}
      </td>
      <td>
        <Badge tono={e?.tono}>{e?.texto ?? f.status}</Badge>
      </td>
    </tr>
  );
}

// ── panel ───────────────────────────────────────────────────────────────────

function Detalle({ f, onClose }: { f: ErpFinding; onClose: () => void }) {
  const e = ESTADO[f.status];
  const c = CONFIANZA[f.explanation.confidence];
  const llave = LLAVE[f.explanation.rule_id];
  const x = f.explanation;

  return (
    <Drawer title={e?.titulo ?? f.status} onClose={onClose}>
      <div style={{ display: "flex", gap: 8, marginBottom: 20, flexWrap: "wrap" }}>
        <Badge tono={e?.tono}>{e?.texto ?? f.status}</Badge>
        <Badge tono={c?.tono} plain>
          confianza: {c?.texto}
        </Badge>
        <span className="origen">{x.rule_id}</span>
      </div>

      <p style={{ marginTop: 0, fontSize: 14.5, lineHeight: 1.6 }}>{x.summary}</p>

      <h3 style={{ marginTop: 26 }}>Los dos identificadores</h3>
      <p className="sub" style={{ margin: "0 0 10px" }}>
        {f.status === "matched"
          ? "Las dos filas de abajo representan la misma cosa, vista desde cada sistema."
          : "Solo hay una punta, y esa ausencia es el hallazgo."}
      </p>
      <div className="tabla">
        <table>
          <colgroup>
            <col style={{ width: "24%" }} />
            <col style={{ width: "46%" }} />
            <col style={{ width: "30%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>Sistema</th>
              <th>Identificador</th>
              <th className="num">Monto</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="dim">Ledger</td>
              <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {f.ledger_movement_id ?? "— no existe —"}
              </td>
              <td className="num">
                <Amount value={f.ledger_amount} colored={false} />
              </td>
            </tr>
            <tr>
              <td className="dim">Odoo</td>
              <td style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {f.erp_move_name ?? "— no existe —"}
                {f.erp_line_id && (
                  <div className="celda-sub">línea {f.erp_line_id}</div>
                )}
              </td>
              <td className="num">
                <Amount value={f.book_amount} colored={false} />
              </td>
            </tr>
            {/*
              `difference` es `$0,00` en un match limpio, no `null` —el contrato
              lo documenta así— y en JS eso es truthy. Mostrar la fila igual
              afirmaba «el ERP registró otro número» encima de un cero.
            */}
            {f.difference && f.difference.cents !== 0 && (
              <tr>
                <td className="dim">Diferencia</td>
                <td className="dim" style={{ fontSize: 12.5 }}>
                  el ERP registró el mismo hecho con otro número
                </td>
                <td className="num neg">{f.difference.formatted}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {llave && (
        <>
          <h3 style={{ marginTop: 26 }}>Cómo se emparejaron</h3>
          <p className="sub" style={{ margin: "0 0 10px" }}>
            {llave.explica}
          </p>
          {x.window && (
            <dl className="kv">
              <dt>Ventana de fecha</dt>
              <dd>
                {fechaLarga(x.window.start)} → {fechaLarga(x.window.end)}
              </dd>
              <dt>Regla</dt>
              <dd style={{ fontFamily: "inherit" }}>{x.window.rule}</dd>
            </dl>
          )}
        </>
      )}

      <h3 style={{ marginTop: 26 }}>Qué significa esta confianza</h3>
      <p className="sub" style={{ margin: 0 }}>
        {c?.significa}
      </p>

      {x.unexplained && (
        <>
          <h3 style={{ marginTop: 26 }}>Plata sin explicar</h3>
          <div className="ladder">
            <div className="total">
              <span>{ESTADO[f.status]?.texto}</span>
              <span className="neg">{x.unexplained.formatted}</span>
            </div>
          </div>
        </>
      )}

      <h3 style={{ marginTop: 26 }}>Referencia</h3>
      <dl className="kv">
        <dt>Id del finding</dt>
        <dd>{f.id}</dd>
        <dt>Tipo de movimiento</dt>
        <dd style={{ fontFamily: "inherit" }}>
          {KIND[f.kind ?? ""] ?? f.kind ?? "—"}
        </dd>
      </dl>
    </Drawer>
  );
}

// ── vocabulario ─────────────────────────────────────────────────────────────

/**
 * `not_posted` es un estado propio, no una ausencia: el asiento existe pero está
 * en borrador o anulado. Contarlo como faltante o como coincidencia sería
 * afirmar algo que no pasó.
 */
const ESTADO: Record<
  string,
  { titulo: string; texto: string; tono?: "ok" | "warn" | "bad" }
> = {
  matched: {
    titulo: "Registrado y coincide",
    texto: "Coincide",
    tono: "ok",
  },
  amount_mismatch: {
    titulo: "Registrado con otro monto",
    texto: "Difiere",
    tono: "bad",
  },
  missing_in_erp: {
    titulo: "Pasó y el ERP no lo registró",
    texto: "Falta en el libro",
    tono: "bad",
  },
  missing_in_ledger: {
    titulo: "El ERP lo registra y nada lo respalda",
    texto: "Sin respaldo",
    tono: "bad",
  },
  not_posted: {
    titulo: "Asiento en borrador o anulado",
    texto: "Sin confirmar",
    tono: "warn",
  },
  out_of_coverage: {
    titulo: "Fuera del período con datos",
    texto: "Falta información",
    tono: "warn",
  },
};

const LLAVE: Record<
  string,
  { texto: string; tono?: "ok" | "warn" | "bad"; explica: string }
> = {
  "erp.matched_by_reference": {
    texto: "referencia",
    tono: "ok",
    explica:
      "La referencia del asiento identifica una sola línea del libro, así que " +
      "sirve de llave. El motor lo verifica contando cuántas veces aparece: una " +
      "referencia repetida no identifica nada.",
  },
  "erp.matched_by_amount_and_date": {
    texto: "monto + fecha",
    tono: "warn",
    explica:
      "La referencia de esta línea se repite en el libro, así que no sirve de " +
      "llave. Se emparejó por monto exacto dentro de una ventana de fecha, " +
      "porque el asiento puede llevar la fecha del hecho o la de registración.",
  },
  "erp.amount_mismatch": {
    texto: "referencia",
    tono: "bad",
    explica:
      "Se emparejaron porque comparten referencia, y por eso se puede afirmar " +
      "que son el mismo hecho. Lo que no coincide es el número.",
  },
};

const CONFIANZA: Record<
  string,
  { texto: string; tono?: "ok" | "warn" | "bad"; significa: string }
> = {
  exact: {
    texto: "Exacta",
    tono: "ok",
    significa:
      "Emparejados por una referencia que identifica una sola línea, y con el " +
      "mismo monto. No hay margen de interpretación.",
  },
  high: {
    texto: "Alta",
    significa:
      "La llave es sólida, pero algo no cierra del todo: o el monto difiere, o " +
      "el emparejamiento fue por monto y fecha en vez de por referencia.",
  },
  medium: {
    texto: "Media",
    tono: "warn",
    significa:
      "Emparejado por monto con diferencia de fecha, o una línea del libro que " +
      "ningún movimiento respalda. Conviene revisarlo a mano.",
  },
  low: {
    texto: "Baja",
    tono: "bad",
    significa: "Sugerido, no afirmado. Requiere revisión humana.",
  },
};

const KIND: Record<string, string> = {
  payment: "Ventas cobradas",
  fee: "Comisiones del canal",
  tax: "IVA y retenciones",
  settlement: "Giros al banco",
  bank_credit: "Créditos del extracto",
  bank_debit: "Débitos del extracto",
  refund: "Devoluciones",
  chargeback: "Contracargos",
  asiento: "Asiento contable",
  other: "Otros",
};
