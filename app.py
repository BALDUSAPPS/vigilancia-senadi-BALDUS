import streamlit as st
import pandas as pd
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from rapidfuzz import fuzz
import easyocr

st.set_page_config(page_title="Vigilancia Marcaria SENADI", page_icon="🛡️", layout="wide")

st.title("🛡️ Sistema de Vigilancia y Oposición de Marcas SENADI")
st.caption("Procesamiento en flujo continuo paralelo (Nube alta velocidad)")

uploaded_file = st.sidebar.file_uploader("1. Sube tu archivo Excel de marcas", type=["xlsx", "xls"])
gaceta_num = st.sidebar.text_input("2. Número de Gaceta", placeholder="Ej. 762")
umbral_similitud = st.sidebar.slider("Umbral de Similitud (%)", min_value=50, max_value=100, value=70)

btn_procesar = st.sidebar.button("Procesar Gaceta Completa", type="primary")

# Cargar el motor de OCR UNA SOLA VEZ globalmente para evitar colapso de RAM
@st.cache_resource
def get_ocr_reader():
    return easyocr.Reader(['es'], gpu=False)

def descargar_y_ocr(args):
    gaceta, page, df_excel, umbral, reader = args
    url = f"http://gaceta.propiedadintelectual.gob.ec:8180/Gacetas/{gaceta}/files/page/{page}.jpg"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            image_bytes = resp.read()
        
        # Procesar con el reader precargado
        text_results = reader.readtext(image_bytes, detail=0)
        texto_pagina = " ".join(text_results).upper()
        
        del image_bytes
        
        if not texto_pagina.strip():
            return page, [], False

        alertas_pag = []
        for _, row in df_excel.iterrows():
            marca_registrada = row['Denominacion_clean']
            if len(marca_registrada) < 3:
                continue
                
            score = fuzz.partial_ratio(marca_registrada, texto_pagina)
            if score >= umbral:
                alertas_pag.append({
                    "Página Gaceta": page,
                    "Tu Marca Registrada": row['Denominacion'],
                    "Clase Int.": row.get('Clase Int.', 'N/A'),
                    "Titular Afectado": row.get('Titular', 'N/A'),
                    "Nivel de Coincidencia": f"{round(score, 1)}%",
                    "Extracto Encontrado": texto_pagina[:140].replace('\n', ' ') + "..."
                })
        return page, alertas_pag, False

    except Exception:
        return page, [], True

if btn_procesar:
    if not uploaded_file or not gaceta_num.strip():
        st.error("Por favor sube tu Excel y escribe el número de la Gaceta.")
    else:
        df_excel = pd.read_excel(uploaded_file)
        df_excel['Denominacion_clean'] = df_excel['Denominacion'].astype(str).str.upper().str.strip()
        
        st.info("Inicializando motor de Inteligencia Artificial...")
        reader = get_ocr_reader()
        
        st.info(f"Analizando Gaceta {gaceta_num} en tiempo real...")
        
        alertas_totales = []
        max_estimado = 1500
        
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # Reducimos a 8 trabajadores para no sobrepasar el límite de RAM de la nube gratuita
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(descargar_y_ocr, (gaceta_num, p, df_excel, umbral_similitud, reader)): p 
                for p in range(1, max_estimado)
            }
            
            paginas_procesadas = 0
            errores_consecutivos = 0
            
            for future in as_completed(futures):
                page, alertas, es_error = future.result()
                
                if es_error:
                    errores_consecutivos += 1
                else:
                    errores_consecutivos = 0
                    alertas_totales.extend(alertas)
                
                paginas_procesadas += 1
                
                if paginas_procesadas % 10 == 0:
                    status_text.text(f"Procesadas {paginas_procesadas} páginas...")
                    progress_bar.progress(min(paginas_procesadas / 1000, 1.0))
                
                if errores_consecutivos > 15 and paginas_procesadas > 50:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

        status_text.success("¡Cotejo completado con éxito!")
        progress_bar.progress(100)
        
        if alertas_totales:
            df_alertas = pd.DataFrame(alertas_totales).drop_duplicates(subset=["Página Gaceta", "Tu Marca Registrada"])
            st.warning(f"⚠️ Se detectaron {len(df_alertas)} posibles conflictos:")
            st.dataframe(df_alertas, use_container_width=True)
            
            csv = df_alertas.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Descargar Reporte (CSV)", csv, f"Alertas_Gaceta_{gaceta_num}.csv", "text/csv")
        else:
            st.balloons()
            st.success("No se detectaron marcas parecidas en esta Gaceta.")
