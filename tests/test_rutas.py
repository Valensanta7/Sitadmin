"""Tests de las páginas y formularios principales."""
from datetime import date, timedelta

import pytest

HOY = date.today()


@pytest.mark.parametrize(
    "url", ["/", "/clientes", "/trabajos", "/cheques", "/gastos", "/cereales", "/facturas", "/reporte"]
)
def test_las_paginas_cargan_con_la_base_vacia(client, url):
    assert client.get(url).status_code == 200


def test_crear_cliente(client, db):
    r = client.post("/clientes/nuevo", data={"nombre": "Estancia La Esperanza", "localidad": "Lobería"})
    assert r.status_code == 302
    assert db.execute("SELECT nombre FROM clientes").fetchone()["nombre"] == "Estancia La Esperanza"
    assert "Estancia La Esperanza" in client.get("/clientes").get_data(as_text=True)


def test_crear_factura_calcula_vencimiento(client, db):
    client.post("/clientes/nuevo", data={"nombre": "Cliente"})
    client.post(
        "/facturas/nuevo",
        data={"cliente_id": "1", "monto": "1500", "fecha_emision": "2025-03-01", "dias_pago": "30"},
    )
    f = db.execute("SELECT fecha_vencimiento, monto FROM facturas").fetchone()
    assert f["fecha_vencimiento"] == "2025-03-31"
    assert f["monto"] == 1500


def test_factura_con_tipo_de_trabajo_crea_el_trabajo(client, db):
    client.post("/clientes/nuevo", data={"nombre": "Cliente"})
    client.post(
        "/facturas/nuevo",
        data={
            "cliente_id": "1", "monto": "5000", "fecha_emision": "2025-03-01",
            "tipo_trabajo": "cosecha", "hectareas": "50", "precio_ha": "100",
        },
    )
    t = db.execute("SELECT tipo, hectareas, estado FROM trabajos").fetchone()
    assert (t["tipo"], t["hectareas"], t["estado"]) == ("cosecha", 50, "terminado")
    assert db.execute("SELECT trabajo_id FROM facturas").fetchone()["trabajo_id"] == 1


def test_cambiar_estado_de_cheque_actualiza_el_saldo(client, db):
    ayer = (HOY - timedelta(days=1)).isoformat()
    client.post(
        "/cheques/nuevo",
        data={"tipo_movimiento": "tercero_recibido", "contraparte": "Acopio", "monto": "800",
              "fecha_vencimiento": ayer},
    )
    import app as sitadmin

    assert sitadmin.calcular_saldo_actual() == 0
    client.post("/cheques/1/estado", data={"estado": "acreditado"})
    assert sitadmin.calcular_saldo_actual() == 800


def test_toggle_gasto_recurrente(client, db):
    client.post(
        "/gastos/recurrentes/nuevo",
        data={"descripcion": "Sueldo", "categoria": "sueldos", "monto": "1000", "frecuencia": "mensual"},
    )
    client.post("/gastos/recurrentes/1/toggle")
    assert db.execute("SELECT activo FROM gastos_recurrentes").fetchone()["activo"] == 0


def test_calculadora_de_pago_del_dashboard(client, db):
    r = client.get("/?monto_calc=100")
    assert r.status_code == 200
