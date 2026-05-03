import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'contratista.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            localidad TEXT,
            telefono TEXT,
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS trabajos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL REFERENCES clientes(id),
            tipo TEXT NOT NULL CHECK(tipo IN ('cosecha','siembra','fumigacion','otro')),
            cultivo TEXT,
            campo TEXT,
            hectareas REAL NOT NULL,
            precio_ha REAL NOT NULL,
            fecha_trabajo DATE,
            fecha_cobro_estimada DATE,
            estado TEXT NOT NULL DEFAULT 'presupuestado'
                CHECK(estado IN ('presupuestado','en_curso','terminado','cobrado')),
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            categoria TEXT NOT NULL
                CHECK(categoria IN ('combustible','reparacion','insumos','sueldos','impuestos','otro')),
            descripcion TEXT NOT NULL,
            proveedor TEXT,
            monto REAL NOT NULL,
            fecha DATE NOT NULL,
            tipo_pago TEXT NOT NULL DEFAULT 'efectivo'
                CHECK(tipo_pago IN ('cheque_propio','transferencia','efectivo')),
            cheque_id INTEGER REFERENCES cheques(id),
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cheques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_movimiento TEXT NOT NULL
                CHECK(tipo_movimiento IN ('propio_emitido','tercero_recibido')),
            modalidad TEXT NOT NULL DEFAULT 'fisico'
                CHECK(modalidad IN ('fisico','echeq')),
            numero TEXT,
            banco TEXT,
            contraparte TEXT NOT NULL,
            monto REAL NOT NULL,
            fecha_emision DATE,
            fecha_vencimiento DATE NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK(estado IN ('pendiente','depositado','acreditado','rechazado','anulado')),
            trabajo_id INTEGER REFERENCES trabajos(id),
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS cobros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trabajo_id INTEGER REFERENCES trabajos(id),
            tipo TEXT NOT NULL CHECK(tipo IN ('transferencia','efectivo')),
            descripcion TEXT,
            monto REAL NOT NULL,
            fecha DATE NOT NULL,
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS gastos_recurrentes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descripcion TEXT NOT NULL,
            categoria TEXT NOT NULL
                CHECK(categoria IN ('combustible','reparacion','insumos','sueldos','impuestos','otro')),
            monto REAL NOT NULL,
            frecuencia TEXT NOT NULL CHECK(frecuencia IN ('mensual','quincenal','semanal')),
            dia_mes INTEGER,
            activo INTEGER NOT NULL DEFAULT 1,
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ventas_cereales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cereal TEXT NOT NULL CHECK(cereal IN ('soja','maiz','trigo','girasol','sorgo','otro')),
            toneladas REAL NOT NULL,
            precio_ton REAL NOT NULL,
            comprador TEXT,
            fecha DATE NOT NULL,
            fecha_cobro DATE,
            forma_cobro TEXT NOT NULL DEFAULT 'transferencia'
                CHECK(forma_cobro IN ('transferencia','efectivo','cheque','a_fijar')),
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK(estado IN ('pendiente','cobrado')),
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS facturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            numero TEXT,
            cliente_id INTEGER NOT NULL REFERENCES clientes(id),
            trabajo_id INTEGER REFERENCES trabajos(id),
            monto REAL NOT NULL,
            fecha_emision DATE NOT NULL,
            dias_pago INTEGER NOT NULL DEFAULT 30,
            fecha_vencimiento DATE NOT NULL,
            estado TEXT NOT NULL DEFAULT 'pendiente'
                CHECK(estado IN ('pendiente','cobrada','vencida')),
            notas TEXT,
            creado_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS config (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL
        );
    """)

    cur.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES ('saldo_inicial', '0')")
    cur.execute("INSERT OR IGNORE INTO config (clave, valor) VALUES ('margen_cobro_dias', '5')")

    # Migraciones seguras para bases existentes
    for sql in [
        "ALTER TABLE gastos ADD COLUMN tipo_pago TEXT NOT NULL DEFAULT 'efectivo'",
        "ALTER TABLE gastos ADD COLUMN cheque_id INTEGER REFERENCES cheques(id)",
    ]:
        try:
            cur.execute(sql)
        except Exception:
            pass

    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print("Base de datos inicializada.")
