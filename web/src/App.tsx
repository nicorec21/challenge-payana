import { useState } from "react";
import { Dashboard } from "./views/Dashboard";
import { Fuentes } from "./views/Fuentes";

type Vista = "panorama" | "fuentes";

const PAGINAS: { id: Vista; nombre: string }[] = [
  { id: "panorama", nombre: "Panorama" },
  { id: "fuentes", nombre: "Fuentes de datos" },
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
      </main>
    </div>
  );
}
