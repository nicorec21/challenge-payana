"""Connector de la API de Wompi.

Sin red: `httpx.MockTransport` sirve respuestas con la forma real de la API,
incluyendo los campos con PII que devuelve `/transactions`.
"""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from conciliacion.ingest.connectors.wompi_api import (
    DISBURSEMENT_FIELDS,
    TRANSACTION_FIELDS,
    WompiApiConnector,
    redact_transaction,
)
from conciliacion.ingest.ports import FetchWindow, IngestionError
from conciliacion.settings import WompiSettings

SETTINGS = WompiSettings(
    public_key="pub_test_x",
    private_key="prv_test_x",
    merchant_id="203607",
    api_base_url="https://api.test/v1",
)

WINDOW = FetchWindow(start=date(2026, 4, 14), end=date(2026, 4, 14))

#: Forma real de una transacción de `/transactions`, con la PII que devuelve
#: de verdad. Los valores sensibles son inventados pero los NOMBRES de campo
#: son los que observamos en la API productiva.
TX_CON_PII = {
    "id": "1203607-1776192294-23532",
    "created_at": "2026-04-14T18:44:55.146Z",
    "finalized_at": "2026-04-14T18:45:39.000Z",
    "amount_in_cents": 22917500,
    "reference": "8s9n47ejuyuw8gqeh38zb",
    "currency": "COP",
    "payment_method_type": "CARD",
    "status": "APPROVED",
    "status_message": None,
    "bill_id": None,
    "payment_link_id": None,
    "disbursement": {
        "id": 3136141,
        "merchant_id": 203607,
        "amount_in_cents": 92637311,
        "status": "APPROVED",
        "created_at": "2026-04-15T13:27:43.992Z",
        "updated_at": "2026-04-15T23:12:10.617Z",
        "bank_account_type": "SAVINGS",
        "bank_account_number": "19300002179",
    },
    # --- todo lo de acá abajo NO debe sobrevivir ---
    "customer_email": "persona.inventada@example.com",
    "customer_data": {
        "full_name": "Nombre Inventado Apellido",
        "phone_number": "+573001112233",
        "device_id": "70a4830483bed3e808cf077722992828",
        "browser_info": {"browser_user_agent": "Mozilla/5.0 (Windows NT 10.0)"},
        "device_data_token": "eyJhbGciOiJIUzI1NiJ9.PAYLOAD",
    },
    "payment_method": {
        "type": "CARD",
        "extra": {
            "bin": "541590",
            "last_four": "8361",
            "brand": "MASTERCARD",
            "card_holder": "nombre inventado apellido",
        },
        "installments": 1,
    },
    "shipping_address": {"address_line_1": "Calle Falsa 123"},
    "payment_source_id": 99887766,
    "redirect_url": "https://app.example/pay?hash=secreto",
}

#: Cadenas que no pueden aparecer en NINGÚN lado después de la ingesta.
VALORES_PII = [
    "persona.inventada@example.com",
    "Nombre Inventado Apellido",
    "+573001112233",
    "70a4830483bed3e808cf077722992828",
    "541590",
    "8361",
    "card_holder",
    "Calle Falsa 123",
    "eyJhbGciOiJIUzI1NiJ9",
]


def transport(pages: list[dict]):
    """Sirve las páginas en orden; falla si piden una que no existe."""
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", 1))
        if page > len(pages):
            return httpx.Response(200, json={"data": [], "meta": {}})
        return httpx.Response(200, json=pages[page - 1])
    return httpx.MockTransport(handler)


def client_for(pages: list[dict]) -> httpx.Client:
    return httpx.Client(transport=transport(pages))


def one_page(items: list[dict]) -> list[dict]:
    return [{"data": items, "meta": {"page": 1, "page_size": 200, "total_results": len(items)}}]


class TestRedaccionDePII:
    """La garantía central: los datos de titulares de tarjeta no entran."""

    def test_la_proyeccion_no_deja_pasar_campos_sensibles(self):
        limpio = redact_transaction(TX_CON_PII)
        assert set(limpio) <= TRANSACTION_FIELDS
        for prohibido in ("customer_email", "customer_data", "payment_method",
                          "shipping_address", "payment_source_id", "redirect_url"):
            assert prohibido not in limpio

    def test_conserva_lo_que_la_conciliacion_necesita(self):
        limpio = redact_transaction(TX_CON_PII)
        assert limpio["id"] == TX_CON_PII["id"]
        assert limpio["amount_in_cents"] == 22917500
        assert limpio["status"] == "APPROVED"
        assert limpio["payment_method_type"] == "CARD"   # el tipo sí, la tarjeta no
        assert limpio["disbursement"]["id"] == 3136141

    def test_redacta_tambien_el_subobjeto_disbursement(self):
        """El número de cuenta bancaria viaja anidado; una proyección que solo
        mire el primer nivel lo deja pasar."""
        limpio = redact_transaction(TX_CON_PII)
        assert set(limpio["disbursement"]) <= DISBURSEMENT_FIELDS
        assert "bank_account_number" not in limpio["disbursement"]

    @pytest.mark.parametrize("valor", VALORES_PII)
    def test_ningun_valor_sensible_sobrevive_a_la_ingesta(self, valor, tmp_path):
        """El test que hace real la garantía: se recorre todo lo que salió del
        connector —registros emitidos Y archivo persistido— buscando cada
        cadena sensible."""
        connector = WompiApiConnector(
            "transactions", SETTINGS,
            archive_dir=tmp_path, client=client_for(one_page([TX_CON_PII])),
        )
        emitidos = json.dumps([r.payload for r in connector.fetch(WINDOW)], default=str)
        en_disco = "\n".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*.json"))

        assert valor not in emitidos
        assert valor not in en_disco

    def test_un_campo_nuevo_desconocido_no_pasa(self):
        """Allowlist, no denylist: si Wompi agrega un campo mañana, no entra
        aunque nadie actualice el código. Falla cerrada."""
        limpio = redact_transaction({**TX_CON_PII, "campo_nuevo_con_pii": "dato"})
        assert "campo_nuevo_con_pii" not in limpio


class TestPaginacion:
    def test_recorre_todas_las_paginas(self):
        pages = [
            {"data": [{"id": f"tx-{i}"} for i in range(200)],
             "meta": {"page": 1, "page_size": 200, "total_results": 250}},
            {"data": [{"id": f"tx-{i}"} for i in range(200, 250)],
             "meta": {"page": 2, "page_size": 200, "total_results": 250}},
        ]
        connector = WompiApiConnector("transactions", SETTINGS, client=client_for(pages))
        assert len(list(connector.fetch(WINDOW))) == 250

    def test_frena_cuando_alcanza_el_total(self):
        connector = WompiApiConnector(
            "transactions", SETTINGS, client=client_for(one_page([{"id": "a"}, {"id": "b"}]))
        )
        assert len(list(connector.fetch(WINDOW))) == 2

    def test_pagina_vacia_termina(self):
        pages = [{"data": [], "meta": {"page": 1, "total_results": 0}}]
        connector = WompiApiConnector("transactions", SETTINGS, client=client_for(pages))
        assert list(connector.fetch(WINDOW)) == []

    def test_paginacion_infinita_falla_fuerte(self):
        """Si `total_results` miente, el connector corta en vez de girar
        indefinidamente contra una API productiva."""
        def handler(request):
            return httpx.Response(200, json={
                "data": [{"id": "x"}],
                "meta": {"page": 1, "total_results": 10**9},
            })
        connector = WompiApiConnector(
            "transactions", SETTINGS, client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with pytest.raises(IngestionError, match="paginación no termina"):
            list(connector.fetch(WINDOW))


class TestContrato:
    def test_exige_rango_de_fechas(self):
        """Sin fechas, la API bajaría el histórico completo de una cuenta
        productiva. Es un error de uso, no un pedido de 'todo'."""
        connector = WompiApiConnector("transactions", SETTINGS, client=client_for(one_page([])))
        with pytest.raises(ValueError, match="rango de fechas"):
            list(connector.fetch(None))
        with pytest.raises(ValueError, match="rango de fechas"):
            list(connector.fetch(FetchWindow(start=date(2026, 4, 1), end=None)))

    def test_recurso_no_soportado(self):
        with pytest.raises(ValueError, match="Recurso no soportado"):
            WompiApiConnector("payouts", SETTINGS)

    def test_manda_el_bearer(self):
        capturado = {}
        def handler(request):
            capturado["auth"] = request.headers.get("authorization")
            capturado["params"] = dict(request.url.params)
            return httpx.Response(200, json={"data": [], "meta": {}})
        connector = WompiApiConnector(
            "disbursements", SETTINGS,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        list(connector.fetch(WINDOW))
        assert capturado["auth"] == "Bearer prv_test_x"
        assert capturado["params"]["from_date"] == "2026-04-14"
        assert capturado["params"]["until_date"] == "2026-04-14"

    def test_error_http_se_reporta_con_contexto(self):
        def handler(request):
            return httpx.Response(422, json={"error": {"type": "INPUT_VALIDATION_ERROR"}})
        connector = WompiApiConnector(
            "transactions", SETTINGS,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(IngestionError, match="422"):
            list(connector.fetch(WINDOW))

    def test_fallo_de_red_se_reporta(self):
        def handler(request):
            raise httpx.ConnectError("sin DNS")
        connector = WompiApiConnector(
            "transactions", SETTINGS,
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(IngestionError, match="Fallo de red"):
            list(connector.fetch(WINDOW))


class TestEvidencia:
    def test_archiva_lo_redactado(self, tmp_path):
        connector = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(one_page([TX_CON_PII])),
        )
        list(connector.fetch(WINDOW))
        archivos = list((tmp_path / "transactions").glob("*.json"))
        assert len(archivos) == 1
        assert archivos[0].name == "20260414-20260414-p001.json"
        guardado = json.loads(archivos[0].read_text(encoding="utf-8"))
        assert set(guardado[0]) <= TRANSACTION_FIELDS

    def test_el_locator_apunta_al_archivo_y_al_item(self, tmp_path):
        """Cada conclusión del sistema tiene que poder rastrearse hasta un
        archivo que alguien abre y una línea que alguien encuentra."""
        connector = WompiApiConnector(
            "transactions", SETTINGS, archive_dir=tmp_path,
            client=client_for(one_page([TX_CON_PII])),
        )
        record = next(iter(connector.fetch(WINDOW)))
        assert record.locator.endswith("#id=1203607-1776192294-23532")
        assert "20260414-20260414-p001.json" in record.locator

    def test_sin_archive_dir_igual_emite(self):
        connector = WompiApiConnector(
            "transactions", SETTINGS, client=client_for(one_page([TX_CON_PII]))
        )
        record = next(iter(connector.fetch(WINDOW)))
        assert record.locator.startswith("wompi_api://transactions#id=")

    def test_un_record_por_item_no_por_pagina(self):
        """A diferencia de un PDF —donde una línea no se interpreta sin el
        encabezado— un objeto JSON de la API se interpreta solo."""
        items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        connector = WompiApiConnector("transactions", SETTINGS, client=client_for(one_page(items)))
        records = list(connector.fetch(WINDOW))
        assert len(records) == 3
        assert [r.payload["id"] for r in records] == ["a", "b", "c"]

    def test_marca_los_registros_como_redactados(self):
        connector = WompiApiConnector(
            "transactions", SETTINGS, client=client_for(one_page([TX_CON_PII]))
        )
        record = next(iter(connector.fetch(WINDOW)))
        assert record.metadata["redacted"] is True
        assert record.metadata["resource"] == "transactions"
