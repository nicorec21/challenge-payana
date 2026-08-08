/**
 * Renderer del subconjunto de Markdown que generan `cfo.py` y `erp_cfo.py`.
 *
 * No es un parser de Markdown y no pretende serlo. El input no es arbitrario:
 * lo produce nuestro propio código, y medido sobre los dos informes completos
 * usa exactamente h1/h2/h3, tablas, listas con `-`, `---`, y en línea
 * `**negrita**`, `*cursiva*` y `` `código` ``. Cero links, cero HTML embebido,
 * cero bloques de código. Traer una librería de Markdown para eso agregaría
 * varios paquetes a un front que hoy tiene dos dependencias.
 *
 * Devuelve elementos de React, nunca HTML como string: sin
 * `dangerouslySetInnerHTML` no hay superficie de inyección, por más que el
 * informe incluya descripciones que vienen del extracto bancario.
 *
 * Si algún día el generador emite algo que esto no entiende, la línea cae al
 * caso por defecto y se muestra como párrafo — se ve pobre, nunca se pierde.
 */

type Props = { source: string };

export function Markdown({ source }: Props) {
  return <div className="md">{bloques(source.split("\n"))}</div>;
}

function bloques(lineas: string[]): React.ReactNode[] {
  const out: React.ReactNode[] = [];
  let i = 0;

  while (i < lineas.length) {
    const linea = lineas[i];

    if (!linea.trim()) {
      i++;
      continue;
    }

    // Tabla: una fila de encabezado, el separador |---|, y las filas.
    if (linea.startsWith("|") && lineas[i + 1]?.trim().startsWith("|--")) {
      const encabezado = celdas(linea);
      const alineacion = celdas(lineas[i + 1]).map(alinear);
      const filas: string[][] = [];
      i += 2;
      while (i < lineas.length && lineas[i].startsWith("|")) {
        filas.push(celdas(lineas[i]));
        i++;
      }
      out.push(
        <div className="md-tabla" key={out.length}>
          <table>
            <thead>
              <tr>
                {encabezado.map((c, n) => (
                  <th key={n} style={{ textAlign: alineacion[n] }}>
                    {inline(c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filas.map((fila, n) => (
                <tr key={n}>
                  {fila.map((c, m) => (
                    <td key={m} style={{ textAlign: alineacion[m] }}>
                      {inline(c)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // Lista: se agrupan los `- ` consecutivos en un solo <ul>.
    if (linea.startsWith("- ")) {
      const items: string[] = [];
      while (i < lineas.length && lineas[i].startsWith("- ")) {
        items.push(lineas[i].slice(2));
        i++;
      }
      out.push(
        <ul key={out.length}>
          {items.map((t, n) => (
            <li key={n}>{inline(t)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^---+$/.test(linea.trim())) {
      out.push(<hr key={out.length} />);
      i++;
      continue;
    }

    const encabezado = linea.match(/^(#{1,6})\s+(.*)$/);
    if (encabezado) {
      const nivel = encabezado[1].length;
      const Tag = `h${Math.min(nivel, 6)}` as "h1";
      out.push(<Tag key={out.length}>{inline(encabezado[2])}</Tag>);
      i++;
      continue;
    }

    // Párrafo: líneas seguidas hasta el próximo bloque o línea en blanco.
    const parrafo: string[] = [];
    while (i < lineas.length && lineas[i].trim() && !esBloque(lineas[i])) {
      parrafo.push(lineas[i]);
      i++;
    }
    if (parrafo.length) {
      out.push(<p key={out.length}>{inline(parrafo.join(" "))}</p>);
    } else {
      i++; // defensivo: nunca dejar de avanzar
    }
  }

  return out;
}

function esBloque(linea: string): boolean {
  return (
    linea.startsWith("|") ||
    linea.startsWith("- ") ||
    /^#{1,6}\s/.test(linea) ||
    /^---+$/.test(linea.trim())
  );
}

function celdas(linea: string): string[] {
  return linea
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

function alinear(sep: string): "left" | "right" | "center" {
  if (sep.startsWith(":") && sep.endsWith(":")) return "center";
  if (sep.endsWith(":")) return "right";
  return "left";
}

/**
 * Marcas en línea. Un solo recorrido con una regex alternada, para que
 * `**$8.822.659,76**` no se rompa cuando el contenido tiene los caracteres de
 * otra marca adentro.
 */
function inline(texto: string): React.ReactNode {
  const partes: React.ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`(.+?)`|\*(.+?)\*/g;
  let ultimo = 0;
  let m: RegExpExecArray | null;

  while ((m = re.exec(texto)) !== null) {
    if (m.index > ultimo) partes.push(texto.slice(ultimo, m.index));
    if (m[1] !== undefined) partes.push(<strong key={partes.length}>{m[1]}</strong>);
    else if (m[2] !== undefined) partes.push(<code key={partes.length}>{m[2]}</code>);
    else partes.push(<em key={partes.length}>{m[3]}</em>);
    ultimo = m.index + m[0].length;
  }
  if (ultimo < texto.length) partes.push(texto.slice(ultimo));

  return partes.length ? partes : texto;
}
