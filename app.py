import streamlit as st
import pandas as pd
import random
import re
import json
import os
import hashlib
import threading
import urllib.request

st.set_page_config(page_title="Banqueo Táctico", layout="centered")

SHEET_URL = "https://docs.google.com/spreadsheets/d/1n5iAy132AUpFIh4Jb_j126aMdsmSJlB92sD2Edqxu4A/edit?usp=drivesdk"
# PEGA AQUÍ TU URL DE APPS SCRIPT (la que termina en /exec)
WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbxaPyR3Fn2_lretLr5C5QARVHDKrWaSehYDP4r0MkT_jiNL4fSZpDLAx0LX8B32pDnxow/exec"
ARCHIVO_LOCAL = "progreso_banqueo.json"

# --- 1. MOTOR DE DATOS Y HASHING ---
def generar_id(texto):
    texto_limpio = str(texto).strip().lower()
    return hashlib.md5(texto_limpio.encode("utf-8")).hexdigest()[:12]

def descargar_sheets_en_vivo(url):
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", url)
    if not match:
        raise ValueError("El enlace no tiene un formato válido de Google Sheets.")
    sheet_id = match.group(1)
    
    gid_match = re.search(r"gid=([0-9]+)", url)
    gid = gid_match.group(1) if gid_match else "0"
    
    live_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"
    
    df = pd.read_csv(live_url)
    df.columns = df.columns.str.strip()
    
    df = df.dropna(subset=["Pregunta", "Correcta"]).reset_index(drop=True)
    df["Curso"] = df["Curso"].astype(str).str.strip().str.title()
    df["Correcta"] = df["Correcta"].astype(str).str.strip().str.upper()
    
    if "Justificacion" in df.columns:
        df["Justificacion"] = df["Justificacion"].fillna("Sin justificación registrada.")
    else:
        df["Justificacion"] = "Sin justificación registrada."
        
    df["id_pregunta"] = df["Pregunta"].apply(generar_id)
    df = df.drop_duplicates(subset=["id_pregunta"]).reset_index(drop=True)
    return df

# --- 2. PERSISTENCIA EN NUBE (WEBHOOK ASÍNCRONO) + CACHÉ LOCAL ---
def _enviar_a_nube_async(payload_str):
    if not WEBHOOK_URL.startswith("https://script.google.com"):
        return
    try:
        req = urllib.request.Request(
            WEBHOOK_URL,
            data=payload_str.encode("utf-8"),
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST"
        )
        urllib.request.urlopen(req, timeout=8)
    except Exception:
        pass

def guardar_progreso():
    datos = {
        "dominadas": list(st.session_state.dominadas),
        "falladas": list(st.session_state.falladas),
        "aciertos": st.session_state.aciertos,
        "total": st.session_state.total,
        "curso_actual": st.session_state.curso_actual,
        "modo_actual": st.session_state.modo_actual,
        "pregunta_actual_id": st.session_state.pregunta_actual_id,
        "cola": st.session_state.cola
    }
    payload_str = json.dumps(datos, ensure_ascii=False)
    
    # 1. Guardado instantáneo en disco temporal (0 ms)
    try:
        with open(ARCHIVO_LOCAL, "w", encoding="utf-8") as f:
            f.write(payload_str)
    except Exception:
        pass
        
    # 2. Guardado permanente en Google Sheets en segundo plano (sin congelar la pantalla)
    hilo = threading.Thread(target=_enviar_a_nube_async, args=(payload_str,), daemon=True)
    hilo.start()

def cargar_progreso():
    # Primero intenta recuperar de Google Sheets (sobrevive a hibernaciones)
    if WEBHOOK_URL.startswith("https://script.google.com"):
        try:
            req = urllib.request.Request(WEBHOOK_URL, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                contenido = resp.read().decode("utf-8")
                datos_nube = json.loads(contenido)
                if datos_nube and isinstance(datos_nube, dict) and "total" in datos_nube:
                    return datos_nube
        except Exception:
            pass
            
    # Respaldo: si falla el internet, lee el archivo local
    if os.path.exists(ARCHIVO_LOCAL):
        try:
            with open(ARCHIVO_LOCAL, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

# --- 3. INICIALIZACIÓN AL ABRIR LA APP ---
if "df" not in st.session_state:
    try:
        st.session_state.df = descargar_sheets_en_vivo(SHEET_URL)
    except Exception:
        st.error("No se pudo conectar al Google Sheets. Verifica el link y los permisos.")
        st.stop()

if "inicializado" not in st.session_state:
    progreso = cargar_progreso()
    if progreso:
        st.session_state.dominadas = set(progreso.get("dominadas", []))
        st.session_state.falladas = set(progreso.get("falladas", []))
        st.session_state.aciertos = progreso.get("aciertos", 0)
        st.session_state.total = progreso.get("total", 0)
        st.session_state.curso_actual = progreso.get("curso_actual", "Todos")
        st.session_state.modo_actual = progreso.get("modo_actual", "Normal")
        st.session_state.pregunta_actual_id = progreso.get("pregunta_actual_id", None)
        st.session_state.cola = progreso.get("cola", [])
    else:
        st.session_state.dominadas = set()
        st.session_state.falladas = set()
        st.session_state.aciertos = 0
        st.session_state.total = 0
        st.session_state.curso_actual = "Todos"
        st.session_state.modo_actual = "Normal"
        st.session_state.pregunta_actual_id = None
        st.session_state.cola = []
        
    st.session_state.respondido = False
    st.session_state.paso = 0
    st.session_state.inicializado = True

df = st.session_state.df
if df.empty:
    st.warning("Tu hoja está conectada pero aún no tiene preguntas válidas.")
    st.stop()

# --- 4. BARRA SUPERIOR Y FILTROS ---
col_curso, col_modo, col_sync = st.columns([2.2, 1.8, 1])

with col_sync:
    st.write("")
    if st.button("🔄 Sync", use_container_width=True, help="Traer preguntas nuevas del Excel"):
        st.session_state.df = descargar_sheets_en_vivo(SHEET_URL)
        guardar_progreso()
        st.rerun()

with col_curso:
    cursos = ["Todos"] + sorted(list(df["Curso"].unique()))
    idx_curso = cursos.index(st.session_state.curso_actual) if st.session_state.curso_actual in cursos else 0
    curso_sel = st.selectbox("Curso:", cursos, index=idx_curso)

with col_modo:
    modos = ["Normal", "Solo Erradas 🔥"]
    idx_modo = 1 if st.session_state.modo_actual == "Solo Erradas 🔥" else 0
    modo_sel = st.selectbox("Modo:", modos, index=idx_modo)

df_curso = df if curso_sel == "Todos" else df[df["Curso"] == curso_sel].reset_index(drop=True)

if modo_sel == "Solo Erradas 🔥":
    df_pendientes = df_curso[df_curso["id_pregunta"].isin(st.session_state.falladas)].reset_index(drop=True)
else:
    df_pendientes = df_curso[~df_curso["id_pregunta"].isin(st.session_state.dominadas)].reset_index(drop=True)

ids_validos = set(df_pendientes["id_pregunta"])
ids_curso = set(df_curso["id_pregunta"])

if (curso_sel != st.session_state.curso_actual or 
    modo_sel != st.session_state.modo_actual):
    st.session_state.curso_actual = curso_sel
    st.session_state.modo_actual = modo_sel
    st.session_state.cola = []
    st.session_state.pregunta_actual_id = None
    st.session_state.respondido = False

st.session_state.cola = [qid for qid in st.session_state.cola if qid in ids_validos]
faltantes_en_cola = list(ids_validos - set(st.session_state.cola) - {st.session_state.pregunta_actual_id})
if faltantes_en_cola:
    random.shuffle(faltantes_en_cola)
    st.session_state.cola = faltantes_en_cola + st.session_state.cola

def avanzar_pregunta(fue_saltada=False):
    if fue_saltada and st.session_state.pregunta_actual_id in ids_validos:
        if st.session_state.pregunta_actual_id not in st.session_state.cola:
            st.session_state.cola.insert(0, st.session_state.pregunta_actual_id)
            
    if st.session_state.cola:
        st.session_state.pregunta_actual_id = st.session_state.cola.pop()
    else:
        st.session_state.pregunta_actual_id = None
        
    st.session_state.respondido = False
    st.session_state.paso += 1
    guardar_progreso()

# FIX 1: No avanzar automáticamente si la pregunta acaba de ser respondida (para mostrar la justificación)
if st.session_state.pregunta_actual_id not in ids_curso or (
    not st.session_state.respondido and st.session_state.pregunta_actual_id not in ids_validos
):
    avanzar_pregunta(fue_saltada=False)

# --- 5. PANEL DE MÉTRICAS ---
total_curso = len(df_curso)
dominadas_curso = len(df_curso[df_curso["id_pregunta"].isin(st.session_state.dominadas)])
erradas_curso = len(df_curso[df_curso["id_pregunta"].isin(st.session_state.falladas)])
porcentaje = round((st.session_state.aciertos / st.session_state.total) * 100) if st.session_state.total > 0 else 0

estado_nube = "☁️ Auto-Save ON" if WEBHOOK_URL.startswith("https://script.google.com") else "⚠️ Solo Local (Pega tu WEBHOOK_URL)"
st.caption(
    f"{estado_nube} | 🎯 **Efectividad:** {st.session_state.aciertos}/{st.session_state.total} ({porcentaje}%) | "
    f"✅ **Dominadas:** {dominadas_curso}/{total_curso} | "
    f"⏳ **Faltan:** {len(ids_validos)} | "
    f"🔥 **Erradas:** {erradas_curso}"
)
st.divider()

# --- 6. RENDERIZADO DE PREGUNTA ---
if st.session_state.pregunta_actual_id is None:
    if modo_sel == "Solo Erradas 🔥":
        st.success("🎉 ¡No tienes preguntas erradas pendientes en este curso!")
    else:
        st.success("🏆 ¡Dominaste el 100% de las preguntas de este filtro!")
        if st.button("♻️ Reiniciar progreso de este curso", type="primary"):
            ids_borrar = set(df_curso["id_pregunta"])
            st.session_state.dominadas -= ids_borrar
            st.session_state.falladas -= ids_borrar
            guardar_progreso()
            st.rerun()
else:
    # FIX 2: Buscar la fila en df_curso (no en df_pendientes, porque al acertar ya salió de pendientes)
    fila = df_curso[df_curso["id_pregunta"] == st.session_state.pregunta_actual_id].iloc[0]

    st.markdown(f"**[{fila['Curso']}]**")
    st.markdown(f"##### {fila['Pregunta']}")

    opciones = {}
    for letra in ["A", "B", "C", "D", "E"]:
        if letra in fila and pd.notna(fila[letra]) and str(fila[letra]).strip() != "":
            opciones[letra] = str(fila[letra]).strip()

    # FIX 3: Bloquear el radio button tras verificar para impedir mutaciones accidentales
    seleccion = st.radio(
        "Alternativas:",
        options=list(opciones.keys()),
        format_func=lambda x: f"{x}) {opciones[x]}",
        index=None,
        disabled=st.session_state.respondido,
        key=f"radio_{st.session_state.pregunta_actual_id}_{st.session_state.paso}"
    )

    btn_col1, btn_col2 = st.columns(2)

    with btn_col1:
        if st.button("✔ Verificar", use_container_width=True, type="primary", disabled=st.session_state.respondido):
            if seleccion and not st.session_state.respondido:
                st.session_state.respondido = True
                st.session_state.total += 1
                qid = st.session_state.pregunta_actual_id
                
                if seleccion == fila["Correcta"]:
                    st.session_state.aciertos += 1
                    st.session_state.dominadas.add(qid)
                    st.session_state.falladas.discard(qid)
                else:
                    st.session_state.falladas.add(qid)
                    if qid not in st.session_state.cola:
                        pos_reinsercion = random.randint(0, max(0, len(st.session_state.cola) - 1))
                        st.session_state.cola.insert(pos_reinsercion, qid)
                        
                guardar_progreso()
                st.rerun()

    with btn_col2:
        if st.button("Siguiente ➔", use_container_width=True):
            avanzar_pregunta(fue_saltada=not st.session_state.respondido)
            st.rerun()

    if st.session_state.respondido:
        correcta = fila["Correcta"]
        if seleccion == correcta:
            st.success(f"¡Correcto! Clave: {correcta} (Retirada del mazo)")
        else:
            st.error(f"Incorrecto. La clave correcta es {correcta} (Reprogramada para repaso)")
        st.info(f"**Justificación:** {fila['Justificacion']}")
