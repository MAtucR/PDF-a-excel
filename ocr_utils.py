"""
Convierte fotos/imágenes de facturas (jpg, png, etc.) en un PDF con una
capa de texto OCR superpuesta, para poder reusar TAL CUAL el mismo
pipeline de extracción de CABECERA que ya existe en parser.py para PDFs
digitales. Además, reconstruye por posición de palabras:
- la tabla de ítems (ver ocr_items.py)
- la fila de Neto/Subtotal/IVA/Total del pie (ver ocr_totales.py)
porque una foto no tiene líneas de tabla reales y pdfplumber no
encuentra nada ahí.

Requiere tener instalado el BINARIO de Tesseract OCR en el sistema
operativo (no alcanza con el paquete de Python `pytesseract`, que es
solo un wrapper que llama a ese binario), Y el paquete de idioma
español ('spa'). Sin el paquete de español, Tesseract reconoce el texto
en inglés por defecto, lo cual arruina bastante la lectura de facturas
en español (tildes, formato de números, etc.). Ver instrucciones en el
README, sección "OCR para fotos/imágenes".

Opcionalmente puede usar PaddleOCR como motor principal (ver
ocr_paddle.py), que suele leer mejor fotos de baja resolución. Si no
está instalado o no arranca, todo sigue funcionando con Tesseract.
"""
import json
import os
import re

import cv2
import numpy as np
import pytesseract
from pytesseract import Output
from PIL import Image, ImageFilter, ImageOps

from escaner import enderezar_documento
import ocr_paddle
from ocr_items import extraer_items_desde_imagen, agrupar_en_lineas
from ocr_totales import extraer_totales_desde_imagen

# Extensiones de imagen que la app acepta además de .pdf
EXTENSIONES_IMAGEN = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

# Ancho mínimo (en píxeles) que le pedimos a la imagen antes de mandarla a
# OCR. Fotos de celular sacadas de lejos, o comprimidas por WhatsApp,
# suelen quedar con poca resolución efectiva sobre las letras, y eso
# arruina el reconocimiento.
ANCHO_MINIMO_OCR = 2200

# Motor de OCR a usar para fotos. Se puede cambiar con la variable de
# entorno MOTOR_OCR:
#   "auto"      (default) usa PaddleOCR si está instalado, si no Tesseract
#   "paddle"    fuerza PaddleOCR (si falla, cae igual a Tesseract)
#   "tesseract" fuerza Tesseract (comportamiento histórico)
# PaddleOCR suele leer bastante mejor fotos de baja resolución
# (WhatsApp), pero Tesseract sigue siendo el fallback siempre disponible.
MOTOR_OCR = os.environ.get("MOTOR_OCR", "auto").lower()


def _usar_paddle() -> bool:
    if MOTOR_OCR == "tesseract":
        return False
    if MOTOR_OCR in ("paddle", "auto"):
        return ocr_paddle.disponible()
    return False


def es_imagen(nombre_archivo: str) -> bool:
    return nombre_archivo.lower().endswith(EXTENSIONES_IMAGEN)


def _hay_idioma_espanol() -> bool:
    try:
        return "spa" in pytesseract.get_languages(config="")
    except Exception:
        return False


def _corregir_rotacion(imagen: Image.Image) -> Image.Image:
    """Corrige rotaciones de 90/180/270 vía el OSD de Tesseract."""
    try:
        osd = pytesseract.image_to_osd(imagen)
        m = re.search(r"Rotate:\s*(\d+)", osd)
        if m:
            angulo = int(m.group(1))
            if angulo:
                imagen = imagen.rotate(-angulo, expand=True)
    except Exception:
        pass
    return imagen


def _preprocesar_base(imagen: Image.Image) -> Image.Image:
    """Pasos comunes a las dos versiones (gris y binaria): orientación
    EXIF, detección de hoja/perspectiva, escala de grises, corrección de
    rotación y upscaling si es chica."""
    imagen = ImageOps.exif_transpose(imagen)
    imagen = enderezar_documento(imagen)
    imagen = imagen.convert("L")
    imagen = _corregir_rotacion(imagen)

    if imagen.width < ANCHO_MINIMO_OCR:
        factor = ANCHO_MINIMO_OCR / imagen.width
        nuevo_alto = int(imagen.height * factor)
        imagen = imagen.resize((ANCHO_MINIMO_OCR, nuevo_alto), Image.LANCZOS)

    return imagen


def _version_gris(base: Image.Image) -> Image.Image:
    """Versión en escala de grises con autocontraste y nitidez.
    Mejor para la CABECERA (textos chicos con variaciones de tono:
    CUIT, fecha, número de factura, CAE)."""
    gris = ImageOps.autocontrast(base)
    gris = gris.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    return gris


def _version_binaria(base: Image.Image) -> Image.Image:
    """Versión binarizada con umbral adaptativo (como un scanner de
    documentos). Cada pixel se compara contra el promedio de su
    vecindario local, eliminando sombras y variaciones de iluminación.
    Mejor para la TABLA DE ÍTEMS (texto alineado en columnas) y para
    los TOTALES del pie de la factura.

    block_size=31 y C=15 se calibraron probando con una foto real de
    factura de distribuidora (Chitarroni) en las condiciones más
    hostiles que teníamos: foto de WhatsApp a 720x1280."""
    arr = np.array(base)
    binaria = cv2.adaptiveThreshold(
        arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15
    )
    return Image.fromarray(binaria)


def _lineas_para_debug(imagen: Image.Image, lang: str) -> list:
    """Texto de cada línea detectada por posición, para diagnóstico."""
    from ocr_items import _es_linea_encabezado
    try:
        datos = pytesseract.image_to_data(imagen, lang=lang, output_type=Output.DICT)
    except pytesseract.TesseractError:
        datos = pytesseract.image_to_data(imagen, output_type=Output.DICT)
    lineas = agrupar_en_lineas(datos)
    return [
        {
            "texto": " ".join(p["texto"] for p in linea),
            "es_encabezado_detectado": _es_linea_encabezado(linea),
        }
        for linea in lineas
    ]


def convertir_imagen_a_pdf_ocr(ruta_imagen: str, ruta_pdf_salida: str, ruta_texto_debug: str = None):
    """
    Toma la ruta de una imagen, le corre OCR y genera en `ruta_pdf_salida`
    un PDF con la imagen + el texto reconocido superpuesto. Además
    reconstruye la tabla de ítems y la fila de totales por posición de
    palabras.

    ENFOQUE MULTI-MOTOR: genera dos versiones de la imagen preprocesada
    — una en escala de grises (mejor para cabecera) y otra binarizada
    con umbral adaptativo tipo scanner (mejor para tabla de ítems y
    totales) — y corre Tesseract sobre ambas. Si PaddleOCR está
    disponible, también corre sobre la versión gris y sus resultados
    tienen prioridad. El texto de todas las versiones se concatena para
    los regex de cabecera (el primero que matchee gana).

    Devuelve una tupla (ruta_pdf, items, totales, advertencia, texto).

    `texto` es el texto combinado de TODOS los motores/versiones. Es
    importante que el llamador lo use para extraer la cabecera: el PDF
    que se genera lleva solamente la capa de texto de Tesseract sobre la
    version gris, asi que si la cabecera se extrae unicamente del PDF se
    pierde todo lo que reconocieron mejor la version binarizada y
    PaddleOCR.
    """
    advertencia = None
    idioma = "spa" if _hay_idioma_espanol() else None

    if idioma is None:
        advertencia = (
            "El paquete de idioma español de Tesseract no parece estar "
            "instalado: el texto se reconoció en inglés, así que es "
            "esperable que varios campos no se detecten bien. Instalá el "
            "paquete 'Spanish' (Windows, durante la instalación de "
            "Tesseract) o 'tesseract-ocr-spa' (Linux) / 'tesseract-lang' "
            "(Mac) — ver README, sección 'OCR para fotos/imágenes'."
        )

    with Image.open(ruta_imagen) as imagen_original:
        base = _preprocesar_base(imagen_original)
        gris = _version_gris(base)
        binaria = _version_binaria(base)
        lang_usado = idioma or "eng"

        # --- OCR sobre la versión GRIS (mejor para cabecera) ---
        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                gris, extension="pdf", lang=lang_usado
            )
            texto_gris = pytesseract.image_to_string(gris, lang=lang_usado)
        except pytesseract.TesseractError:
            lang_usado = None
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(gris, extension="pdf")
            texto_gris = pytesseract.image_to_string(gris)
            if advertencia is None:
                advertencia = (
                    "No se pudo usar el idioma español para el OCR (revisá "
                    "que 'spa' esté bien instalado); se usó el idioma por "
                    "defecto de Tesseract."
                )

        # --- OCR sobre la versión BINARIZADA (mejor para ítems/totales) ---
        try:
            texto_bin = pytesseract.image_to_string(binaria, lang=lang_usado or "eng")
        except pytesseract.TesseractError:
            texto_bin = pytesseract.image_to_string(binaria)

        # --- OCR con PaddleOCR, si está disponible ---
        # PaddleOCR trabaja mejor sobre la imagen en GRIS/color que sobre
        # la binarizada: sus modelos fueron entrenados con fotos reales,
        # así que la binarización le saca información en vez de ayudarlo
        # (al revés que Tesseract).
        datos_paddle = None
        texto_paddle = ""
        if _usar_paddle():
            try:
                datos_paddle = ocr_paddle.datos_estilo_tesseract(gris)
                if datos_paddle and datos_paddle.get("text"):
                    texto_paddle = ocr_paddle.texto_plano(gris)
                else:
                    datos_paddle = None
            except Exception:
                datos_paddle = None
                texto_paddle = ""

            # Si paddleocr esta instalado pero no se pudo usar, avisamos:
            # sin esto el fallback a Tesseract es invisible y uno cree que
            # esta usando Paddle cuando en realidad no.
            if datos_paddle is None:
                motivo = ocr_paddle.motivo_fallo() or "motivo desconocido"
                aviso = (
                    f"paddleocr esta instalado pero no se pudo usar, se proceso "
                    f"con Tesseract. {motivo} "
                    f"(corre 'python diagnostico_paddle.py' para mas detalle)"
                )
                advertencia = f"{advertencia} | {aviso}" if advertencia else aviso

        # Texto combinado: las versiones de Tesseract (gris + binarizada)
        # más la de PaddleOCR si corrió. Los regex de cabecera
        # (parser.extraer_cabecera) agarran la primera ocurrencia que
        # matchee — así se benefician de todas las versiones sin riesgo
        # de conflicto. Paddle va primero por ser el más confiable en
        # fotos de baja resolución.
        partes = [p for p in (texto_paddle, texto_gris, texto_bin) if p]
        texto = "\n".join(partes)

        # --- Ítems: primero Paddle (si corrió), después Tesseract ---
        items = []
        if datos_paddle:
            try:
                items = extraer_items_desde_imagen(
                    gris, lang=lang_usado or "eng", datos=datos_paddle
                )
            except Exception:
                items = []

        if not items:
            try:
                items = extraer_items_desde_imagen(binaria, lang=lang_usado or "eng")
            except Exception:
                items = []

        if not items:
            try:
                items = extraer_items_desde_imagen(gris, lang=lang_usado or "eng")
            except Exception:
                items = []

        # --- Totales: mismo orden de preferencia ---
        totales = {}
        if datos_paddle:
            try:
                totales = extraer_totales_desde_imagen(
                    gris, lang=lang_usado or "eng", datos=datos_paddle
                )
            except Exception:
                totales = {}

        if not totales:
            try:
                totales = extraer_totales_desde_imagen(binaria, lang=lang_usado or "eng")
            except Exception:
                totales = {}

        if not totales:
            try:
                totales = extraer_totales_desde_imagen(gris, lang=lang_usado or "eng")
            except Exception:
                totales = {}

        lineas_debug = None
        if ruta_texto_debug and not items:
            try:
                lineas_debug = _lineas_para_debug(binaria, lang_usado or "eng")
            except Exception:
                lineas_debug = None

    with open(ruta_pdf_salida, "wb") as f:
        f.write(pdf_bytes)

    if ruta_texto_debug:
        with open(ruta_texto_debug, "w", encoding="utf-8") as f:
            f.write(texto)
        try:
            ruta_items_debug = ruta_texto_debug.replace("_texto_ocr.txt", "_items_debug.json")
            contenido_debug = {
                "items": items,
                "totales": totales,
                "motor_usado": "paddleocr" if datos_paddle else "tesseract",
            }
            if lineas_debug is not None:
                contenido_debug["lineas_detectadas"] = lineas_debug
            with open(ruta_items_debug, "w", encoding="utf-8") as f:
                json.dump(contenido_debug, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return ruta_pdf_salida, items, totales, advertencia, texto
