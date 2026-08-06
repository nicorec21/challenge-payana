# ADR-0004 — La explicación es un objeto de dominio, no un string

**Estado:** aceptado
**Fases:** 2 y 3

## Contexto

El challenge define un único requisito no negociable:

> lo único no negociable es que lo que el sistema afirma sea explicable

Y define **dos usuarios** con necesidades distintas:

1. el CFO — humano ocupado, quiere entender sin dolor;
2. una IA contadora — necesita consumir la info de forma programática y razonar.

El camino obvio es que el motor concluya y que después cada renderer arme su
texto: el CLI imprime prosa, la API devuelve un JSON.

## Decisión

El motor produce un `Explanation` **estructurado**
([`domain/explanation.py`](../../src/conciliacion/domain/explanation.py)) junto
con cada conclusión:

```python
Explanation(
    rule_id="flow.daily_batch_t1",       # estable, filtrable sin parsear prosa
    summary="...",                       # frase para el CFO
    source_movement_ids=(...),           # qué relaciona
    target_movement_ids=(...),
    gross=..., net=...,                  # qué montos
    adjustments=(Adjustment(...),),      # por qué difieren, y si fue
                                         #   DECLARED / INFERRED / ALLOCATED
    window=TimeWindow(...),              # qué ventana consideró
    confidence=Confidence.HIGH,
    alternatives=(Alternative(...),),    # qué descartó y por qué
    unexplained=Money(...),              # qué NO logra explicar
)
```

Los renderers (Markdown para el CFO, JSON para la IA) son proyecciones puras de
esta estructura. Ninguno calcula nada.

## Justificación

**Un solo motor, dos proyecciones.** Si el reporte del CFO y el JSON se generan
por caminos distintos, divergen — y entonces el sistema afirma dos cosas
distintas sobre el mismo hecho. En una herramienta de conciliación eso no es un
bug de presentación, es pérdida de auditabilidad.

**Lo estructurado hace explicable lo que la prosa esconde.** Tres campos que
solo existen porque son objetos:

- `adjustments[].source` distingue **observado** (`DECLARED`) de **supuesto**
  (`INFERRED`) de **prorrateado** (`ALLOCATED`). Un texto que dice "comisión
  $10" no dice si Wompi la declaró o si la dedujimos de la diferencia. Es
  exactamente la distinción entre "el sistema sabe" y "el sistema supone".
- `unexplained` fuerza a que el residuo sea explícito. Sin ese campo, la salida
  fácil es meter cualquier diferencia dentro de "comisiones" y que la
  conciliación *parezca* cerrar.
- `alternatives` es lo que convierte un match en un argumento. El challenge
  advierte que puede haber varios subconjuntos que suman lo mismo; exponer lo
  descartado y por qué es tan importante como el match elegido.

**Testeable.** Se puede afirmar `explanation.is_balanced` o
`adjustment_ratio < 0.05` en un test. Sobre un string solo se puede hacer
`assert "comisión" in texto`, que no verifica nada.

## `Confidence` es ordinal, no un float

`EXACT | HIGH | MEDIUM | LOW` en vez de un score 0..1.

Un `0.87` sugiere una calibración que no tenemos: saldría de pesos elegidos a
ojo y se leería como probabilidad. El CFO necesita decidir si firma o si
revisa, y eso es una escala de acción, no un decimal. Para ordenar findings
dentro de un mismo nivel están los campos numéricos reales (`unexplained`,
`adjustment_ratio`), que sí significan algo.

## `ExplanationBuilder`

Las reglas acumulan evidencia vía builder en vez de construir `Explanation` a
mano. Motivo práctico: si registrar un descarte es caro, las reglas dejan de
registrarlo, y la explicabilidad se degrada sola con el tiempo.

## Consecuencias

- **Contra:** más verboso que `return match, f"matcheó por {motivo}"`. Cada
  regla nueva debe poblar los campos.
- **A favor:** las salidas no pueden divergir; se puede consultar por
  `rule_id`; el residuo inexplicado es visible por construcción.

## Alternativas descartadas

- **Logging estructurado como explicación.** Los logs son para el que debuggea
  el sistema; la explicación es para el que audita la plata. Ciclos de vida
  distintos: la explicación se persiste con el finding y se versiona con la corrida.
- **Explicación generada por LLM.** Fluida, pero no auditable ni determinista;
  un reporte que cambia entre corridas sobre la misma data no sirve de
  respaldo contable. Un LLM puede *leer* este JSON — de hecho, ese es el
  segundo usuario.
