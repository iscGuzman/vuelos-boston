"""Monitor de precios: Morelia/Querétaro -> Boston (feb 2027).

Uso:
  python vuelos.py run      # consulta Google Flights y guarda precios en precios.db
  python vuelos.py report   # resume lo mejor encontrado y los patrones por hora/día
  python vuelos.py html     # genera reporte.html (también se genera tras cada `run`)
"""

import json
import os
import random
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from fast_flights import FlightQuery, create_query, get_flights

ORIGENES = ["MLM", "QRO"]
DESTINO = "BOS"
SALIDAS = [f"2027-02-{d:02d}" for d in range(5, 11)]          # 5 al 10 feb
REGRESOS = ["2027-05-24", "2027-05-27", "2027-05-30"]           # finales de mayo
MAX_MIN = 9 * 60                                                # máx. 9 h por trayecto
DB = Path(__file__).with_name("precios.db")


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS precios (
        consultado TEXT, tipo TEXT, origen TEXT, ida TEXT, regreso TEXT,
        precio INTEGER, aerolinea TEXT, ruta TEXT, salida TEXT, llegada TEXT)""")
    return con


def buscar(tipo, origen, fecha):
    # tipo 'ida' = origen->BOS ; tipo 'regreso' = BOS->origen (solo ida en ambos casos)
    de, a = (origen, DESTINO) if tipo == "ida" else (DESTINO, origen)
    vuelos = [FlightQuery(date=fecha, from_airport=de, to_airport=a,
                          max_duration_minutes=MAX_MIN)]
    q = create_query(flights=vuelos, trip="one-way", language="es", currency="MXN")
    res = None
    for intento in range(3):  # Google a veces responde vacío; reintentar con pausa
        try:
            res = list(get_flights(q))
            break
        except Exception as e:
            err = type(e).__name__
            time.sleep(20 * (intento + 1))
    if res is None:
        print(f"  ! {tipo} {origen} {fecha}: {err}")
        return []
    filas = []
    for f in res:
        s = f.flights
        hhmm = lambda t: f"{t[0]:02d}:{t[1]:02d}"
        filas.append((tipo, origen, fecha, None, f.price, ", ".join(f.airlines),
                      "-".join([s[0].from_airport.code] + [x.to_airport.code for x in s]),
                      hhmm(s[0].departure.time), hhmm(s[-1].arrival.time)))
    return filas


def run():
    ahora = datetime.now().isoformat(timespec="minutes")
    con = db()
    tareas = [("ida", o, d) for o in ORIGENES for d in SALIDAS]
    tareas += [("regreso", o, r) for o in ORIGENES for r in REGRESOS]
    total = 0
    for t in tareas:
        filas = buscar(*t)
        con.executemany("INSERT INTO precios VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [(ahora, *f) for f in filas])
        con.commit()
        total += len(filas)
        if filas:
            m = min(filas, key=lambda f: f[4])
            print(f"  {t[0]:8} {t[1]} {t[2]}  ${m[4]:,} {m[5]} {m[6]}")
        time.sleep(random.uniform(8, 15))  # pausado para que Google no bloquee
    print(f"{ahora}: {total} opciones guardadas en {DB.name}", flush=True)
    html()
    avisar_minimos(con, ahora)


def avisar_minimos(con, ahora):
    """Manda un aviso por ntfy si esta consulta rompió el mínimo histórico."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return
    for tipo, texto in (("ida", "Ida a Boston (feb)"), ("regreso", "Regreso de Boston (may)")):
        nuevo = con.execute("""SELECT precio, origen, ida, aerolinea, ruta, salida FROM precios
                               WHERE consultado=? AND tipo=? ORDER BY precio LIMIT 1""",
                            (ahora, tipo)).fetchone()
        antes = con.execute("SELECT MIN(precio) FROM precios WHERE consultado<? AND tipo=?",
                            (ahora, tipo)).fetchone()[0]
        if not nuevo or antes is None or nuevo[0] >= antes:
            continue
        precio, origen, fecha, aer, ruta, salida = nuevo
        msg = {
            "topic": topic,
            "title": f"✈️ Nuevo mínimo: ${precio:,} MXN",
            "message": (f"{texto}: {ruta.replace('-', ' → ')} el {fecha} a las {salida}, {aer}.\n"
                        f"Antes el mínimo era ${antes:,} (bajó ${antes - precio:,})."),
            "tags": ["airplane", "moneybag"],
            "priority": 4,
        }
        if os.environ.get("REPORTE_URL"):
            msg["click"] = os.environ["REPORTE_URL"]
        req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20)
            print(f"Aviso enviado: {tipo} ${precio:,}")
        except Exception as e:
            print(f"No se pudo enviar el aviso: {e}")


def report():
    con = db()
    q = lambda sql, *a: con.execute(sql, a).fetchall()
    ult = q("SELECT MAX(consultado) FROM precios")[0][0]
    if not ult:
        print("Sin datos todavía. Corre: python vuelos.py run"); return
    print(f"Última consulta: {ult}   (consultas totales: "
          f"{q('SELECT COUNT(DISTINCT consultado) FROM precios')[0][0]})\n")

    print("== Más barato ahora (solo ida) ==")
    for r in q("""SELECT origen, ida, MIN(precio), aerolinea, ruta, salida, llegada
                  FROM precios WHERE consultado=? AND tipo='ida'
                  GROUP BY origen, ida ORDER BY 3""", ult):
        print(f"  {r[0]} {r[1]}  ${r[2]:,}  {r[3]}  {r[4]}  {r[5]}-{r[6]}")

    print("\n== Regreso a finales de mayo (BOS -> origen, solo ida) ==")
    for r in q("""SELECT origen, ida, MIN(precio), aerolinea, ruta, salida, llegada
                  FROM precios WHERE consultado=? AND tipo='regreso'
                  GROUP BY origen, ida ORDER BY 3""", ult):
        print(f"  {r[0]} {r[1]}  ${r[2]:,}  {r[3]}  {r[4]}  {r[5]}-{r[6]}")

    for o in ORIGENES:
        ida = q("SELECT MIN(precio) FROM precios WHERE consultado=? AND tipo='ida' AND origen=?", ult, o)[0][0]
        reg = q("SELECT MIN(precio) FROM precios WHERE consultado=? AND tipo='regreso' AND origen=?", ult, o)[0][0]
        if ida and reg:
            print(f"\n{o}: ida ${ida:,} + regreso ${reg:,} = ${ida + reg:,} (dos boletos sencillos)."
                  " Compara contra el redondo en Google Flights antes de comprar.")

    # Patrón: precio mínimo de cada consulta, agrupado por hora y día de la semana
    base = """WITH c AS (SELECT consultado, MIN(precio) p FROM precios
              WHERE tipo='ida' GROUP BY consultado)"""
    print("\n== Patrón por hora de consulta (mín. solo ida, promedio) ==")
    for r in q(base + """ SELECT substr(consultado,12,2) h, ROUND(AVG(p)), COUNT(*)
                         FROM c GROUP BY h ORDER BY h"""):
        print(f"  {r[0]}:00  ${int(r[1]):,}  ({r[2]} consultas)")
    print("\n== Patrón por día de la semana ==")
    dias = "Dom Lun Mar Mié Jue Vie Sáb".split()
    for r in q(base + """ SELECT strftime('%w', consultado) d, ROUND(AVG(p)), COUNT(*)
                         FROM c GROUP BY d ORDER BY d"""):
        print(f"  {dias[int(r[0])]}  ${int(r[1]):,}  ({r[2]} consultas)")
    print("\n== Histórico del mínimo (solo ida) ==")
    for r in q(base + " SELECT consultado, p FROM c ORDER BY consultado DESC LIMIT 15"):
        print(f"  {r[0]}  ${r[1]:,}")


def html():
    con = db()
    con.row_factory = sqlite3.Row
    ult, pri, n = con.execute("SELECT MAX(consultado), MIN(consultado), COUNT(DISTINCT consultado) FROM precios").fetchone()
    actual = [dict(r) for r in con.execute(
        """SELECT tipo, origen, ida AS fecha, precio, aerolinea, ruta, salida, llegada
           FROM precios WHERE consultado=? ORDER BY precio""", (ult,))]
    serie = [dict(r) for r in con.execute(
        """SELECT consultado AS c, tipo, origen, ida AS fecha, MIN(precio) AS precio
           FROM precios GROUP BY consultado, tipo, origen, ida ORDER BY consultado""")]
    data = json.dumps({"ultimo": ult, "primero": pri, "n": n, "actual": actual, "serie": serie},
                      ensure_ascii=False).replace("</", "<\\/")
    plantilla = Path(__file__).with_name("plantilla.html").read_text(encoding="utf-8")
    out = Path(__file__).with_name("reporte.html")
    out.write_text(plantilla.replace("/*DATA*/null", data), encoding="utf-8")
    print(f"Reporte actualizado: {out}")


if __name__ == "__main__":
    {"run": run, "report": report, "html": html}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
