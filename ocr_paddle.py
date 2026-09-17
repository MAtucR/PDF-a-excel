"""
Motor de OCR alternativo basado en PaddleOCR.

Tesseract fue diseñado para documentos escaneados con buena resolución.
PaddleOCR usa modelos de deep learning entrenados con fotos reales
("in the wild"), y suele leer bastante mejor imágenes de baja resolución
o comprimidas — que es el caso de las fotos que llegan por WhatsApp.
No es un LLM y no manda nada a ninguna API: baja los modelos una sola
vez y después corre 100% local, igual que Tesseract.

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

Si PaddleOCR no está instalado o no se puede inicializar, el llamador
sigue con Tesseract: nunca rompe el flujo existente. El motivo del
fallo queda disponible en `motivo_fallo()` para poder mostrarlo.
"""
import numpy as np

# Instancia global perezosa: crear el pipeline de PaddleOCR es caro
# (carga los modelos en memoria), así que se hace una sola vez y se
# reusa entre facturas.
_OCR = None
_INTENTO_FALLIDO = False
_ULTIMO_ERROR = None  # motivo del ultimo fallo, para poder mostrarlo al usuario


def motivo_fallo():
    """Mensaje del ultimo error al intentar usar PaddleOCR, o None si
    nunca fallo. Lo usa ocr_utils para avisarle al usuario por que se
    proceso la factura con Tesseract en vez de Paddle."""
    return _ULTIMO_ERROR


def disponible() -> bool:
    """True si el paquete paddleocr se puede importar."""
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


def _inferencia_de_prueba(ocr):
    """Corre una inferencia minima sobre una imagen sintetica para
    verificar que esta configuracion REALMENTE funciona.

    Hace falta porque PaddleOCR puede construirse sin errores y recien
    explotar al inferir (pasa con el backend oneDNN de paddlepaddle
    3.3.x). Sin esta validacion elegiriamos una configuracion rota, y el
    fallo aparecería recién al procesar una factura de verdad.

    Devuelve (True, None) si anduvo, o (False, excepcion) si no.
    """
    try:
        imagen = np.full((80, 400, 3), 255, dtype=np.uint8)
        imagen[30:50, 20:380] = 0  # una banda negra, suficiente para ejercitar el grafo
        if hasattr(ocr, "predict"):
            ocr.predict(imagen)
        else:
            ocr.ocr(imagen)
        return True, None
    except Exception as e:
        return False, e


def _obtener_ocr(lang: str = "es"):
    """Devuelve la instancia de PaddleOCR, creándola la primera vez.
    Si la creación falla (falta el paquete, no pudo bajar los modelos,
    no hay red la primera vez, etc.) devuelve None, guarda el motivo en
    _ULTIMO_ERROR y lo recuerda, para no reintentar en cada factura."""
    global _OCR, _INTENTO_FALLIDO, _ULTIMO_ERROR
    if _OCR is not None:
        return _OCR
    if _INTENTO_FALLIDO:
        return None

    try:
        from paddleocr import PaddleOCR
    except Exception as e:
        _INTENTO_FALLIDO = True
        _ULTIMO_ERROR = f"no se pudo importar paddleocr ({type(e).__name__}: {e})"
        return None

    # Probamos varias configuraciones y nos quedamos con la primera que
    # ademas de CONSTRUIR pueda correr una inferencia de prueba (ver
    # _inferencia_de_prueba). Los dos motivos por los que hace falta
    # probar varias:
    #
    # 1. El nombre de los parametros cambio entre versiones de PaddleOCR
    #    (2.x usa use_angle_cls, 3.x usa use_textline_orientation).
    #
    # 2. Con paddlepaddle 3.3.x en Windows, el backend oneDNN (MKLDNN)
    #    rompe la inferencia con:
    #       NotImplementedError: ConvertPirAttribute2RuntimeAttribute
    #       not support [pir::ArrayAttribute<pir::DoubleAttribute>]
    #    y el error aparece SOLO al inferir: el constructor devuelve un
    #    objeto aparentemente sano. Por eso las variantes con
    #    enable_mkldnn=False van primero, y por eso validamos con una
    #    inferencia real antes de dar por buena una configuracion.
    #    (Verificado con diagnostico_paddle2.py sobre paddleocr 3.7.0 +
    #    paddlepaddle 3.3.1 en Windows: sin oneDNN anda, con oneDNN no.)
    intentos = [
        dict(lang=lang, use_textline_orientation=True, enable_mkldnn=False),
        dict(lang=lang, use_angle_cls=True, enable_mkldnn=False),
        dict(lang=lang, enable_mkldnn=False),
        dict(lang=lang, use_textline_orientation=True),
        dict(lang=lang, use_angle_cls=True),
        dict(lang=lang),
        dict(),
    ]
    ultimo = None
    for kwargs in intentos:
        try:
            candidato = PaddleOCR(**kwargs)
        except Exception as e:
            ultimo = e
            continue

        ok, error_inferencia = _inferencia_de_prueba(candidato)
        if ok:
            _OCR = candidato
            _ULTIMO_ERROR = None
            return _OCR
        ultimo = error_inferencia

    _INTENTO_FALLIDO = True
    detalle = str(ultimo).split("\n")[0][:200] if ultimo else "motivo desconocido"
    if "ConvertPirAttribute" in detalle or "onednn" in detalle.lower():
        _ULTIMO_ERROR = (
            "PaddleOCR no puede inferir en esta maquina: el backend oneDNN de "
            "paddlepaddle rompe el grafo, y desactivarlo tampoco alcanzo. "
            f"Detalle: {detalle}. Probar 'pip install paddlepaddle==3.0.0' o "
            "correr diagnostico_paddle2.py."
        )
    elif "hosting" in detalle.lower() or "network" in detalle.lower() or "connect" in detalle.lower():
        _ULTIMO_ERROR = (
            "PaddleOCR no pudo descargar sus modelos (hace falta internet "
            f"la primera vez). Detalle: {detalle}"
        )
    else:
        _ULTIMO_ERROR = f"PaddleOCR no pudo inicializarse. Detalle: {detalle}"
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
