import streamlit as st
import pandas as pd
import sqlite3
import qrcode
from io import BytesIO
from datetime import datetime
from barcode import Code128
from barcode.writer import ImageWriter

try:
    from zoneinfo import ZoneInfo
    TZ_UY = ZoneInfo("America/Montevideo")
except Exception:
    TZ_UY = None


def ahora():
    """Fecha y hora actual en Uruguay (el servidor de Streamlit Cloud usa UTC)."""
    return datetime.now(TZ_UY) if TZ_UY else datetime.now()


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
    # MIGRACIÓN: agrega la columna de precio de venta si la base es vieja
    cursor.execute("PRAGMA table_info(stock)")
    columnas = [fila[1] for fila in cursor.fetchall()]
    if "Precio_Venta" not in columnas:
        cursor.execute("ALTER TABLE stock ADD COLUMN Precio_Venta REAL DEFAULT 0")

    # Tabla donde se registra cada venta escaneada
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ventas (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            Fecha TEXT,
            Lote TEXT,
            Nombre_Parte TEXT,
            Cantidad REAL,
            Precio_Unitario REAL,
            Total REAL,
            Costo_Unitario REAL
        )
    ''')
    conn.commit()
    conn.close()


def load_data():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM stock", conn)
    conn.close()
    return df


def load_ventas():
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql_query("SELECT * FROM ventas ORDER BY Id DESC", conn)
    conn.close()
    return df


def insert_item(lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado, precio=0.0):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO stock (Lote, Ubicacion, N_Parte, Nombre_Parte, Costo_Unitario, En_Stock, Reservado, UdM, Estado, Precio_Venta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado, precio))
        conn.commit()
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False
    finally:
        conn.close()


def update_full_item(lote_original, nuevo_lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado, precio=None):
    """Permite modificar absolutamente todo el producto, incluyendo la llave primaria (Lote).
    Si no se indica precio, se conserva el precio de venta que ya tenía."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        if precio is None:
            cursor.execute("SELECT Precio_Venta FROM stock WHERE Lote = ?", (lote_original,))
            fila = cursor.fetchone()
            precio = float(fila[0]) if fila and fila[0] is not None else 0.0

        if lote_original != nuevo_lote:
            cursor.execute("DELETE FROM stock WHERE Lote = ?", (lote_original,))

        cursor.execute('''
            INSERT OR REPLACE INTO stock (Lote, Ubicacion, N_Parte, Nombre_Parte, Costo_Unitario, En_Stock, Reservado, UdM, Estado, Precio_Venta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (nuevo_lote, ubicacion, n_parte, nombre, costo, stock, reservado, udm, estado, precio))
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


# ================================================
# ESCANEO DE CÓDIGO DE BARRAS (VENTA)
# ================================================
def procesar_scan():
    """Se ejecuta cuando el lector escribe un código y aprieta Enter.
    Busca el producto por Lote o por Nº de Parte (SKU), descuenta stock y registra la venta."""
    codigo = str(st.session_state.get("scan", "")).strip()
    st.session_state["scan"] = ""  # limpia el campo para el próximo escaneo
    if not codigo:
        return

    cantidad = float(st.session_state.get("scan_cant", 1.0))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT Lote, Nombre_Parte, Costo_Unitario, Precio_Venta, En_Stock, Reservado, UdM
            FROM stock
            WHERE Lote = ? COLLATE NOCASE OR N_Parte = ? COLLATE NOCASE
            LIMIT 1
        ''', (codigo, codigo))
        fila = cursor.fetchone()

        if fila is None:
            st.session_state["scan_msg"] = ("error", f"❌ No existe ningún producto con el código «{codigo}».")
            return

        lote, nombre, costo, precio, en_stock, reservado, udm = fila
        precio = float(precio or 0)
        costo = float(costo or 0)
        disponible = float(en_stock) - float(reservado or 0)

        if cantidad > disponible:
            st.session_state["scan_msg"] = (
                "error",
                f"⚠️ Stock insuficiente de «{nombre}»: disponible {disponible:g} {udm}, se intentó vender {cantidad:g}."
            )
            return

        total = precio * cantidad
        cursor.execute("UPDATE stock SET En_Stock = En_Stock - ? WHERE Lote = ?", (cantidad, lote))
        cursor.execute('''
            INSERT INTO ventas (Fecha, Lote, Nombre_Parte, Cantidad, Precio_Unitario, Total, Costo_Unitario)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (ahora().strftime("%Y-%m-%d %H:%M:%S"), lote, nombre, cantidad, precio, total, costo))
        conn.commit()

        nuevo_stock = float(en_stock) - cantidad
        texto = f"✅ Vendido: {nombre} × {cantidad:g} → $U {total:,.2f} | Quedan {nuevo_stock:g} {udm}"
        if precio == 0:
            texto += "  ⚠️ (este producto tiene precio de venta 0, cargalo en «Modificar Todo el Producto»)"
        st.session_state["scan_msg"] = ("ok", texto)
    except Exception as e:
        conn.rollback()
        st.session_state["scan_msg"] = ("error", f"Error al registrar la venta: {e}")
    finally:
        conn.close()


def deshacer_ultima_venta():
    """Devuelve el stock de la última venta y la elimina del registro."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT Id, Lote, Nombre_Parte, Cantidad FROM ventas ORDER BY Id DESC LIMIT 1")
        fila = cursor.fetchone()
        if fila is None:
            st.session_state["scan_msg"] = ("error", "No hay ventas para deshacer.")
            return
        id_venta, lote, nombre, cantidad = fila
        cursor.execute("UPDATE stock SET En_Stock = En_Stock + ? WHERE Lote = ?", (cantidad, lote))
        cursor.execute("DELETE FROM ventas WHERE Id = ?", (id_venta,))
        conn.commit()
        st.session_state["scan_msg"] = ("ok", f"↩️ Venta deshecha: se devolvieron {cantidad:g} de «{nombre}» al stock.")
    except Exception as e:
        conn.rollback()
        st.session_state["scan_msg"] = ("error", f"Error al deshacer: {e}")
    finally:
        conn.close()


# Inicializar base de datos y leer información inicial
init_db()
df_raw = load_data()
df_raw["Precio_Venta"] = df_raw["Precio_Venta"].fillna(0)

# Procesar reglas matemáticas básicas y renombrar columnas a Pesos Uruguayos ($U)
if df_raw.empty:
    df_view = pd.DataFrame(columns=["Lote", "Ubicación", "Nº Parte", "Nombre de Parte", "Costo Unitario ($U)", "Precio Venta ($U)", "En_Stock", "Disponible", "Reservado", "UdM", "Estado"])
else:
    df_raw["Disponible"] = df_raw["En_Stock"] - df_raw["Reservado"]
    df_view = df_raw.rename(columns={
        "Ubicacion": "Ubicación",
        "N_Parte": "Nº Parte",
        "Nombre_Parte": "Nombre de Parte",
        "Costo_Unitario": "Costo Unitario ($U)",
        "Precio_Venta": "Precio Venta ($U)"
    })
    column_order = ["Lote", "Ubicación", "Nº Parte", "Nombre de Parte", "Costo Unitario ($U)", "Precio Venta ($U)", "En_Stock", "Disponible", "Reservado", "UdM", "Estado"]
    df_view = df_view[column_order]

# --- INTERFAZ GRÁFICA DE USUARIO ---
st.title("📦 Sistema de Control de Stock Interno")
st.caption("Base de Datos Permanente Activa | Moneda: Pesos Uruguayos ($U)")
st.markdown("---")

# ================================================
# CAJA: ESCANEO DE CÓDIGO DE BARRAS
# ================================================
with st.container(border=True):
    st.markdown("### 🛒 Caja — Escanear Código de Barras")
    col_scan, col_cant = st.columns([3, 1])
    with col_scan:
        st.text_input(
            "📷 Escaneá el producto (hacé clic acá y pasá el lector)",
            key="scan",
            on_change=procesar_scan,
            placeholder="Código del producto..."
        )
    with col_cant:
        st.number_input("Cantidad por escaneo", min_value=1.0, value=1.0, step=1.0, key="scan_cant")

    msg = st.session_state.pop("scan_msg", None)
    if msg:
        if msg[0] == "ok":
            st.success(msg[1])
        else:
            st.error(msg[1])

    df_ventas = load_ventas()
    if not df_ventas.empty:
        hoy = ahora().strftime("%Y-%m-%d")
        ventas_hoy = df_ventas[df_ventas["Fecha"].str.startswith(hoy)]
        total_hoy = float(ventas_hoy["Total"].sum()) if not ventas_hoy.empty else 0.0
        costo_hoy = float((ventas_hoy["Costo_Unitario"] * ventas_hoy["Cantidad"]).sum()) if not ventas_hoy.empty else 0.0
        m1, m2, m3 = st.columns(3)
        m1.metric("Vendido hoy", f"$U {total_hoy:,.2f}")
        m2.metric("Ganancia hoy", f"$U {total_hoy - costo_hoy:,.2f}")
        m3.metric("Ventas hoy", f"{len(ventas_hoy)}")

        with st.expander("📜 Últimas ventas"):
            st.dataframe(
                df_ventas.drop(columns=["Id"]).head(30).style.format({
                    "Precio_Unitario": "$U {:,.2f}",
                    "Total": "$U {:,.2f}",
                    "Costo_Unitario": "$U {:,.2f}"
                }),
                use_container_width=True,
                hide_index=True
            )
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                st.button("↩️ Deshacer última venta", on_click=deshacer_ultima_venta, use_container_width=True)
            with col_u2:
                st.download_button(
                    "📥 Exportar ventas (CSV)",
                    df_ventas.to_csv(index=False).encode("utf-8"),
                    "reporte_ventas.csv",
                    "text/csv",
                    use_container_width=True
                )

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
            precio_venta = st.number_input("Precio de Venta ($U)", min_value=0.0, value=0.0, step=1.0)
            en_stock = st.number_input("Cantidad en Stock", min_value=0.0, value=0.0, step=1.0)
            reservado = st.number_input("Cantidad Reservada", min_value=0.0, value=0.0, step=1.0)
            udm = st.selectbox("Unidad de Medida", ["pcs", "units", "kg", "litre"])
            estado = st.selectbox("Estado", ["Recibido", "Solicitado", "Planificado"])

            if st.form_submit_button("Guardar en Almacén"):
                if nuevo_lote and nom_parte:
                    if insert_item(nuevo_lote, nueva_ubi, n_parte, nom_parte, costo, en_stock, reservado, udm, estado, precio_venta):
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
                # Se utiliza .iloc[0] para extraer la fila correctamente como un registro individual
                datos_viejos = df_raw[df_raw["Lote"] == lote_mod].iloc[0]

                # ================================================
                # AJUSTE RÁPIDO (SUMAR / RESTAR) PARA
                # STOCK, COSTO UNITARIO Y CANTIDAD RESERVADA
                # ================================================
                st.markdown("**➕➖ Ajuste Rápido de Cantidades**")

                def bloque_ajuste(etiqueta, valor_actual, campo_key, sufijo_unidad=""):
                    """Dibuja un mini-formulario de sumar/restar para un campo numérico
                    y devuelve el nuevo valor si se apretó sumar o restar, o None si no."""
                    st.caption(f"{etiqueta} actual: {valor_actual:g}{sufijo_unidad}")
                    col_cant, col_sumar, col_restar = st.columns([2, 1, 1])
                    with col_cant:
                        cantidad = st.number_input(
                            f"Cantidad a mover ({etiqueta})",
                            min_value=0.0,
                            value=0.0,
                            step=1.0,
                            key=f"cant_{campo_key}_{lote_mod}"
                        )
                    with col_sumar:
                        st.write("")
                        st.write("")
                        click_sumar = st.button("➕ Sumar", key=f"btn_sumar_{campo_key}_{lote_mod}", use_container_width=True)
                    with col_restar:
                        st.write("")
                        st.write("")
                        click_restar = st.button("➖ Restar", key=f"btn_restar_{campo_key}_{lote_mod}", use_container_width=True)

                    if click_sumar:
                        if cantidad > 0:
                            return float(valor_actual) + cantidad
                        else:
                            st.error(f"Ingresá una cantidad mayor a 0 para sumar en {etiqueta}.")
                    if click_restar:
                        if cantidad > 0:
                            nuevo_valor = float(valor_actual) - cantidad
                            if nuevo_valor < 0:
                                st.error(f"No se puede restar más de lo que hay en {etiqueta}.")
                            else:
                                return nuevo_valor
                        else:
                            st.error(f"Ingresá una cantidad mayor a 0 para restar en {etiqueta}.")
                    return None

                # --- Cantidad en Stock ---
                nuevo_stock = bloque_ajuste("Cantidad en Stock", datos_viejos['En_Stock'], "stock", f" {datos_viejos['UdM']}")
                if nuevo_stock is not None:
                    if update_full_item(
                        lote_mod, lote_mod,
                        datos_viejos['Ubicacion'], datos_viejos['N_Parte'], datos_viejos['Nombre_Parte'],
                        datos_viejos['Costo_Unitario'], nuevo_stock, datos_viejos['Reservado'],
                        datos_viejos['UdM'], datos_viejos['Estado']
                    ):
                        st.success(f"Cantidad en Stock actualizada a {nuevo_stock:g}.")
                        st.rerun()

                st.markdown("---")

                # --- Costo Unitario ---
                nuevo_costo = bloque_ajuste("Costo Unitario ($U)", datos_viejos['Costo_Unitario'], "costo")
                if nuevo_costo is not None:
                    if update_full_item(
                        lote_mod, lote_mod,
                        datos_viejos['Ubicacion'], datos_viejos['N_Parte'], datos_viejos['Nombre_Parte'],
                        nuevo_costo, datos_viejos['En_Stock'], datos_viejos['Reservado'],
                        datos_viejos['UdM'], datos_viejos['Estado']
                    ):
                        st.success(f"Costo Unitario actualizado a $U {nuevo_costo:g}.")
                        st.rerun()

                st.markdown("---")

                # --- Cantidad Reservada ---
                nuevo_reservado = bloque_ajuste("Cantidad Reservada", datos_viejos['Reservado'], "reservado", f" {datos_viejos['UdM']}")
                if nuevo_reservado is not None:
                    if update_full_item(
                        lote_mod, lote_mod,
                        datos_viejos['Ubicacion'], datos_viejos['N_Parte'], datos_viejos['Nombre_Parte'],
                        datos_viejos['Costo_Unitario'], datos_viejos['En_Stock'], nuevo_reservado,
                        datos_viejos['UdM'], datos_viejos['Estado']
                    ):
                        st.success(f"Cantidad Reservada actualizada a {nuevo_reservado:g}.")
                        st.rerun()

                st.markdown("---")

                with st.form("form_modificacion_total"):
                    st.markdown(f"Corrigiendo datos de: **{datos_viejos['Nombre_Parte']}**")

                    c_lote = st.text_input("Código de Lote", value=str(datos_viejos['Lote']))
                    c_ubi = st.text_input("Ubicación / Estante", value=str(datos_viejos['Ubicacion']))
                    c_nparte = st.text_input("Número de Parte (SKU)", value=str(datos_viejos['N_Parte']))
                    c_nombre = st.text_input("Nombre del Artículo", value=str(datos_viejos['Nombre_Parte']))
                    c_costo = st.number_input("Costo Unitario ($U)", value=float(datos_viejos['Costo_Unitario']), step=1.0)
                    c_precio = st.number_input("Precio de Venta ($U)", min_value=0.0, value=float(datos_viejos['Precio_Venta']), step=1.0)
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
                            if update_full_item(lote_mod, c_lote, c_ubi, c_nparte, c_nombre, c_costo, c_stock, c_res, c_udm, c_estado, c_precio):
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
        df_view.style.format({
            "Costo Unitario ($U)": "$U {:,.2f}",
            "Precio Venta ($U)": "$U {:,.2f}"
        }),
        use_container_width=True,
        hide_index=True
    )

# Módulo de generación de códigos QR
if not df_view.empty:
    st.markdown("---")
    st.markdown("### 🖼️ Generador de Códigos QR para Etiquetas")
    lote_seleccionado = st.selectbox("Selecciona un Lote para ver su QR:", options=df_view["Lote"].unique(), key="select_qr")

    if lote_seleccionado:
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

# Módulo de generación de códigos de barras (para imprimir y pegar en cada producto)
if not df_view.empty:
    st.markdown("---")
    st.markdown("### 🏷️ Generador de Códigos de Barras (para el lector)")
    st.caption("El código de barras contiene el Código de Lote del producto. Al escanearlo en la Caja, descuenta stock y registra la venta.")
    lote_barras = st.selectbox("Selecciona un Lote para ver su código de barras:", options=df_view["Lote"].unique(), key="select_barras")

    if lote_barras:
        try:
            buf_bar = BytesIO()
            Code128(str(lote_barras), writer=ImageWriter()).write(buf_bar)
            bytes_bar = buf_bar.getvalue()

            col_bar_img, col_bar_txt = st.columns(2)
            with col_bar_img:
                st.image(bytes_bar, width=300)
            with col_bar_txt:
                st.download_button("💾 Descargar código de barras (PNG)", bytes_bar, f"BARRAS_{lote_barras}.png", "image/png")
        except Exception as e:
            st.error(f"No se pudo generar el código de barras para «{lote_barras}». Usá solo letras sin tildes, números y guiones. ({e})")