"""Tests de la lógica de negocio: días hábiles, saldo y gastos recurrentes."""
from datetime import date, timedelta

import app as sitadmin

HOY = date.today()
AYER = (HOY - timedelta(days=1)).isoformat()
MANANA = (HOY + timedelta(days=1)).isoformat()


# ── sumar_dias_habiles ──────────────────────────────────────────────────────

def test_dias_habiles_dentro_de_la_semana():
    # Lunes 6/10/2025 + 2 días hábiles = miércoles 8/10
    assert sitadmin.sumar_dias_habiles("2025-10-06", 2) == "2025-10-08"


def test_dias_habiles_saltea_fin_de_semana():
    # Viernes 10/10/2025 + 1 día hábil = lunes 13/10
    assert sitadmin.sumar_dias_habiles("2025-10-10", 1) == "2025-10-13"


def test_dias_habiles_desde_sabado():
    # Sábado 11/10/2025 + 1 día hábil = lunes 13/10
    assert sitadmin.sumar_dias_habiles("2025-10-11", 1) == "2025-10-13"


def test_cero_dias_habiles_devuelve_la_misma_fecha():
    assert sitadmin.sumar_dias_habiles("2025-10-11", 0) == "2025-10-11"


# ── calcular_saldo_actual ───────────────────────────────────────────────────

def _set_saldo_inicial(db, valor):
    db.execute("UPDATE config SET valor=? WHERE clave='saldo_inicial'", (str(valor),))
    db.commit()


def _cheque(db, tipo, monto, vencimiento, estado):
    cur = db.execute(
        """INSERT INTO cheques (tipo_movimiento, contraparte, monto, fecha_vencimiento, estado)
           VALUES (?, 'Test', ?, ?, ?)""",
        (tipo, monto, vencimiento, estado),
    )
    db.commit()
    return cur.lastrowid


def test_saldo_vacio_es_el_saldo_inicial(db):
    _set_saldo_inicial(db, 1000)
    assert sitadmin.calcular_saldo_actual() == 1000


def test_saldo_suma_cobros_hasta_hoy_y_no_los_futuros(db):
    db.execute("INSERT INTO cobros (tipo, monto, fecha) VALUES ('efectivo', 500, ?)", (AYER,))
    db.execute("INSERT INTO cobros (tipo, monto, fecha) VALUES ('efectivo', 9999, ?)", (MANANA,))
    db.commit()
    assert sitadmin.calcular_saldo_actual() == 500


def test_cheque_de_tercero_suma_solo_cuando_esta_acreditado(db):
    _cheque(db, "tercero_recibido", 300, AYER, "pendiente")
    _cheque(db, "tercero_recibido", 200, AYER, "depositado")
    assert sitadmin.calcular_saldo_actual() == 0

    _cheque(db, "tercero_recibido", 700, AYER, "acreditado")
    assert sitadmin.calcular_saldo_actual() == 700


def test_cheque_propio_resta_cuando_se_deposita_o_acredita(db):
    _set_saldo_inicial(db, 1000)
    _cheque(db, "propio_emitido", 100, AYER, "pendiente")
    assert sitadmin.calcular_saldo_actual() == 1000

    _cheque(db, "propio_emitido", 250, AYER, "depositado")
    _cheque(db, "propio_emitido", 150, AYER, "acreditado")
    assert sitadmin.calcular_saldo_actual() == 600


def test_gasto_pagado_con_cheque_propio_no_se_resta_dos_veces(db):
    _set_saldo_inicial(db, 1000)
    cheque_id = _cheque(db, "propio_emitido", 400, AYER, "acreditado")
    db.execute(
        """INSERT INTO gastos (categoria, descripcion, monto, fecha, tipo_pago, cheque_id)
           VALUES ('combustible', 'Gasoil', 400, ?, 'cheque_propio', ?)""",
        (AYER, cheque_id),
    )
    db.commit()
    # Se descuenta una sola vez (por el cheque), no 800
    assert sitadmin.calcular_saldo_actual() == 600


def test_gasto_en_efectivo_resta(db):
    _set_saldo_inicial(db, 1000)
    db.execute(
        "INSERT INTO gastos (categoria, descripcion, monto, fecha) VALUES ('insumos', 'Repuestos', 120, ?)",
        (AYER,),
    )
    db.commit()
    assert sitadmin.calcular_saldo_actual() == 880


def test_venta_de_cereal_suma_solo_si_esta_cobrada(db):
    db.execute(
        """INSERT INTO ventas_cereales (cereal, toneladas, precio_ton, fecha, fecha_cobro, estado)
           VALUES ('soja', 10, 300, ?, ?, 'pendiente')""",
        (AYER, AYER),
    )
    db.execute(
        """INSERT INTO ventas_cereales (cereal, toneladas, precio_ton, fecha, fecha_cobro, estado)
           VALUES ('maiz', 20, 150, ?, ?, 'cobrado')""",
        (AYER, AYER),
    )
    db.commit()
    assert sitadmin.calcular_saldo_actual() == 3000  # 20 t × 150


# ── proyectar_recurrentes ───────────────────────────────────────────────────

def _recurrente(db, frecuencia, monto=100, dia_mes=None, activo=1):
    db.execute(
        """INSERT INTO gastos_recurrentes (descripcion, categoria, monto, frecuencia, dia_mes, activo)
           VALUES ('Fijo', 'sueldos', ?, ?, ?, ?)""",
        (monto, frecuencia, dia_mes, activo),
    )
    db.commit()


def _fechas(inicio, limite):
    return [e["fecha"] for e in sitadmin.proyectar_recurrentes(inicio, limite)]


def test_recurrente_mensual(db):
    _recurrente(db, "mensual", dia_mes=10)
    assert _fechas(date(2025, 1, 1), date(2025, 3, 31)) == ["2025-01-10", "2025-02-10", "2025-03-10"]


def test_recurrente_mensual_dia_31_en_febrero_usa_el_ultimo_dia(db):
    _recurrente(db, "mensual", dia_mes=31)
    assert _fechas(date(2025, 2, 1), date(2025, 2, 28)) == ["2025-02-28"]


def test_recurrente_mensual_no_incluye_fechas_antes_del_inicio(db):
    _recurrente(db, "mensual", dia_mes=5)
    assert _fechas(date(2025, 1, 20), date(2025, 2, 20)) == ["2025-02-05"]


def test_recurrente_mensual_cruza_el_anio(db):
    _recurrente(db, "mensual", dia_mes=1)
    assert _fechas(date(2025, 12, 1), date(2026, 1, 31)) == ["2025-12-01", "2026-01-01"]


def test_recurrente_quincenal(db):
    _recurrente(db, "quincenal")
    assert _fechas(date(2025, 1, 1), date(2025, 2, 14)) == ["2025-01-01", "2025-01-15", "2025-02-01"]


def test_recurrente_semanal(db):
    _recurrente(db, "semanal")
    assert _fechas(date(2025, 1, 1), date(2025, 1, 22)) == [
        "2025-01-01", "2025-01-08", "2025-01-15", "2025-01-22",
    ]


def test_recurrente_inactivo_no_se_proyecta(db):
    _recurrente(db, "semanal", activo=0)
    assert _fechas(date(2025, 1, 1), date(2025, 1, 31)) == []


def test_recurrente_es_salida_de_dinero(db):
    _recurrente(db, "mensual", monto=250, dia_mes=1)
    (evento,) = sitadmin.proyectar_recurrentes(date(2025, 1, 1), date(2025, 1, 31))
    assert evento["signo"] == -1 and evento["monto"] == 250


# ── primer_fecha_disponible ─────────────────────────────────────────────────

def test_primer_fecha_disponible_encuentra_el_primer_dia_con_saldo():
    d1 = (HOY + timedelta(days=1)).isoformat()
    d2 = (HOY + timedelta(days=2)).isoformat()
    eventos = [
        {"fecha": d1, "monto": 300, "signo": 1},
        {"fecha": d2, "monto": 500, "signo": 1},
    ]
    assert sitadmin.primer_fecha_disponible(700, 100, eventos) == (d2, 900)


def test_primer_fecha_disponible_sin_fondos_suficientes():
    eventos = [{"fecha": MANANA, "monto": 50, "signo": 1}]
    assert sitadmin.primer_fecha_disponible(1000, 0, eventos) == (None, None)
