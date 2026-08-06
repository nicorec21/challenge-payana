"""Carga de secretos y endpoints desde el entorno.

Acá SOLO viven credenciales, URLs y rutas. Las reglas de negocio están en
`config.py`, versionadas en git: una tasa de comisión tiene que poder revisarse
en un diff y reproducirse entre corridas, cosa que una variable de entorno no
garantiza.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: Raíz del repo. Las rutas relativas del .env se resuelven contra esto, no
#: contra el cwd: `conciliacion ingest` debe hacer lo mismo desde cualquier
#: directorio.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Falta la variable de entorno {name}. "
            f"Copiá .env.example a .env y completala."
        )
    return value


def _path(name: str, default: str) -> Path:
    raw = os.getenv(name, default).strip() or default
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


@dataclass(frozen=True, slots=True)
class WompiSettings:
    public_key: str
    private_key: str
    merchant_id: str
    api_base_url: str
    events_secret: str = ""

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.private_key}"}


@dataclass(frozen=True, slots=True)
class OdooSettings:
    url: str
    db: str
    user_id: int
    api_key: str
    company_id: int
    journal_wompi: int
    journal_bancolombia: int


@dataclass(frozen=True, slots=True)
class Settings:
    raw_data_dir: Path
    out_dir: Path
    database_path: Path
    _wompi: WompiSettings | None
    _odoo: OdooSettings | None

    @property
    def wompi(self) -> WompiSettings:
        """Falla acá y no al importar: se puede correr la ingesta de archivos
        locales sin tener credenciales de Wompi configuradas."""
        if self._wompi is None:
            raise RuntimeError("Credenciales de Wompi incompletas en .env")
        return self._wompi

    @property
    def odoo(self) -> OdooSettings:
        if self._odoo is None:
            raise RuntimeError("Credenciales de Odoo incompletas en .env")
        return self._odoo


def load_settings(env_file: Path | None = None) -> Settings:
    """Lee el .env. Las variables ya presentes en el entorno tienen prioridad
    (así CI puede inyectarlas sin archivo)."""
    load_dotenv(env_file or PROJECT_ROOT / ".env", override=False)

    wompi: WompiSettings | None = None
    try:
        wompi = WompiSettings(
            public_key=_require("WOMPI_PUBLIC_KEY"),
            private_key=_require("WOMPI_PRIVATE_KEY"),
            merchant_id=_require("WOMPI_MERCHANT_ID"),
            api_base_url=os.getenv("WOMPI_API_BASE_URL", "https://production.wompi.co/v1"),
            events_secret=os.getenv("WOMPI_EVENTS_SECRET", ""),
        )
    except RuntimeError:
        pass

    odoo: OdooSettings | None = None
    try:
        odoo = OdooSettings(
            url=_require("ODOO_URL"),
            db=_require("ODOO_DB"),
            user_id=int(_require("ODOO_USER_ID")),
            api_key=_require("ODOO_API_KEY"),
            company_id=int(_require("ODOO_COMPANY_ID")),
            journal_wompi=int(os.getenv("ODOO_JOURNAL_WOMPI", "48")),
            journal_bancolombia=int(os.getenv("ODOO_JOURNAL_BANCOLOMBIA", "49")),
        )
    except RuntimeError:
        pass

    return Settings(
        raw_data_dir=_path("RAW_DATA_DIR", "data/raw"),
        out_dir=_path("OUT_DIR", "data/out"),
        database_path=_path("DATABASE_PATH", "conciliacion.db"),
        _wompi=wompi,
        _odoo=odoo,
    )
