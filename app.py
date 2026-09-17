import os
import uuid
from flask import Flask, request, render_template, send_file, flash, redirect, url_for
from openpyxl import Workbook, load_workbook

from parser import procesar_factura

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
EXCEL_PATH = os.path.join(OUTPUT_DIR, "facturas.xlsx")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = "dev"  # solo para uso local

CABECERA_COLUMNAS = [
    "archivo", "tipo_comprobante", "numero_factura", "fecha_facturacion",
    "cuit_emisor", "razon_social_cliente", "cuit_cliente", "condicion_iva_cliente",
    "neto", "iva", "subtotal", "total", "cae", "vencimiento_cae",
]

ITEMS_COLUMNAS = [
    "archivo", "numero_factura", "sku", "descripcion", "cantidad",
    "precio_unitario", "descuento", "neto", "interno", "iva", "subtotal",
]


def asegurar_excel():
    """Crea el excel con 2 hojas (Cabeceras / Items) si no existe."""
    if os.path.exists(EXCEL_PATH):
        return
    wb = Workbook()
    hoja_cab = wb.active
    hoja_cab.title = "Cabeceras"
    hoja_cab.append(CABECERA_COLUMNAS)

    hoja_items = wb.create_sheet("Items")
    hoja_items.append(ITEMS_COLUMNAS)

    wb.save(EXCEL_PATH)


def agregar_factura_a_excel(nombre_archivo: str, resultado: dict):
    asegurar_excel()
    wb = load_workbook(EXCEL_PATH)
    hoja_cab = wb["Cabeceras"]
    hoja_items = wb["Items"]

    cab = resultado["cabecera"]
    fila_cab = [nombre_archivo] + [cab.get(c) for c in CABECERA_COLUMNAS[1:]]
    hoja_cab.append(fila_cab)

    numero_factura = cab.get("numero_factura")
    for item in resultado["items"]:
        fila_item = [nombre_archivo, numero_factura] + [
            item.get(c) for c in ITEMS_COLUMNAS[2:]
        ]
        hoja_items.append(fila_item)

    wb.save(EXCEL_PATH)


@app.route("/", methods=["GET"])
def index():
    ya_existe = os.path.exists(EXCEL_PATH)
    return render_template("index.html", excel_existe=ya_existe)


@app.route("/subir", methods=["POST"])
def subir():
    archivo = request.files.get("factura")
    if not archivo or archivo.filename == "":
        flash("No seleccionaste ningún archivo.")
        return redirect(url_for("index"))

    if not archivo.filename.lower().endswith(".pdf"):
        flash("El archivo debe ser un PDF.")
        return redirect(url_for("index"))

    nombre_unico = f"{uuid.uuid4().hex[:8]}_{archivo.filename}"
    ruta_pdf = os.path.join(UPLOAD_DIR, nombre_unico)
    archivo.save(ruta_pdf)

    try:
        resultado = procesar_factura(ruta_pdf)
        agregar_factura_a_excel(archivo.filename, resultado)
        campos_vacios = [k for k, v in resultado["cabecera"].items() if v is None]
        if campos_vacios:
            flash(f"Factura cargada, pero no se pudieron detectar estos campos: {', '.join(campos_vacios)}")
        else:
            flash(f"Factura '{archivo.filename}' procesada correctamente ({len(resultado['items'])} ítems detectados).")
    except Exception as e:
        flash(f"Error procesando el PDF: {e}")

    return redirect(url_for("index"))


@app.route("/descargar")
def descargar():
    if not os.path.exists(EXCEL_PATH):
        flash("Todavía no se generó ningún Excel.")
        return redirect(url_for("index"))
    return send_file(EXCEL_PATH, as_attachment=True, download_name="facturas.xlsx")


@app.route("/reiniciar", methods=["POST"])
def reiniciar():
    if os.path.exists(EXCEL_PATH):
        os.remove(EXCEL_PATH)
    flash("Excel reiniciado.")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)
