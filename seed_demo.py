"""Carga datos ficticios para probar la app o sacar capturas.

Uso:
    SITADMIN_DB=demo.db python3 seed_demo.py
    SITADMIN_DB=demo.db python3 app.py

Todos los nombres y montos son inventados.
"""
from datetime import date, timedelta

from database import get_connection, init_db


def d(dias):
    return (date.today() + timedelta(days=dias)).isoformat()


def main():
    init_db()
    conn = get_connection()
    if conn.execute("SELECT COUNT(*) FROM clientes").fetchone()[0]:
        print("La base ya tiene datos; no se cargó nada.")
        return

    conn.execute("UPDATE config SET valor='2500000' WHERE clave='saldo_inicial'")

    clientes = [
        ("Estancia Los Álamos", "Lobería", "2262 400001"),
        ("Agro San Martín SA", "Tandil", "249 400002"),
        ("Campo La Tranquera", "Necochea", "2262 400003"),
    ]
    conn.executemany("INSERT INTO clientes (nombre, localidad, telefono) VALUES (?,?,?)", clientes)

    trabajos = [
        (1, "cosecha", "soja", "Lote 4", 120, 38000, d(-20), d(10), "terminado"),
        (2, "siembra", "trigo", "El Bajo", 85, 26000, d(-5), d(25), "en_curso"),
        (3, "fumigacion", "maíz", "Potrero 2", 60, 9000, d(-40), d(-10), "cobrado"),
        (1, "siembra", "maíz", "Lote 7", 150, 27000, d(15), d(45), "presupuestado"),
    ]
    conn.executemany(
        """INSERT INTO trabajos (cliente_id, tipo, cultivo, campo, hectareas, precio_ha,
           fecha_trabajo, fecha_cobro_estimada, estado) VALUES (?,?,?,?,?,?,?,?,?)""",
        trabajos,
    )

    cheques = [
        ("tercero_recibido", "fisico", "00012345", "Banco Nación", "Agro San Martín SA", 1800000, d(-3), "acreditado"),
        ("tercero_recibido", "echeq", "E-77810", "Banco Provincia", "Estancia Los Álamos", 2200000, d(12), "pendiente"),
        ("propio_emitido", "echeq", "E-55120", "Banco Galicia", "Repuestos del Sur", 950000, d(3), "pendiente"),
        ("propio_emitido", "fisico", "00098765", "Banco Galicia", "Combustibles Lobería", 1300000, d(-8), "acreditado"),
    ]
    conn.executemany(
        """INSERT INTO cheques (tipo_movimiento, modalidad, numero, banco, contraparte, monto,
           fecha_vencimiento, estado) VALUES (?,?,?,?,?,?,?,?)""",
        cheques,
    )

    gastos = [
        ("combustible", "Gasoil cosecha", "Combustibles Lobería", 1300000, d(-8), "cheque_propio", 4),
        ("reparacion", "Cambio de correas cosechadora", "Taller Méndez", 420000, d(-15), "transferencia", None),
        ("insumos", "Lubricantes", "Repuestos del Sur", 180000, d(-2), "efectivo", None),
    ]
    conn.executemany(
        """INSERT INTO gastos (categoria, descripcion, proveedor, monto, fecha, tipo_pago, cheque_id)
           VALUES (?,?,?,?,?,?,?)""",
        gastos,
    )

    conn.executemany(
        "INSERT INTO gastos_recurrentes (descripcion, categoria, monto, frecuencia, dia_mes) VALUES (?,?,?,?,?)",
        [("Sueldos operarios", "sueldos", 1600000, "mensual", 5),
         ("Seguro maquinaria", "otro", 210000, "mensual", 20)],
    )

    conn.execute(
        "INSERT INTO cobros (trabajo_id, tipo, descripcion, monto, fecha) VALUES (3, 'transferencia', 'Fumigación Potrero 2', 540000, ?)",
        (d(-10),),
    )

    conn.executemany(
        """INSERT INTO ventas_cereales (cereal, toneladas, precio_ton, comprador, fecha, fecha_cobro, forma_cobro, estado)
           VALUES (?,?,?,?,?,?,?,?)""",
        [("soja", 60, 310000, "Acopio Costa Atlántica", d(-12), d(-4), "transferencia", "cobrado"),
         ("trigo", 45, 190000, "Cooperativa Lobería", d(-1), d(20), "transferencia", "pendiente")],
    )

    conn.execute(
        """INSERT INTO facturas (numero, cliente_id, trabajo_id, monto, fecha_emision, dias_pago, fecha_vencimiento, estado)
           VALUES ('0001-00000124', 1, 1, 4560000, ?, 30, ?, 'pendiente')""",
        (d(-20), d(10)),
    )

    conn.commit()
    conn.close()
    print("Datos de demo cargados.")


if __name__ == "__main__":
    main()
