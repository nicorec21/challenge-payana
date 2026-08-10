"""Replay del archivo: la ingesta offline reproduce la ingesta de red.

La garantía que se fija acá es de equivalencia: un ledger construido desde los
payloads archivados tiene que ser idéntico —movimiento a movimiento, locator a
locator— al que construyó el connector de red que los archivó. Si divergen, la
promesa de `--offline` (reproducir la conciliación completa recién clonado) se
rompe en silencio.
"""

import json
from datetime import date
from pathlib import Path

import httpx

from conciliacion.ingest.adapters.wompi_api import WompiApiTransactionsAdapter
from conciliacion.ingest.connectors.archive_replay import ArchiveReplayConnector
from conciliacion.ingest.connectors.wompi_api import WompiApiConnector
from conciliacion.ingest.ports import FetchWindow
from conciliacion.settings import Settings, WompiSettings
from conciliacion.sources import build_registry

SETTINGS = WompiSettings(
    public_key="pub_test_x",
    private_key="prv_test_x",
    merchant_id="203607",
    api_base_url="https://api.test/v1",
)

WINDOW = FetchWindow(start=date(2026, 4, 14), end=date(2026, 4, 15))

TRANSACCIONES = [
    {
        "id": "1203607-1776192294-23532",
        "created_at": "2026-04-14T18:44:55.146Z",
        "amount_in_cents": 22917500,
        "currency": "COP",
        "reference": "8s9n47ejuyuw8gqeh38zb",
        "payment_method_type": "CARD",
        "status": "APPROVED",
        "disbursement": {"id": 3136141, "amount_in_cents": 92637311},
    },
    {
        "id": "1203607-1776117137-25637",
        "created_at": "2026-04-15T02:22:17.000Z",
        "amount_in_cents": 1000000,
        "currency": "COP",
        "reference": "otra-ref",
        "payment_method_type": "CARD",
        "status": "DECLINED",
        "disbursement": None,
    },
]


def client_for(items: list[dict]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": items, "meta": {"page": 1, "total_results": len(items)}}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def replay_de(archive_dir: Path) -> ArchiveReplayConnector:
    return ArchiveReplayConnector(
        archive_dir / "transactions",
        fragment="id",
        metadata={"resource": "transactions", "redacted": True},
        connector_id="replay_wompi_transactions",
        # En producción ambos resuelven contra la raíz del repo; en el test el
        # archivo vive en tmp_path y se fija la misma base que usa el de red.
        project_root=Path.cwd(),
    )


class TestEquivalenciaConLaRed:
    def test_reemite_los_mismos_registros_que_archivo_la_red(self, tmp_path):
        red = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(TRANSACCIONES),
        )
        de_red = list(red.fetch(WINDOW))
        de_replay = list(replay_de(tmp_path).fetch())

        assert [r.locator for r in de_replay] == [r.locator for r in de_red]
        assert [r.payload for r in de_replay] == [r.payload for r in de_red]

    def test_los_movimientos_son_identicos_por_ambos_caminos(self, tmp_path):
        """La equivalencia que importa: mismo `Movement.id`, mismo todo. Es lo
        que hace que la conciliación regenerada offline sea diffeable contra la
        generada en vivo (`docs/salida/`)."""
        adapter = WompiApiTransactionsAdapter()
        red = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(TRANSACCIONES),
        )
        de_red = [m for r in red.fetch(WINDOW) for m in adapter.parse(r)]
        de_replay = [
            m for r in replay_de(tmp_path).fetch() for m in adapter.parse(r)
        ]

        assert de_replay == de_red
        assert [m.id for m in de_replay] == [m.id for m in de_red]

    def test_el_sniff_reconoce_los_registros_del_replay(self, tmp_path):
        """El adapter elige por `metadata["resource"]`: si el replay no lo
        emitiera, la ingesta offline descartaría todo en silencio."""
        red = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(TRANSACCIONES),
        )
        list(red.fetch(WINDOW))
        adapter = WompiApiTransactionsAdapter()
        assert all(adapter.sniff(r) for r in replay_de(tmp_path).fetch())


class TestContratoDelReplay:
    def test_ignora_la_ventana_y_no_escribe(self, tmp_path):
        """El archivo ES la ventana que se pidió al capturarlo; el replay no
        recorta ni agrega evidencia."""
        red = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(TRANSACCIONES),
        )
        list(red.fetch(WINDOW))
        antes = sorted(p.name for p in tmp_path.rglob("*"))

        acotada = FetchWindow(start=date(2030, 1, 1), end=date(2030, 1, 2))
        assert len(list(replay_de(tmp_path).fetch(acotada))) == len(TRANSACCIONES)
        assert sorted(p.name for p in tmp_path.rglob("*")) == antes

    def test_directorio_inexistente_emite_vacio(self, tmp_path):
        assert list(replay_de(tmp_path / "no_existe").fetch()) == []

    def test_el_fragment_de_odoo_usa_line(self, tmp_path):
        lineas = [{"id": 98, "debit": 100.0, "credit": 0.0}]
        (tmp_path / "lines-p001.json").write_text(
            json.dumps(lineas), encoding="utf-8"
        )
        connector = ArchiveReplayConnector(
            tmp_path,
            fragment="line",
            metadata={"account_code": "1110001", "redacted": True},
            connector_id="replay_odoo_1110001",
            project_root=Path.cwd(),
        )
        record = next(iter(connector.fetch()))
        assert record.locator.endswith("lines-p001.json#line=98")
        assert record.metadata["page"] == 1


class TestRegistryOffline:
    def test_offline_declara_las_mismas_fuentes_que_online(self, tmp_path):
        """`--offline` ya no omite fuentes: las sirve por replay. Un clon
        fresco ve el mismo mapa de fuentes que una corrida con credenciales."""
        settings = Settings(
            raw_data_dir=tmp_path,
            out_dir=tmp_path / "out",
            database_path=tmp_path / "db.sqlite",
            _wompi=None,   # sin credenciales: el replay no las necesita
            _odoo=None,
        )
        registry = build_registry(settings, offline=True)
        nombres = {
            spec.name
            for ledger in ("wompi", "bancolombia", "wompi_erp", "bancolombia_erp")
            for spec in registry.sources_for(ledger)
        }
        assert nombres == {
            "bancolombia_extractos_pdf",
            "wompi_desembolsos_csv",
            "wompi_api_transacciones",
            "wompi_api_desembolsos",
            "odoo_libro_wompi",
            "odoo_libro_bancolombia",
        }
