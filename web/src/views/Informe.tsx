/**
 * El informe del CFO, renderizado.
 *
 * Es la misma salida que escribe `conciliacion reconcile` en `data/out/` y la
 * misma que devuelve la API en Markdown: una sola generación, tres formas de
 * leerla. Que la web arme su propio texto haría que el informe impreso y el de
 * pantalla pudieran afirmar cosas distintas sobre el mismo hecho (ADR-0004).
 *
 * Tiene página propia y no un panel: el informe es un documento largo —tablas
 * anchas, secciones— y se lee de arriba abajo, no de reojo mientras se navega.
 */

import { api, descargaUrl } from "../api";
import { Markdown } from "../markdown";
import { Async, useAsync } from "../ui";

export type InformeId =
  | { tipo: "flujo"; canal: string; banco: string }
  | { tipo: "erp"; ledger: string };

const TITULO: Record<InformeId["tipo"], string> = {
  flujo: "Informe de flujo · canal → banco",
  erp: "Informe del libro · contra Odoo",
};

export function Informe({ id, onVolver }: { id: InformeId; onVolver: () => void }) {
  const state = useAsync(
    () =>
      id.tipo === "flujo"
        ? api.flowMarkdown(id.canal, id.banco)
        : api.erpMarkdown(id.ledger),
    [id.tipo, id.tipo === "flujo" ? id.canal : id.ledger],
  );

  const descarga =
    id.tipo === "flujo" ? descargaUrl.flow(id.canal, id.banco) : descargaUrl.erp(id.ledger);

  return (
    <>
      <div className="informe-head">
        <button className="chip" onClick={onVolver}>
          ← Volver
        </button>
        <h1>{TITULO[id.tipo]}</h1>
        {/* `download` además del header: si alguien copia el link, el archivo
            sigue bajando como archivo. */}
        <a className="chip" href={descarga} download>
          Descargar .md ↓
        </a>
      </div>

      <p className="sub">
        Lo mismo que escribe <code>conciliacion reconcile</code> en{" "}
        <code>data/out/</code>. Generado en esta corrida, no cacheado.
      </p>

      <Async state={state}>{(md) => <Markdown source={md} />}</Async>
    </>
  );
}
