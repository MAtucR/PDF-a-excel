"""
Reconstruye Neto/IVA/Subtotal/Total del pie de la factura a partir de
las posiciones de las palabras del OCR, para fotos en las que estos
datos vienen en una fila de "totales" tipo:

    TOTAL NETO |        IVA         | PER 5329 | IMP. INT. |   TOTAL
    211362.27  | IVA 21.0% 44386.07 |   0.0    |    0.00   | 255,748.34

En vez de "Total: 1234,56" en texto corrido (que es lo que buscan los
regex de parser.py). Es el mismo problema que resuelve ocr_items.py
para la tabla de ítems, pero para esta fila puntual: no hay líneas de
tabla reales, así que hay que emparejar por posición horizontal las
etiquetas con los números de la fila de abajo.

ENFOQUE: se busca la LÍNEA que hace de encabezado (la que tiene varias
etiquetas juntas), se agrupan TODAS sus columnas — incluidas las que no
nos interesan, como "PER 5329" o "IMP. INT." — y después cada número de
la fila de valores se asigna a la columna cuyo centro tenga más cerca.

Los tres detalles que hacen falta para que esto funcione, y que una
versión anterior no contemplaba (por eso devolvía {} en esta factura):

1. La fila de VALORES puede contener ella misma alguna de las palabras
   clave ("211362.27 IVA 21.0% 44386.07 ..."), así que buscar las
   etiquetas palabra por palabra engancha esa "IVA" y desplaza todo. Por
   eso se identifica primero la línea de encabezado completa.

2. Hay que mapear también las columnas que NO nos interesan. Si solo se
   toman neto/iva/total como columnas, el "0.00" de "IMP. INT." cae en
   el rango de "TOTAL" y se lo lleva antes que el total de verdad.

3. La etiqueta hay que clasificarla COMPLETA, no palabra por palabra:
   "TOTAL NETO" es la columna del neto, no la del total.
"""
import re

import pytesseract
from pytesseract import Output

from ocr_items import agrupar_en_lineas


def _palabra_normalizada(texto: str) -> str:
    return re.sub(r"[^a-záéíóúñ]", "", texto.lower())


def _es_etiqueta(norm: str) -> bool:
    """La palabra es una de las que pueden encabezar una columna de
    totales."""
    return norm in ("neto", "iva", "total") or norm.endswith("total")


def _campo_de_columna(etiqueta: str):
    """Clasifica el texto COMPLETO de una columna del encabezado.

    Se mira la columna entera y no palabra por palabra porque la
    etiqueta suele ser compuesta: "TOTAL NETO" es la columna del neto,
    no la del total, y decidirlo mirando solo la palabra "TOTAL" da el
    campo equivocado. Por eso 'neto' se chequea primero.
    """
    e = re.sub(r"[^a-záéíóúñ ]", " ", etiqueta.lower())
    e = " ".join(e.split())
    if not e:
        return None
    if "neto" in e:
        return "neto"
    if e == "total":
        return "total"
    if "subtotal" in e or (e.endswith("total") and e != "total"):
        return "subtotal"
    if "iva" in e:
        return "iva"
    if "total" in e:
        # "TOTAL" acompañado de otra cosa que no reconocemos
        return "total"
    return None


def _es_numero(token: str) -> bool:
    t = token.strip()
    # Sin al menos un dígito no es un número: el OCR suelta tokens de
    # pura puntuación (".", ",", "-") como ruido, y como la tercera
    # alternativa del regex los matcheaba, contaban como "número",
    # ocupaban una columna vía setdefault y BLOQUEABAN el valor real de
    # esa columna (que después quedaba vacía en el Excel).
    if not any(ch.isdigit() for ch in t):
        return False
    return bool(re.fullmatch(r"-?[\d.]+,\d{2}", t) or re.fullmatch(r"-?\d+(\.\d+)?%?", t)
                or re.fullmatch(r"-?[\d.,]+", t))


def _es_porcentaje(token: str) -> bool:
    """Los porcentajes ('21.0%', '21,0%') NO son valores de la fila: son
    parte de la etiqueta del IVA. Si no se descartan, el IVA se lleva la
    alícuota en vez del importe."""
    return "%" in token


def _agrupar_en_columnas(linea: list) -> list:
    """Agrupa las palabras de una línea en columnas, cortando donde hay
    un hueco horizontal grande. Devuelve [(texto, centro_x), ...].

    El umbral se calcula a partir del alto de las letras (proxy del
    tamaño de fuente): las palabras de una misma etiqueta están separadas
    por un espacio simple, mientras que entre columnas de una tabla hay
    varios espacios de separación.
    """
    if not linea:
        return []

    palabras = sorted(linea, key=lambda p: p["centro_x"])
    alturas = sorted(p["height"] for p in palabras)
    altura = alturas[len(alturas) // 2] or 10
    hueco_minimo = altura * 1.6

    columnas = []
    grupo = [palabras[0]]
    for p in palabras[1:]:
        fin_anterior = max(w["centro_x"] + w.get("width", 0) / 2 for w in grupo)
        inicio = p["centro_x"] - p.get("width", 0) / 2
        if inicio - fin_anterior > hueco_minimo:
            columnas.append(grupo)
            grupo = [p]
        else:
            grupo.append(p)
    columnas.append(grupo)

    resultado = []
    for grupo in columnas:
        texto = " ".join(w["texto"] for w in grupo)
        izq = min(w["centro_x"] - w.get("width", 0) / 2 for w in grupo)
        der = max(w["centro_x"] + w.get("width", 0) / 2 for w in grupo)
        resultado.append((texto, (izq + der) / 2))
    return resultado


def _es_linea_encabezado_totales(linea: list) -> bool:
    """Una línea es el encabezado de la fila de totales si tiene al menos
    dos etiquetas reconocibles y no es mayoritariamente numérica (eso
    último la distinguiría de la fila de valores)."""
    etiquetas = sum(1 for p in linea if _es_etiqueta(_palabra_normalizada(p["texto"])))
    if etiquetas < 2:
        return False
    numeros = sum(1 for p in linea if _es_numero(p["texto"]))
    return numeros <= len(linea) * 0.4


def extraer_totales_desde_imagen(imagen, lang: str = "spa", datos: dict = None) -> dict:
    """Devuelve un dict {campo: valor_como_texto} con lo que pudo
    reconstruir (neto/subtotal/iva/total), o {} si no encontró nada
    reconocible. Los valores quedan como texto (ej. '6846,32'); la
    conversión a número la hace parser.limpiar_numero.

    `datos` permite pasar las posiciones de palabras ya calculadas por
    otro motor de OCR (ver ocr_paddle.datos_estilo_tesseract); si es
    None se corre Tesseract sobre `imagen` como siempre.
    """
    if datos is None:
        try:
            datos = pytesseract.image_to_data(imagen, lang=lang, output_type=Output.DICT)
        except pytesseract.TesseractError:
            datos = pytesseract.image_to_data(imagen, output_type=Output.DICT)

    lineas = agrupar_en_lineas(datos)
    if not lineas:
        return {}

    # Se recorre de abajo hacia arriba: la fila de totales está al pie, y
    # "SUBTOTAL"/"IMPORTE" también aparecen mucho más arriba como
    # encabezado de la tabla de ítems.
    for idx in range(len(lineas) - 1, -1, -1):
        if not _es_linea_encabezado_totales(lineas[idx]):
            continue

        columnas = _agrupar_en_columnas(lineas[idx])
        campos = [(_campo_de_columna(texto), centro) for texto, centro in columnas]
        if not any(campo for campo, _ in campos):
            continue

        # Fila de valores: la primera línea de abajo con al menos dos
        # números que no sean porcentajes.
        top_encabezado = max(p["top"] for p in lineas[idx])
        fila_valores = None
        for linea in lineas[idx + 1:]:
            if min(p["top"] for p in linea) <= top_encabezado:
                continue
            numeros = [p for p in linea
                       if _es_numero(p["texto"]) and not _es_porcentaje(p["texto"])]
            if len(numeros) >= 2:
                fila_valores = numeros
                break
        if not fila_valores:
            continue

        # Cada número va a la columna cuyo centro tenga más cerca. Se usan
        # TODAS las columnas del encabezado (no solo las que nos
        # interesan) para que los valores de las columnas intermedias
        # — "PER 5329", "IMP. INT." — no se cuelen en las nuestras.
        resultado = {}
        for palabra in sorted(fila_valores, key=lambda p: p["centro_x"]):
            campo, _ = min(
                campos, key=lambda c: abs(c[1] - palabra["centro_x"])
            )
            if campo:
                resultado.setdefault(campo, palabra["texto"])

        if resultado:
            return resultado

    return {}
