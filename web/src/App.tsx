import { useEffect, useState } from "react";
import { Dashboard } from "./views/Dashboard";
import { Erp } from "./views/Erp";
import { Flujo } from "./views/Flujo";
import { Fuentes } from "./views/Fuentes";
import { Informe, type InformeId } from "./views/Informe";

type Vista = "panorama" | "fuentes" | "flujo" | "erp" | "informe";

/**
 * Las dos preguntas del enunciado son dos vistas distintas, y no se mezclan:
 * «¿llegó la plata al banco?» (flujo) y «¿lo refleja el libro?» (ERP).
 */
const PAGINAS: { id: Vista; nombre: string }[] = [
  { id: "panorama", nombre: "Panorama" },
  { id: "fuentes", nombre: "Fuentes de datos" },
  { id: "flujo", nombre: "Flujo · canal → banco" },
  { id: "erp", nombre: "Libro · contra Odoo" },
];

/**
 * Ruta en el hash, sin router.
 *
 * Un informe es lo que alguien manda por mail —«mirá el punto 3»— y sin URL
 * propia eso obliga a explicar dónde clickear. El hash alcanza: no necesita
 * que el server sepa de rutas del front, así que `vite preview` y cualquier
 * hosting estático se comportan igual que el dev server.
 *
 * `#informe/flujo/wompi/bancolombia` · `#informe/erp/wompi` · `#flujo`
 */
function leerHash(): { vista: Vista; informe: InformeId | null } {
  const partes = window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);

  if (partes[0] === "informe") {
    if (partes[1] === "erp" && partes[2]) {
      return { vista: "informe", informe: { tipo: "erp", ledger: partes[2] } };
    }
    if (partes[1] === "flujo") {
      return {
        vista: "informe",
        informe: {
          tipo: "flujo",
          canal: partes[2] ?? "wompi",
          banco: partes[3] ?? "bancolombia",
        },
      };
    }
  }

  const vista = PAGINAS.find((p) => p.id === partes[0])?.id ?? "panorama";
  return { vista, informe: null };
}

export function App() {
  const [ruta, setRuta] = useState(leerHash);

  useEffect(() => {
    const onHash = () => setRuta(leerHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const ir = (destino: string) => {
    window.location.hash = destino;
  };

  const { vista, informe } = ruta;

  return (
    <div className="app">
      <nav className="side">
        <div className="brand">
          Conciliación
          <small>Alimentos Alcázar</small>
        </div>

        <div className="nav">
          {PAGINAS.map((p) => (
            <button
              key={p.id}
              className={vista === p.id ? "on" : ""}
              onClick={() => ir(p.id)}
            >
              {p.nombre}
            </button>
          ))}
        </div>
      </nav>

      <main className="main">
        {vista === "panorama" && <Dashboard />}
        {vista === "fuentes" && <Fuentes />}
        {vista === "flujo" && <Flujo />}
        {vista === "erp" && <Erp />}
        {vista === "informe" && informe && (
          <Informe
            id={informe}
            onVolver={() => ir(informe.tipo === "erp" ? "erp" : "flujo")}
          />
        )}
      </main>
    </div>
  );
}
