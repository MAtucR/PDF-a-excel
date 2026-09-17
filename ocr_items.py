"""
Reconstruye la tabla de ítems de una factura a partir de las posiciones
de cada palabra que devuelve Tesseract (pytesseract.image_to_data), para
los casos en los que la imagen NO tiene líneas de tabla reales (fotos de
facturas) y por lo tanto pdfplumber.extract_tables() no encuentra nada
(esa función necesita bordes/líneas vectoriales dibujadas en el PDF, y
una foto sólo tiene píxeles).

Es un enfoque heurístico "por columnas":
1. Agrupa las palabras reconocidas en líneas, por cercanía vertical.
2. Ubica la línea de encabezado de la tabla (mismas palabras clave que
   ya se usan para PDFs en parser.ITEM_HEADER_HINTS).
3. Define un rango horizontal ("columna") por cada palabra del
   encabezado, usando el punto medio entre encabezados consecutivos
   como límite entre columnas.
4. Para cada línea siguiente, agrupa sus palabras en esas columnas
   según su posición horizontal, hasta llegar a la fila de "Totales"
   o a un salto en blanco grande (fin de la tabla).

Es un best-effort: en fotos reales puede fallar o mezclar columnas,
sobre todo si la foto está muy inclinada o hay descripciones muy largas
que se superponen con la columna de al lado. Conviene revisar el Excel
resultante cuando el origen es una foto.
"""
import pytesseract
from pytesseract import Output

from parser import ITEM_HEADER_HINTS, _normalizar_header

CONFIANZA_MINIMA = 40  # descarta palabras que el OCR reconoció con poca confianza


def _parsear_confianza(valor) -> int:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return -1


def agrupar_en_lineas(datos: dict) -> list:
    """Agrupa las palabras de pytesseract.image_to_data en líneas, por
    cercanía vertical. No confiamos ciegamente en block/par/line_num de
    Tesseract: en zonas de tabla sin texto corrido, Tesseract puede
    segmentar bloques de forma poco intuitiva. Cada línea resultante es
    una lista de palabras ordenadas de izquierda a derecha.

    Pública (sin _ adelante) porque también la usa ocr_totales.py para
    reconstruir la fila de Subtotal/IVA/Total del pie de la factura.
    """
    palabras = []
    n = len(datos["text"])
    for i in range(n):
        texto = (datos["text"][i] or "").strip()
        conf = _parsear_confianza(datos["conf"][i])
        if not texto or conf < CONFIANZA_MINIMA:
            continue
        top = datos["top"][i]
        height = datos["height"][i]
        left = datos["left"][i]
        width = datos["width"][i]
        palabras.append({
            "texto": texto,
            "top": top,
            "height": height,
            "centro_y": top + height / 2,
            "centro_x": left + width / 2,
        })

    if not palabras:
        return []

    palabras.sort(key=lambda p: (p["centro_y"], p["centro_x"]))
    alturas = sorted(p["height"] for p in palabras)
    altura_tipica = alturas[len(alturas) // 2]
    umbral = max(altura_tipica * 0.7, 8)

    lineas = []
    linea_actual = [palabras[0]]
    for p in palabras[1:]:
        if abs(p["centro_y"] - linea_actual[-1]["centro_y"]) <= umbral:
            linea_actual.append(p)
        else:
            lineas.append(sorted(linea_actual, key=lambda w: w["centro_x"]))
            linea_actual = [p]
    lineas.append(sorted(linea_actual, key=lambda w: w["centro_x"]))

    return lineas


def _es_linea_encabezado(linea: list) -> bool:
    hits = sum(
        1 for p in linea
        if any(hint in p["texto"].lower() for hint in ITEM_HEADER_HINTS)
    )
    return hits >= 2


def _es_linea_totales(linea: list) -> bool:
    texto = " ".join(p["texto"] for p in linea).lower()
    primer_texto = linea[0]["texto"].strip().replace(".", "").replace(",", "")
    primera_es_numero = primer_texto.isdigit()
    return "total" in texto and not primera_es_numero


def extraer_items_desde_imagen(imagen, lang: str = "spa") -> list:
    """Devuelve una lista de dicts {columna: valor}, con la mejor
    reconstrucción posible de la tabla de ítems a partir de las
    posiciones de las palabras que reconoció el OCR. Devuelve lista
    vacía si no pudo identificar una fila de encabezado reconocible."""
    try:
        datos = pytesseract.image_to_data(imagen, lang=lang, output_type=Output.DICT)
    except pytesseract.TesseractError:
        datos = pytesseract.image_to_data(imagen, output_type=Output.DICT)

    lineas = agrupar_en_lineas(datos)
    if not lineas:
        return []

    idx_encabezado = None
    for i, linea in enumerate(lineas):
        if _es_linea_encabezado(linea):
            idx_encabezado = i
            break
    if idx_encabezado is None:
        return []

    linea_encabezado = lineas[idx_encabezado]
    columnas = []  # lista de (nombre_columna, centro_x), ordenada por centro_x
    for palabra in linea_encabezado:
        nombre = _normalizar_header(palabra["texto"])
        if nombre:
            columnas.append((nombre, palabra["centro_x"]))
    if not columnas:
        return []

    columnas.sort(key=lambda c: c[1])

    # Límites (bins) entre columnas: el punto medio entre cada par de
    # encabezados consecutivos, con extremos abiertos a los costados.
    bordes = [float("-inf")]
    for (_, x1), (_, x2) in zip(columnas, columnas[1:]):
        bordes.append((x1 + x2) / 2)
    bordes.append(float("inf"))

    def _columna_para(centro_x):
        for i in range(len(columnas)):
            if bordes[i] <= centro_x < bordes[i + 1]:
                return columnas[i][0]
        return columnas[-1][0]

    altura_media = sum(p["height"] for p in linea_encabezado) / len(linea_encabezado)
    salto_maximo = altura_media * 6  # un salto en blanco grande = fin de la tabla

    items = []
    top_anterior = max(p["top"] + p["height"] for p in linea_encabezado)

    for linea in lineas[idx_encabezado + 1:]:
        top_linea = min(p["top"] for p in linea)
        if top_linea - top_anterior > salto_maximo:
            break
        if _es_linea_totales(linea):
            break

        fila = {}
        for palabra in linea:
            col = _columna_para(palabra["centro_x"])
            if col in fila:
                fila[col] += f" {palabra['texto']}"
            else:
                fila[col] = palabra["texto"]

        if fila:
            items.append(fila)

        top_anterior = max(p["top"] + p["height"] for p in linea)

    return items
