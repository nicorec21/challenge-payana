## Qué cambia

<!-- Una o dos frases. El "cómo" está en el diff. -->

## Por qué

<!-- Si hubo un trade-off con alternativas descartadas, esto va en un ADR, no acá. -->

---

## Documentación — regla de oro

> Ningún PR se da por terminado sin actualizar la documentación que su cambio
> volvió falsa. En el mismo cambio, no en uno aparte.

Marcá lo que corresponda, o tachá lo que no aplique:

- [ ] **Hecho nuevo sobre los datos reales** → `CLAUDE.md` § Hechos
- [ ] **Trampa** que engañó o va a engañar → `CLAUDE.md` § Trampas conocidas
- [ ] **Invariante nuevo** → `CLAUDE.md` § Invariantes **+ un test que lo fije**
- [ ] **Decisión con trade-off** → ADR nuevo en `docs/adr/`
- [ ] **Comando / fuente / forma de correr algo** → `README.md`
- [ ] **Convención de código** → `CLAUDE.md` § Convenciones
- [ ] **Una decisión previa resultó incorrecta** → editar el ADR marcando el
      cambio (no borrarlo: el razonamiento viejo explica por qué el código es así)
- [ ] Nada de lo anterior aplica

### Números

`CLAUDE.md` y `README.md` afirman cantidades verificables: cantidad de tests,
426 movimientos, 58 líneas de Wompi, 9/9 declarado vs 47/55 inferido, el saldo
de −$8.822.659,76.

- [ ] Mi cambio no mueve ninguno
- [ ] Los moví y los actualicé **en todos los lugares donde aparecen**

## Verificación

- [ ] `pytest` en verde
- [ ] `ruff check .` limpio
- [ ] Si toqué ingesta o dominio: `conciliacion ingest bancolombia --offline`
      sigue dando 426 movimientos y reingerir da 0 nuevos
