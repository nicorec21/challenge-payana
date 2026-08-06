import { useState } from "react";
import { api } from "./api";
import { Async, useAsync } from "./ui";
import { Conciliacion } from "./views/Conciliacion";
import { Desembolsos } from "./views/Desembolsos";
import { Extracto } from "./views/Extracto";
import { Movimientos } from "./views/Movimientos";
import { Sistema } from "./views/Sistema";
import { Transacciones } from "./views/Transacciones";

type Vista =
  | { tipo: "sistema" }
  | { tipo: "conciliacion" }
  | { tipo: "extracto"; ledger: string }
  | { tipo: "movimientos"; ledger: string }
  | { tipo: "transacciones"; ledger: string }
  | { tipo: "desembolsos"; ledger: string };

export function App() {
  const [vista, setVista] = useState<Vista>({ tipo: "sistema" });
  const sistema = useAsync(() => api.system(), []);

  const on = (v: Vista) =>
    vista.tipo === v.tipo && (!("ledger" in v) || ("ledger" in vista && vista.ledger === v.ledger));

  return (
    <div className="app">
      <nav className="side">
        <div className="brand">
          Conciliación
          <small>Alimentos Alcázar</small>
        </div>

        <div className="nav">
          <button className={on({ tipo: "sistema" }) ? "on" : ""} onClick={() => setVista({ tipo: "sistema" })}>
            Sistema
          </button>
          <button
            className={on({ tipo: "conciliacion" }) ? "on" : ""}
            onClick={() => setVista({ tipo: "conciliacion" })}
          >
            Conciliación de flujo
          </button>

          <Async state={sistema}>
            {(data) => (
              <>
                {data.ledgers.map((l) => (
                  <div key={l.id}>
                    <div className="nav-label">{l.name}</div>
                    {l.role === "bank" && (
                      <button
                        className={on({ tipo: "extracto", ledger: l.id }) ? "on" : ""}
                        onClick={() => setVista({ tipo: "extracto", ledger: l.id })}
                        style={{ width: "100%" }}
                      >
                        Extracto
                      </button>
                    )}
                    {l.role === "channel" && (
                      <>
                        <button
                          className={on({ tipo: "transacciones", ledger: l.id }) ? "on" : ""}
                          onClick={() => setVista({ tipo: "transacciones", ledger: l.id })}
                          style={{ width: "100%" }}
                        >
                          Transacciones
                        </button>
                        <button
                          className={on({ tipo: "desembolsos", ledger: l.id }) ? "on" : ""}
                          onClick={() => setVista({ tipo: "desembolsos", ledger: l.id })}
                          style={{ width: "100%" }}
                        >
                          Desembolsos
                        </button>
                      </>
                    )}
                    <button
                      className={on({ tipo: "movimientos", ledger: l.id }) ? "on" : ""}
                      onClick={() => setVista({ tipo: "movimientos", ledger: l.id })}
                      style={{ width: "100%" }}
                    >
                      Movimientos
                    </button>
                  </div>
                ))}
              </>
            )}
          </Async>
        </div>
      </nav>

      <main className="main">
        {vista.tipo === "sistema" && <Sistema />}
        {vista.tipo === "conciliacion" && <Conciliacion canal="wompi" banco="bancolombia" />}
        {vista.tipo === "extracto" && <Extracto ledgerId={vista.ledger} />}
        {vista.tipo === "movimientos" && <Movimientos ledgerId={vista.ledger} />}
        {vista.tipo === "transacciones" && <Transacciones ledgerId={vista.ledger} />}
        {vista.tipo === "desembolsos" && <Desembolsos ledgerId={vista.ledger} />}
      </main>
    </div>
  );
}
