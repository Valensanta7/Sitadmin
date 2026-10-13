import os
from flask import Flask, render_template, request, redirect, url_for
from database import get_connection, init_db
from datetime import date, timedelta
import calendar

app = Flask(__name__)
app.jinja_env.globals['enumerate'] = enumerate

# ── HELPERS ──────────────────────────────────────────────────────────────────

def get_config(clave, default=0):
    conn = get_connection()
    row  = conn.execute("SELECT valor FROM config WHERE clave=?", (clave,)).fetchone()
    conn.close()
    return float(row['valor']) if row else default

def get_saldo_inicial():
    return get_config('saldo_inicial', 0)

def get_margen_cobro():
    return int(get_config('margen_cobro_dias', 5))

def sumar_dias_habiles(fecha_str, dias):
    d = date.fromisoformat(fecha_str)
    agregados = 0
    while agregados < dias:
        d += timedelta(days=1)
        if d.weekday() < 5:
            agregados += 1
    return d.isoformat()

def calcular_saldo_actual():
    hoy  = date.today().isoformat()
    conn = get_connection()
    saldo = get_saldo_inicial()

    # + cobros en efectivo/transferencia hasta hoy
    r = conn.execute("SELECT COALESCE(SUM(monto),0) FROM cobros WHERE fecha <= ?", (hoy,)).fetchone()
    saldo += r[0]

    # + cheques de terceros acreditados hasta hoy
    r = conn.execute("""SELECT COALESCE(SUM(monto),0) FROM cheques
        WHERE tipo_movimiento='tercero_recibido' AND estado='acreditado'
          AND fecha_vencimiento <= ?""", (hoy,)).fetchone()
    saldo += r[0]

    # - cheques propios depositados/acreditados hasta hoy
    r = conn.execute("""SELECT COALESCE(SUM(monto),0) FROM cheques
        WHERE tipo_movimiento='propio_emitido' AND estado IN ('depositado','acreditado')
          AND fecha_vencimiento <= ?""", (hoy,)).fetchone()
    saldo -= r[0]

    # - gastos hasta hoy (excepto los pagados con cheque propio para no duplicar)
    r = conn.execute("""SELECT COALESCE(SUM(monto),0) FROM gastos
        WHERE fecha <= ? AND (tipo_pago != 'cheque_propio' OR cheque_id IS NULL)""", (hoy,)).fetchone()
    saldo -= r[0]

    # + ventas cereales cobradas hasta hoy
    r = conn.execute("""SELECT COALESCE(SUM(toneladas*precio_ton),0) FROM ventas_cereales
        WHERE estado='cobrado' AND fecha_cobro <= ?""", (hoy,)).fetchone()
    saldo += r[0]

    # + facturas cobradas hasta hoy
    r = conn.execute("""SELECT COALESCE(SUM(monto),0) FROM facturas
        WHERE estado='cobrada' AND fecha_vencimiento <= ?""", (hoy,)).fetchone()
    saldo += r[0]

    conn.close()
    return saldo

def proyectar_recurrentes(inicio, limite):
    conn  = get_connection()
    fijos = conn.execute("SELECT * FROM gastos_recurrentes WHERE activo=1").fetchall()
    conn.close()
    for g in fijos:
        desc = f"{g['descripcion']} ({g['categoria']} · {g['frecuencia']})"
        d = inicio
        while d <= limite:
            if g['frecuencia'] == 'mensual':
                dia   = min(g['dia_mes'] or 1, calendar.monthrange(d.year, d.month)[1])
                fecha = date(d.year, d.month, dia)
                if inicio <= fecha <= limite:
                    yield {'fecha': fecha.isoformat(), 'monto': g['monto'],
                           'tipo': 'gasto_fijo', 'descripcion': desc, 'signo': -1}
                mes  = d.month + 1 if d.month < 12 else 1
                anio = d.year if d.month < 12 else d.year + 1
                d    = date(anio, mes, 1)
            elif g['frecuencia'] == 'quincenal':
                for dia in [1, 15]:
                    dia_real = min(dia, calendar.monthrange(d.year, d.month)[1])
                    fecha    = date(d.year, d.month, dia_real)
                    if inicio <= fecha <= limite:
                        yield {'fecha': fecha.isoformat(), 'monto': g['monto'],
                               'tipo': 'gasto_fijo', 'descripcion': desc, 'signo': -1}
                mes  = d.month + 1 if d.month < 12 else 1
                anio = d.year if d.month < 12 else d.year + 1
                d    = date(anio, mes, 1)
            elif g['frecuencia'] == 'semanal':
                if inicio <= d <= limite:
                    yield {'fecha': d.isoformat(), 'monto': g['monto'],
                           'tipo': 'gasto_fijo', 'descripcion': desc, 'signo': -1}
                d += timedelta(days=7)

def calcular_flujo(dias=90):
    hoy    = date.today()
    inicio = hoy - timedelta(days=30)
    limite = hoy + timedelta(days=dias)
    conn   = get_connection()
    margen = get_margen_cobro()

    eventos = []

    # Trabajos sin factura vinculada (evita duplicados)
    for row in conn.execute("""
        SELECT t.fecha_cobro_estimada AS fecha, t.hectareas*t.precio_ha AS monto,
               'cobro_trabajo' AS tipo,
               'Cobro: ' || c.nombre || ' · ' || t.tipo ||
               ' (' || CAST(CAST(t.hectareas AS INT) AS TEXT) || ' ha)' AS descripcion
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        WHERE t.estado != 'cobrado'
          AND t.fecha_cobro_estimada IS NOT NULL
          AND t.fecha_cobro_estimada BETWEEN ? AND ?
          AND t.id NOT IN (SELECT trabajo_id FROM facturas WHERE trabajo_id IS NOT NULL)
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        eventos.append({'fecha': row['fecha'], 'monto': row['monto'],
                        'tipo': row['tipo'], 'descripcion': row['descripcion'], 'signo': 1})

    # Cheques de terceros recibidos — con demora de acreditación (físico +2 días hábiles, ECHEQ +1)
    for row in conn.execute("""
        SELECT fecha_vencimiento, monto, modalidad, contraparte, estado
        FROM cheques
        WHERE tipo_movimiento='tercero_recibido'
          AND estado IN ('pendiente','depositado','acreditado')
          AND fecha_vencimiento BETWEEN ? AND ?
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        demora = 1 if row['modalidad'] == 'echeq' else 2
        tipo   = 'cheque_cobrado' if row['estado'] == 'acreditado' else 'cheque_entrada'
        fecha  = sumar_dias_habiles(row['fecha_vencimiento'], demora)
        eventos.append({'fecha': fecha, 'monto': row['monto'], 'tipo': tipo,
                        'descripcion': f"Cheque a cobrar: {row['contraparte']} · {row['modalidad'].upper()}",
                        'signo': 1})

    # Cobros en efectivo/transferencia
    for row in conn.execute("""
        SELECT cb.fecha, cb.monto, cb.descripcion, cb.tipo
        FROM cobros cb WHERE cb.fecha BETWEEN ? AND ?
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        tipo = 'cobro_realizado' if row['fecha'] < hoy.isoformat() else 'cobro_transferencia'
        eventos.append({'fecha': row['fecha'], 'monto': row['monto'], 'tipo': tipo,
                        'descripcion': row['descripcion'] or f"Cobro {row['tipo']}", 'signo': 1})

    # Cheques propios emitidos (salida)
    for row in conn.execute("""
        SELECT fecha_vencimiento, monto, contraparte, modalidad, estado
        FROM cheques
        WHERE tipo_movimiento='propio_emitido'
          AND estado IN ('pendiente','depositado','acreditado')
          AND fecha_vencimiento BETWEEN ? AND ?
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        tipo = 'cheque_pagado' if row['estado'] in ('depositado','acreditado') else 'cheque_salida'
        eventos.append({'fecha': row['fecha_vencimiento'], 'monto': row['monto'], 'tipo': tipo,
                        'descripcion': f"Cheque a pagar: {row['contraparte']} · {row['modalidad'].upper()}",
                        'signo': -1})

    # Gastos manuales (sin los pagados con cheque propio para no duplicar)
    for row in conn.execute("""
        SELECT fecha, monto, descripcion, categoria
        FROM gastos
        WHERE fecha BETWEEN ? AND ?
          AND (tipo_pago != 'cheque_propio' OR cheque_id IS NULL)
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        eventos.append({'fecha': row['fecha'], 'monto': row['monto'], 'tipo': 'gasto',
                        'descripcion': f"{row['descripcion']} ({row['categoria']})", 'signo': -1})

    # Facturas (fecha segura = vencimiento + margen días)
    for row in conn.execute("""
        SELECT f.fecha_vencimiento, f.monto, f.estado, c.nombre AS cliente_nombre, f.numero
        FROM facturas f JOIN clientes c ON c.id=f.cliente_id
        WHERE f.fecha_vencimiento BETWEEN ? AND ?
          AND f.estado IN ('pendiente','cobrada')
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        fecha_segura = (date.fromisoformat(row['fecha_vencimiento']) + timedelta(days=margen)).isoformat()
        tipo = 'factura_cobrada' if row['estado'] == 'cobrada' else 'factura_pendiente'
        desc = f"Factura {row['cliente_nombre']}"
        if row['numero']:
            desc += f" N° {row['numero']}"
        eventos.append({'fecha': fecha_segura, 'monto': row['monto'], 'tipo': tipo,
                        'descripcion': desc, 'signo': 1})

    # Ventas de cereales
    for row in conn.execute("""
        SELECT fecha_cobro AS fecha, toneladas*precio_ton AS monto, cereal, comprador, estado
        FROM ventas_cereales
        WHERE fecha_cobro IS NOT NULL AND fecha_cobro BETWEEN ? AND ?
    """, (inicio.isoformat(), limite.isoformat())).fetchall():
        tipo = 'cereal_cobrado' if row['estado'] == 'cobrado' else 'cereal_pendiente'
        desc = f"Cereal {row['cereal'].capitalize()}"
        if row['comprador']:
            desc += f" · {row['comprador']}"
        eventos.append({'fecha': row['fecha'], 'monto': row['monto'], 'tipo': tipo,
                        'descripcion': desc, 'signo': 1})

    conn.close()

    # Gastos recurrentes proyectados (solo futuros)
    for e in proyectar_recurrentes(hoy, limite):
        eventos.append(e)

    eventos.sort(key=lambda e: e['fecha'])

    saldo = calcular_saldo_actual()
    for e in eventos:
        if e['fecha'] >= hoy.isoformat():
            saldo += e['monto'] * e['signo']
        e['saldo_acumulado'] = saldo

    return eventos

def alertas_cheques():
    hoy    = date.today()
    limite = hoy + timedelta(days=10)
    conn   = get_connection()

    proximos = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='propio_emitido' AND estado='pendiente'
          AND fecha_vencimiento BETWEEN ? AND ?
        ORDER BY fecha_vencimiento
    """, (hoy.isoformat(), limite.isoformat())).fetchall()

    propios_vencidos = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='propio_emitido' AND estado='pendiente'
          AND fecha_vencimiento < ?
        ORDER BY fecha_vencimiento
    """, (hoy.isoformat(),)).fetchall()

    terceros_vencidos = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='tercero_recibido' AND estado='pendiente'
          AND fecha_vencimiento < ?
        ORDER BY fecha_vencimiento
    """, (hoy.isoformat(),)).fetchall()

    conn.close()
    return proximos, propios_vencidos, terceros_vencidos

def calcular_manana():
    manana = (date.today() + timedelta(days=1)).isoformat()
    conn   = get_connection()
    cheques = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='propio_emitido' AND estado='pendiente'
          AND fecha_vencimiento = ?
        ORDER BY monto DESC
    """, (manana,)).fetchall()
    conn.close()
    total = sum(c['monto'] for c in cheques)
    return cheques, total

def primer_fecha_disponible(monto, saldo_actual, eventos):
    hoy   = date.today().isoformat()
    saldo = saldo_actual
    for e in eventos:
        if e['fecha'] < hoy:
            continue
        saldo += e['monto'] * e['signo']
        if saldo >= monto:
            return e['fecha'], saldo
    return None, None


# ── DASHBOARD ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    hoy_str   = date.today().isoformat()
    manana_str = (date.today() + timedelta(days=1)).strftime('%d/%m/%Y')
    eventos   = calcular_flujo(90)
    alertas, propios_vencidos, terceros_vencidos = alertas_cheques()
    saldo_actual  = calcular_saldo_actual()
    saldo_inicial = get_saldo_inicial()
    margen        = get_margen_cobro()
    manana_cheques, manana_total = calcular_manana()
    manana_diferencia = saldo_actual - manana_total

    monto_calc    = request.args.get('monto_calc', type=float)
    fecha_sugerida = None
    saldo_en_fecha = None
    if monto_calc and monto_calc > 0:
        fecha_sugerida, saldo_en_fecha = primer_fecha_disponible(monto_calc, saldo_actual, eventos)

    futuros = [e for e in eventos if e['fecha'] >= hoy_str]
    saldo_proyectado = saldo_actual + sum(e['monto'] * e['signo'] for e in futuros)

    return render_template('index.html',
        eventos=eventos,
        alertas=alertas,
        propios_vencidos=propios_vencidos,
        terceros_vencidos=terceros_vencidos,
        saldo_actual=saldo_actual,
        saldo_inicial=saldo_inicial,
        saldo_proyectado=saldo_proyectado,
        manana_cheques=manana_cheques,
        manana_total=manana_total,
        manana_diferencia=manana_diferencia,
        manana_str=manana_str,
        monto_calc=monto_calc,
        fecha_sugerida=fecha_sugerida,
        saldo_en_fecha=saldo_en_fecha,
        margen=margen,
        hoy=hoy_str
    )

@app.route('/config/saldo', methods=['POST'])
def actualizar_saldo_inicial():
    valor = request.form.get('saldo_inicial', '0')
    conn  = get_connection()
    conn.execute("INSERT OR REPLACE INTO config (clave,valor) VALUES ('saldo_inicial',?)", (valor,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/config/margen', methods=['POST'])
def actualizar_margen():
    valor = request.form.get('margen_cobro_dias', '5')
    conn  = get_connection()
    conn.execute("INSERT OR REPLACE INTO config (clave,valor) VALUES ('margen_cobro_dias',?)", (valor,))
    conn.commit()
    conn.close()
    return redirect(url_for('facturas'))


# ── CLIENTES ─────────────────────────────────────────────────────────────────

@app.route('/clientes')
def clientes():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM clientes ORDER BY nombre").fetchall()

    deuda_facturas = {r['cliente_id']: r['total'] for r in conn.execute("""
        SELECT cliente_id, SUM(monto) as total FROM facturas
        WHERE estado='pendiente' GROUP BY cliente_id
    """).fetchall()}

    deuda_trabajos = {r['cliente_id']: r['total'] for r in conn.execute("""
        SELECT cliente_id, SUM(hectareas*precio_ha) as total FROM trabajos
        WHERE estado != 'cobrado'
          AND id NOT IN (SELECT trabajo_id FROM facturas WHERE trabajo_id IS NOT NULL)
        GROUP BY cliente_id
    """).fetchall()}

    deuda = {}
    for cid in set(list(deuda_facturas.keys()) + list(deuda_trabajos.keys())):
        deuda[cid] = (deuda_facturas.get(cid) or 0) + (deuda_trabajos.get(cid) or 0)

    conn.close()
    return render_template('clientes.html', clientes=rows, deuda=deuda)

@app.route('/clientes/nuevo', methods=['POST'])
def nuevo_cliente():
    f = request.form
    conn = get_connection()
    conn.execute("INSERT INTO clientes (nombre,localidad,telefono,notas) VALUES (?,?,?,?)",
        (f['nombre'], f.get('localidad'), f.get('telefono'), f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('clientes'))

@app.route('/clientes/<int:id>/eliminar', methods=['POST'])
def eliminar_cliente(id):
    conn = get_connection()
    conn.execute("DELETE FROM clientes WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('clientes'))


# ── TRABAJOS ─────────────────────────────────────────────────────────────────

@app.route('/trabajos')
def trabajos():
    conn = get_connection()
    rows = conn.execute("""
        SELECT t.*, c.nombre AS cliente_nombre, t.hectareas*t.precio_ha AS total
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        ORDER BY t.fecha_trabajo DESC
    """).fetchall()
    clientes = conn.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    conn.close()
    return render_template('trabajos.html', trabajos=rows, clientes=clientes)

@app.route('/trabajos/nuevo', methods=['POST'])
def nuevo_trabajo():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO trabajos
            (cliente_id,tipo,cultivo,campo,hectareas,precio_ha,
             fecha_trabajo,fecha_cobro_estimada,estado,notas)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (f['cliente_id'], f['tipo'], f.get('cultivo'), f.get('campo'),
          float(f['hectareas']), float(f['precio_ha']),
          f.get('fecha_trabajo') or None, f.get('fecha_cobro_estimada') or None,
          f.get('estado','presupuestado'), f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('trabajos'))

@app.route('/trabajos/<int:id>/estado', methods=['POST'])
def cambiar_estado_trabajo(id):
    conn = get_connection()
    conn.execute("UPDATE trabajos SET estado=? WHERE id=?", (request.form['estado'], id))
    conn.commit()
    conn.close()
    return redirect(url_for('trabajos'))

@app.route('/trabajos/<int:id>/eliminar', methods=['POST'])
def eliminar_trabajo(id):
    conn = get_connection()
    conn.execute("UPDATE cheques  SET trabajo_id=NULL WHERE trabajo_id=?", (id,))
    conn.execute("UPDATE facturas SET trabajo_id=NULL WHERE trabajo_id=?", (id,))
    conn.execute("UPDATE cobros   SET trabajo_id=NULL WHERE trabajo_id=?", (id,))
    conn.execute("DELETE FROM trabajos WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('trabajos'))

@app.route('/trabajos/<int:id>/remito')
def remito(id):
    conn = get_connection()
    trabajo = conn.execute("""
        SELECT t.*, c.nombre AS cliente_nombre, c.localidad, c.telefono,
               t.hectareas*t.precio_ha AS total
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        WHERE t.id=?
    """, (id,)).fetchone()
    cheques = conn.execute(
        "SELECT * FROM cheques WHERE trabajo_id=? ORDER BY fecha_vencimiento", (id,)
    ).fetchall()
    cobros = conn.execute(
        "SELECT * FROM cobros WHERE trabajo_id=? ORDER BY fecha", (id,)
    ).fetchall()
    conn.close()
    if not trabajo:
        return "Trabajo no encontrado", 404
    return render_template('remito.html', trabajo=trabajo, cheques=cheques,
                           cobros=cobros, hoy=date.today().strftime('%d/%m/%Y'))


# ── CHEQUES ──────────────────────────────────────────────────────────────────

@app.route('/cheques')
def cheques():
    conn = get_connection()
    rows = conn.execute("""
        SELECT c.*, t.tipo AS trabajo_tipo, cl.nombre AS cliente_nombre
        FROM cheques c
        LEFT JOIN trabajos t ON t.id=c.trabajo_id
        LEFT JOIN clientes cl ON cl.id=t.cliente_id
        ORDER BY c.fecha_vencimiento ASC
    """).fetchall()
    trabajos = conn.execute("""
        SELECT t.id, c.nombre || ' · ' || t.tipo ||
               ' (' || CAST(CAST(t.hectareas AS INT) AS TEXT) || ' ha)' AS label
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        ORDER BY t.fecha_trabajo DESC
    """).fetchall()
    conn.close()
    return render_template('cheques.html', cheques=rows, trabajos=trabajos,
                           hoy=date.today().isoformat())

@app.route('/cheques/nuevo', methods=['POST'])
def nuevo_cheque():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO cheques
            (tipo_movimiento,modalidad,numero,banco,contraparte,
             monto,fecha_emision,fecha_vencimiento,estado,trabajo_id,notas)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (f['tipo_movimiento'], f.get('modalidad','fisico'),
          f.get('numero'), f.get('banco'), f['contraparte'],
          float(f['monto']), f.get('fecha_emision') or None,
          f['fecha_vencimiento'], f.get('estado','pendiente'),
          f.get('trabajo_id') or None, f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('cheques'))

@app.route('/cheques/<int:id>/estado', methods=['POST'])
def cambiar_estado_cheque(id):
    conn = get_connection()
    conn.execute("UPDATE cheques SET estado=? WHERE id=?", (request.form['estado'], id))
    conn.commit()
    conn.close()
    return redirect(url_for('cheques'))

@app.route('/cheques/<int:id>/eliminar', methods=['POST'])
def eliminar_cheque(id):
    conn = get_connection()
    conn.execute("DELETE FROM cheques WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('cheques'))


# ── COBROS ───────────────────────────────────────────────────────────────────

@app.route('/cobros/nuevo', methods=['POST'])
def nuevo_cobro():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO cobros (trabajo_id,tipo,descripcion,monto,fecha,notas)
        VALUES (?,?,?,?,?,?)
    """, (f.get('trabajo_id') or None, f['tipo'],
          f.get('descripcion'), float(f['monto']), f['fecha'], f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/cobros/<int:id>/eliminar', methods=['POST'])
def eliminar_cobro(id):
    conn = get_connection()
    conn.execute("DELETE FROM cobros WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))


# ── GASTOS ───────────────────────────────────────────────────────────────────

@app.route('/gastos')
def gastos():
    conn = get_connection()
    rows = conn.execute("""
        SELECT g.*, ch.numero AS cheque_numero
        FROM gastos g LEFT JOIN cheques ch ON ch.id=g.cheque_id
        ORDER BY g.fecha DESC
    """).fetchall()
    recurrentes   = conn.execute(
        "SELECT * FROM gastos_recurrentes ORDER BY activo DESC, descripcion"
    ).fetchall()
    cheques_propios = conn.execute("""
        SELECT * FROM cheques WHERE tipo_movimiento='propio_emitido'
          AND estado='pendiente' ORDER BY fecha_vencimiento
    """).fetchall()
    conn.close()
    return render_template('gastos.html', gastos=rows, recurrentes=recurrentes,
                           cheques_propios=cheques_propios)

@app.route('/gastos/nuevo', methods=['POST'])
def nuevo_gasto():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO gastos (categoria,descripcion,proveedor,monto,fecha,tipo_pago,cheque_id,notas)
        VALUES (?,?,?,?,?,?,?,?)
    """, (f['categoria'], f['descripcion'], f.get('proveedor'),
          float(f['monto']), f['fecha'],
          f.get('tipo_pago','efectivo'),
          f.get('cheque_id') or None,
          f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('gastos'))

@app.route('/gastos/<int:id>/eliminar', methods=['POST'])
def eliminar_gasto(id):
    conn = get_connection()
    conn.execute("DELETE FROM gastos WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('gastos'))

@app.route('/gastos/recurrentes/nuevo', methods=['POST'])
def nuevo_gasto_recurrente():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO gastos_recurrentes (descripcion,categoria,monto,frecuencia,dia_mes,notas)
        VALUES (?,?,?,?,?,?)
    """, (f['descripcion'], f['categoria'], float(f['monto']),
          f['frecuencia'], f.get('dia_mes') or None, f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('gastos'))

@app.route('/gastos/recurrentes/<int:id>/toggle', methods=['POST'])
def toggle_gasto_recurrente(id):
    conn = get_connection()
    conn.execute("UPDATE gastos_recurrentes SET activo=1-activo WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('gastos'))

@app.route('/gastos/recurrentes/<int:id>/eliminar', methods=['POST'])
def eliminar_gasto_recurrente(id):
    conn = get_connection()
    conn.execute("DELETE FROM gastos_recurrentes WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('gastos'))


# ── CEREALES ─────────────────────────────────────────────────────────────────

@app.route('/cereales')
def cereales():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM ventas_cereales ORDER BY fecha DESC").fetchall()
    conn.close()
    return render_template('cereales.html', cereales=rows)

@app.route('/cereales/nuevo', methods=['POST'])
def nuevo_cereal():
    f = request.form
    conn = get_connection()
    conn.execute("""
        INSERT INTO ventas_cereales
            (cereal,toneladas,precio_ton,comprador,fecha,fecha_cobro,forma_cobro,estado,notas)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (f['cereal'], float(f['toneladas']), float(f['precio_ton']),
          f.get('comprador'), f['fecha'],
          f.get('fecha_cobro') or None,
          f.get('forma_cobro','transferencia'),
          f.get('estado','pendiente'),
          f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('cereales'))

@app.route('/cereales/<int:id>/estado', methods=['POST'])
def cambiar_estado_cereal(id):
    conn = get_connection()
    conn.execute("UPDATE ventas_cereales SET estado=? WHERE id=?", (request.form['estado'], id))
    conn.commit()
    conn.close()
    return redirect(url_for('cereales'))

@app.route('/cereales/<int:id>/eliminar', methods=['POST'])
def eliminar_cereal(id):
    conn = get_connection()
    conn.execute("DELETE FROM ventas_cereales WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('cereales'))


# ── FACTURAS ─────────────────────────────────────────────────────────────────

@app.route('/facturas')
def facturas():
    conn = get_connection()
    rows = conn.execute("""
        SELECT f.*, c.nombre AS cliente_nombre, t.tipo AS trabajo_tipo
        FROM facturas f
        JOIN clientes c ON c.id=f.cliente_id
        LEFT JOIN trabajos t ON t.id=f.trabajo_id
        ORDER BY f.fecha_vencimiento DESC
    """).fetchall()
    clientes = conn.execute("SELECT id, nombre FROM clientes ORDER BY nombre").fetchall()
    trabajos = conn.execute("""
        SELECT t.id, c.nombre || ' · ' || t.tipo ||
               ' (' || CAST(CAST(t.hectareas AS INT) AS TEXT) || ' ha)' AS label
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        ORDER BY t.fecha_trabajo DESC
    """).fetchall()
    margen = get_margen_cobro()
    conn.close()
    return render_template('facturas.html', facturas=rows, clientes=clientes,
                           trabajos=trabajos, margen=margen, hoy=date.today().isoformat())

@app.route('/facturas/nuevo', methods=['POST'])
def nueva_factura():
    f    = request.form
    dias = int(f.get('dias_pago', 30))
    fecha_emision     = f['fecha_emision']
    fecha_vencimiento = (date.fromisoformat(fecha_emision) + timedelta(days=dias)).isoformat()
    conn = get_connection()

    trabajo_id = f.get('trabajo_id') or None
    if not trabajo_id and f.get('tipo_trabajo'):
        hectareas = float(f.get('hectareas') or 1)
        precio_ha = float(f.get('precio_ha') or float(f['monto']))
        cur = conn.execute("""
            INSERT INTO trabajos
                (cliente_id,tipo,cultivo,campo,hectareas,precio_ha,
                 fecha_trabajo,fecha_cobro_estimada,estado,notas)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (int(f['cliente_id']), f['tipo_trabajo'],
              f.get('cultivo') or None, f.get('campo') or None,
              hectareas, precio_ha,
              f.get('fecha_trabajo') or fecha_emision,
              fecha_vencimiento, 'terminado', f.get('notas')))
        trabajo_id = cur.lastrowid

    conn.execute("""
        INSERT INTO facturas
            (numero,cliente_id,trabajo_id,monto,fecha_emision,dias_pago,fecha_vencimiento,estado,notas)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (f.get('numero') or None, int(f['cliente_id']),
          trabajo_id, float(f['monto']),
          fecha_emision, dias, fecha_vencimiento,
          f.get('estado','pendiente'), f.get('notas')))
    conn.commit()
    conn.close()
    return redirect(url_for('facturas'))

@app.route('/facturas/<int:id>/estado', methods=['POST'])
def cambiar_estado_factura(id):
    conn = get_connection()
    conn.execute("UPDATE facturas SET estado=? WHERE id=?", (request.form['estado'], id))
    conn.commit()
    conn.close()
    return redirect(url_for('facturas'))

@app.route('/facturas/<int:id>/eliminar', methods=['POST'])
def eliminar_factura(id):
    conn = get_connection()
    conn.execute("DELETE FROM facturas WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('facturas'))


# ── REPORTE ──────────────────────────────────────────────────────────────────

@app.route('/reporte')
def reporte():
    hoy  = date.today()
    mes  = int(request.args.get('mes',  hoy.month))
    anio = int(request.args.get('anio', hoy.year))

    primer_dia = date(anio, mes, 1).isoformat()
    ultimo_dia = date(anio, mes, calendar.monthrange(anio, mes)[1]).isoformat()

    conn = get_connection()

    trabajos_mes = conn.execute("""
        SELECT t.*, c.nombre AS cliente_nombre, t.hectareas*t.precio_ha AS total
        FROM trabajos t JOIN clientes c ON c.id=t.cliente_id
        WHERE t.fecha_trabajo BETWEEN ? AND ?
        ORDER BY t.fecha_trabajo
    """, (primer_dia, ultimo_dia)).fetchall()

    cheques_cobrados = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='tercero_recibido'
          AND estado NOT IN ('rechazado','anulado')
          AND fecha_vencimiento BETWEEN ? AND ?
        ORDER BY fecha_vencimiento
    """, (primer_dia, ultimo_dia)).fetchall()

    cobros_transfer = conn.execute("""
        SELECT cb.*, cl.nombre AS cliente_nombre
        FROM cobros cb
        LEFT JOIN trabajos t ON t.id=cb.trabajo_id
        LEFT JOIN clientes cl ON cl.id=t.cliente_id
        WHERE cb.fecha BETWEEN ? AND ?
        ORDER BY cb.fecha
    """, (primer_dia, ultimo_dia)).fetchall()

    facturas_mes = conn.execute("""
        SELECT f.*, c.nombre AS cliente_nombre
        FROM facturas f JOIN clientes c ON c.id=f.cliente_id
        WHERE f.fecha_vencimiento BETWEEN ? AND ? AND f.estado='cobrada'
        ORDER BY f.fecha_vencimiento
    """, (primer_dia, ultimo_dia)).fetchall()

    cereales_mes = conn.execute("""
        SELECT *, toneladas*precio_ton AS total FROM ventas_cereales
        WHERE fecha_cobro BETWEEN ? AND ? AND estado='cobrado'
        ORDER BY fecha_cobro
    """, (primer_dia, ultimo_dia)).fetchall()

    cheques_pagados = conn.execute("""
        SELECT * FROM cheques
        WHERE tipo_movimiento='propio_emitido'
          AND estado NOT IN ('anulado')
          AND fecha_vencimiento BETWEEN ? AND ?
        ORDER BY fecha_vencimiento
    """, (primer_dia, ultimo_dia)).fetchall()

    gastos_mes = conn.execute("""
        SELECT g.*, ch.numero AS cheque_numero
        FROM gastos g LEFT JOIN cheques ch ON ch.id=g.cheque_id
        WHERE g.fecha BETWEEN ? AND ?
        ORDER BY g.fecha
    """, (primer_dia, ultimo_dia)).fetchall()

    conn.close()

    inicio_mes = date(anio, mes, 1)
    fin_mes    = date(anio, mes, calendar.monthrange(anio, mes)[1])
    gastos_fijos_mes = list(proyectar_recurrentes(inicio_mes, fin_mes))

    total_trabajos = sum(t['total'] for t in trabajos_mes)
    total_cobrado  = (sum(c['monto'] for c in cheques_cobrados)
                      + sum(c['monto'] for c in cobros_transfer)
                      + sum(f['monto'] for f in facturas_mes)
                      + sum(c['total'] for c in cereales_mes))
    total_pagado   = sum(c['monto'] for c in cheques_pagados)
    total_gastos   = (sum(g['monto'] for g in gastos_mes)
                      + sum(g['monto'] for g in gastos_fijos_mes))
    resultado      = total_cobrado - total_pagado - total_gastos

    meses_nombres = ['Enero','Febrero','Marzo','Abril','Mayo','Junio',
                     'Julio','Agosto','Septiembre','Octubre','Noviembre','Diciembre']

    return render_template('reporte.html',
        mes=mes, anio=anio, mes_nombre=meses_nombres[mes-1],
        trabajos_mes=trabajos_mes,
        cheques_cobrados=cheques_cobrados,
        cobros_transfer=cobros_transfer,
        facturas_mes=facturas_mes,
        cereales_mes=cereales_mes,
        cheques_pagados=cheques_pagados,
        gastos_mes=gastos_mes,
        gastos_fijos_mes=gastos_fijos_mes,
        total_trabajos=total_trabajos,
        total_cobrado=total_cobrado,
        total_pagado=total_pagado,
        total_gastos=total_gastos,
        resultado=resultado,
        meses=meses_nombres,
        anios=list(range(hoy.year - 2, hoy.year + 2))
    )


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    init_db()
    app.run(debug=os.environ.get('FLASK_DEBUG') == '1', port=5050)
