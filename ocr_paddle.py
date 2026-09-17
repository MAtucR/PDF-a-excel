"""
Motor de OCR alternativo basado en PaddleOCR.

Tesseract fue diseñado para documentos escaneados con buena resolución.
PaddleOCR usa modelos de deep learning entrenados con fotos reales
("in the wild"), y suele leer bastante mejor imágenes de baja resolución
o comprimidas — que es el caso de las fotos que llegan por WhatsApp.
No es un LLM y no manda nada a ninguna API: baja los modelos una sola
vez (~10MB) y después corre 100% local, igual que Tesseract.

Este módulo es un ADAPTADOR: expone los resultados de PaddleOCR en el
MISMO formato de diccionario que devuelve `pytesseract.image_to_data`
(claves text/conf/left/top/width/height, una entrada por palabra), para
que `ocr_items.agrupar_en_lineas` y `ocr_totales` funcionen sin cambios
sobre cualquiera de los dos motores.

Diferencia clave entre motores: Tesseract devuelve una caja por PALABRA;
PaddleOCR devuelve una caja por REGIÓN de texto detectada (que según el
espaciado puede ser una celda, un fragmento de fila, o una fila entera).
Para poder reusar la reconstrucción por columnas, cada región se parte
en palabras y se les reparte la posición horizontal de forma
proporcional a su largo en caracteres dentro de la región. Es una
aproximación, pero funciona bien con las tipografías monoespaciadas
(tipo máquina de escribir / matriz de puntos) de las facturas
pre-impresas de distribuidoras, que es donde más lo necesitamos.

Si PaddleOCR no está instalado, `disponible()` devuelve False y el
llamador sigue con Tesseract: nunca rompe el flujo existente.
"""
import numpy as np

# Instancia global perezosa: crear el pipeline de PaddleOCR es caro
# (carga los modelos en memoria), así que se hace una sola vez y se
# reusa entre facturas.
_OCR = None
_INTENTO_FALLIDO = False


def disponible() -> bool:
    """True si el paquete paddleocr se puede importar."""
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def _obtener_ocr(lang: str = "es"):
    """Devuelve la instancia de PaddleOCR, creándola la primera vez.
    Si la creación falla (falta el paquete, no pudo bajar los modelos,
    no hay red la primera vez, etc.) devuelve None y lo recuerda, para
    no reintentar en cada factura."""
    global _OCR, _INTENTO_FALLIDO
    if _OCR is not None:
        return _OCR
    if _INTENTO_FALLIDO:
        return None
    try:
        from paddleocr import PaddleOCR
        _OCR = PaddleOCR(lang=lang, use_textline_orientation=True)
        return _OCR
    except Exception:
        _INTENTO_FALLIDO = True
        return None


def _resultado_a_regiones(salida):
    """Normaliza la salida de PaddleOCR a una lista de
    (texto, confianza, caja) sin importar la versión del paquete.

    PaddleOCR cambió el formato entre versiones:
    - API vieja (.ocr()): [[[caja, (texto, score)], ...]]
    - API nueva (.predict()): [{'rec_texts': [...], 'rec_scores': [...],
                                'rec_polys'/'dt_polys': [...]}]
    """
    regiones = []
    if not salida:
        return regiones

    primero = salida[0]

    # --- Formato nuevo (dict) ---
    if isinstance(primero, dict):
        textos = primero.get("rec_texts") or []
        scores = primero.get("rec_scores") or []
        cajas = primero.get("rec_polys")
        if cajas is None:
            cajas = primero.get("dt_polys") or []
        for i, texto in enumerate(textos):
            score = scores[i] if i < len(scores) else 0.0
            caja = cajas[i] if i < len(cajas) else None
            if caja is not None:
                regiones.append((texto, float(score), np.asarray(caja, dtype=float)))
        return regiones

    # --- Formato viejo (listas anidadas) ---
    lineas = primero if isinstance(primero, list) else salida
    for entrada in lineas or []:
        try:
            caja, (texto, score) = entrada[0], entrada[1]
            regiones.append((texto, float(score), np.asarray(caja, dtype=float)))
        except Exception:
            continue
    return regiones


def datos_estilo_tesseract(imagen, lang: str = "es", confianza_minima: float = 0.3) -> dict:
    """Corre PaddleOCR sobre una imagen PIL y devuelve un dict con el
    mismo formato que `pytesseract.image_to_data(output_type=DICT)`.

    Devuelve un dict con listas vacías si PaddleOCR no está disponible o
    si falla, para que el llamador pueda detectarlo y caer a Tesseract.
    """
    vacio = {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    ocr = _obtener_ocr(lang)
    if ocr is None:
        return vacio

    arr = np.array(imagen.convert("RGB"))

    try:
        # API nueva
        salida = ocr.predict(arr)
    except AttributeError:
        try:
            salida = ocr.ocr(arr)
        except Exception:
            return vacio
    except Exception:
        try:
            salida = ocr.ocr(arr)
        except Exception:
            return vacio

    regiones = _resultado_a_regiones(salida)
    if not regiones:
        return vacio

    datos = {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []}

    for texto, score, caja in regiones:
        if not texto or score < confianza_minima:
            continue

        xs = caja[:, 0]
        ys = caja[:, 1]
        x0, x1 = float(xs.min()), float(xs.max())
        y0, y1 = float(ys.min()), float(ys.max())
        ancho_region = max(x1 - x0, 1.0)
        alto_region = max(y1 - y0, 1.0)

        palabras = texto.split()
        if not palabras:
            continue

        # Repartir el ancho de la región entre las palabras, proporcional
        # a su largo en caracteres (incluyendo los espacios que las
        # separan), para aproximar dónde cae cada palabra.
        total_chars = sum(len(p) for p in palabras) + (len(palabras) - 1)
        total_chars = max(total_chars, 1)

        offset_chars = 0
        for palabra in palabras:
            frac_inicio = offset_chars / total_chars
            frac_fin = (offset_chars + len(palabra)) / total_chars

            left = x0 + ancho_region * frac_inicio
            width = max(ancho_region * (frac_fin - frac_inicio), 1.0)

            datos["text"].append(palabra)
            datos["conf"].append(score * 100.0)  # pytesseract usa 0-100
            datos["left"].append(int(round(left)))
            datos["top"].append(int(round(y0)))
            datos["width"].append(int(round(width)))
            datos["height"].append(int(round(alto_region)))

            offset_chars += len(palabra) + 1  # +1 por el espacio

    return datos


def texto_plano(imagen, lang: str = "es", confianza_minima: float = 0.3) -> str:
    """Texto reconocido por PaddleOCR, agrupado en líneas por cercanía
    vertical, para alimentar los regex de cabecera de parser.py.
    Devuelve "" si PaddleOCR no está disponible."""
    datos = datos_estilo_tesseract(imagen, lang=lang, confianza_minima=confianza_minima)
    if not datos["text"]:
        return ""

    # Reusamos el mismo agrupador por posición que se usa para los ítems,
    # así el texto sale con las palabras de cada fila juntas (importante
    # para los regex de cabecera tipo "CUIT: 30-...", donde etiqueta y
    # valor están separados horizontalmente).
    from ocr_items import agrupar_en_lineas

    lineas = agrupar_en_lineas(datos)
    return "\n".join(" ".join(p["texto"] for p in linea) for linea in lineas)
