# sitadmin — Contractor Administration System

A custom administrative web application built for a contracting business (real client, in active daily use in production). Manages clients, jobs, expenses, invoices, cereal sales and a full check lifecycle.

## Tech Stack

- **Backend:** Python 3 · Flask 3
- **Database:** SQLite (single-file)
- **Templates:** Jinja2
- **Frontend:** HTML, CSS

## Features

### Core entities (10-table relational schema)

- `clientes` — clients
- `trabajos` — jobs / works performed
- `gastos` — expenses (linked optionally to a check)
- `gastos_recurrentes` — recurring expenses (weekly / fortnightly / monthly)
- `cobros` — payments received
- `cheques` — check movements (own emitted **and** third-party received) with status lifecycle: `emitido → depositado → acreditado`
- `facturas` — invoices
- `ventas_cereales` — cereal sales (tons × price/ton)
- `config` — runtime configuration (e.g. initial balance, payment-window in business days)

### Business logic highlights

- **Real-time balance projection** — `calcular_saldo_actual()` aggregates payments, deposited checks, expenses, cereal sales and invoices to compute the cash position for the day
- **Recurring expense forecasting** — `proyectar_recurrentes(inicio, limite)` walks through monthly / fortnightly / weekly schedules to project upcoming expenses across a date range
- **Business-day arithmetic** — `sumar_dias_habiles(fecha, dias)` skips weekends to estimate when a payment will land
- **Check lifecycle modeling** — own checks reduce the balance only once they're deposited or credited; third-party checks add to the balance only on credit date — preventing double-counting

## Project structure

```
.
├── app.py              # Flask app, routes and business logic (~840 lines)
├── database.py         # Schema bootstrap, connection helper (~155 lines)
├── contratista.db      # SQLite database (gitignored in production)
├── requirements.txt    # Flask
└── templates/          # 10 Jinja2 templates
    ├── base.html
    ├── index.html
    ├── clientes.html
    ├── trabajos.html
    ├── gastos.html
    ├── cheques.html
    ├── facturas.html
    ├── cereales.html
    ├── remito.html
    └── reporte.html
```

## Running locally

```bash
# 1. Clone and enter the project
git clone https://github.com/<your-username>/sitadmin.git
cd sitadmin

# 2. Set up a virtualenv
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run
python3 app.py
# Open http://localhost:5000
```

The SQLite schema is created automatically on first run via `init_db()`.

## What I learned building this

- Designing a 10-table relational schema for a non-trivial financial domain
- Implementing a state machine for check lifecycle (`emitido / recibido_tercero / depositado / acreditado`)
- Modelling cash flow with multiple parallel sources (payments, checks, cereal sales, invoices)
- Building forecasting logic that walks recurring schedules across arbitrary date ranges
- Skipping weekends with custom business-day arithmetic
- Shipping a system to a real, non-technical user and supporting it in production

## Author

Valentín Santamaría — final-year Software Development student, based in Dublin.
[LinkedIn](https://www.linkedin.com/in/valentin-santamaria-dev)
