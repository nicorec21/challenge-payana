"""Puertos de ingesta: dos ejes ortogonales.

La decisión de diseño central de la Fase 1 es separar:

  Connector — CÓMO llegan los bytes (archivo local, API REST, webhook, S3...)
  Adapter   — QUÉ significan esos bytes (layout A, layout B, JSON de Wompi...)

Son ejes independientes: un PDF con layout nuevo servido por el mismo connector
de archivo local cuesta 1 adapter. Una API nueva que devuelve un layout ya
conocido cuesta 1 connector. Si los fusionáramos en un solo "DataSource", cada
combinación nueva costaría una clase entera.

Ver ADR-0002.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from ..domain.movement import Movement


@dataclass(frozen=True, slots=True)
class RawRecord:
    """Una unidad cruda tal como salió de la fuente, antes de interpretarla.

    Es la evidencia. Todo `Movement` apunta a un `RawRecord` vía `raw_ref`, y
    por eso cualquier conclusión del sistema se puede rastrear hasta el byte
    original. Sin esto la explicabilidad se corta en la frontera de la ingesta.
    """

    #: Origen legible y estable: "data/raw/bancolombia/2025-03.pdf#pagina=2,linea=17"
    locator: str
    #: El payload. Dict para JSON/API, str para línea de texto/CSV, bytes para binario.
    payload: Any
    #: Momento de captura, no de ocurrencia del movimiento.
    fetched_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FetchWindow:
    """Rango que se le pide al connector. `None` = todo lo disponible."""

    start: date | None = None
    end: date | None = None


@runtime_checkable
class Connector(Protocol):
    """Trae bytes crudos de algún lado. No interpreta nada.

    Implementaciones: LocalFileConnector, WompiApiConnector, OdooRpcConnector.
    Un WebhookConnector entraría por el mismo protocolo sin tocar los adapters.
    """

    #: Identificador estable, aparece en los reportes.
    connector_id: str

    def fetch(self, window: FetchWindow | None = None) -> Iterator[RawRecord]:
        """Emite registros crudos. Perezoso: un extracto anual no entra cómodo
        en memoria y además queremos poder cortar temprano."""
        ...


@runtime_checkable
class Adapter(Protocol):
    """Traduce registros crudos de UN layout al modelo canónico.

    Un adapter no sabe de dónde vinieron los bytes ni dónde se van a guardar
    los movimientos. Esa ignorancia es lo que lo hace testeable con un fixture
    y reusable entre connectors.
    """

    #: Identificador estable. Va en `Movement.source_id`, o sea que aparece en
    #: la clave de idempotencia: cambiarlo re-ingiere todo.
    source_id: str
    #: Ledger destino de los movimientos que produce.
    ledger_id: str

    def sniff(self, record: RawRecord) -> bool:
        """¿Este adapter entiende este registro?

        Permite registrar varios adapters para la misma fuente física y que el
        pipeline elija por contenido. Es el mecanismo que resuelve "PDF con
        layout A o layout B" sin que el usuario tenga que decir cuál es.
        """
        ...

    def parse(self, record: RawRecord) -> Iterator[Movement]:
        """Traduce. Puede emitir 0..N movimientos por registro: una línea de
        extracto puede ser 1 movimiento, y un settlement de Wompi puede
        explotar en pago + comisión + IVA + retención."""
        ...


@runtime_checkable
class Auditor(Protocol):
    """Contrasta un registro contra las reglas de negocio y reporta desacuerdos.

    **Opcional**: un adapter que no lo implemente se ingiere igual. El pipeline
    lo detecta con `isinstance`.

    Va separado de `parse` a propósito, por tres razones:

    - `parse` traduce, y traducir no es juzgar. Un adapter que decide si un dato
      "está bien" mientras lo lee mezcla dos responsabilidades y termina
      corrigiendo en silencio (ver "los adapters no clasifican" en CLAUDE.md).
    - El resultado **no se persiste**. Un aviso guardado en `Movement.metadata`
      quedaría congelado: el movimiento es inmutable, así que un desvío contra
      el tarifario de enero seguiría afirmándose en marzo aunque la config ya se
      hubiera corregido. Auditar en cada corrida siempre habla del ahora.
    - Un desvío puede no tener movimiento donde colgarse. Si una retención baja
      a cero, la fila no emite movimiento —y esa desaparición es justamente lo
      que hay que avisar—.

    Devuelve mensajes ya redactados: quien los muestra (CLI, API, reporte) no
    debería tener que saber qué significa la desviación.
    """

    def audit(self, record: RawRecord) -> Iterator[str]:
        """Avisos sobre un registro que **ya parseó bien**. Vacío = todo cierra."""
        ...


class IngestionError(Exception):
    """Falla de parseo atribuible a un registro concreto."""

    def __init__(self, message: str, locator: str, cause: Exception | None = None) -> None:
        super().__init__(f"{message} [{locator}]")
        self.locator = locator
        self.cause = cause
