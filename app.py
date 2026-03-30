import streamlit as st
import pandas as pd
import numpy as np
import io
from datetime import datetime
from dateutil.relativedelta import relativedelta

st.set_page_config(
    page_title="Monitor Cartera Jurídica · Credifamilia",
    page_icon="📊",
    layout="wide",
)

# ─────────────────────────────────────────────
#  CONFIGURACIÓN — editar aquí si cambian reglas
# ─────────────────────────────────────────────
MORA_LIM      = 30   # días límite para "al día"
MESES_VENTANA = 5    # meses en la ventana de evaluación
MESES_PROV    = 2    # meses consecutivos para mejorar calificación
MESES_PROYEC  = 6    # meses a proyectar hacia adelante

CALIFICACIONES = ["A", "B", "C", "D", "E1", "E2", "E3"]
PORCENTAJES    = {"A": 0.01, "B": 0.032, "C": 0.10,
                  "D": 0.20, "E1": 0.30, "E2": 0.60, "E3": 1.00}

# Índices columnas vector (0-based): A=0 crédito, B=1 mes actual ... M=12 mes-11
VEC_CRED    = 0
VEC_ACT     = 1   # col B: mes actual
VEC_ANT     = 2   # col C: mes anterior
VEC_RESTO_I = 3   # col D en adelante
VEC_RESTO_F = 12  # col M (inclusive)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def limpiar(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return str(v).strip().rstrip(".0").replace(",", "")

def to_int(v):
    try:
        return int(float(v))
    except Exception:
        return None

def mejorar_cal(cal):
    i = CALIFICACIONES.index(cal)
    return CALIFICACIONES[i - 1] if i > 0 else cal

def fmt_cop(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "$0"
    if abs(v) >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}MM"
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    return f"${v:,.0f}"

def mes_label(offset=0):
    d = datetime.today() + relativedelta(months=offset)
    meses = ["Ene","Feb","Mar","Abr","May","Jun",
              "Jul","Ago","Sep","Oct","Nov","Dic"]
    return f"{meses[d.month-1]} {d.year}"

# ─────────────────────────────────────────────
#  REGLA DE RETIRO CON GABELA
# ─────────────────────────────────────────────
def evaluar_regla(vec):
    """
    vec: [mora_act, mora_ant, mora-2, mora-3, mora-4, mora-5, ...]  (días en mora)
    Ventana 5 meses: posiciones 0-4
    Regla base:
      pos 0 (actual)   → obligatorio < MORA_LIM
      pos 1 (anterior) → obligatorio < MORA_LIM
      pos 2, 3, 4      → máximo 1 puede ser >= MORA_LIM (gabela)

    Condición ADICIONAL para gabela:
      El mes 6 (pos 5) DEBE existir y ser < MORA_LIM.
      Esto garantiza que el cliente tenía historial de buen pagador ANTES
      de la ventana, evitando que un cliente recién al día (4 meses) salga
      vía gabela usando como "mes malo" el mes en que entró al proceso.

    Retorna (cumple, meses_ok, tiene_gabela)
    """
    if len(vec) < MESES_VENTANA:
        ok = sum(1 for v in vec if v is not None and v < MORA_LIM)
        return False, ok, False

    w = vec[:MESES_VENTANA]
    if any(v is None for v in w):
        ok = sum(1 for v in w if v is not None and v < MORA_LIM)
        return False, ok, False

    if w[0] >= MORA_LIM or w[1] >= MORA_LIM:
        ok = sum(1 for v in w if v < MORA_LIM)
        return False, ok, False

    malos_resto  = sum(1 for v in w[2:] if v >= MORA_LIM)
    ok           = sum(1 for v in w if v < MORA_LIM)

    if malos_resto == 0:
        # Cumple sin gabela
        return True, ok, False

    if malos_resto == 1:
        # Tiene potencial gabela — validar historial previo
        # El mes 6 (posición 5) debe existir y estar al día
        mes6 = vec[5] if len(vec) > 5 else None
        if mes6 is not None and mes6 < MORA_LIM:
            return True, ok, True   # cumple CON gabela — cliente juicioso
        else:
            return False, ok, False  # no cumple — sin historial previo suficiente

    # malos_resto >= 2 → no cumple
    return False, ok, False


def meses_para_cumplir(vec):
    """Meses que faltan asumiendo mora=0 en el futuro. None si no aplica."""
    if not vec or vec[0] is None or vec[0] >= MORA_LIM:
        return None
    cumple, _, _ = evaluar_regla(vec)
    if cumple:
        return 0
    for extra in range(1, MESES_PROYEC + 1):
        vec_fut = [0] * extra + list(vec)
        c, _, _ = evaluar_regla(vec_fut[:MESES_VENTANA])
        if c:
            return extra
    return None

# ─────────────────────────────────────────────
#  PROCESAMIENTO DE ARCHIVOS
# ─────────────────────────────────────────────
#  HELPERS ADICIONALES
# ─────────────────────────────────────────────
def limpiar_cred(v):
    """
    Limpia número de crédito preservando TODOS los dígitos.
    Evita que Excel convierta 1234560 → 1234560.0 → '123456' (pérdida del cero).
    """
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, float):
        # Si es entero exacto, conviértelo a int primero para evitar decimales
        if v == int(v):
            return str(int(v)).strip()
        return str(v).strip()
    if isinstance(v, int):
        return str(v).strip()
    # String: quitar espacios, comas y ".0" final
    s = str(v).strip().replace(",", "")
    if s.endswith(".0"):
        s = s[:-2]
    return s

# ─────────────────────────────────────────────
def proc_cartera(f):
    """
    Lee Query Cartera (hojas Hipo y Con).
    Retorna dos dicts por cédula:
      hipo_por_ced: cédula → {credito, capital, dias_mora, tipo='Hipo'}
      cons_por_ced: cédula → {credito, capital, dias_mora, tipo='Con'}
    El crédito hipotecario es el "madre"; el consumo es accesorio.
    """
    xf          = pd.ExcelFile(f)
    hipo_por_ced = {}
    cons_por_ced = {}

    for hoja in ["Hipo", "Con"]:
        if hoja not in xf.sheet_names:
            st.warning(f"Hoja '{hoja}' no encontrada.")
            continue
        # Forzar col A (crédito) como string para preservar dígitos
        df = xf.parse(hoja, header=None, dtype={0: str})
        for _, r in df.iloc[1:].iterrows():
            ced  = limpiar(r.iloc[5] if len(r) > 5 else None)
            cred = limpiar_cred(r.iloc[0] if len(r) > 0 else None)
            if not ced or not cred:
                continue
            cap  = float(r.iloc[12]) if len(r) > 12 and r.iloc[12] not in [None, ""] else 0.0
            mora = to_int(r.iloc[19]) if len(r) > 19 else 0
            mora = mora or 0
            dat  = {"credito": cred, "cedula": ced, "capital": cap,
                    "dias_mora": mora, "tipo": hoja}
            if hoja == "Hipo":
                hipo_por_ced[ced] = dat
            else:
                cons_por_ced[ced] = dat

    return hipo_por_ced, cons_por_ced


def proc_juridicos(f):
    df  = pd.read_excel(f, header=None)
    jur = {}
    for _, r in df.iloc[1:].iterrows():
        ced = limpiar(r.iloc[0] if len(r) > 0 else None)
        if not ced:
            continue
        etapa = str(r.iloc[12]).strip() if len(r) > 12 and r.iloc[12] not in [None,""] else ""
        jur[ced] = etapa
    return jur


def proc_calificaciones(f):
    df   = pd.read_excel(f, header=None, dtype={0: str})
    cals = {}
    for _, r in df.iloc[1:].iterrows():
        cred = limpiar_cred(r.iloc[0] if len(r) > 0 else None)
        cal  = str(r.iloc[1]).strip().upper() if len(r) > 1 and r.iloc[1] not in [None,""] else ""
        if cred and cal in CALIFICACIONES:
            cals[cred] = cal
    return cals


def proc_vector(f):
    """
    Col A(0): crédito hipotecario  Col B(1): mora mes actual  Col C-M(2-12): meses anteriores
    Fuerza col A como string para preservar todos los dígitos del número de crédito.
    Retorna dict: credito_str -> [mora_act, mora_ant, mora-2, ..., mora-11]
    """
    df  = pd.read_excel(f, header=None, dtype={0: str})
    vec = {}
    for _, r in df.iloc[1:].iterrows():
        cred = limpiar_cred(r.iloc[VEC_CRED] if len(r) > VEC_CRED else None)
        if not cred:
            continue
        moras = []
        for col in range(VEC_ACT, min(VEC_RESTO_F + 1, len(r))):
            moras.append(to_int(r.iloc[col]))
        vec[cred] = moras
    return vec

# ─────────────────────────────────────────────
#  ANÁLISIS PRINCIPAL
# ─────────────────────────────────────────────
def analizar(cartera_tuple, juridicos, calificaciones, vector):
    """
    Lógica de cruce:
      1. hipo_por_ced × juridicos (por cédula) → clientes hipotecarios con proceso
      2. Para cada uno: buscar vector por número de crédito hipotecario (string exacto)
      3. mora_act y mora_ant SIEMPRE del vector (col B y C)
      4. Si existe crédito consumo para esa cédula: validar su mora del query
         - mora_consumo < 30 → OK, flujo normal
         - mora_consumo >= 30 → bloquea retiro/suspender, genera alerta
      5. El crédito consumo NUNCA se busca en el vector (solo hipotecarios van ahí)
    """
    if not cartera_tuple or not juridicos or not vector:
        return []

    hipo_por_ced, cons_por_ced = cartera_tuple
    jur_set    = set(juridicos.keys())
    resultados = []

    for ced, dat in hipo_por_ced.items():
        # ── Solo clientes con proceso jurídico ──
        if ced not in jur_set:
            continue

        cred    = dat["credito"]   # número crédito hipotecario (string exacto)
        capital = dat["capital"]
        etapa   = juridicos.get(ced, "")

        # ── Mora consumo del query (si existe crédito consumo para esta cédula) ──
        cons      = cons_por_ced.get(ced)
        mora_cons = cons["dias_mora"] if cons else None
        alerta_cons = (mora_cons is not None and mora_cons >= MORA_LIM)

        # ── Vector por número de crédito hipotecario (match exacto string) ──
        vec = vector.get(cred)

        # mora_act y mora_ant SIEMPRE del vector
        if vec and len(vec) >= 1 and vec[0] is not None:
            mora_act = vec[0]
        else:
            mora_act = dat["dias_mora"] or 0   # fallback al query solo si no hay vector

        mora_ant = vec[1] if (vec and len(vec) > 1 and vec[1] is not None) else None

        if not vec:
            vec = [mora_act]

        # ── Evaluar regla del vector ──
        cumple, meses_ok, tiene_gabela = evaluar_regla(vec)
        meses_falt = meses_para_cumplir(vec)

        if meses_falt is not None and meses_falt > 0:
            mes_salida_lbl = mes_label(meses_falt)
        elif meses_falt == 0:
            mes_salida_lbl = "Este mes"
        else:
            mes_salida_lbl = "—"

        # ── Estado procesal — bloqueado si consumo en mora ──
        if cumple and not alerta_cons:
            estado = "RETIRAR"
            sufijo = " (con gabela)" if tiene_gabela else ""
            rec    = f"{meses_ok}/5 meses OK{sufijo}"
            prio   = 1
        elif cumple and alerta_cons:
            # Cumple la regla del vector pero consumo está en mora → bloquear
            estado = "ALERTA CONSUMO"
            rec    = f"Cumple regla Hipo pero consumo tiene {mora_cons} días mora"
            prio   = 2
        elif mora_act < MORA_LIM and (mora_ant is None or mora_ant >= MORA_LIM) and not alerta_cons:
            estado = "SUSPENDER"
            rec    = "Entró al día este mes — iniciar monitoreo"
            prio   = 3
        elif mora_act < MORA_LIM and (mora_ant is None or mora_ant >= MORA_LIM) and alerta_cons:
            estado = "ALERTA CONSUMO"
            rec    = f"Hipo al día pero consumo tiene {mora_cons} días mora"
            prio   = 2
        elif mora_act < MORA_LIM:
            estado = "MONITOREO"
            rec    = f"{meses_ok}/5 meses OK — continuar seguimiento"
            prio   = 4
        else:
            estado = "MANTENER"
            rec    = f"Mora Hipo: {mora_act} días"
            prio   = 5

        # ── Alerta rodando ──
        rodando = (mora_ant is not None and mora_ant < MORA_LIM and mora_act >= MORA_LIM)

        # ── Provisiones: mora_act = 0 Y mora_ant = 0 exactamente ──
        ok2 = (len(vec) >= 2
               and vec[0] is not None and vec[0] == 0
               and vec[1] is not None and vec[1] == 0)

        cal_act    = calificaciones.get(cred)
        cal_nva    = None
        liberacion = 0.0
        mejora     = False
        if ok2 and cal_act and cal_act in CALIFICACIONES and cal_act != "A":
            cal_nva    = mejorar_cal(cal_act)
            liberacion = (PORCENTAJES[cal_act] - PORCENTAJES[cal_nva]) * capital
            mejora     = True

        resultados.append({
            "cedula":          ced,
            "credito":         cred,
            "capital":         capital,
            "tipo":            "Hipo",
            "etapa":           etapa,
            "mora_act":        mora_act,
            "mora_ant":        mora_ant,
            "mora_cons":       mora_cons,
            "alerta_cons":     alerta_cons,
            "cred_cons":       cons["credito"] if cons else None,
            "vec":             vec,
            "meses_ok":        meses_ok,
            "cumple":          cumple,
            "tiene_gabela":    tiene_gabela,
            "estado":          estado,
            "rec":             rec,
            "prio":            prio,
            "rodando":         rodando,
            "meses_falt":      meses_falt,
            "mes_salida_lbl":  mes_salida_lbl,
            "cal_act":         cal_act,
            "cal_nva":         cal_nva,
            "liberacion":      liberacion,
            "mejora":          mejora,
        })

    resultados.sort(key=lambda x: (x["prio"], -x["capital"]))
    return resultados



def calc_proyeccion(res):
    """Proyección 6 meses — orden cronológico garantizado (offset 1→6)."""
    rows = []
    acum_cred = 0
    acum_cap  = 0
    acum_lib  = 0
    for offset in range(1, MESES_PROYEC + 1):
        salidas_mes = [r for r in res if r["meses_falt"] == offset]
        acum_cred  += len(salidas_mes)
        acum_cap   += sum(r["capital"]    for r in salidas_mes)
        acum_lib   += sum(r["liberacion"] for r in salidas_mes if r["mejora"])
        rows.append({
            "Mes":                  mes_label(offset),
            "_orden":               offset,           # para ordenar, oculto en UI
            "Salen ese mes":        len(salidas_mes),
            "Acumulado créditos":   acum_cred,
            "Capital acumulado":    acum_cap,
            "Liberación acumulada": acum_lib,
        })
    df = pd.DataFrame(rows).sort_values("_orden").drop(columns=["_orden"])
    return df


def calc_hist_suspensiones(res):
    """
    Suspensiones por mes: mora[offset] < 30 y mora[offset+1] >= 30.
    Orden cronológico: mes más antiguo primero → mes actual al final.
    """
    hist = []
    acum = 0
    # Recorremos de más antiguo (offset alto) a más reciente (offset 0)
    for offset in range(MESES_VENTANA - 1, -1, -1):
        cnt = 0
        for r in res:
            v = r["vec"]
            if len(v) <= offset:
                continue
            m_x  = v[offset]
            m_x1 = v[offset + 1] if len(v) > offset + 1 else None
            if m_x is not None and m_x < MORA_LIM:
                if m_x1 is None or m_x1 >= MORA_LIM:
                    cnt += 1
        acum += cnt
        hist.append({
            "Mes":         mes_label(-offset),
            "_orden":      -offset,          # offset negativo → cronológico
            "Suspendidos": cnt,
            "Acumulado":   acum,
        })
    df = pd.DataFrame(hist).sort_values("_orden").drop(columns=["_orden"])
    return df

# ─────────────────────────────────────────────
#  SESSION STATE
# ─────────────────────────────────────────────
for k, v in [("cartera",({},{})),("juridicos",{}),("calificaciones",{}),
              ("vector",{}),("resultados",[])]:
    if k not in st.session_state:
        st.session_state[k] = v

# ─────────────────────────────────────────────
#  SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.header("📂 Archivos")

    for cfg in [
        ("1 · Query Cartera",       "Hojas Hipo y Con · Col A,F,M,T", "up_car", proc_cartera,        "cartera",        lambda r: f"✓ {len(r[0])} hipotecarios · {len(r[1])} consumo"),
        ("2 · Procesos Jurídicos",  "Col A: Cédula · Col M: Etapa",   "up_jur", proc_juridicos,      "juridicos",      lambda r: f"✓ {len(r)} con proceso"),
        ("3 · Vector de Moras",     "Col A: Crédito · Col B-M: Mora mes actual→mes-11", "up_vec", proc_vector, "vector", lambda r: f"✓ {len(r)} vectores"),
        ("4 · Calificaciones",      "Col A: Crédito · Col B: A-E3 (opcional)", "up_cal", proc_calificaciones, "calificaciones", lambda r: f"✓ {len(r)} calificaciones"),
    ]:
        lbl, cap, key, fn, sk, sfn = cfg
        st.subheader(lbl)
        st.caption(cap)
        f = st.file_uploader(f"Subir {lbl}", type=["xlsx","xls"], key=key)
        if f:
            with st.spinner(f"Procesando..."):
                try:
                    r = fn(f)
                    st.session_state[sk] = r
                    st.success(sfn(r))
                except Exception as e:
                    st.error(f"Error: {e}")
        st.divider()

    if st.button("🔍 Analizar", type="primary", use_container_width=True):
        hipo, cons = st.session_state.cartera
        if not hipo:
            st.warning("Sube el Query Cartera.")
        elif not st.session_state.juridicos:
            st.warning("Sube los Procesos Jurídicos.")
        elif not st.session_state.vector:
            st.warning("Sube el Vector de Moras.")
        else:
            with st.spinner("Analizando..."):
                st.session_state.resultados = analizar(
                    st.session_state.cartera,
                    st.session_state.juridicos,
                    st.session_state.calificaciones,
                    st.session_state.vector,
                )
            st.success(f"✓ {len(st.session_state.resultados)} clientes analizados")

# ─────────────────────────────────────────────
#  CONTENIDO PRINCIPAL
# ─────────────────────────────────────────────
st.title("Monitor de Cartera Jurídica")
st.caption("Credifamilia · Regla 5 meses con gabela · Proyección 6 meses")
st.divider()

res       = st.session_state.resultados
tiene_cal = bool(st.session_state.calificaciones)

if not res:
    st.info("Sube los 3 archivos obligatorios y presiona **Analizar**.")
    with st.expander("📋 Reglas de negocio activas"):
        st.markdown(f"""
**Regla de retiro (con gabela):**
- Ventana de **{MESES_VENTANA} meses**: mes actual + 4 anteriores
- Mes actual y mes anterior → mora < {MORA_LIM} días (obligatorio)
- De los 3 meses restantes → máximo **1** puede ser ≥ {MORA_LIM} días

**Mejora de calificación:** {MESES_PROV} meses consecutivos al día → sube una letra
**Proyección:** {MESES_PROYEC} meses hacia adelante
""")
    st.stop()

# ── KPIs ──────────────────────────────────────
total       = len(res)
retirar     = sum(1 for r in res if r["estado"]=="RETIRAR")
suspender   = sum(1 for r in res if r["estado"]=="SUSPENDER")
monitoreo   = sum(1 for r in res if r["estado"]=="MONITOREO")
mantener    = sum(1 for r in res if r["estado"]=="MANTENER")
alert_cons  = sum(1 for r in res if r["estado"]=="ALERTA CONSUMO")
n_rod       = sum(1 for r in res if r["rodando"])
mejoran     = sum(1 for r in res if r["mejora"])
lib_tot     = sum(r["liberacion"] for r in res)

c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
c1.metric("Total con proceso",     total)
c2.metric("🔴 Retirar",            retirar)
c3.metric("🟡 Suspender",          suspender)
c4.metric("🔵 Monitoreo",          monitoreo)
c5.metric("🟠 Alerta consumo",     alert_cons)
c6.metric("🚨 Rodando este mes",   n_rod, delta="deterioro" if n_rod else None, delta_color="inverse")
c7.metric("💚 Libera provisión",   fmt_cop(lib_tot), f"{mejoran} clientes" if mejoran else None)

st.divider()

# ── ALERTAS RODANDO ────────────────────────────
rodando_list = [r for r in res if r["rodando"]]
if rodando_list:
    with st.expander(f"🚨 Clientes rodando este mes — {len(rodando_list)} alertas", expanded=True):
        st.caption("Estaban al día el mes pasado y este mes entraron en mora ≥ 30 días. Gestión urgente.")
        df_rod = pd.DataFrame([{
            "Cédula":       r["cedula"],
            "Crédito":      r["credito"],
            "Tipo":         "Hipotecario" if r["tipo"]=="Hipo" else "Consumo",
            "Capital":      r["capital"],
            "Mora anterior":r["mora_ant"],
            "Mora actual":  r["mora_act"],
            "Etapa":        r["etapa"],
        } for r in rodando_list])
        st.dataframe(df_rod, hide_index=True, use_container_width=True,
            column_config={
                "Capital":       st.column_config.NumberColumn(format="$ %d"),
                "Mora anterior": st.column_config.NumberColumn(format="%d d"),
                "Mora actual":   st.column_config.NumberColumn(format="%d d"),
            })
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_rod.to_excel(w, index=False, sheet_name="Rodando")
        buf.seek(0)
        st.download_button("📥 Exportar alerta rodando", buf,
            f"Alerta_Rodando_{datetime.today().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_rod")
    st.divider()

# ── PROYECCIÓN + HISTORIAL ─────────────────────
st.subheader("📈 Proyección 6 meses · Historial de suspensiones")
df_proy = calc_proyeccion(res)
df_hist = calc_hist_suspensiones(res)

cp, ch = st.columns(2)
with cp:
    st.markdown("**Créditos que saldrán del proceso jurídico**")
    st.caption("Clientes al día hoy, asumiendo mora < 30 días en meses futuros.")
    if df_proy["Salen ese mes"].sum() > 0:
        st.markdown("*Por mes*")
        st.bar_chart(df_proy.set_index("Mes")["Salen ese mes"])
        st.markdown("*Acumulado*")
        st.line_chart(df_proy.set_index("Mes")["Acumulado créditos"])
        # Tabla con formato COP legible
        df_proy_fmt = df_proy.copy()
        df_proy_fmt["Capital acumulado"]    = df_proy_fmt["Capital acumulado"].apply(fmt_cop)
        df_proy_fmt["Liberación acumulada"] = df_proy_fmt["Liberación acumulada"].apply(fmt_cop)
        st.dataframe(df_proy_fmt, hide_index=True, use_container_width=True)
    else:
        st.info("Sin créditos proyectados para salir en 6 meses.")

with ch:
    st.markdown("**Historial de suspensiones — últimos 5 meses**")
    st.caption("Clientes que pasaron de mora ≥ 30 a < 30 días. Más antiguo → más reciente.")
    if df_hist["Suspendidos"].sum() > 0:
        st.markdown("*Por mes*")
        st.bar_chart(df_hist.set_index("Mes")["Suspendidos"])
        st.markdown("*Acumulado*")
        st.line_chart(df_hist.set_index("Mes")["Acumulado"])
        st.dataframe(df_hist, hide_index=True, use_container_width=True)
    else:
        st.info("Sin suspensiones registradas en el vector.")

st.divider()

# ── PROVISIONES ────────────────────────────────
if tiene_cal and mejoran > 0:
    st.subheader("💰 Provisiones")
    ca, cb = st.columns(2)
    with ca:
        st.markdown("**Liberación por calificación**")
        d = {}
        for r in res:
            if r["mejora"] and r["cal_act"]:
                d[r["cal_act"]] = d.get(r["cal_act"],0) + r["liberacion"]
        # Orden siempre A→E3
        df_l = pd.DataFrame([
            {"Calificación":k,"Liberación":d[k],
             "Clientes":sum(1 for r in res if r["cal_act"]==k and r["mejora"])}
            for k in CALIFICACIONES if k in d
        ])
        if not df_l.empty:
            df_l = df_l.set_index("Calificación")
            st.bar_chart(df_l["Liberación"])
            df_l_fmt = df_l.reset_index().copy()
            df_l_fmt["Liberación"] = df_l_fmt["Liberación"].apply(fmt_cop)
            st.dataframe(df_l_fmt, hide_index=True, use_container_width=True)
    with cb:
        st.markdown("**Distribución por calificación**")
        d2 = {}
        for r in res:
            if r["cal_act"]:
                d2[r["cal_act"]] = d2.get(r["cal_act"],0) + 1
        # Orden siempre A→E3
        df_d = pd.DataFrame([
            {"Calificación":k,"Clientes":d2[k],"% provisión":f"{PORCENTAJES[k]*100:.1f}%"}
            for k in CALIFICACIONES if k in d2
        ])
        if not df_d.empty:
            st.bar_chart(df_d.set_index("Calificación")["Clientes"])
            st.dataframe(df_d, hide_index=True, use_container_width=True)
    st.divider()

# ── POTENCIAL DE LIBERACIÓN DE PROVISIONES ─────
if tiene_cal:
    st.subheader("🎯 Potencial de liberación de provisiones")
    st.caption(
        "Por cada mes de salida: ¿cuánto liberarían los clientes que salen ese mes "
        "si todos tuvieran mora = 0? vs. cuántos ya lo cumplen hoy. Solo informativo."
    )

    # ────────────────────────────────────────────
    # Mes actual (offset=0): clientes que YA cumplen la regla (RETIRAR)
    # Meses futuros (offset=1-6): clientes proyectados a salir ese mes
    # Para cada cohorte calculamos:
    #   pot  = suma liberación si todos en esa cohorte tuvieran mora=0 hoy y anterior
    #   real = suma liberación de los que ya cumplen mora=0 (ok2=True)
    # ────────────────────────────────────────────

    rows_mes = []
    for offset in range(0, MESES_PROYEC + 1):
        if offset == 0:
            lbl     = f"{mes_label(0)} (hoy)"
            cohorte = [r for r in res if r["estado"] == "RETIRAR"]
        else:
            lbl     = mes_label(offset)
            cohorte = [r for r in res if r["meses_falt"] == offset]

        if not cohorte:
            rows_mes.append({
                "Mes":              lbl,
                "_orden":           offset,
                "Clientes cohorte": 0,
                "Potencial ($)":    0.0,
                "Real hoy ($)":     0.0,
                "Clientes reales":  0,
                "% avance":         0.0,
            })
            continue

        pot  = 0.0
        real = 0.0
        n_real = 0

        for r in cohorte:
            cal = r["cal_act"]
            if not cal or cal not in CALIFICACIONES or cal == "A":
                continue
            cal_nva_pot = mejorar_cal(cal)
            lib_pot     = (PORCENTAJES[cal] - PORCENTAJES[cal_nva_pot]) * r["capital"]
            pot += lib_pot
            # Ya cumple mora=0 dos meses
            if r["mejora"]:
                real   += r["liberacion"]
                n_real += 1

        pct = (real / pot * 100) if pot > 0 else 0.0
        rows_mes.append({
            "Mes":              lbl,
            "_orden":           offset,
            "Clientes cohorte": len(cohorte),
            "Potencial ($)":    pot,
            "Real hoy ($)":     real,
            "Clientes reales":  n_real,
            "% avance":         round(pct, 1),
        })

    df_mes = pd.DataFrame(rows_mes).sort_values("_orden").drop(columns=["_orden"])

    # ── KPI resumen total ──
    tot_pot  = df_mes["Potencial ($)"].sum()
    tot_real = df_mes["Real hoy ($)"].sum()
    tot_pct  = (tot_real / tot_pot * 100) if tot_pot > 0 else 0

    k1, k2, k3 = st.columns(3)
    k1.metric("Potencial total (todos los meses)", fmt_cop(tot_pot))
    k2.metric("Real acumulado hoy",                fmt_cop(tot_real))
    k3.metric("% avance total",                    f"{tot_pct:.1f}%")
    st.progress(min(tot_pct / 100, 1.0))
    st.caption(f"Falta por capturar: **{fmt_cop(tot_pot - tot_real)}**")

    st.divider()

    # ── Gráfica barras: potencial vs real por mes ──
    st.markdown("**Potencial vs real por mes de salida**")
    df_chart = df_mes.set_index("Mes")[["Potencial ($)", "Real hoy ($)"]]
    st.bar_chart(df_chart)

    # ── Tabla detallada por mes ──
    df_fmt = df_mes.copy()
    df_fmt["Potencial ($)"]  = df_fmt["Potencial ($)"].apply(fmt_cop)
    df_fmt["Real hoy ($)"]   = df_fmt["Real hoy ($)"].apply(fmt_cop)
    df_fmt["% avance"]       = df_fmt["% avance"].apply(lambda x: f"{x:.1f}%")
    df_fmt = df_fmt.rename(columns={
        "Clientes cohorte": "Clientes que salen",
        "Clientes reales":  "Ya cumplen mora=0",
    })
    st.dataframe(df_fmt, hide_index=True, use_container_width=True)

    # ── Por calificación dentro de cada mes: desglose ──
    with st.expander("Ver desglose por calificación y mes"):
        for offset in range(0, MESES_PROYEC + 1):
            lbl     = f"{mes_label(0)} (hoy)" if offset == 0 else mes_label(offset)
            cohorte = [r for r in res if r["estado"]=="RETIRAR"] if offset == 0 \
                      else [r for r in res if r["meses_falt"] == offset]
            if not cohorte:
                continue
            rows_cal = []
            for cal in CALIFICACIONES:
                sub = [r for r in cohorte if r["cal_act"] == cal]
                if not sub:
                    continue
                cal_nva_pot = mejorar_cal(cal)
                pot_cal  = sum((PORCENTAJES[cal]-PORCENTAJES[cal_nva_pot])*r["capital"] for r in sub)
                real_cal = sum(r["liberacion"] for r in sub if r["mejora"])
                pct_cal  = (real_cal/pot_cal*100) if pot_cal > 0 else 0
                rows_cal.append({
                    "Cal.":           cal,
                    "Clientes":       len(sub),
                    "Potencial":      fmt_cop(pot_cal),
                    "Real hoy":       fmt_cop(real_cal),
                    "% avance":       f"{pct_cal:.1f}%",
                    "Falta liberar":  fmt_cop(pot_cal - real_cal),
                })
            if rows_cal:
                st.markdown(f"**{lbl}**")
                st.dataframe(pd.DataFrame(rows_cal), hide_index=True, use_container_width=True)

    st.divider()

# ── TABLA DETALLE ──────────────────────────────
st.subheader("Detalle por cliente")

def vec_vis(v):
    return " ".join("✓" if (x is not None and x < MORA_LIM) else "✗" for x in v[:MESES_VENTANA])

tabs = st.tabs([
    f"Todos ({total})",
    f"🔴 Retirar ({retirar})",
    f"🟡 Suspender ({suspender})",
    f"🔵 Monitoreo ({monitoreo})",
    f"⚫ Mantener ({mantener})",
    f"🟠 Alerta consumo ({alert_cons})",
    f"🚨 Rodando ({n_rod})",
    f"💚 Mejoran cal. ({mejoran})",
])
filtros = [
    lambda r: True,
    lambda r: r["estado"]=="RETIRAR",
    lambda r: r["estado"]=="SUSPENDER",
    lambda r: r["estado"]=="MONITOREO",
    lambda r: r["estado"]=="MANTENER",
    lambda r: r["estado"]=="ALERTA CONSUMO",
    lambda r: r["rodando"],
    lambda r: r["mejora"],
]

for i, (tab, filtro) in enumerate(zip(tabs, filtros)):
    with tab:
        filas = [r for r in res if filtro(r)]
        if not filas:
            st.info("Sin registros.")
            continue
        rows = []
        for r in filas:
            # ── Columna S: liberación potencial si mora actual = 0 ──
            # Condición: mes anterior (vec[1]) ya era 0 Y tiene calificación mejorable
            cal = r["cal_act"]
            vec_r = r["vec"]
            mora_ant_r = vec_r[1] if len(vec_r) > 1 and vec_r[1] is not None else None
            if cal and cal in CALIFICACIONES and cal != "A" and mora_ant_r == 0:
                cal_nva_pot  = mejorar_cal(cal)
                lib_pot_s    = (PORCENTAJES[cal] - PORCENTAJES[cal_nva_pot]) * r["capital"]
            else:
                lib_pot_s = 0.0

            row = {
                "Estado":                r["estado"],
                "Cédula":                r["cedula"],
                "Cred. Hipo":            r["credito"],
                "Cred. Consumo":         r["cred_cons"] or "—",
                "Capital":               r["capital"],
                "Mora Hipo actual":      r["mora_act"],
                "Mora Hipo anterior":    r["mora_ant"],
                "Mora Consumo":          r["mora_cons"] if r["mora_cons"] is not None else "—",
                "Alerta consumo":        "⚠️ Sí" if r["alerta_cons"] else "—",
                "Vector 5m":             vec_vis(r["vec"]),
                "OK/5":                  f"{r['meses_ok']}/5",
                "Gabela":                "Sí" if r["tiene_gabela"] else "—",
                "Mes proyectado salida": r["mes_salida_lbl"],
                "Etapa":                 r["etapa"],
                "Nota":                  r["rec"],
            }
            if tiene_cal:
                row["Cal. actual"]            = r["cal_act"] or "—"
                row["Cal. nueva"]             = r["cal_nva"] or "—"
                row["Liberación real ($)"]    = r["liberacion"]        # col R
                row["Liberación potencial ($)"] = lib_pot_s            # col S
            rows.append(row)

        df_t = pd.DataFrame(rows)
        ccfg = {
            "Capital":                   st.column_config.NumberColumn(format="$ %,.0f"),
            "Mora Hipo actual":          st.column_config.NumberColumn(format="%d d"),
            "Mora Hipo anterior":        st.column_config.NumberColumn(format="%d d"),
        }
        if tiene_cal:
            ccfg["Liberación real ($)"]       = st.column_config.NumberColumn(format="$ %,.0f")
            ccfg["Liberación potencial ($)"]  = st.column_config.NumberColumn(format="$ %,.0f")
        st.dataframe(df_t, hide_index=True, use_container_width=True, column_config=ccfg)

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df_t.to_excel(w, index=False, sheet_name="Detalle")
        buf.seek(0)
        st.download_button("📥 Exportar esta vista", buf,
            f"Monitor_{i}_{datetime.today().strftime('%Y-%m-%d')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_{i}")

st.divider()
st.caption(f"Generado: {datetime.today().strftime('%d/%m/%Y %H:%M')} · "
           f"Jurídicos: {len(st.session_state.juridicos)} · "
           f"Vector: {len(st.session_state.vector)} créditos")
