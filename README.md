# 📒 Agenda Grandstream (agenda-gs)

Panel web para administrar la agenda de los teléfonos **Grandstream** (probado con GXP1610) conectados a un **UCM6300** (UCM6302A).

Une en un solo `phonebook.xml` las extensiones y los contactos del **LDAP del UCM** con los **contactos externos** que das de alta desde la web. Así, al presionar la tecla **Agenda**, el teléfono muestra la lista completa sin tener que buscar.

> Desarrollado por **AXTI Sistemas**.

---

## ✨ Funciones

- 🔐 **Panel web con login** para dar de alta, editar y borrar contactos externos.
- 📡 **Lectura automática del LDAP del UCM** (extensiones y otras agendas LDAP), con caché configurable.
- 📞 **Publica `phonebook.xml`** en el formato de Grandstream, con grupos.
- 🎛️ **Control de lo que ven los teléfonos**:
  - Incluir o excluir **todo** el UCM con un botón.
  - Ocultar **grupos completos** del UCM (por ejemplo, solo extensiones, sin externos del UCM).
  - Ocultar **contactos sueltos** del UCM (faxes, salas, porteros, extensiones de prueba).
  - **Activar / desactivar** tus contactos sin borrarlos.
- 📥 **Importar / exportar CSV** (compatible con Excel, UTF-8 o Latin-1, separado por `,` o `;`).
- 🔑 **Token en la URL** del XML para que no quede en una ruta obvia.
- 🧹 Quita contactos duplicados (mismo nombre y número principal).

---

## 🗂️ Estructura del repo

```
agenda-gs/
├── app.py              # Aplicación Flask (panel + XML)
├── requirements.txt    # Dependencias de Python
├── Dockerfile          # Imagen de la app
├── docker-compose.yml  # Stack para Portainer / Docker Compose
├── .gitignore
└── README.md
```

---

## 🚀 Instalación con Portainer (desde GitHub)

### 1. Datos del LDAP del UCM

En el UCM: **Configuración del sistema → Servidor LDAP**

| Dato | Valor normal |
|---|---|
| Base DN | `dc=pbx,dc=com` |
| Superusuario (Root DN) | `cn=admin,dc=pbx,dc=com` |
| Contraseña Superusuario | la que tengas ahí (no es la de la web del UCM) |

> Si no la sabes, cámbiala en esa misma pantalla, guarda y aplica cambios. Evita el signo `$`.

### 2. Crear el stack

**Stacks → Add stack**

- **Name:** `agenda-gs`
- **Build method:** Repository
- **Repository URL:** `https://github.com/TU_USUARIO/agenda-gs`
- **Repository reference:** `refs/heads/main`
- **Compose path:** `docker-compose.yml`
- **Authentication:** solo si el repo es privado (usuario + token de GitHub con permiso *Contents: Read-only*)

### 3. Variables de entorno (en Portainer)

| Variable | Ejemplo | Descripción |
|---|---|---|
| `APP_PORT` | `8087` | Puerto donde se publica el panel |
| `APP_USER` | `admin` | Usuario del panel web |
| `APP_PASSWORD` | `********` | Contraseña del panel web |
| `SECRET_KEY` | cadena larga aleatoria | Firma de la sesión |
| `UCM_HOST` | `192.168.1.10` | IP del UCM (vacío = no leer el UCM) |
| `UCM_BIND_PASSWORD` | `********` | Contraseña Superusuario del LDAP del UCM |
| `XML_TOKEN` | `miempresa` | Parte de la ruta del XML: `/TOKEN/phonebook.xml` |

> ⚠️ **Nunca pongas contraseñas en `docker-compose.yml`**. Van solo en las variables de Portainer.

### 4. Deploy

Da clic en **Deploy the stack**. En **Containers → agenda-gs → Logs** debe aparecer:

```
Listening at: http://0.0.0.0:8000
```

Abre `http://IP_SERVIDOR:8087` e inicia sesión.

---

## ⚙️ Variables opcionales

Puedes agregarlas al `environment` del compose si las necesitas:

| Variable | Default | Descripción |
|---|---|---|
| `UCM_PORT` | `389` | Puerto LDAP del UCM |
| `UCM_BASE_DN` | `dc=pbx,dc=com` | `dc=pbx,dc=com` lee todas las agendas; `ou=pbx,dc=pbx,dc=com` solo extensiones |
| `UCM_BIND_DN` | — | `cn=admin,dc=pbx,dc=com` |
| `UCM_FILTER` | `(AccountNumber=*)` | Filtro LDAP de búsqueda |
| `UCM_PBX_GROUP` | `Extensiones` | Nombre del grupo para `ou=pbx` |
| `CACHE_SECONDS` | `300` | Cada cuánto vuelve a leer el UCM |
| `ACCOUNT_INDEX` | `1` | Cuenta SIP con la que marca el teléfono (prueba `0` si sale por otra cuenta) |
| `DB_PATH` | `/data/agenda.db` | Ruta de la base SQLite |
| `TZ` | `America/Mexico_City` | Zona horaria |

---

## 📄 docker-compose.yml de referencia

```yaml
services:
  agenda:
    build: .
    image: agenda-gs:latest
    pull_policy: build          # evita que busque la imagen en Docker Hub
    container_name: agenda-gs
    ports:
      - "${APP_PORT:-8087}:8000"
    environment:
      TZ: "America/Mexico_City"
      APP_USER: ${APP_USER}
      APP_PASSWORD: ${APP_PASSWORD}
      SECRET_KEY: ${SECRET_KEY}
      UCM_HOST: ${UCM_HOST}
      UCM_PORT: "389"
      UCM_BASE_DN: "dc=pbx,dc=com"
      UCM_BIND_DN: "cn=admin,dc=pbx,dc=com"
      UCM_BIND_PASSWORD: ${UCM_BIND_PASSWORD}
      XML_TOKEN: ${XML_TOKEN}
    volumes:
      - agenda_data:/data
    restart: unless-stopped

volumes:
  agenda_data:
```

---

## ☎️ Configuración en el UCM (Zero Config)

### Agenda XML (Plantilla global o Política global)

| Campo | Valor |
|---|---|
| Habilitado | ✅ |
| Fuente | **Manual** |
| Protocolo | **HTTP** |
| Dirección del servidor | `IP_SERVIDOR:8087/TOKEN` |
| Usuario / contraseña | vacíos |

> Sin `http://` y sin `/phonebook.xml`: el teléfono lo agrega solo.

- **Política global:** se aplica a todos los teléfonos.
- **Plantilla global:** solo a los teléfonos o modelos donde la asignes. Tiene más prioridad que la política global.
- Quita la fuente **"pbx"** en los otros niveles para que no choquen.

Prioridad: `Política global < Plantilla global < Plantilla de modelo < Dispositivo`

### Plantilla de modelo (GXP1610)

| Opción | Valor |
|---|---|
| Phonebook Key Function | **Local Phonebook** |
| Intervalo de descarga | `60` (usa `5` solo para pruebas) |
| Eliminar entradas editadas manualmente | **Sí** |

Después manda la configuración al teléfono (o reinícialo) y presiona la tecla **Agenda**.

---

## 🔗 Rutas de la app

| Ruta | Login | Descripción |
|---|---|---|
| `/` | ✅ | Panel principal |
| `/login`, `/logout` | — | Sesión |
| `/TOKEN/phonebook.xml` | ❌ | Agenda para los teléfonos |
| `/exportar.csv` | ✅ | Exportar contactos web |
| `/salud` | ❌ | Health check (`ok`) |

---

## 📥 Formato CSV

```csv
nombre,apellido,empresa,trabajo,celular,otro,grupo,activo
Juan,Pérez,Cliente SA,5551234567,5512345678,,Clientes,1
María,López,Proveedor SA,,5598765432,,Proveedores,0
```

- `nombre` es obligatorio y al menos un número.
- `activo`: `1` = se envía a los teléfonos, `0` = guardado pero oculto.

---

## 🔄 Actualizar

1. Sube los cambios a la rama `main`.
2. Portainer → **Stacks → agenda-gs → Pull and redeploy**.
3. **Deja "Re-pull image" desactivado** (la imagen se construye local, no existe en Docker Hub).

Para cambiar variables (token, contraseñas, puerto): **Edit Git settings → Environment variables**.

La base de datos se migra sola entre versiones; no se pierden contactos.

---

## 💾 Respaldo

Los contactos y filtros están en el volumen:

```
/var/lib/docker/volumes/agenda-gs_agenda_data/_data/agenda.db
```

Inclúyelo en tus respaldos. También puedes usar **Exportar CSV** desde el panel.

---

## 🛠️ Solución de problemas

| Problema | Solución |
|---|---|
| `port is already allocated` | Cambia `APP_PORT` a un puerto libre (`sudo ss -tlnp \| grep PUERTO`) |
| `pull access denied for agenda-gs` | Desactiva "Re-pull image" o agrega `pull_policy: build` |
| `Error LDAP: invalidCredentials` | Revisa `UCM_BIND_PASSWORD` (y el `$`, escríbelo como `$$`) |
| `Error LDAP: timed out / refused` | Revisa la IP del UCM y el puerto 389: `nc -zv IP_UCM 389` |
| El XML responde `Forbidden` | El token de la URL no coincide con `XML_TOKEN` |
| El teléfono no muestra contactos | Revisa en la web del teléfono **Agenda → Gestión de agenda** que tenga la ruta correcta |
| Contactos duplicados | Usa `UCM_BASE_DN=ou=pbx,dc=pbx,dc=com` u oculta el grupo repetido desde el panel |
| Marca por otra cuenta | Cambia `ACCOUNT_INDEX` a `0` |

---

## 🔒 Seguridad

- Úsalo **solo en la red local**. No lo publiques con Nginx Proxy Manager ni Cloudflare.
- El XML **no pide contraseña**; solo lo protege el token de la URL.
- Las contraseñas viven solo en las variables de Portainer, nunca en el repo.
- Si el repo es público, revisa que no subas capturas o archivos con IPs, contraseñas o contraseñas SIP.

---

## 🧰 Tecnologías

Python 3.12 · Flask · ldap3 · Gunicorn · SQLite · Docker

---

© AXTI Sistemas
