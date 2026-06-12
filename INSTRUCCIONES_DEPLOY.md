# 🏆 Mundial 2026 — Guía de Deploy

## Estructura de archivos

```
tu-proyecto/
├── app.py                  ← Backend FastAPI (tu API)
├── requirements.txt        ← Dependencias Python
├── render.yaml             ← Config de Render.com
└── static/
    └── index.html          ← Frontend HTML (copia static_index.html aquí)
```

---

## Paso 1 — Preparar el repositorio

1. Crea una carpeta en tu computadora, por ejemplo `mundial2026/`
2. Copia `app.py`, `requirements.txt`, `render.yaml` a esa carpeta
3. Crea la subcarpeta `static/` y copia `static_index.html` como `static/index.html`
4. Sube todo a un repositorio en **GitHub** (público o privado)

---

## Paso 2 — Deploy en Render.com

1. Ve a https://render.com y crea una cuenta (gratis)
2. Haz click en **New → Web Service**
3. Conecta tu repositorio de GitHub
4. Render detectará `render.yaml` automáticamente

### Configurar la variable de entorno (API Key)
En el dashboard de Render, ve a tu servicio → **Environment**:
- Key: `API_FOOTBALL_KEY`
- Value: `tu_api_key_aquí`

---

## Paso 3 — Actualizar el HTML con tu URL de Render

Una vez que Render te asigne la URL (algo como `https://mundial2026-api.onrender.com`):

1. Abre `static/index.html`
2. Busca esta línea al inicio del script:
   ```javascript
   const BASE_URL = '';
   ```
3. Cámbiala a:
   ```javascript
   const BASE_URL = 'https://TU-APP.onrender.com';
   ```
4. Sube los cambios a GitHub → Render hace redeploy automático

---

## Endpoints disponibles

| Endpoint | Descripción |
|----------|-------------|
| `GET /api/matches` | Todos los partidos del Mundial 2026 |
| `GET /api/match/{id}` | Detalle: eventos + estadísticas + alineaciones |
| `GET /api/standings` | Tabla de posiciones de los 12 grupos |
| `GET /api/mexico` | Todos los partidos de México con stats |
| `GET /api/semifinals` | Los dos partidos de semifinales |
| `GET /api/health` | Healthcheck del servidor |

---

## Estrategia de caché (llamadas a API-Football)

| Estado del partido | TTL del caché |
|-------------------|---------------|
| Terminado (FT/AET/PEN) | 24 horas |
| En vivo (1H/HT/2H/ET) | 2 minutos |
| Por jugar (NS) | 30 minutos |
| Standings | 5 minutos |

Con un plan **gratuito de API-Football (100 req/día)** esto funciona para los días del Mundial.
Para mayor seguridad usa el plan **Basic ($10/mes, 500 req/día)**.

---

## Plan gratuito de Render — nota importante

El plan gratuito de Render **suspende el servicio** tras 15 minutos de inactividad.
La primera visita tardará ~30 segundos en despertar.

Para evitarlo:
- Cambia a **Render Starter** ($7/mes), o
- Usa **UptimeRobot** (gratis) para hacer ping a `/api/health` cada 10 minutos

---

## Probar localmente (sin Render)

```bash
cd tu-proyecto/
pip install -r requirements.txt
export API_FOOTBALL_KEY="tu_key_aquí"   # en Windows: set API_FOOTBALL_KEY=tu_key
python app.py
```

Luego abre http://localhost:8000 en el navegador.
