import { useState } from "react";
import { Dashboard } from "./views/Dashboard";
import { Erp } from "./views/Erp";
import { Flujo } from "./views/Flujo";
import { Fuentes } from "./views/Fuentes";

type Vista = "panorama" | "fuentes" | "flujo" | "erp";

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

export function App() {
  const [vista, setVista] = useState<Vista>("panorama");

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
              onClick={() => setVista(p.id)}
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
      </main>
    </div>
  );
}
