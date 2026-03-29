# Monitor de Cartera Jurídica — Credifamilia
## Guía de despliegue en Streamlit Cloud

---

## Archivos del proyecto

```
cartera_juridica/
├── app.py            ← Aplicación principal
├── requirements.txt  ← Dependencias Python
└── DESPLIEGUE.md     ← Esta guía
```

---

## Paso 1 — Crear cuenta en GitHub (5 minutos)

1. Ve a https://github.com y haz clic en **Sign up**
2. Usa el correo corporativo de Credifamilia
3. Elige el plan **Free**
4. Verifica tu correo

---

## Paso 2 — Crear el repositorio en GitHub

1. En GitHub, haz clic en el botón verde **New** (esquina superior izquierda)
2. Nombre del repositorio: `monitor-cartera-juridica`
3. Visibilidad: **Private** (recomendado para datos internos)
4. Haz clic en **Create repository**

---

## Paso 3 — Subir los archivos

### Opción A — Desde el navegador (más fácil)

1. Dentro del repositorio recién creado, haz clic en **uploading an existing file**
2. Arrastra y suelta los dos archivos: `app.py` y `requirements.txt`
3. Escribe un mensaje como "Primera versión Monitor Cartera"
4. Haz clic en **Commit changes**

### Opción B — Con GitHub Desktop (si tienes instalado)

1. Clona el repositorio
2. Copia los archivos a la carpeta local
3. Haz commit y push

---

## Paso 4 — Crear cuenta en Streamlit Cloud (3 minutos)

1. Ve a https://streamlit.io/cloud
2. Haz clic en **Sign up**
3. Elige **Continue with GitHub** — esto conecta automáticamente tu cuenta

---

## Paso 5 — Desplegar la aplicación

1. En Streamlit Cloud, haz clic en **New app**
2. Selecciona tu repositorio: `monitor-cartera-juridica`
3. Branch: `main`
4. Main file path: `app.py`
5. Haz clic en **Deploy**
6. Espera 2-3 minutos mientras instala las dependencias
7. ¡Tu app estará viva en una URL como:
   `https://monitor-cartera-juridica-xxxx.streamlit.app`

---

## Paso 6 — Compartir con el equipo

1. Copia la URL de la app
2. Compártela por Teams o correo con el equipo de Credifamilia
3. Cualquier persona con la URL puede acceder desde el navegador
4. No necesita instalar nada

> **Nota de seguridad:** Si quieres restringir el acceso solo a personas de la organización, en Streamlit Cloud puedes ir a Settings → Sharing y activar **Restrict access** para que solo usuarios con correo @credifamilia.com puedan entrar.

---

## Cómo usar la app mes a mes

### Flujo mensual recomendado:

```
1. Abrir la URL de la app
2. Cargar el historial.json desde SharePoint  ← conserva la historia acumulada
3. Subir el nuevo QUERY CARTERA del mes
4. Subir PROCESOS JURIDICOS (si hubo cambios)
5. Subir CALIFICACIONES (si hubo cambios)
6. Presionar ANALIZAR
7. Revisar el tablero y exportar el Excel de reporte
8. Descargar el historial.json actualizado y guardarlo en SharePoint
```

### Carpeta recomendada en SharePoint:
```
SharePoint / Cartera / Monitor Jurídico /
├── historial.json          ← siempre la versión más reciente
├── Reportes /
│   ├── Monitor_Cartera_2025-01.xlsx
│   ├── Monitor_Cartera_2025-02.xlsx
│   └── ...
```

---

## Reglas de negocio implementadas

| Regla | Lógica |
|---|---|
| Retirar demanda | 5 meses consecutivos con mora < 30 días |
| Suspender proceso | Cliente pasó de ≥30 a <30 días mora este mes |
| En monitoreo | Mora < 30 días pero aún no completa 5 meses |
| Mantener proceso | Mora ≥ 30 días |
| Mejora calificación | 2 meses consecutivos con mora < 30 días → sube una letra |
| Liberación de provisión | (% cal. actual − % cal. nueva) × capital |

### Calificaciones y % de provisión:
| Cal. | % Provisión |
|------|-------------|
| A    | 1.00%       |
| B    | 3.20%       |
| C    | 10.00%      |
| D    | 20.00%      |
| E1   | 30.00%      |
| E2   | 60.00%      |
| E3   | 100.00%     |

---

## Cómo modificar las reglas de negocio

Abre `app.py` y busca estas constantes al inicio del archivo:

```python
MORA_LIM  = 30   # Días de mora permitidos (límite)
MESES_MON = 5    # Meses consecutivos para retirar demanda
MESES_PROV = 2   # Meses consecutivos para mejorar calificación

PORCENTAJES = {
    "A": 0.01, "B": 0.032, "C": 0.10,
    "D": 0.20, "E1": 0.30, "E2": 0.60, "E3": 1.00
}
```

Cambia el valor, guarda el archivo, y Streamlit Cloud lo actualiza automáticamente en 1-2 minutos.

---

## Soporte técnico

Para modificar reglas o agregar funcionalidades, cualquier persona con acceso al repositorio de GitHub puede editar el archivo `app.py` directamente desde el navegador.

Generado con asistencia de Claude · Anthropic
