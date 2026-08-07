import { useMemo, useState } from "react";
import {
  api,
  type FlowFinding,
  type FlowReport,
  type MovementView,
  type TransactionBreakdown,
} from "../api";
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
 * Conciliación de flujo: ¿la plata que Wompi prometió llegó al banco?
 *
 * La fila es el **finding**, no el giro: los tres créditos huérfanos —los
 * únicos problemas reales— no tienen giro, y una tabla de giros los dejaría
 * fuera o en una sección aparte que nadie mira.
 *
 * Dos niveles de profundidad, porque son dos preguntas distintas:
 *
 * - el acordeón contesta **de qué está hecho** el giro (sus ventas);
 * - el panel contesta **por qué el motor concluye eso** (regla, ventana,
 *   ajustes con su origen, alternativas descartadas, evidencia).
 *
 * Nada acá recalcula: la confianza, el residuo y los montos vienen del motor.
 * Si el front dedujera un nivel de confianza propio, el sistema afirmaría dos
 * cosas distintas sobre el mismo hecho.
 */

const TAMANO = 25;

export function Flujo() {
  const reporte = useAsync(() => api.flow("wompi", "bancolombia"), []);
  const grupos = useAsync(() => api.groups("wompi"), []);
  const banco = useAsync(() => api.movements("bancolombia", { limit: 500 }), []);

  const [estado, setEstado] = useState("");
  const [confianza, setConfianza] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [expandido, setExpandido] = useState<string | null>(null);
  const [abierto, setAbierto] = useState<FlowFinding | null>(null);

  /**
   * Venta (con su desglose) indexada por el id del movimiento `payment`, que es
   * lo que el finding referencia en `transaction_ids`.
   */
  const ventaPorMovimiento = useMemo(() => {
    const mapa = new Map<string, TransactionBreakdown>();
    for (const g of grupos.data?.groups ?? []) {
      for (const t of g.transactions) {
        for (const m of t.movements) {
          if (m.kind === "payment") mapa.set(m.id, t);
        }
      }
    }
    return mapa;
  }, [grupos.data]);

  /** El finding trae el id del crédito bancario, no su descripción. */
  const movimientoBanco = useMemo(() => {
    const mapa = new Map<string, MovementView>();
    for (const m of banco.data?.items ?? []) mapa.set(m.id, m);
    return mapa;
  }, [banco.data]);

  const filtrar = (f: FlowFinding) => {
    if (estado && f.status !== estado) return false;
    if (confianza && f.explanation.confidence !== confianza) return false;
    if (q) {
      const aguja = q.toLowerCase();
      const paja = [
        f.id,
        f.explanation.summary,
        f.settlement_amount?.formatted,
        f.bank_amount?.formatted,
        f.occurred_on,
        f.bank_movement_id && movimientoBanco.get(f.bank_movement_id)?.description,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (!paja.includes(aguja)) return false;
    }
    return true;
  };

  const limpiar = (fn: () => void) => {
    fn();
    setPage(1);
    setExpandido(null);
  };

  return (
    <>
      <h1>Conciliación de flujo</h1>
      <p className="sub">
        Wompi liquida un giro por día hábil y el banco lo acredita. Esta vista
        recorre giro por giro y crédito por crédito: qué se encontró, con qué
        confianza y por qué.
      </p>

      <Async state={reporte}>
        {(r) => {
          const visibles = r.findings.filter(filtrar);
          const paginas = Math.max(1, Math.ceil(visibles.length / TAMANO));
          const actual = Math.min(page, paginas);
          const enPantalla = visibles.slice((actual - 1) * TAMANO, actual * TAMANO);

          return (
            <>
              <Veredicto r={r} />
              <Kpis r={r} />

              <h2>Los dos saltos</h2>
              <p className="sub">
                El enunciado describe un salto. Los datos tienen dos, y solo uno
                se busca.
              </p>
              <Saltos r={r} />

              <h2>Detalle · {r.findings.length} conclusiones</h2>
              <p className="sub">
                En orden cronológico, sin priorizar: el sistema no decide qué
                mirás primero. Los filtros sí.
              </p>

              <div className="toolbar">
                <input
                  type="search"
                  placeholder="Buscar monto, fecha, descripción…"
                  value={q}
                  onChange={(e) => limpiar(() => setQ(e.target.value))}
                />
                <button
                  className={`chip ${!estado && !confianza ? "on" : ""}`}
                  onClick={() =>
                    limpiar(() => {
                      setEstado("");
                      setConfianza("");
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
                <span className="dim" style={{ margin: "0 4px" }}>
                  confianza
                </span>
                {ORDEN_CONFIANZA.filter((c) => r.by_confidence[c]).map((c) => (
                  <button
                    key={c}
                    className={`chip ${confianza === c ? "on" : ""}`}
                    onClick={() =>
                      limpiar(() => setConfianza(confianza === c ? "" : c))
                    }
                  >
                    {CONFIANZA[c].texto} ({r.by_confidence[c]})
                  </button>
                ))}
                <a className="chip" href="/api/reconciliation/flow/report.md">
                  Descargar informe ↓
                </a>
              </div>

              <div className="tabla">
                <table>
                  {/*
                    Las columnas de plata son dos lados, no dos conceptos: lo que
                    salió del canal y lo que entró al banco. Un finding sin una
                    de las dos puntas deja esa celda vacía, y ese hueco ES el
                    hallazgo.
                  */}
                  <colgroup>
                    <col style={{ width: "31%" }} />
                    <col style={{ width: "10%" }} />
                    <col style={{ width: "16%" }} />
                    <col style={{ width: "16%" }} />
                    <col style={{ width: "12%" }} />
                    <col style={{ width: "15%" }} />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>Conclusión</th>
                      <th>Fecha</th>
                      <th className="num">Canal</th>
                      <th className="num">Banco</th>
                      <th>Confianza</th>
                      <th>Estado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {enPantalla.map((f) => (
                      <Fila
                        key={f.id}
                        f={f}
                        ventas={f.transaction_ids
                          .map((id) => ventaPorMovimiento.get(id))
                          .filter((v): v is TransactionBreakdown => v != null)}
                        credito={
                          f.bank_movement_id
                            ? movimientoBanco.get(f.bank_movement_id)
                            : undefined
                        }
                        expandido={expandido === f.id}
                        onToggle={() =>
                          setExpandido(expandido === f.id ? null : f.id)
                        }
                        onAbrir={() => setAbierto(f)}
                      />
                    ))}
                    {enPantalla.length === 0 && (
                      <tr>
                        <td colSpan={6} className="empty">
                          Ningún finding con ese filtro.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>

                <Paginacion
                  page={actual}
                  pages={paginas}
                  total={visibles.length}
                  unit="conclusiones"
                  onPage={setPage}
                />
              </div>
            </>
          );
        }}
      </Async>

      {abierto && (
        <Detalle
          f={abierto}
          credito={
            abierto.bank_movement_id
              ? movimientoBanco.get(abierto.bank_movement_id)
              : undefined
          }
          onClose={() => setAbierto(null)}
        />
      )}
    </>
  );
}

// ── encabezado ──────────────────────────────────────────────────────────────

function Veredicto({ r }: { r: FlowReport }) {
  const fuera = r.counts.out_of_coverage ?? 0;
  const limpio = r.problem_count === 0;
  return (
    <div className={`banner ${limpio ? "ok" : "bad"}`}>
      {limpio ? (
        <>
          <strong>Todo el dinero del período está explicado.</strong>{" "}
        </>
      ) : (
        <>
          <strong>
            {r.problem_count} caso(s) requieren revisión por {r.disputed_amount.formatted}.
          </strong>{" "}
        </>
      )}
      Se conciliaron {r.counts.matched ?? 0} giros por {r.matched_amount.formatted}.
      {fuera > 0 && (
        <>
          {" "}
          Hay {fuera} giro(s) fuera del período que cubren los datos:{" "}
          <strong>falta información, no plata.</strong>
        </>
      )}
    </div>
  );
}

function Kpis({ r }: { r: FlowReport }) {
  return (
    <div className="cards">
      <div className="card">
        <div className="meta">Conciliado</div>
        <div className="kpi pos">{r.matched_amount.formatted}</div>
        <div className="meta">
          {r.counts.matched ?? 0} giros encontrados en el extracto
        </div>
      </div>
      <div className="card">
        <div className="meta">En disputa</div>
        <div className={`kpi ${r.problem_count ? "neg" : "pos"}`}>
          {r.disputed_amount.formatted}
        </div>
        <div className="meta">
          {r.problem_count} caso(s) que exigen acción
        </div>
      </div>
      <div className="card">
        <div className="meta">Error de estimación</div>
        <div className="kpi">{r.rounding_amount.formatted}</div>
        <div className="meta">
          acumulado de inferir comisiones. No es plata en disputa
        </div>
      </div>
      <div className="card">
        <div className="meta">Período comparable</div>
        <div className="kpi" style={{ fontSize: 19 }}>
          {r.coverage.overlap
            ? `${fechaLarga(r.coverage.overlap.from)} → ${fechaLarga(r.coverage.overlap.to)}`
            : "sin período en común"}
        </div>
        <div className="meta">
          fuera de acá el sistema no afirma, informa que no sabe
        </div>
      </div>
    </div>
  );
}

/**
 * Los dos saltos, explícitos.
 *
 * Que el primero no se busque es la decisión más fuerte del motor (ADR-0010) y
 * la que más se malinterpreta: sin decirlo, parece que el sistema esquivó la
 * ambigüedad que advierte el enunciado.
 */
function Saltos({ r }: { r: FlowReport }) {
  const ventas = r.findings.reduce((n, f) => n + f.transaction_ids.length, 0);
  return (
    <div className="cards">
      <div className="card">
        <div className="meta">Salto 1 · venta → giro</div>
        <h3 style={{ margin: "10px 0 6px" }}>No se busca: se lee</h3>
        <p className="sub" style={{ margin: 0, fontSize: 13 }}>
          Cada una de las {ventas} ventas declara su <code>disbursement_id</code>.
          Agruparlas es un <code>GROUP BY</code> sobre un dato observado, no una
          búsqueda de subconjuntos que sumen lo mismo.
        </p>
      </div>
      <div className="card">
        <div className="meta">Salto 2 · giro → crédito bancario</div>
        <h3 style={{ margin: "10px 0 6px" }}>Se busca por monto y fecha</h3>
        <p className="sub" style={{ margin: 0, fontSize: 13 }}>
          Candidatos: créditos del extracto dentro de la ventana de días hábiles.
          Gana el de monto igual y fecha más cercana. La descripción del banco
          nunca es llave: cambió a mitad del período.
        </p>
      </div>
    </div>
  );
}

// ── fila ────────────────────────────────────────────────────────────────────

function Fila({
  f,
  ventas,
  credito,
  expandido,
  onToggle,
  onAbrir,
}: {
  f: FlowFinding;
  ventas: TransactionBreakdown[];
  credito: MovementView | undefined;
  expandido: boolean;
  onToggle: () => void;
  onAbrir: () => void;
}) {
  const e = ESTADO[f.status];
  const c = CONFIANZA[f.explanation.confidence];
  const desplegable = ventas.length > 0;

  return (
    <>
      <tr
        className={`clickable padre ${expandido ? "abierto" : ""}`}
        onClick={() => (desplegable ? onToggle() : onAbrir())}
      >
        <td>
          <div className="celda-principal">
            {desplegable && (
              <span className={`chevron ${expandido ? "abierto" : ""}`}>▶</span>
            )}
            {e?.titulo ?? f.status}
          </div>
          <div className="celda-sub">{subtitulo(f, ventas, credito)}</div>
        </td>
        <td className="dim">{f.occurred_on ? fechaLarga(f.occurred_on) : "—"}</td>
        <td className="num">
          <Amount value={f.settlement_amount} colored={false} />
        </td>
        <td className="num">
          <Amount value={f.bank_amount} colored={false} />
        </td>
        <td>
          <Badge tono={c?.tono} plain>
            {c?.texto ?? f.explanation.confidence}
          </Badge>
        </td>
        <td>
          <Badge tono={e?.tono}>{e?.texto ?? f.status}</Badge>
        </td>
      </tr>

      {expandido &&
        ventas.map((v) => (
          <tr key={v.transaction_id} className="hijo">
            <td>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12 }}>
                {v.transaction_id}
              </div>
              <div className="celda-sub">
                {v.has_declared_deductions
                  ? `comisiones declaradas ${v.total_deductions.formatted}`
                  : "comisiones inferidas del tarifario"}
              </div>
            </td>
            <td className="dim">{fechaLarga(v.occurred_on)}</td>
            <td className="num">
              <Amount value={v.gross} colored={false} />
            </td>
            <td className="num dim">—</td>
            <td colSpan={2} className="dim" style={{ fontSize: 12 }}>
              venta que compone el giro
            </td>
          </tr>
        ))}

      {expandido && (
        <tr className="resumen">
          {/*
            El total va bajo la columna del lado al que pertenece: el neto del
            canal bajo «Canal», el crédito bajo «Banco». Es la línea donde se ve
            que las dos puntas cierran.
          */}
          <td colSpan={2}>
            Bruto {f.explanation.gross?.formatted ?? "—"} menos comisiones e
            impuestos {f.explanation.adjustments_total?.formatted ?? "—"}
          </td>
          <td className="num total-fila">
            {f.settlement_amount?.formatted ?? "—"}
          </td>
          <td className="num total-fila">{f.bank_amount?.formatted ?? "—"}</td>
          <td colSpan={2}>
            <button className="chip" onClick={onAbrir}>
              Ver el razonamiento →
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

function subtitulo(
  f: FlowFinding,
  ventas: TransactionBreakdown[],
  credito: MovementView | undefined,
): string {
  if (ventas.length > 0) {
    const bruto = f.explanation.gross?.formatted;
    return `${ventas.length} venta(s)${bruto ? ` · bruto ${bruto}` : ""}`;
  }
  if (credito) return credito.description;
  return ESTADO[f.status]?.ayuda ?? "";
}

// ── panel de razonamiento ───────────────────────────────────────────────────

function Detalle({
  f,
  credito,
  onClose,
}: {
  f: FlowFinding;
  credito: MovementView | undefined;
  onClose: () => void;
}) {
  const e = ESTADO[f.status];
  const c = CONFIANZA[f.explanation.confidence];
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

      <h3 style={{ marginTop: 26 }}>Por qué esta confianza</h3>
      <p className="sub" style={{ margin: "0 0 12px" }}>
        {c?.significa} Es una escala de acción, no una probabilidad: un score
        decimal sugeriría una precisión que no tenemos.
      </p>
      <Senales f={f} />

      {x.gross && (
        <>
          <h3 style={{ marginTop: 26 }}>Cómo se llega del bruto al crédito</h3>
          <p className="sub" style={{ margin: "0 0 8px" }}>
            Cada línea dice si el canal lo declaró o si el sistema lo calculó.
          </p>
          <div className="ladder">
            <div>
              <span>Bruto cobrado</span>
              <span>{x.gross.formatted}</span>
            </div>
            {x.adjustments.map((a, i) => (
              <div key={i}>
                <span className="dim">
                  {AJUSTE[a.kind] ?? a.kind}{" "}
                  <Badge tono={a.source === "declared" ? "ok" : "warn"} plain>
                    {a.source === "declared" ? "declarado" : "inferido"}
                  </Badge>
                </span>
                <span className="neg">−{a.amount.formatted}</span>
              </div>
            ))}
            <div className="total">
              <span>Giro que declara el canal</span>
              <span>{f.settlement_amount?.formatted ?? "—"}</span>
            </div>
            <div>
              <span>Crédito en el extracto</span>
              <span>{f.bank_amount?.formatted ?? "— no se encontró —"}</span>
            </div>
            {x.unexplained && (
              <div>
                <span className="dim">Sin explicar</span>
                <span className="neg">{x.unexplained.formatted}</span>
              </div>
            )}
          </div>
          {/*
            Las notas se deduplican: con desglose declarado las tres dicen lo
            mismo, y repetirlo tres veces hace que no se lea ninguna.
          */}
          <ul className="notas">
            {[...new Set(x.adjustments.map((a) => a.note))].map((nota) => (
              <li key={nota}>{nota}</li>
            ))}
          </ul>
        </>
      )}

      {x.window && (
        <>
          <h3 style={{ marginTop: 26 }}>Dónde se buscó</h3>
          <dl className="kv">
            <dt>Ventana</dt>
            <dd>
              {fechaLarga(x.window.start)} → {fechaLarga(x.window.end)}
            </dd>
            <dt>Regla</dt>
            <dd style={{ fontFamily: "inherit" }}>{x.window.rule}</dd>
            {credito && (
              <>
                <dt>Crédito elegido</dt>
                <dd style={{ fontFamily: "inherit" }}>
                  {fechaLarga(credito.occurred_on)} · {credito.description}
                </dd>
              </>
            )}
          </dl>
        </>
      )}

      {x.alternatives.length > 0 && (
        <>
          <h3 style={{ marginTop: 26 }}>Qué se descartó</h3>
          <p className="sub" style={{ margin: "0 0 10px" }}>
            Exponer lo descartado es lo que convierte un match en un argumento.
          </p>
          {x.alternatives.map((a, i) => (
            <div key={i} className="descartado">
              <div>{a.description}</div>
              <div className="dim" style={{ fontSize: 12.5, marginTop: 4 }}>
                {a.rejected_because}
              </div>
            </div>
          ))}
        </>
      )}

      <h3 style={{ marginTop: 26 }}>Movimientos que participan</h3>
      <dl className="kv">
        <dt>Del canal</dt>
        <dd>{x.source_movement_ids.join("\n") || "—"}</dd>
        <dt>Del banco</dt>
        <dd>{x.target_movement_ids.join("\n") || "—"}</dd>
        <dt>Id del finding</dt>
        <dd>{f.id}</dd>
      </dl>

      {credito?.raw_ref && (
        <>
          <h3 style={{ marginTop: 26 }}>Evidencia</h3>
          <p className="sub" style={{ margin: 0 }}>
            De qué byte del extracto salió el crédito.
          </p>
          <pre>{credito.raw_ref}</pre>
          <div style={{ marginTop: 10 }}>
            <Origen source={credito.source_id} />
          </div>
        </>
      )}
    </Drawer>
  );
}

/**
 * Las señales que el motor miró, como hechos.
 *
 * No recalcula el nivel: lo lee de la explicación y muestra los insumos que lo
 * determinaron, para que se pueda auditar el criterio sin leer el código.
 */
function Senales({ f }: { f: FlowFinding }) {
  const x = f.explanation;
  const declarados = x.adjustments.some((a) => a.source === "declared");
  const inferidos = x.adjustments.some((a) => a.source === "inferred");
  //: Un hallazgo que parte del banco no tiene giro ni ventas. Mostrarle las
  //: señales de la escalera diría «no cierra» sobre una cuenta que no existe.
  const hayGiro = f.settlement_movement_id != null;

  const filas: { senal: string; valor: string; ok: boolean | null }[] = [];

  if (hayGiro) {
    filas.push(
      {
        senal: "Origen de los descuentos",
        valor: declarados
          ? "declarados por el canal en el reporte de desembolso"
          : inferidos
            ? "inferidos del tarifario"
            : "sin desglose: no hay ventas cargadas que componerlo",
        ok: declarados ? true : inferidos ? null : false,
      },
      {
        senal: "La explicación cierra sola",
        valor: x.is_balanced ? "bruto − ajustes = neto" : "no cierra",
        ok: x.is_balanced,
      },
      {
        senal: "Ventas que componen el giro",
        valor:
          f.transaction_ids.length > 0
            ? `${f.transaction_ids.length}, todas declaran su desembolso`
            : "ninguna cargada: el período de origen está fuera de los datos",
        ok: f.transaction_ids.length > 0,
      },
    );
  } else {
    filas.push({
      senal: "Origen de este crédito",
      valor: "ningún giro declarado por el canal lo explica",
      ok: false,
    });
  }

  filas.push(
    {
      senal: "Plata sin explicar",
      valor: x.unexplained ? x.unexplained.formatted : "$0 — cierra exacto",
      ok: !x.unexplained,
    },
    {
      /*
        No toda alternativa es un competidor: en los créditos huérfanos el motor
        registra acá por qué la descripción no sirve como llave. Solo
        `ambiguous` significa que hubo empate, y es el motor quien lo declara.
      */
      senal: "Alternativas evaluadas",
      valor:
        x.alternatives.length === 0
          ? "ninguna: la conclusión es única bajo la regla"
          : f.status === "ambiguous"
            ? `${x.alternatives.length} empataron — requiere revisión humana`
            : `${x.alternatives.length}, con el motivo del descarte anotado abajo`,
      ok: f.status !== "ambiguous",
    },
  );

  return (
    <div className="tabla">
      <table>
        <colgroup>
          <col style={{ width: "42%" }} />
          <col style={{ width: "58%" }} />
        </colgroup>
        <tbody>
          {filas.map((r) => (
            <tr key={r.senal}>
              <td className="dim" style={{ fontSize: 13 }}>
                {r.senal}
              </td>
              <td
                style={{ fontSize: 13 }}
                className={r.ok === false ? "neg" : r.ok ? "" : "dim"}
              >
                {r.valor}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── vocabulario ─────────────────────────────────────────────────────────────

/**
 * Cada estado dice **qué le pasa a la plata**, no qué hizo el algoritmo.
 *
 * `unmatched_settlement` y `out_of_coverage` se ven idénticos en los datos —un
 * giro sin crédito— y significan lo contrario: falta plata vs. falta data.
 */
const ESTADO: Record<
  string,
  { titulo: string; texto: string; tono?: "ok" | "warn" | "bad"; ayuda: string }
> = {
  matched: {
    titulo: "Giro acreditado en el banco",
    texto: "Conciliado",
    tono: "ok",
    ayuda: "El giro que declaró el canal apareció en el extracto.",
  },
  unmatched_settlement: {
    titulo: "Giro sin crédito bancario",
    texto: "Falta plata",
    tono: "bad",
    ayuda:
      "El canal declaró un giro y no hay ningún crédito por ese monto en el " +
      "extracto, dentro de un período que sí tenemos cubierto.",
  },
  unmatched_bank: {
    titulo: "Crédito sin giro que lo explique",
    texto: "Origen sin identificar",
    tono: "bad",
    ayuda:
      "Entró plata al banco que parece del canal, y ningún giro declarado la " +
      "explica.",
  },
  out_of_coverage: {
    titulo: "Fuera del período con datos",
    texto: "Falta información",
    tono: "warn",
    ayuda:
      "No se puede concluir: una de las dos fuentes no cubre ese período. " +
      "No es un faltante de plata.",
  },
  ambiguous: {
    titulo: "Más de un crédito posible",
    texto: "Ambiguo",
    tono: "bad",
    ayuda:
      "Varios créditos del extracto encajan con el giro. El motor eligió uno " +
      "y expone los otros.",
  },
};

const ORDEN_CONFIANZA = ["exact", "high", "medium", "low"];

const CONFIANZA: Record<
  string,
  { texto: string; tono?: "ok" | "warn" | "bad"; significa: string }
> = {
  exact: {
    texto: "Exacta",
    tono: "ok",
    significa:
      "Match único, descuentos declarados por el canal y residuo cero. Cada " +
      "peso está respaldado por un dato observado, no calculado.",
  },
  high: {
    texto: "Alta",
    significa:
      "Match único y coherente, pero las comisiones se infirieron del " +
      "tarifario. El error de esa inferencia está acotado a un centavo por venta.",
  },
  medium: {
    texto: "Media",
    tono: "warn",
    significa:
      "Match plausible con una señal débil: residuo, fecha fuera de lo " +
      "esperado, o un giro del que no se sabe de qué está compuesto.",
  },
  low: {
    texto: "Baja",
    tono: "bad",
    significa:
      "Match sugerido, no afirmado. Hubo alternativas que encajaban igual de " +
      "bien o la cuenta no cierra. Requiere revisión humana.",
  },
};

const AJUSTE: Record<string, string> = {
  commission: "Comisión del canal",
  tax: "IVA sobre la comisión",
  withholding: "Retención en la fuente",
  rounding: "Redondeo",
  refund: "Devolución",
  chargeback: "Contracargo",
  unexplained: "Diferencia sin explicar",
};
