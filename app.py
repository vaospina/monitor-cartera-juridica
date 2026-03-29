import streamlit as st
import pandas as pd
import numpy as np
import json
import io
from datetime import datetime

# ─────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Monitor Cartera Jurídica · Credifamilia",
    page_icon="📊",
    layout="wide",
)

MORA_LIM = 30
MESES_MON = 5
MESES_PROV = 2

CALIFICACIONES = ["A", "B", "C", "D", "E1", "E2", "E3"]
PORCENTAJES = {"A": 0.01, "B": 0.032, "C": 0.10, "D": 0.20, "E1": 0.30, "E2": 0.60, "E3": 1.00}

CAL_COLORES = {
    "A":  "#27500A", "B":  "#185FA5", "C":  "#854F0B",
    "D":  "#A32D2D", "E1": "#791F1F", "E2": "#3C3489", "E3": "#712B13",
}
CAL_BG = {
    "A":  "#EAF3DE", "B":  "#E6F1FB", "C":  "#FAEEDA",
    "D":  "#FCEBEB", "E1": "#FCEBEB", "E2": "#EEEDFE", "E3": "#FAECE7",
}

ESTADO_COLORES = {
    "RETIRAR":   "#A32D2D",
    "SUSPENDER": "#854F0B",
    "MONITOREO": "#185FA5",
    "MANTENER":  "#5F5E5A",
}

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def limpiar(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return str(v).strip().rstrip(".0").replace(",", "")

def parse_mes(v):
    """Extrae YYYY-MM de un valor de celda (número serial Excel o string)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if isinstance(v, (int, float)):
        try:
            origen = datetime(1899, 12, 30)
            d = origen + pd.Timedelta(days=int(v))
            return d.strftime("%Y-%m")
        except Exception:
            return None
    if isinstance(v, str):
        try:
            d = pd.to_datetime(v, dayfirst=True)
            return d.strftime("%Y-%m")
        except Exception:
            return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m")
    return None

def mejorar_cal(cal):
    idx = CALIFICACIONES.index(cal)
    return CALIFICACIONES[idx - 1] if idx > 0 else cal

def fmt_cop(v):
    if v is None or np.isnan(v):
        return "—"
    if abs(v) >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}MM"
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    return f"${v:,.0f}"

# ─────────────────────────────────────────────
#  PROCESAMIENTO DE ARCHIVOS
# ─────────────────────────────────────────────
def procesar_cartera(uploaded_file):
    """Lee QUERY CARTERA — hojas Hipo y Con. Retorna (mes_key, lista_filas)."""
    xf = pd.ExcelFile(uploaded_file)
    rows = []
    mes_key = None

    for hoja in ["Hipo", "Con"]:
        if hoja not in xf.sheet_names:
            st.warning(f"No se encontró la hoja '{hoja}' en el archivo.")
            continue
        df = xf.parse(hoja, header=None)
        for _, r in df.iloc[1:].iterrows():
            cedula = limpiar(r.iloc[5] if len(r) > 5 else None)
            if not cedula:
                continue
            if mes_key is None:
                mes_key = parse_mes(r.iloc[1] if len(r) > 1 else None)
            rows.append({
                "credito":    limpiar(r.iloc[0] if len(r) > 0 else ""),
                "cedula":     cedula,
                "capital":    float(r.iloc[12]) if len(r) > 12 and r.iloc[12] not in [None, ""] else 0.0,
                "dias_mora":  int(r.iloc[19]) if len(r) > 19 and r.iloc[19] not in [None, ""] else 0,
                "tipo":       hoja,
            })
    if mes_key is None:
        mes_key = datetime.today().strftime("%Y-%m")
    return mes_key, rows


def procesar_juridicos(uploaded_file):
    """Lee PROCESOS JURIDICOS — Col A: cédula, Col M (índice 12): etapa."""
    df = pd.read_excel(uploaded_file, header=None)
    jur = {}
    for _, r in df.iloc[1:].iterrows():
        cedula = limpiar(r.iloc[0] if len(r) > 0 else None)
        if not cedula:
            continue
        etapa = str(r.iloc[12]).strip() if len(r) > 12 and r.iloc[12] not in [None, ""] else ""
        jur[cedula] = etapa
    return jur


def procesar_calificaciones(uploaded_file):
    """Lee CALIFICACIONES — Col A: crédito, Col B: calificación."""
    df = pd.read_excel(uploaded_file, header=None)
    cals = {}
    for _, r in df.iloc[1:].iterrows():
        cred = limpiar(r.iloc[0] if len(r) > 0 else None)
        cal  = str(r.iloc[1]).strip().upper() if len(r) > 1 and r.iloc[1] not in [None, ""] else ""
        if cred and cal in CALIFICACIONES:
            cals[cred] = cal
    return cals


# ─────────────────────────────────────────────
#  ANÁLISIS PRINCIPAL
# ─────────────────────────────────────────────
def analizar(historial, juridicos, calificaciones):
    """
    Cruza historial acumulado contra clientes jurídicos y aplica reglas de negocio.
    Retorna lista de dicts con resultado por cliente.
    """
    meses = sorted(historial.keys())
    if not meses or not juridicos:
        return []

    jur_set = set(juridicos.keys())

    # Construir índice por cédula
    cedulas = set()
    for mes in meses:
        for r in historial[mes]:
            if r["cedula"] in jur_set:
                cedulas.add(r["cedula"])

    resultados = []
    for ced in cedulas:
        mv = {}   # mes → dias_mora
        cv = {}   # mes → capital
        cr = {}   # mes → credito
        last = None
        for mes in meses:
            r = next((x for x in historial[mes] if x["cedula"] == ced), None)
            if r:
                mv[mes] = r["dias_mora"]
                cv[mes] = r["capital"]
                cr[mes] = r["credito"]
                last = r

        meses_ced = sorted(mv.keys())
        if not meses_ced:
            continue

        ul_mes   = meses_ced[-1]
        mora_act = mv[ul_mes]
        capital  = cv[ul_mes]
        credito  = last["credito"]
        tipo     = last["tipo"]

        # Vector últimos 5 meses
        u5    = meses_ced[-MESES_MON:]
        m5    = [mv[m] for m in u5]
        ok5   = len(m5) == MESES_MON and all(v < MORA_LIM for v in m5)
        ok5cnt = sum(1 for v in m5 if v < MORA_LIM)

        # Mes anterior
        pen      = meses_ced[-2] if len(meses_ced) >= 2 else None
        mora_pen = mv[pen] if pen else None

        # Estado procesal
        if ok5:
            estado = "RETIRAR"
            rec    = f"5 meses consecutivos con mora < {MORA_LIM} días"
            prio   = 1
        elif mora_act < MORA_LIM and (mora_pen is None or mora_pen >= MORA_LIM):
            estado = "SUSPENDER"
            rec    = "Cliente se puso al día este mes — iniciar monitoreo"
            prio   = 2
        elif mora_act < MORA_LIM:
            estado = "MONITOREO"
            rec    = f"{ok5cnt} de {MESES_MON} meses OK — continuar seguimiento"
            prio   = 3
        else:
            estado = "MANTENER"
            rec    = f"Mora vigente: {mora_act} días — proceso continúa"
            prio   = 4

        # Provisiones — regla 2 meses consecutivos mora < 30
        u2     = meses_ced[-MESES_PROV:]
        ok2    = len(u2) == MESES_PROV and all(mv[m] < MORA_LIM for m in u2)
        cal_act = calificaciones.get(credito)
        cal_nva = None
        liberacion = 0.0
        mejora = False
        if ok2 and cal_act and cal_act in CALIFICACIONES and cal_act != "A":
            cal_nva    = mejorar_cal(cal_act)
            liberacion = (PORCENTAJES[cal_act] - PORCENTAJES[cal_nva]) * capital
            mejora     = True

        resultados.append({
            "cedula":     ced,
            "credito":    credito,
            "capital":    capital,
            "tipo":       tipo,
            "etapa":      juridicos.get(ced, ""),
            "mora_act":   mora_act,
            "u5":         u5,
            "m5":         m5,
            "ok5cnt":     ok5cnt,
            "estado":     estado,
            "rec":        rec,
            "prio":       prio,
            "mv":         mv,
            "cal_act":    cal_act,
            "cal_nva":    cal_nva,
            "liberacion": liberacion,
            "mejora":     mejora,
        })

    resultados.sort(key=lambda x: x["prio"])
    return resultados


# ─────────────────────────────────────────────
#  EXPORTAR EXCEL
# ─────────────────────────────────────────────
def exportar_excel(resultados, tiene_cal):
    rows = []
    for r in resultados:
        row = {
            "Estado":           r["estado"],
            "Cédula":           r["cedula"],
            "Crédito":          r["credito"],
            "Tipo":             "Hipotecario" if r["tipo"] == "Hipo" else "Consumo",
            "Capital":          r["capital"],
            "Días mora actual": r["mora_act"],
            "Etapa procesal":   r["etapa"],
            "Últimos 5 meses":  ", ".join(r["u5"]),
            "Moras últ. 5":     ", ".join(str(v) for v in r["m5"]),
            "Meses OK":         r["ok5cnt"],
            "Recomendación":    r["rec"],
        }
        if tiene_cal:
            row["Cal. actual"]         = r["cal_act"] or ""
            row["Cal. nueva"]          = r["cal_nva"] or ""
            row["Liberación provisión"]= r["liberacion"]
        rows.append(row)

    df = pd.DataFrame(rows)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Monitor Cartera")
        ws = writer.sheets["Monitor Cartera"]
        # Ancho de columnas automático
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 4, 40)
    buf.seek(0)
    return buf


# ─────────────────────────────────────────────
#  INICIALIZAR SESSION STATE
# ─────────────────────────────────────────────
if "historial" not in st.session_state:
    st.session_state.historial = {}
if "juridicos" not in st.session_state:
    st.session_state.juridicos = {}
if "calificaciones" not in st.session_state:
    st.session_state.calificaciones = {}
if "resultados" not in st.session_state:
    st.session_state.resultados = []


# ─────────────────────────────────────────────
#  UI — CABECERA
# ─────────────────────────────────────────────
st.title("Monitor de Cartera Jurídica")
st.caption("Credifamilia · Procesos ejecutivos hipotecarios y de consumo · Regla 5 meses / mora < 30 días")
st.divider()

# ─────────────────────────────────────────────
#  SIDEBAR — CARGA DE ARCHIVOS E HISTORIAL
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Archivos")

    # ── 1. Historial guardado ──────────────────
    st.subheader("Historial acumulado")
    hist_file = st.file_uploader(
        "Cargar historial.json (desde SharePoint)",
        type=["json"],
        help="Descarga este archivo al final de cada sesión y guárdalo en SharePoint.",
    )
    if hist_file:
        try:
            cargado = json.load(hist_file)
            st.session_state.historial = cargado
            st.success(f"Historial cargado: {len(cargado)} mes(es)")
        except Exception:
            st.error("Error al leer el historial.")

    meses_ok = sorted(st.session_state.historial.keys())
    if meses_ok:
        st.caption("Meses en historial: " + " · ".join(meses_ok))

    if st.button("🗑️ Borrar historial", use_container_width=True):
        st.session_state.historial = {}
        st.session_state.resultados = []
        st.rerun()

    st.divider()

    # ── 2. Query Cartera ──────────────────────
    st.subheader("1 · Query Cartera")
    st.caption("QUERY CARTERA (D-MM-AAAA).xlsx · Hojas: Hipo y Con")
    cartera_file = st.file_uploader(
        "Subir Query Cartera", type=["xlsx", "xls"], key="cartera"
    )
    if cartera_file:
        with st.spinner("Procesando cartera..."):
            try:
                mes_key, filas = procesar_cartera(cartera_file)
                st.session_state.historial[mes_key] = filas
                st.success(f"✓ {len(filas)} registros — mes {mes_key}")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()

    # ── 3. Procesos jurídicos ─────────────────
    st.subheader("2 · Procesos Jurídicos")
    st.caption("PROCESOS JURIDICOS.xlsx · Col A: Cédula · Col M: Etapa")
    jur_file = st.file_uploader(
        "Subir Procesos Jurídicos", type=["xlsx", "xls"], key="juridicos"
    )
    if jur_file:
        with st.spinner("Procesando jurídicos..."):
            try:
                jur = procesar_juridicos(jur_file)
                st.session_state.juridicos = jur
                st.success(f"✓ {len(jur)} clientes con proceso")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()

    # ── 4. Calificaciones (opcional) ──────────
    st.subheader("3 · Calificaciones (opcional)")
    st.caption("CALIFICACIONES.xlsx · Col A: Crédito · Col B: Calificación A-E3")
    cal_file = st.file_uploader(
        "Subir Calificaciones", type=["xlsx", "xls"], key="calificaciones"
    )
    if cal_file:
        with st.spinner("Procesando calificaciones..."):
            try:
                cals = procesar_calificaciones(cal_file)
                st.session_state.calificaciones = cals
                st.success(f"✓ {len(cals)} créditos con calificación")
            except Exception as e:
                st.error(f"Error: {e}")

    st.divider()

    # ── Botón analizar ────────────────────────
    if st.button("🔍 Analizar", type="primary", use_container_width=True):
        if not st.session_state.historial:
            st.warning("Sube al menos un Query Cartera.")
        elif not st.session_state.juridicos:
            st.warning("Sube el archivo de Procesos Jurídicos.")
        else:
            with st.spinner("Analizando..."):
                st.session_state.resultados = analizar(
                    st.session_state.historial,
                    st.session_state.juridicos,
                    st.session_state.calificaciones,
                )
            st.success(f"Análisis completo: {len(st.session_state.resultados)} clientes")

    st.divider()

    # ── Descargar historial actualizado ───────
    hist_json = json.dumps(st.session_state.historial, ensure_ascii=False, indent=2)
    st.download_button(
        label="💾 Descargar historial.json",
        data=hist_json,
        file_name="historial.json",
        mime="application/json",
        use_container_width=True,
        help="Guarda este archivo en SharePoint para la próxima sesión.",
    )


# ─────────────────────────────────────────────
#  CONTENIDO PRINCIPAL
# ─────────────────────────────────────────────
res = st.session_state.resultados
tiene_cal = bool(st.session_state.calificaciones)

if not res:
    st.info("Sube los archivos en el panel izquierdo y presiona **Analizar** para generar el reporte.")
    with st.expander("📋 Formato esperado de archivos"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("""
**QUERY CARTERA (D-MM-AAAA).xlsx**
- Hojas: `Hipo` y `Con`
- Col A → Crédito
- Col B → Fecha (detecta el mes automáticamente)
- Col F → Cédula *(llave de cruce)*
- Col M → Capital
- Col T → Días en mora
""")
        with col2:
            st.markdown("""
**PROCESOS JURIDICOS.xlsx**
- Solo clientes con demanda activa
- Col A → Cédula *(llave de cruce)*
- Col M → Etapa procesal
""")
        with col3:
            st.markdown("""
**CALIFICACIONES.xlsx** *(opcional)*
- Col A → Número de crédito
- Col B → Calificación (A / B / C / D / E1 / E2 / E3)

**% de provisión por calificación:**
A=1% · B=3.2% · C=10% · D=20%
E1=30% · E2=60% · E3=100%
""")
    st.stop()

# ── KPIs ─────────────────────────────────────
total    = len(res)
retirar  = sum(1 for r in res if r["estado"] == "RETIRAR")
suspender= sum(1 for r in res if r["estado"] == "SUSPENDER")
monitoreo= sum(1 for r in res if r["estado"] == "MONITOREO")
mantener = sum(1 for r in res if r["estado"] == "MANTENER")
mejoran  = sum(1 for r in res if r["mejora"])
lib_total= sum(r["liberacion"] for r in res)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Total con proceso",    total)
k2.metric("🔴 Retirar demanda",   retirar)
k3.metric("🟡 Suspender proceso", suspender)
k4.metric("🔵 En monitoreo",      monitoreo)
k5.metric("💚 Libera provisión",  fmt_cop(lib_total), f"{mejoran} clientes" if mejoran else "—")

st.divider()

# ── Gráficas de provisiones ───────────────────
if tiene_cal and mejoran > 0:
    st.subheader("Análisis de provisiones")
    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Liberación de provisión por calificación actual**")
        datos_lib = {}
        for r in res:
            if r["mejora"] and r["cal_act"]:
                datos_lib[r["cal_act"]] = datos_lib.get(r["cal_act"], 0) + r["liberacion"]
        if datos_lib:
            df_lib = pd.DataFrame([
                {"Calificación": k, "Liberación ($)": v, "Clientes": sum(1 for r in res if r["cal_act"] == k and r["mejora"])}
                for k, v in sorted(datos_lib.items(), key=lambda x: CALIFICACIONES.index(x[0]))
            ])
            st.bar_chart(df_lib.set_index("Calificación")["Liberación ($)"])
            st.dataframe(df_lib, hide_index=True, use_container_width=True)

    with col_b:
        st.markdown("**Distribución de cartera jurídica por calificación**")
        dist = {}
        for r in res:
            if r["cal_act"]:
                dist[r["cal_act"]] = dist.get(r["cal_act"], 0) + 1
        if dist:
            df_dist = pd.DataFrame([
                {"Calificación": k, "Clientes": v, "% provisión": f"{PORCENTAJES[k]*100:.1f}%"}
                for k, v in sorted(dist.items(), key=lambda x: CALIFICACIONES.index(x[0]))
            ])
            st.bar_chart(df_dist.set_index("Calificación")["Clientes"])
            st.dataframe(df_dist, hide_index=True, use_container_width=True)

    st.divider()

# ── Tabla principal ────────────────────────────
st.subheader("Detalle por cliente")

tabs = st.tabs([
    f"Todos ({total})",
    f"🔴 Retirar ({retirar})",
    f"🟡 Suspender ({suspender})",
    f"🔵 Monitoreo ({monitoreo})",
    f"⚫ Mantener ({mantener})",
    f"💚 Mejoran calificación ({mejoran})",
])

filtros = [
    lambda r: True,
    lambda r: r["estado"] == "RETIRAR",
    lambda r: r["estado"] == "SUSPENDER",
    lambda r: r["estado"] == "MONITOREO",
    lambda r: r["estado"] == "MANTENER",
    lambda r: r["mejora"],
]

for tab, filtro in zip(tabs, filtros):
    with tab:
        filas_tab = [r for r in res if filtro(r)]
        if not filas_tab:
            st.info("Sin registros en esta categoría.")
            continue

        rows_df = []
        for r in filas_tab:
            # Vector visual: ✓ / ✗
            vec = " ".join("✓" if v < MORA_LIM else "✗" for v in r["m5"])
            row = {
                "Estado":        r["estado"],
                "Cédula":        r["cedula"],
                "Crédito":       r["credito"],
                "Tipo":          "Hipo" if r["tipo"] == "Hipo" else "Cons.",
                "Capital":       r["capital"],
                "Mora actual":   r["mora_act"],
                "Etapa procesal":r["etapa"],
                f"Vector {MESES_MON}m":  vec,
                f"OK/{MESES_MON}":       f"{r['ok5cnt']}/{MESES_MON}",
                "Recomendación": r["rec"],
            }
            if tiene_cal:
                row["Cal. actual"] = r["cal_act"] or "—"
                row["Cal. nueva"]  = r["cal_nva"] or "—"
                row["Liberación"]  = r["liberacion"]
            rows_df.append(row)

        df_tab = pd.DataFrame(rows_df)

        # Formato de capital y liberación
        col_fmt = {"Capital": "{:,.0f}"}
        if tiene_cal:
            col_fmt["Liberación"] = "{:,.0f}"

        st.dataframe(
            df_tab,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Capital":      st.column_config.NumberColumn("Capital", format="$ %d"),
                "Mora actual":  st.column_config.NumberColumn("Mora (días)", format="%d d"),
                "Liberación":   st.column_config.NumberColumn("Liberación $", format="$ %d") if tiene_cal else None,
                "Estado":       st.column_config.TextColumn("Estado", width="small"),
            }
        )

st.divider()

# ── Exportar ───────────────────────────────────
col_ex1, col_ex2 = st.columns([1, 3])
with col_ex1:
    fecha_hoy = datetime.today().strftime("%Y-%m-%d")
    excel_buf = exportar_excel(res, tiene_cal)
    st.download_button(
        label="📥 Exportar a Excel",
        data=excel_buf,
        file_name=f"Monitor_Cartera_{fecha_hoy}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

st.caption(
    f"Generado el {datetime.today().strftime('%d/%m/%Y %H:%M')} · "
    f"Historial: {len(st.session_state.historial)} mes(es) · "
    f"Procesos activos: {len(st.session_state.juridicos)} clientes"
)
