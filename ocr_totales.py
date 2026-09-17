"""
Reconstruye Neto/IVA/Subtotal/Total del pie de la factura a partir de
las posiciones de las palabras del OCR, para fotos en las que estos
datos vienen en una fila de "totales" tipo:

    Subtotal | Dto | IIBB* | IVA | Detalle IVA | Total
    6846,32  | 41315,26 | 0 | 0 | 0.00 | 156846,31

En vez de "Total: 1234,56" en texto corrido (que es lo que buscan los
regex de parser.py). Es el mismo problema que resuelve ocr_items.py
para la tabla de ítems, pero para esta fila puntual: no hay líneas de
tabla reales, así que ubicamos las palabras clave (Subtotal/Iva/Total)
por posición y las emparejamos con los números de la fila de valores
más cercana, por cercanía horizontal.
"""
import re
import pytesseract
from pytesseract import Output

from ocr_items import agrupar_en_lineas


def _palabra_normalizada(texto: str) -> str:
    return re.sub(r"[^a-záéíóúñ]", "", texto.lower())


def _campo_de_palabra(norm: str):
    """Identifica si una palabra (ya normalizada) corresponde a alguno de
    los campos que buscamos. Para 'subtotal' usamos coincidencia por
    substring en vez de una lista fija de variantes: la 'S' o 'SU'
    inicial de "SUBTOTAL" es justamente la parte que más se suele leer
    mal en fotos (recortes de borde, sombras, etc.), y puede salir como
    'ubtotal', 'jbtotal', 'gbtotal', etc. — pero siempre termina en
    'total'. Para 'total' exigimos la palabra EXACTA para no confundirla
    con esas mismas variantes de 'subtotal'.
    """
    if norm == "neto":
        return "neto"
    if norm == "iva":
        return "iva"
    if norm == "total":
        return "total"
    if norm.endswith("total") and norm != "total":
        return "subtotal"
    return None


def _es_numero(token: str) -> bool:
    t = token.strip()
    return bool(re.fullmatch(r"-?[\d.]+,\d{2}", t) or re.fullmatch(r"-?\d+(\.\d+)?%?", t))


def extraer_totales_desde_imagen(imagen, lang: str = "spa", datos: dict = None) -> dict:
    """Devuelve un dict {campo: valor_como_texto} con lo que pudo
    reconstruir (neto/subtotal/iva/total), o {} si no encontró nada
    reconocible. Los valores quedan como texto (ej. '6846,32'); la
    conversión a número la hace parser.limpiar_numero.

    `datos` permite pasar las posiciones de palabras ya calculadas por
    otro motor de OCR (ver ocr_paddle.datos_estilo_tesseract); si es
    None se corre Tesseract sobre `imagen` como siempre."""
    if datos is None:
        try:
            datos = pytesseract.image_to_data(imagen, lang=lang, output_type=Output.DICT)
        except pytesseract.TesseractError:
            datos = pytesseract.image_to_data(imagen, output_type=Output.DICT)

    lineas = agrupar_en_lineas(datos)
    if not lineas:
        return {}

    # Buscamos cada palabra clave EMPEZANDO POR EL FINAL del documento y
    # de derecha a izquierda dentro de cada línea: "SUBTOTAL" también
    # aparece como encabezado de la tabla de ítems (mucho más arriba), y
    # algunas facturas repiten la palabra "TOTAL" DOS VECES en la misma
    # fila de encabezado (ej. "TOTAL NETO ... IMP. INT TOTAL", donde la
    # primera es parte de un rótulo compuesto y la segunda es la columna
    # real). Recorriendo todo el documento de atrás para adelante —
    # tanto línea por línea como palabra por palabra dentro de cada
    # línea — nos quedamos siempre con la ocurrencia más a la derecha y
    # más abajo, que es la que realmente encabeza la columna de valores.
    palabras_en_orden_inverso = [
        palabra for linea in reversed(lineas) for palabra in reversed(linea)
    ]

    candidatos = []  # (campo, centro_x, top)
    campos_ya_encontrados = set()
    for palabra in palabras_en_orden_inverso:
        if len(campos_ya_encontrados) == 4:
            break
        norm = _palabra_normalizada(palabra["texto"])
        campo = _campo_de_palabra(norm)
        if campo and campo not in campos_ya_encontrados:
            candidatos.append((campo, palabra["centro_x"], palabra["top"]))
            campos_ya_encontrados.add(campo)

    if not candidatos:
        return {}

    candidatos.sort(key=lambda c: c[1])  # de izquierda a derecha
    top_encabezado = max(c[2] for c in candidatos)

    # La fila de valores: la primera línea DEBAJO del encabezado que
    # tenga varios tokens que parecen números.
    fila_valores = None
    for linea in lineas:
        top_linea = min(p["top"] for p in linea)
        if top_linea <= top_encabezado:
            continue
        numeros = [p for p in linea if _es_numero(p["texto"])]
        if len(numeros) >= 2:
            fila_valores = sorted(linea, key=lambda p: p["centro_x"])
            break

    if not fila_valores:
        return {}

    bordes = [float("-inf")]
    for (_, x1, _), (_, x2, _) in zip(candidatos, candidatos[1:]):
        bordes.append((x1 + x2) / 2)
    bordes.append(float("inf"))

    def _campo_para(centro_x):
        for i in range(len(candidatos)):
            if bordes[i] <= centro_x < bordes[i + 1]:
                return candidatos[i][0]
        return candidatos[-1][0]

    resultado = {}
    for palabra in fila_valores:
        if not _es_numero(palabra["texto"]):
            continue
        campo = _campo_para(palabra["centro_x"])
        # Nos quedamos con el primer número que caiga en cada campo (el
        # más cercano a su etiqueta, de izquierda a derecha), por si hay
        # columnas intermedias (Dto, IIBB) que no nos interesan y caen
        # en el mismo rango.
        resultado.setdefault(campo, palabra["texto"])

    return resultado
