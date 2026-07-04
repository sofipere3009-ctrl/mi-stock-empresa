import streamlit as st
import pandas as pd
import sqlite3
import qrcode
from io import BytesIO
import io

# 1. CONFIGURACIÓN DE LA PÁGINA ERP
st.set_page_config(page_title="ERP Stock Profesional", layout="wide")

# 2. CONFIGURACIÓN DE LA BASE DE DATOS LOCAL (SQLite)
DB_NAME = "inventario_interno.db"
URL_SHEET = "https://google.com"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stock (
            Lote TEXT PRIMARY KEY,
            Ubicacion TEXT,
            N_Parte TEXT,
            Nombre_Parte TEXT,
            Costo_Unitario REAL,
            En_Stock REAL,
            Reservado REAL,
            UdM TEXT,
            Estado TEXT
        )
    ''')
    conn.commit()
    conn.close()

def load_data():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM stock", conn)
    conn.close()
    return df

def insert_item(lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO stock (Lote, Ubicacion, N_Parte, Nombre_Parte, Costo_Unitario, En_Stock, Reservado, UdM, Estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False
    finally:
        conn.close()

def update_full_item(lote_original, nuevo_lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado):
    """Permite modificar absolutamente todo el producto, incluyendo la llave primaria (Lote)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        if lote_original != nuevo_lote:
            cursor.execute("DELETE FROM stock WHERE Lote = ?", (lote_original,))
        
        cursor.execute('''
            INSERT OR REPLACE INTO stock (Lote, Ubicacion, N_Parte, Nombre_Parte, Costo_Unitario, En_Stock, Reservado, UdM, Estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (nuevo_lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"Error al actualizar: {e}")
        return False
    finally:
        conn.close()

def delete_item(lote):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM stock WHERE Lote = ?", (lote,))
    conn.commit()
    conn.close()

# Inicializar base de datos y leer información inicial
init_db()
df_raw = load_data()

# Procesar reglas matemáticas básicas y renombrar columnas a Pesos Uruguayos ($U)
if df_raw.empty:
    df_view = pd.DataFrame(columns=["Lote", "Ubicación", "Nº Parte", "Nombre de Parte", "Costo Unitario ($U)", "En_Stock", "Disponible", "Reservado", "UdM", "Estado"])
else:
    df_raw["Disponible"] = df_raw["En_Stock"] - df_raw["Reservado"]
    df_view = df_raw.rename(columns={
        "Ubicacion": "Ubicación",
        "N_Parte": "Nº Parte",
        "Nombre_Parte": "Nombre de Parte",
        "Costo_Unitario": "Costo Unitario ($U)"
    })
    column_order = ["Lote", "Ubicación", "Nº Parte", "Nombre de Parte", "Costo Unitario ($U)", "En_Stock", "Disponible", "Reservado", "UdM", "Estado"]
    df_view = df_view[column_order]
# --- INTERFAZ GRÁFICA DE USUARIO ---
st.title("📦 Sistema de Control de Stock Interno")
st.caption("Base de Datos Permanente Activa | Moneda: Pesos Uruguayos ($U)")
st.markdown("---")

# Fila superior de acciones operativas (4 columnas estables)
col_btn1, col_btn2, col_btn3, col_btn4 = st.columns(4)

with col_btn1:
    with st.expander("➕ Registrar Nuevo Lote", expanded=df_raw.empty):
        with st.form("form_registro", clear_on_submit=True):
            nuevo_lote = st.text_input("Código de Lote (ej. L001)")
            nueva_ubi = st.text_input("Ubicación / Estante", value="Shelf A")
            n_parte = st.text_input("Número de Parte (SKU)")
            nom_parte = st.text_input("Nombre del Artículo")
            costo = st.number_input("Costo Unitario ($U)", min_value=0.0, value=0.0, step=1.0)
            en_stock = st.number_input("Cantidad en Stock", min_value=0.0, value=0.0, step=1.0)
            reservado = st.number_input("Cantidad Reservada", min_value=0.0, value=0.0, step=1.0)
            udm = st.selectbox("Unidad de Medida", ["pcs", "units", "kg", "litre"])
            estado = st.selectbox("Estado", ["Recibido", "Solicitado", "Planificado"])
            
            if st.form_submit_button("Guardar en Almacén"):
                if nuevo_lote and nom_parte:
                    if insert_item(nuevo_lote, nueva_ubi, n_parte, nom_parte, costo, en_stock, reservado, udm, estado):
                        st.success("Guardado correctamente.")
                        st.rerun()
                else:
                    st.error("El Código de Lote y el Nombre son obligatorios.")

with col_btn2:
    with st.expander("✏️ Modificar Todo el Producto", expanded=False):
        if not df_view.empty:
            opciones_lotes = [""] + list(df_view["Lote"].unique())
            lote_mod = st.selectbox("Selecciona lote a corregir:", options=opciones_lotes, key="select_modificar")
            
            if lote_mod != "":
                # CORRECCIÓN AQUÍ: Se utiliza .iloc[0] para extraer la fila correctamente como un registro individual
                datos_viejos = df_raw[df_raw["Lote"] == lote_mod].iloc[0]
                
                with st.form("form_modificacion_total"):
                    st.markdown(f"Corrigiendo datos de: **{datos_viejos['Nombre_Parte']}**")
                    
                    c_lote = st.text_input("Código de Lote", value=str(datos_viejos['Lote']))
                    c_ubi = st.text_input("Ubicación / Estante", value=str(datos_viejos['Ubicacion']))
                    c_nparte = st.text_input("Número de Parte (SKU)", value=str(datos_viejos['N_Parte']))
                    c_nombre = st.text_input("Nombre del Artículo", value=str(datos_viejos['Nombre_Parte']))
                    c_costo = st.number_input("Costo Unitario ($U)", value=float(datos_viejos['Costo_Unitario']), step=1.0)
                    c_stock = st.number_input("Cantidad en Stock", value=float(datos_viejos['En_Stock']), step=1.0)
                    c_res = st.number_input("Cantidad Reservada", value=float(datos_viejos['Reservado']), step=1.0)
                    
                    list_udm = ["pcs", "units", "kg", "litre"]
                    idx_udm = list_udm.index(datos_viejos['UdM']) if datos_viejos['UdM'] in list_udm else 0
                    c_udm = st.selectbox("Unidad de Medida", list_udm, index=idx_udm)
                    
                    list_est = ["Recibido", "Solicitado", "Planificado"]
                    idx_est = list_est.index(datos_viejos['Estado']) if datos_viejos['Estado'] in list_est else 0
                    c_estado = st.selectbox("Estado Operativo", list_est, index=idx_est)
                    
                    if st.form_submit_button("Aplicar Cambios Totales"):
                        if c_lote and c_nombre:
                            if update_full_item(lote_mod, c_lote, c_ubi, c_nparte, c_nombre, c_costo, c_stock, c_res, c_udm, c_estado):
                                st.success("Producto actualizado por completo.")
                                st.rerun()
                        else:
                            st.error("El lote y el nombre no pueden quedar vacíos.")
        else:
            st.write("No hay lotes para modificar.")

with col_btn3:
    with st.expander("🗑️ Eliminar un Lote", expanded=False):
        if not df_view.empty:
            lote_a_borrar = st.selectbox("Selecciona lote a borrar:", options=[""] + list(df_view["Lote"].dropna().unique()))
            if st.button("Confirmar Eliminación") and lote_a_borrar != "":
                delete_item(lote_a_borrar)
                st.success(f"Lote {lote_a_borrar} eliminado.")
                st.rerun()
        else:
            st.write("No hay lotes para eliminar.")

with col_btn4:
    csv_bytes = df_view.to_csv(index=False).encode('utf-8')
    st.download_button("📥 Exportar CSV", csv_bytes, "reporte_stock.csv", "text/csv", disabled=df_view.empty, use_container_width=True)

# Sección de búsqueda y filtrado interactivo
st.markdown("### 🔍 Buscador y Filtros del Almacén")
col_f1, col_f2 = st.columns(2)
with col_f1:
    search = st.text_input("Buscar por lote o artículo:", disabled=df_view.empty)
with col_f2:
    opciones_estado = list(df_view["Estado"].unique()) if not df_view.empty else []
    estados_seleccionados = st.multiselect("Filtrar por estado:", options=opciones_estado, default=opciones_estado, disabled=df_view.empty)

if search and not df_view.empty:
    df_view = df_view[df_view["Nombre de Parte"].str.contains(search, case=False) | df_view["Lote"].str.contains(search, case=False)]
if estados_seleccionados and not df_view.empty:
    df_view = df_view[df_view["Estado"].isin(estados_seleccionados)]

# Visualización de la tabla limpia con formato uruguayo ($U)
st.markdown("### Vista General de Lotes Activos")
if df_view.empty:
    st.info("El inventario está completamente vacío. Usa el formulario de arriba para añadir tus productos reales.")
else:
    st.dataframe(
        df_view.style.format({"Costo Unitario ($U)": "$U {:,.2f}"}),
        use_container_width=True,
        hide_index=True
    )

# Módulo de generación de códigos QR
if not df_view.empty:
    st.markdown("---")
    st.markdown("### 🖼️ Generador de Códigos QR para Etiquetas")
    lote_seleccionado = st.selectbox("Selecciona un Lote para ver su QR:", options=df_view["Lote"].unique(), key="select_qr")

    if lote_seleccionado:
        # CORRECCIÓN AQUÍ: Se utiliza .iloc[0] para evitar errores de extracción en el QR
        lote_info = df_raw[df_raw["Lote"] == lote_seleccionado].iloc[0]
        qr_content = f"LOTE: {lote_info['Lote']}\nItem: {lote_info['Nombre_Parte']}\nDisponible: {lote_info['En_Stock'] - lote_info['Reservado']} {lote_info['UdM']}"
        
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(qr_content)
        qr.make(fit=True)
        img_qr = qr.make_image(fill_color="black", back_color="white")
        
        buf = BytesIO()
        img_qr.save(buf, format="PNG")
        byte_im = buf.getvalue()
        
        col_qr_img, col_qr_txt = st.columns(2)
        with col_qr_img:
            st.image(byte_im, width=150)
        with col_qr_txt:
            st.download_button("💾 Descargar QR (PNG)", byte_im, f"QR_{lote_seleccionado}.png", "image/png")