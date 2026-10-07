"""
Agenda Grandstream - AXTI Sistemas
- Panel web con login para administrar contactos externos
- Lee extensiones/contactos del LDAP del UCM
- Publica phonebook.xml (formato Grandstream) para los telefonos
"""
import csv
import hmac
import io
import os
import re
import sqlite3
import threading
import time
from functools import wraps
from xml.sax.saxutils import escape

from flask import (Flask, Response, flash, redirect, render_template,
                   request, session, url_for)
from jinja2 import DictLoader
from ldap3 import NONE, SUBTREE, Connection, Server

# ----------------------------------------------------------------- Config
APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "cambiar")
SECRET_KEY = os.getenv("SECRET_KEY", "cambia-esta-clave-larga")

UCM_HOST = os.getenv("UCM_HOST", "")               # IP del UCM (vacio = no leer UCM)
UCM_PORT = int(os.getenv("UCM_PORT", "389"))
UCM_BASE_DN = os.getenv("UCM_BASE_DN", "dc=pbx,dc=com")
UCM_BIND_DN = os.getenv("UCM_BIND_DN", "")         # ej. cn=admin,dc=pbx,dc=com
UCM_BIND_PASSWORD = os.getenv("UCM_BIND_PASSWORD", "")
UCM_FILTER = os.getenv("UCM_FILTER", "(AccountNumber=*)")
UCM_PBX_GROUP = os.getenv("UCM_PBX_GROUP", "Extensiones")  # nombre para ou=pbx
CACHE_SECONDS = int(os.getenv("CACHE_SECONDS", "300"))

ACCOUNT_INDEX = os.getenv("ACCOUNT_INDEX", "1")    # cuenta SIP con la que se marca
XML_TOKEN = os.getenv("XML_TOKEN", "")             # opcional: /TOKEN/phonebook.xml
DB_PATH = os.getenv("DB_PATH", "/data/agenda.db")

app = Flask(__name__)
app.secret_key = SECRET_KEY

# ----------------------------------------------------------------- Base de datos
def query(sql, args=(), commit=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, args)
        if commit:
            conn.commit()
            return cur.lastrowid
        return cur.fetchall()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    query("""CREATE TABLE IF NOT EXISTS contactos(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        apellido TEXT DEFAULT '',
        empresa TEXT DEFAULT '',
        trabajo TEXT DEFAULT '',
        celular TEXT DEFAULT '',
        otro TEXT DEFAULT '',
        grupo TEXT DEFAULT 'Externos')""", commit=True)


init_db()


def limpiar_num(n):
    return re.sub(r"[^\d+*#]", "", n or "")

# ----------------------------------------------------------------- LDAP UCM
_cache = {"time": 0, "data": [], "error": None}
_lock = threading.Lock()


def _val(attrs, key):
    v = attrs.get(key.lower(), "")
    if isinstance(v, list):
        v = v[0] if v else ""
    if isinstance(v, bytes):
        v = v.decode("utf-8", "ignore")
    return str(v).strip()


def _grupo_dn(dn):
    for part in dn.split(","):
        k, _, v = part.partition("=")
        if k.strip().lower() == "ou":
            v = v.strip()
            return UCM_PBX_GROUP if v.lower() == "pbx" else v
    return "UCM"


def leer_ucm(force=False):
    if not UCM_HOST:
        return [], "UCM_HOST no configurado"
    with _lock:
        if not force and time.time() - _cache["time"] < CACHE_SECONDS:
            return _cache["data"], _cache["error"]
        try:
            server = Server(UCM_HOST, port=UCM_PORT, get_info=NONE, connect_timeout=5)
            conn = Connection(server, user=UCM_BIND_DN or None,
                              password=UCM_BIND_PASSWORD or None,
                              auto_bind=True, receive_timeout=10)
            conn.search(UCM_BASE_DN, UCM_FILTER, search_scope=SUBTREE, attributes=["*"])
            items = []
            for e in conn.response:
                if e.get("type") != "searchResEntry":
                    continue
                a = {k.lower(): v for k, v in e.get("attributes", {}).items()}
                nombre = _val(a, "FirstName")
                apellido = _val(a, "LastName")
                if not nombre and not apellido:
                    nombre = _val(a, "CallerIDName") or _val(a, "AccountNumber")
                nums = []
                for tipo, campo in (("Work", "AccountNumber"),
                                    ("Mobile", "MobileNumber"),
                                    ("Home", "HomeNumber")):
                    n = limpiar_num(_val(a, campo))
                    if n:
                        nums.append((tipo, n))
                if nums:
                    items.append({"nombre": nombre, "apellido": apellido,
                                  "grupo": _grupo_dn(e.get("dn", "")),
                                  "numeros": nums, "origen": "UCM"})
            conn.unbind()
            _cache.update(time=time.time(), data=items, error=None)
        except Exception as ex:  # conserva los datos anteriores si falla
            _cache.update(time=time.time(), error=str(ex))
        return _cache["data"], _cache["error"]

# ----------------------------------------------------------------- Contactos
def contactos_locales():
    out = []
    for r in query("SELECT * FROM contactos ORDER BY nombre, apellido"):
        apellido = r["apellido"] or ""
        if r["empresa"]:
            apellido = f"{apellido} ({r['empresa']})".strip()
        nums = [(t, limpiar_num(r[c])) for t, c in
                (("Work", "trabajo"), ("Mobile", "celular"), ("Home", "otro")) if limpiar_num(r[c])]
        if nums:
            out.append({"nombre": r["nombre"], "apellido": apellido,
                        "grupo": r["grupo"] or "Externos", "numeros": nums, "origen": "WEB"})
    return out


def todos_los_contactos():
    ucm, _ = leer_ucm()
    vistos, final = set(), []
    for c in ucm + contactos_locales():
        clave = (c["nombre"].lower(), c["numeros"][0][1])
        if clave in vistos:
            continue
        vistos.add(clave)
        final.append(c)
    final.sort(key=lambda c: (c["grupo"] != UCM_PBX_GROUP, c["grupo"].lower(),
                              c["nombre"].lower(), c["apellido"].lower()))
    return final


def construir_xml():
    contactos = todos_los_contactos()
    grupos = {}
    for c in contactos:
        grupos.setdefault(c["grupo"], len(grupos) + 1)
    x = ['<?xml version="1.0" encoding="UTF-8"?>', "<AddressBook>"]
    for nombre, gid in grupos.items():
        x.append(f"  <pbgroup>\n    <id>{gid}</id>\n    <name>{escape(nombre)}</name>\n  </pbgroup>")
    for c in contactos:
        x.append("  <Contact>")
        x.append(f"    <LastName>{escape(c['apellido'])}</LastName>")
        x.append(f"    <FirstName>{escape(c['nombre'])}</FirstName>")
        for tipo, num in c["numeros"]:
            x.append(f'    <Phone type="{tipo}">\n'
                     f"      <phonenumber>{escape(num)}</phonenumber>\n"
                     f"      <accountindex>{ACCOUNT_INDEX}</accountindex>\n"
                     f"    </Phone>")
        x.append(f"    <Group>{grupos[c['grupo']]}</Group>")
        x.append("  </Contact>")
    x.append("</AddressBook>")
    return "\n".join(x)

# ----------------------------------------------------------------- Auth
def login_requerido(f):
    @wraps(f)
    def wrap(*a, **kw):
        if not session.get("ok"):
            return redirect(url_for("login"))
        return f(*a, **kw)
    return wrap


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = request.form.get("usuario", "")
        p = request.form.get("password", "")
        if hmac.compare_digest(u, APP_USER) and hmac.compare_digest(p, APP_PASSWORD):
            session["ok"] = True
            return redirect(url_for("index"))
        flash("Usuario o contraseña incorrectos")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ----------------------------------------------------------------- Panel
@app.route("/")
@login_requerido
def index():
    edit = None
    if request.args.get("editar"):
        rows = query("SELECT * FROM contactos WHERE id=?", (request.args["editar"],))
        edit = rows[0] if rows else None
    ucm, err = leer_ucm()
    locales = query("SELECT * FROM contactos ORDER BY grupo, nombre, apellido")
    grupos = sorted({r["grupo"] for r in locales} | {"Externos", "Clientes", "Proveedores"})
    base = request.host + (f"/{XML_TOKEN}" if XML_TOKEN else "")
    return render_template("index.html", c=edit, locales=locales, ucm=ucm, err=err,
                           grupos=grupos, phone_path=base,
                           xml_url=f"http://{base}/phonebook.xml",
                           total=len(todos_los_contactos()))


@app.route("/guardar", methods=["POST"])
@login_requerido
def guardar():
    f = request.form
    datos = (f.get("nombre", "").strip(), f.get("apellido", "").strip(),
             f.get("empresa", "").strip(), f.get("trabajo", "").strip(),
             f.get("celular", "").strip(), f.get("otro", "").strip(),
             f.get("grupo", "").strip() or "Externos")
    if not datos[0]:
        flash("El nombre es obligatorio")
        return redirect(url_for("index"))
    if not any(limpiar_num(n) for n in datos[3:6]):
        flash("Captura al menos un número")
        return redirect(url_for("index"))
    if f.get("id"):
        query("""UPDATE contactos SET nombre=?, apellido=?, empresa=?, trabajo=?,
                 celular=?, otro=?, grupo=? WHERE id=?""", datos + (f["id"],), commit=True)
        flash("Contacto actualizado")
    else:
        query("""INSERT INTO contactos(nombre, apellido, empresa, trabajo, celular, otro, grupo)
                 VALUES (?,?,?,?,?,?,?)""", datos, commit=True)
        flash("Contacto agregado")
    return redirect(url_for("index"))


@app.route("/borrar/<int:cid>", methods=["POST"])
@login_requerido
def borrar(cid):
    query("DELETE FROM contactos WHERE id=?", (cid,), commit=True)
    flash("Contacto eliminado")
    return redirect(url_for("index"))


@app.route("/sync", methods=["POST"])
@login_requerido
def sync():
    data, err = leer_ucm(force=True)
    flash(f"Error al leer UCM: {err}" if err else f"UCM leído: {len(data)} contactos")
    return redirect(url_for("index"))


@app.route("/importar", methods=["POST"])
@login_requerido
def importar():
    archivo = request.files.get("csv")
    if not archivo:
        return redirect(url_for("index"))
    raw = archivo.read()
    try:
        texto = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = raw.decode("latin-1")
    try:
        dialecto = csv.Sniffer().sniff(texto[:2048], delimiters=",;")
    except csv.Error:
        dialecto = csv.excel
    n = 0
    for r in csv.DictReader(io.StringIO(texto), dialect=dialecto):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        if not r.get("nombre"):
            continue
        query("""INSERT INTO contactos(nombre, apellido, empresa, trabajo, celular, otro, grupo)
                 VALUES (?,?,?,?,?,?,?)""",
              (r.get("nombre"), r.get("apellido", ""), r.get("empresa", ""),
               r.get("trabajo", ""), r.get("celular", ""), r.get("otro", ""),
               r.get("grupo") or "Externos"), commit=True)
        n += 1
    flash(f"Importados {n} contactos")
    return redirect(url_for("index"))


@app.route("/exportar.csv")
@login_requerido
def exportar():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["nombre", "apellido", "empresa", "trabajo", "celular", "otro", "grupo"])
    for r in query("SELECT * FROM contactos ORDER BY grupo, nombre"):
        w.writerow([r["nombre"], r["apellido"], r["empresa"], r["trabajo"],
                    r["celular"], r["otro"], r["grupo"]])
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=contactos.csv"})

# ----------------------------------------------------------------- XML telefonos
@app.route("/phonebook.xml")
@app.route("/<token>/phonebook.xml")
def phonebook(token=None):
    if XML_TOKEN and token != XML_TOKEN:
        return Response("Forbidden", status=403)
    return Response(construir_xml(), mimetype="application/xml")


@app.route("/salud")
def salud():
    return "ok"

# ----------------------------------------------------------------- Plantillas
BASE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agenda Grandstream</title>
<style>
body{font-family:system-ui,Segoe UI,Arial;background:#f3f5f9;margin:0;color:#222}
.wrap{max-width:1100px;margin:auto;padding:20px}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap}
h1{margin:0;font-size:24px} h2{font-size:18px;margin-top:0}
.card{background:#fff;border-radius:10px;padding:18px;margin:16px 0;box-shadow:0 1px 4px #0001}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;align-items:end}
label{font-size:13px;color:#555;display:flex;flex-direction:column;gap:4px}
input,select{padding:8px;border:1px solid #ccd;border-radius:6px;font-size:14px}
button,.btn{background:#0b5ed7;color:#fff;border:0;padding:9px 14px;border-radius:6px;cursor:pointer;text-decoration:none;font-size:14px}
.sec{background:#6c757d}.del{background:#dc3545;padding:5px 9px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{padding:7px;border-bottom:1px solid #eee;text-align:left}
th{background:#f8f9fb}
.muted{color:#666;font-size:13px} code{background:#eef;padding:2px 6px;border-radius:4px}
.flash{background:#fff3cd;padding:10px;border-radius:6px;margin:10px 0}
.err{background:#f8d7da;padding:10px;border-radius:6px}
.tag{background:#e7f1ff;color:#0b5ed7;padding:2px 8px;border-radius:10px;font-size:12px}
</style></head><body><div class="wrap">
{% for m in get_flashed_messages() %}<div class="flash">{{ m }}</div>{% endfor %}
{% block body %}{% endblock %}
</div>
<script>
function filtrar(inp, tabla){
  const q = document.getElementById(inp).value.toLowerCase();
  document.querySelectorAll('#'+tabla+' tbody tr').forEach(tr=>{
    tr.style.display = tr.innerText.toLowerCase().includes(q) ? '' : 'none';
  });
}
</script></body></html>"""

LOGIN = """{% extends "base.html" %}{% block body %}
<div class="card" style="max-width:360px;margin:80px auto">
<h1 style="margin-bottom:16px">Agenda Grandstream</h1>
<form method="post" style="display:flex;flex-direction:column;gap:10px">
<label>Usuario<input name="usuario" required autofocus></label>
<label>Contraseña<input name="password" type="password" required></label>
<button>Entrar</button></form></div>{% endblock %}"""

INDEX = """{% extends "base.html" %}{% block body %}
<div class="top"><h1>Agenda Grandstream</h1>
<div><a class="btn sec" href="{{ xml_url }}" target="_blank">Ver phonebook.xml</a>
<a class="btn sec" href="{{ url_for('logout') }}">Salir</a></div></div>
<p class="muted">Ruta para los teléfonos (Phonebook XML Server Path): <code>{{ phone_path }}</code>
&nbsp;·&nbsp; Contactos totales en la agenda: <b>{{ total }}</b></p>

<div class="card">
<h2>{{ 'Editar contacto' if c else 'Nuevo contacto externo' }}</h2>
<form method="post" action="{{ url_for('guardar') }}" class="grid">
<input type="hidden" name="id" value="{{ c.id if c else '' }}">
<label>Nombre *<input name="nombre" required value="{{ c.nombre if c else '' }}"></label>
<label>Apellido<input name="apellido" value="{{ c.apellido if c else '' }}"></label>
<label>Empresa<input name="empresa" value="{{ c.empresa if c else '' }}"></label>
<label>Tel. trabajo<input name="trabajo" value="{{ c.trabajo if c else '' }}"></label>
<label>Celular<input name="celular" value="{{ c.celular if c else '' }}"></label>
<label>Otro<input name="otro" value="{{ c.otro if c else '' }}"></label>
<label>Grupo<input name="grupo" list="grupos" value="{{ c.grupo if c else 'Externos' }}"></label>
<datalist id="grupos">{% for g in grupos %}<option value="{{ g }}">{% endfor %}</datalist>
<div><button>Guardar</button>
{% if c %}<a class="btn sec" href="{{ url_for('index') }}">Cancelar</a>{% endif %}</div>
</form></div>

<div class="card">
<div class="top"><h2>Contactos externos (web) — {{ locales|length }}</h2>
<input id="q1" placeholder="Filtrar..." onkeyup="filtrar('q1','t1')"></div>
<table id="t1"><thead><tr><th>Nombre</th><th>Empresa</th><th>Trabajo</th><th>Celular</th><th>Otro</th><th>Grupo</th><th></th></tr></thead><tbody>
{% for r in locales %}<tr>
<td>{{ r.nombre }} {{ r.apellido }}</td><td>{{ r.empresa }}</td><td>{{ r.trabajo }}</td>
<td>{{ r.celular }}</td><td>{{ r.otro }}</td><td><span class="tag">{{ r.grupo }}</span></td>
<td style="white-space:nowrap"><a class="btn sec" style="padding:5px 9px" href="?editar={{ r.id }}">Editar</a>
<form method="post" action="{{ url_for('borrar', cid=r.id) }}" style="display:inline" onsubmit="return confirm('¿Eliminar contacto?')">
<button class="del">X</button></form></td></tr>
{% else %}<tr><td colspan="7" class="muted">Sin contactos todavía</td></tr>{% endfor %}
</tbody></table>
<hr style="border:0;border-top:1px solid #eee;margin:16px 0">
<form method="post" action="{{ url_for('importar') }}" enctype="multipart/form-data" class="grid">
<label>Importar CSV (columnas: nombre, apellido, empresa, trabajo, celular, otro, grupo)
<input type="file" name="csv" accept=".csv" required></label>
<div><button>Importar</button> <a class="btn sec" href="{{ url_for('exportar') }}">Exportar CSV</a></div>
</form></div>

<div class="card">
<div class="top"><h2>Desde el UCM (solo lectura) — {{ ucm|length }}</h2>
<div><input id="q2" placeholder="Filtrar..." onkeyup="filtrar('q2','t2')">
<form method="post" action="{{ url_for('sync') }}" style="display:inline"><button>Actualizar desde UCM</button></form></div></div>
{% if err %}<p class="err">Error LDAP: {{ err }}</p>{% endif %}
<table id="t2"><thead><tr><th>Nombre</th><th>Números</th><th>Grupo</th></tr></thead><tbody>
{% for u in ucm %}<tr><td>{{ u.nombre }} {{ u.apellido }}</td>
<td>{% for t, n in u.numeros %}{{ n }} <span class="muted">({{ t }})</span> {% endfor %}</td>
<td><span class="tag">{{ u.grupo }}</span></td></tr>
{% else %}<tr><td colspan="3" class="muted">Sin datos del UCM</td></tr>{% endfor %}
</tbody></table></div>
{% endblock %}"""

app.jinja_loader = DictLoader({"base.html": BASE, "login.html": LOGIN, "index.html": INDEX})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
