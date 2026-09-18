"""
Parser de facturas argentinas (AFIP) a partir de PDFs con capa de texto.
No usa LLM: regex para cabecera + detección de tablas de pdfplumber para ítems.

Cada campo de cabecera puede tener una o más expresiones regulares
alternativas (se prueban en orden, se usa la primera que matchee), porque
distintos proveedores/sistemas de facturación usan etiquetas distintas
para el mismo dato (por ej. "Razón Social:" vs "Sres:", o "CAE Nro" vs
"C.A.E.:").
"""
import re
import pdfplumber


# ---------------------------------------------------------------------------
# 1. CABECERA — regex sobre el texto plano
# ---------------------------------------------------------------------------

HEADER_PATTERNS = {
    "tipo_comprobante": [
        # Caso típico: la palabra (FACTURA/...) seguida de la letra A/B/C
        # (o R de Remito, que no tiene CAE y no es válido como factura).
        r"\b(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO|REMITO)\b[\s\S]{0,15}?\b([ABCR])\b",
        # Facturas pre-impresas de distribuidoras: la letra viene en un
        # recuadro que el OCR a veces lee ANTES que la palabra.
        r"\b([ABCR])\b[\s\S]{0,40}?\b(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO|REMITO)\b",
        # Si no aparece la letra cerca (el recuadro quedó lejos en el
        # diseño, o el OCR no la captó ahí), al menos guardamos la
        # palabra del tipo de comprobante.
        r"\b(FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO|REMITO)\b",
    ],
    "numero_factura": [
        r"(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO|REMITO)\s+[ABCR]\s+([\d\-]{5,20})",
        # Formato "0003-01084580" (punto de venta de 4 dígitos + número
        # de 7/8), tolerando espacios sueltos alrededor del guión (pasa
        # cuando el OCR mete un espacio de más ahí, ej. "0011 -00016528").
        r"\b(\d{4}\s*-\s*\d{7,8})\b",
        # Otros formatos con distinta cantidad de dígitos a cada lado del
        # guión (ej. remitos: "90099-00004158").
        r"\b(\d{3,6}\s*-\s*\d{6,9})\b",
    ],
    "fecha_facturacion": [
        r"Fecha\s*(?:de\s*)?facturaci[oó]n\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        # Formato simple "FECHA:15/09/2026" de facturas pre-impresas.
        r"\bFECHA\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
    ],
    "cuit_emisor": [
        # Acepta ":" o ";" como separador (el OCR a veces confunde uno
        # con el otro).
        r"C\.?U\.?I\.?T\.?\s*[:;]?\s*(\d{2}[-\s]?\d{8}[-\s]?\d{1})",
    ],
    "condicion_iva_emisor": [
        r"(Responsable Inscripto|Monotributista|Exento|Consumidor Final)",
    ],
    "razon_social_cliente": [
        r"Raz[oó]n Social\s*:?\s*(.+)",
        r"Sres\.?\s*:?\s*(.+)",
    ],
    "cuit_cliente": [
        r"CUIT\s*[:;]?\s*(\d{2}[-\s]?\d{8}[-\s]?\d{1})(?!.*C\.U\.I\.T)",
        # Algunos comprobantes de consumidor final ponen un código más
        # corto (no un CUIT completo de 11 dígitos) bajo la misma etiqueta.
        r"CUIT\s*[:;]?\s*(\d{6,11})(?!.*C\.U\.I\.T)",
    ],
    "cliente_codigo": [
        r"Cliente\s*:?\s*(\d{3,})",
    ],
    "condicion_iva_cliente": [
        r"Condici[oó]n(?:\s+de)?\s+IVA\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|MONOTRIBUTO|EXENTO|CONSUMIDOR FINAL)",
        r"CLIENTE\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|MONOTRIBUTO|EXENTO|CONSUMIDOR FINAL)",
        r"IVA\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|MONOTRIBUTO|EXENTO|CONSUMIDOR FINAL)",
        r"IVA\s*:?\s*(CONS\.?\s*FINAL)",
        r"\bRESP\.?\s*(MONOTRIBUTO|INSCRIPTO)\b",
    ],
    "neto": [
        r"\bNeto\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)",
        r"\bNeto\b[^\d\n]{0,15}\d+[,.]\d{1,2}%\s*[^\d\n]{0,10}([\d\.]+[,.]\d{2})",
    ],
    "iva": [
        r"\bI\.?V\.?A\.?\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)",
        r"\bI\.?V\.?A\.?\b[^\d\n]{0,15}\d+[,.]\d{1,2}%\s*[^\d\n]{0,10}([\d\.]+[,.]\d{2})",
    ],
    "subtotal": [r"\bSubTotal\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)"],
    "total": [
        # (?!\s*Neto): "TOTAL NETO 211.362,27" no es el total de la
        # factura, es el neto — sin este lookahead este patrón se llevaba
        # ese importe. Además el campo se resuelve con la ÚLTIMA
        # ocurrencia del documento y no la primera (ver
        # CAMPOS_ULTIMA_OCURRENCIA): el total final viene después de los
        # subtotales por página, "Total Neto", transportes, etc.
        r"\bTotal\b(?!\s*Neto\b)[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*\d)(?!\s*%)",
    ],
    "cae": [
        r"C\.?A\.?E\.?[A]?\s*N[º°ro.]{1,4}\.?\s*:?\s*(\d{10,15})",
        r"N[uú]mero\s*(?:de\s*)?C\.?A\.?E\.?\s*:?\s*(\d{10,15})",
        r"C\.?A\.?E\.?\s*N[º°]\s*:?\s*(\d{10,15})",
        r"C\.?A\.?E\.?\s*:?\s*(\d{10,15})",
    ],
    "vencimiento_cae": [
        # Los patrones ESPECÍFICOS de CAE van primero: muchas facturas
        # tienen un "Vencimiento: dd/mm/aaaa" de PAGO antes del del CAE,
        # y como se usa el primer patrón que matchea, con el genérico
        # adelante se guardaba la fecha equivocada. Se tolera también el
        # formato AFIP "Fecha de Vto. de CAE".
        r"Vto\.?\s*(?:de\s*)?C\.?A\.?E\.?\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        r"VENC\.?\s*(?:de\s*)?C\.?A\.?E\.?\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        r"Vencimiento\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        r"Vencimiento\s*:?\s*(\d{8})\b",
    ],
    "punto_venta": [r"\b(\d{4})-\d{7,8}\b"],
}

# Campos donde importa la ÚLTIMA ocurrencia del patrón en el texto y no
# la primera. El caso concreto es "total": en el texto de una factura
# suele haber varios "Total ..." (Total Neto, totales parciales,
# transporte entre páginas) y el total de verdad es el último.
CAMPOS_ULTIMA_OCURRENCIA = {"total"}


def limpiar_numero(value):
    """Convierte un monto de texto a float, detectando automáticamente si
    usa el formato argentino (punto de miles, coma decimal: '13.175,99')
    o el formato con punto decimal ('143287.43', común en algunos
    sistemas de facturación aunque no sea lo habitual en Argentina).

    Tolera además dos deformaciones típicas del OCR sobre fotos:
    el separador decimal leído como espacio (\"1781 00\"), y la coma de
    miles leída como punto, que deja el número con DOS puntos
    (\"9.722.83\" por \"9.722,83\").
    """
    if not value:
        return None
    v = str(value).strip()

    # El OCR a veces lee el separador decimal como un ESPACIO
    # ("1781 00" en vez de "1781.00"). Si lo que queda despues del
    # espacio son exactamente 2 digitos y el resto son digitos, lo
    # tratamos como el decimal.
    m = re.fullmatch(r"([\d.,]+)\s+(\d{2})", v)
    if m and any(ch.isdigit() for ch in m.group(1)):
        v = f"{m.group(1)}.{m.group(2)}"

    v = v.replace(" ", "")
    if not v:
        return None

    try:
        if "," in v and "." in v:
            if v.rfind(",") > v.rfind("."):
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", "")
        elif "," in v:
            partes = v.split(",")
            if len(partes[-1]) == 2 or len(partes) == 2:
                v = "".join(partes[:-1]) + "." + partes[-1]
            else:
                v = v.replace(",", "")
        elif "." in v:
            # Puede venir con MAS DE UN punto: el OCR confunde la coma de
            # miles con un punto ("9.722.83" por "9.722,83"). En ese caso
            # los puntos de adelante son separadores de miles y el ultimo
            # es el decimal, siempre que deje 2 digitos atras
            # ("255.748.34" -> 255748.34). Si el ultimo grupo no tiene 2
            # digitos, son todos separadores de miles ("1.234.567").
            partes = v.split(".")
            if len(partes[-1]) == 2:
                v = "".join(partes[:-1]) + "." + partes[-1]
            else:
                v = v.replace(".", "")
        return float(v)
    except ValueError:
        return None


def limpiar_cantidad(value):
    """Convierte la CANTIDAD de un item a numero.

    Va aparte de limpiar_numero porque estas facturas escriben las
    cantidades con TRES decimales ("1.000" es 1, no mil; "24.000" es 24),
    al reves de lo que asumiria un parser de montos.

    Ademas la celda suele venir contaminada con texto de la columna de
    al lado, porque la descripcion se desborda sobre la de cantidad
    ("LEI- 24.000", "(X20) 20.000", "100 12.000"). Por eso nos quedamos
    con el ULTIMO token que parezca una cantidad: el sobrante de la
    descripcion siempre queda a la izquierda.
    """
    if value is None:
        return None
    texto = str(value)

    # Tokens con forma de cantidad de 3 decimales: 1.000 / 24,000
    candidatos = re.findall(r"\d+[.,]\d{3}\b", texto)
    if candidatos:
        elegido = candidatos[-1]
        try:
            return float(elegido.replace(",", ".").replace(".", "", elegido.count(".") - 1)
                         if elegido.count(".") > 1 else elegido.replace(",", "."))
        except ValueError:
            return None

    # Si no, el ultimo numero suelto que haya
    sueltos = re.findall(r"\d+(?:[.,]\d+)?", texto)
    if sueltos:
        return limpiar_numero(sueltos[-1])
    return None


def limpiar_sku(value):
    """Se queda con el codigo de articulo, descartando el texto de la
    descripcion que el OCR pego en la misma celda ("312040 AERR",
    "333916 /PILA", "210015 /")."""
    if value is None:
        return None
    m = re.search(r"\d{4,}", str(value))
    return m.group(0) if m else (str(value).strip() or None)


def _normalizar_fecha(valor):
    if not valor:
        return valor
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", valor)
    if m:
        anio, mes, dia = m.groups()
        return f"{dia}/{mes}/{anio}"
    return valor


def _normalizar_condicion_iva(valor):
    if not valor:
        return valor
    v = valor.strip().rstrip(".")
    if re.fullmatch(r"CONS\.?\s*FINAL", v, re.IGNORECASE):
        return "CONSUMIDOR FINAL"
    if re.fullmatch(r"INSCRIPTO", v, re.IGNORECASE):
        return "RESPONSABLE INSCRIPTO"
    return v.upper() if v.upper() == "MONOTRIBUTO" else valor.strip()


def _resolver_colision_emisor_cliente(resultado, texto, campo_emisor, campo_cliente,
                                       patron_valor_tpl, valores_siempre_cliente=None):
    if not resultado.get(campo_emisor) or resultado.get(campo_emisor) != resultado.get(campo_cliente):
        return

    valor = resultado[campo_emisor]

    if valores_siempre_cliente and valor.strip().upper() in valores_siempre_cliente:
        resultado[campo_emisor] = None
        return

    razon_match = None
    for patron in HEADER_PATTERNS["razon_social_cliente"]:
        razon_match = re.search(patron, texto, re.IGNORECASE)
        if razon_match:
            break

    ocurrencia = re.search(patron_valor_tpl.format(valor=re.escape(valor)), texto, re.IGNORECASE)

    if razon_match and ocurrencia:
        if ocurrencia.start() < razon_match.start():
            resultado[campo_cliente] = None
        else:
            resultado[campo_emisor] = None
    elif ocurrencia and re.search(r"\bCONSUMIDOR\s+FINAL\b", texto, re.IGNORECASE):
        resultado[campo_cliente] = None
    else:
        # Cuando no hay contexto para decidir, antes se anulaban los DOS
        # campos y se perdia un valor que si se habia leido bien. El del
        # emisor no se exporta al Excel, asi que lo barato es anular solo
        # ese y conservar el del cliente (que es la columna que se
        # escribe).
        resultado[campo_emisor] = None


def extraer_cabecera(texto: str) -> dict:
    resultado = {}
    for campo, patrones in HEADER_PATTERNS.items():
        if isinstance(patrones, str):
            patrones = [patrones]

        valor = None
        for patron in patrones:
            if campo in CAMPOS_ULTIMA_OCURRENCIA:
                matches = list(re.finditer(patron, texto, re.IGNORECASE))
                m = matches[-1] if matches else None
            else:
                m = re.search(patron, texto, re.IGNORECASE)
            if m:
                valor = m.group(m.lastindex).strip()
                break
        resultado[campo] = valor

    # CUIT por POSICIÓN: los patrones de cuit_emisor y cuit_cliente son
    # casi iguales, así que con re.search los dos capturaban la MISMA
    # (primera) ocurrencia y después había que adivinar de quién era
    # (y en el peor caso se anulaban los dos, perdiendo un CUIT bien
    # leído). En las facturas argentinas el CUIT del emisor aparece
    # siempre antes que el del cliente, así que: primera ocurrencia
    # completa = emisor, y la siguiente con un valor DISTINTO = cliente.
    patron_cuit = r"C\.?U\.?I\.?T\.?\s*[:;]?\s*(\d{2}[-\s]?\d{8}[-\s]?\d{1})"
    cuits_vistos = []
    digitos_vistos = set()
    for m in re.finditer(patron_cuit, texto, re.IGNORECASE):
        valor_cuit = m.group(1)
        solo_digitos = re.sub(r"\D", "", valor_cuit)
        if solo_digitos not in digitos_vistos:
            digitos_vistos.add(solo_digitos)
            cuits_vistos.append(valor_cuit)
    if cuits_vistos:
        resultado["cuit_emisor"] = cuits_vistos[0]
        if len(cuits_vistos) > 1:
            resultado["cuit_cliente"] = cuits_vistos[1]
        elif resultado.get("cuit_cliente") and \
                re.sub(r"\D", "", resultado["cuit_cliente"]) == re.sub(r"\D", "", cuits_vistos[0]):
            # Hay un solo CUIT en toda la factura: es el del emisor. (Si
            # el patrón corto de cuit_cliente —códigos de 6 a 11 dígitos—
            # capturó otra cosa distinta, eso se respeta.)
            resultado["cuit_cliente"] = None

    if resultado.get("numero_factura"):
        resultado["numero_factura"] = re.sub(r"\s+", "", resultado["numero_factura"])

    for campo_monto in ("neto", "iva", "subtotal", "total"):
        resultado[campo_monto] = limpiar_numero(resultado.get(campo_monto))

    for campo_fecha in ("fecha_facturacion", "vencimiento_cae"):
        resultado[campo_fecha] = _normalizar_fecha(resultado.get(campo_fecha))
    resultado["condicion_iva_cliente"] = _normalizar_condicion_iva(
        resultado.get("condicion_iva_cliente")
    )

    if not resultado.get("vencimiento_cae") and resultado.get("cae"):
        m = re.search(re.escape(resultado["cae"]) + r"[\s\S]{0,60}?(\d{8})\b", texto)
        if m:
            resultado["vencimiento_cae"] = _normalizar_fecha(m.group(1))

    # Factura A: emisor y cliente son AMBOS Responsable Inscripto por
    # definición de AFIP, así que que las dos condiciones coincidan no
    # es una colisión a resolver sino el caso normal.
    es_factura_a_ri = (
        resultado.get("tipo_comprobante") == "A"
        and resultado.get("condicion_iva_cliente")
        and "INSCRIPTO" in resultado["condicion_iva_cliente"].upper()
    )
    if not es_factura_a_ri:
        _resolver_colision_emisor_cliente(
            resultado, texto, "condicion_iva_emisor", "condicion_iva_cliente",
            r"IVA\s*:?\s*{valor}",
            valores_siempre_cliente={"CONSUMIDOR FINAL"},
        )

    return resultado


# ---------------------------------------------------------------------------
# 2. TABLA DE ÍTEMS — pdfplumber extract_table + fallback heurístico
# ---------------------------------------------------------------------------

# Encabezados típicos que suelen aparecer en la fila de título de la tabla.
# También los usa ocr_items.py para detectar la fila de encabezado en
# fotos (a partir de palabras sueltas reconocidas por OCR).
ITEM_HEADER_HINTS = ["sku", "codigo", "código", "descripcion", "descripción",
                     "cantidad", "cant", "unitario", "precio", "subtotal", "iva",
                     "importe", "neto", "dto",
                     # Variantes de OCR que aparecen en imágenes binarizadas
                     # (la letra se deforma un poco por el umbral adaptativo,
                     # y Tesseract lee caracteres parecidos pero distintos).
                     "cob1go", "cobigo", "cod1go", "codlgo",
                     "akticulo", "articulo", "artículo",
                     "unit", "desc", "meto"]

# Subconjunto de hints que solo aparecen en el encabezado de la tabla de
# ÍTEMS y nunca en la fila de totales del pie. Sin esta distinción, el
# encabezado de la tabla de TOTALES ("TOTAL NETO | IVA | SUBTOTAL |
# TOTAL") junta 2+ hits de los hints genéricos (neto, iva, subtotal...),
# se lo trataba como encabezado de ítems, y la fila de valores de abajo
# entraba al Excel como un ítem fantasma con solo montos.
ITEM_HEADER_HINTS_ESPECIFICOS = [
    "sku", "codigo", "código", "cob1go", "cobigo", "cod1go", "codlgo",
    "descripcion", "descripción", "desc",
    "articulo", "artículo", "akticulo",
    "cantidad", "cant", "unitario", "unit",
]


def _fila_es_encabezado(fila: list) -> bool:
    texto_fila = " ".join([c or "" for c in fila]).lower()
    hits = sum(1 for hint in ITEM_HEADER_HINTS if hint in texto_fila)
    tiene_especifico = any(h in texto_fila for h in ITEM_HEADER_HINTS_ESPECIFICOS)
    return hits >= 2 and tiene_especifico


def _fila_es_totales(fila: list) -> bool:
    texto_fila = " ".join([c or "" for c in fila]).lower()
    return "total" in texto_fila and not any(c and c.strip().isdigit() for c in fila[:1])


def ajustar_columna_desc(nombres: list, crudos: list) -> list:
    """Resuelve la ambigüedad de la abreviatura "DESC"/"DESC.": puede
    abreviar DESCUENTO o DESCRIPCIÓN. _normalizar_header la mapea a
    descuento, que es lo correcto cuando la tabla además tiene su propia
    columna de descripción; pero si NO hay ninguna columna de
    descripción en el encabezado, la abreviatura era de la descripción,
    y sin este ajuste todos los textos de los ítems caían en la columna
    "descuento" del Excel.

    `nombres` son los encabezados ya normalizados y `crudos` los textos
    originales de esas mismas celdas (en el mismo orden), para tocar
    solo las columnas que vinieron de la abreviatura ambigua y no un
    "Descuento" escrito entero."""
    if "descripcion" in nombres:
        return nombres
    ajustados = list(nombres)
    for i, (nombre, crudo) in enumerate(zip(ajustados, crudos)):
        if nombre == "descuento" and crudo and re.fullmatch(
                r"desc\.?", str(crudo).strip(), re.IGNORECASE):
            ajustados[i] = "descripcion"
    return ajustados


def extraer_items(pdf_path: str) -> list:
    items = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tablas = page.extract_tables()
            for tabla in tablas:
                if not tabla or len(tabla) < 2:
                    continue

                header_idx = None
                for i, fila in enumerate(tabla):
                    if _fila_es_encabezado(fila):
                        header_idx = i
                        break
                if header_idx is None:
                    continue

                headers_crudos = tabla[header_idx]
                headers = [(_normalizar_header(c)) for c in headers_crudos]
                headers = ajustar_columna_desc(headers, headers_crudos)

                for fila in tabla[header_idx + 1:]:
                    if _fila_es_totales(fila):
                        break
                    if not any(fila):
                        continue
                    fila_dict = {}
                    for col_name, valor in zip(headers, fila):
                        if col_name:
                            fila_dict[col_name] = (valor or "").strip()
                    if fila_dict:
                        items.append(fila_dict)
    return items


TOTAL_HEADER_HINTS = ["neto", "interno", "iva", "subtotal", "total", "res.", "perc"]


def extraer_totales_tabla(pdf_path: str) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for tabla in page.extract_tables():
                for i, fila in enumerate(tabla[:-1]):
                    celdas = [(c or "").strip().lower() for c in fila]
                    hits = sum(1 for h in TOTAL_HEADER_HINTS if any(h in c for c in celdas))
                    tiene_neto = any("neto" in c for c in celdas)
                    tiene_total = any(c == "total" for c in celdas)
                    if hits >= 3 and tiene_neto and tiene_total:
                        valores = tabla[i + 1]
                        resultado = {}
                        for header, valor in zip(fila, valores):
                            key = _normalizar_header(header)
                            if key:
                                resultado[key] = limpiar_numero((valor or "").strip())
                        return resultado
    return {}


def _normalizar_header(nombre: str):
    """Mapea el texto de un encabezado de columna a su nombre canónico.

    Además de las variantes "normales" (ej. "Precio" y "Unitario" para
    precio_unitario, "Importe" para subtotal), incluye variantes de OCR
    que aparecen cuando la imagen pasa por binarización adaptativa: el
    umbral cambia ligeramente la forma de las letras, y Tesseract lee
    caracteres parecidos pero distintos (ej. "cob1go" en vez de
    "codigo", "akticulo" en vez de "articulo", "meto" en vez de "neto").

    También maneja el caso del encabezado compuesto "DESCRIPCION DEL
    ARTICULO": como ocr_items.py trata cada PALABRA del encabezado como
    una columna separada, "del" y "articulo" (y sus variantes OCR) se
    mapean a "descripcion" para que las palabras de datos que caigan en
    esos rangos se concatenen en el campo correcto.

    OJO con "desc": acá se mapea a descuento, pero es ambiguo (también
    abrevia "descripción"). La desambiguación por contexto la hace
    ajustar_columna_desc, mirando el encabezado completo.
    """
    if not nombre:
        return None
    n = nombre.strip().lower().replace(".", "")
    mapa = {
        # --- Columna SKU / CODIGO ---
        "sku": "sku", "codigo": "sku", "código": "sku",
        # Variantes OCR de "CODIGO" (binarización deforma la D/I/O)
        "cob1go": "sku", "cobigo": "sku", "cod1go": "sku", "codlgo": "sku",

        # --- Columna DESCRIPCION ---
        "descripcion": "descripcion", "descripción": "descripcion",
        # "DESCRIPCION DEL ARTICULO" partido en 3 palabras: las 3 van
        # al mismo campo para que ocr_items las concatene.
        "del": "descripcion",
        "articulo": "descripcion", "artículo": "descripcion",
        "akticulo": "descripcion",  # variante OCR de "ARTICULO"

        # --- Columna CANTIDAD ---
        "cantidad": "cantidad", "cant": "cantidad",

        # --- Columna PRECIO UNITARIO ---
        "unitario": "precio_unitario", "precio unitario": "precio_unitario",
        "unit": "precio_unitario", "unit,": "precio_unitario",
        "precio": "precio_unitario", "precio neto": "precio_unitario",
        # "P. UNIT" partido: la "P" sola va a precio_unitario
        "p": "precio_unitario",

        # --- Columna DESCUENTO ---
        "descuento": "descuento", "dto": "descuento", "desc": "descuento",

        # --- Columna NETO ---
        "neto": "neto",
        "meto": "neto",  # variante OCR de "NETO"
        # "PR. NETO" partido: "PR" sola va a neto (el valor real
        # viene en la palabra siguiente que cae en la columna NETO)
        "pr": "neto", "pr:": "neto",

        # --- Columnas de impuestos ---
        "interno": "interno", "internos": "interno",
        "iva": "iva",

        # --- Columna SUBTOTAL / IMPORTE ---
        "subtotal": "subtotal", "importe": "subtotal", "total neto": "subtotal",
    }
    return mapa.get(n, n.replace(" ", "_"))


# ---------------------------------------------------------------------------
# 3. Función principal
# ---------------------------------------------------------------------------

def procesar_factura(pdf_path: str) -> dict:
    with pdfplumber.open(pdf_path) as pdf:
        texto_completo = "\n".join(page.extract_text() or "" for page in pdf.pages)

    cabecera = extraer_cabecera(texto_completo)
    items = extraer_items(pdf_path)

    totales_tabla = extraer_totales_tabla(pdf_path)
    for campo in ("neto", "iva", "subtotal", "total"):
        if totales_tabla.get(campo) is not None:
            cabecera[campo] = totales_tabla[campo]

    return {
        "cabecera": cabecera,
        "items": items,
        "texto_crudo": texto_completo,
    }


# ---------------------------------------------------------------------------
# 4. Validación aritmética de los montos extraídos
# ---------------------------------------------------------------------------

# Tolerancia absoluta por redondeos de centavos entre campos.
_TOLERANCIA = 0.05


def _formatear_monto(v) -> str:
    """Formato argentino para mostrar montos en los avisos: $1.234,56."""
    texto = f"{v:,.2f}"
    return "$" + texto.replace(",", "@").replace(".", ",").replace("@", ".")


def validar_montos(resultado: dict) -> list:
    """Chequea que los montos extraídos cierren aritméticamente entre sí
    y devuelve una lista de advertencias (strings) para mostrarle al
    usuario; vacía si todo cuadra o si faltan datos para comparar.

    La idea es convertir errores de lectura SILENCIOSOS (un "total" que
    en realidad agarró otro número de la factura, un ítem que no se
    leyó) en avisos visibles, sin bloquear la carga: la fila igual se
    escribe al Excel, pero el usuario sabe que esa factura hay que
    revisarla.

    Los chequeos son deliberadamente conservadores para no "llorar
    lobo": se avisa cuando la cuenta es imposible o muy improbable, no
    cuando simplemente no se puede verificar. Por ejemplo, un total algo
    mayor que neto + IVA puede ser legítimo (percepciones, impuestos
    internos, que no se extraen como campos propios), así que ahí solo
    se avisa si la diferencia es demasiado grande; un total MENOR que
    neto + IVA, en cambio, no puede pasar nunca.
    """
    advertencias = []
    cab = resultado.get("cabecera") or {}
    neto = cab.get("neto")
    iva = cab.get("iva")
    total = cab.get("total")

    # --- neto + IVA contra el total ---
    if neto is not None and iva is not None and total is not None:
        esperado = round(neto + iva, 2)
        diferencia = round(total - esperado, 2)
        if diferencia < -_TOLERANCIA:
            advertencias.append(
                f"los montos no cuadran: el total ({_formatear_monto(total)}) es "
                f"MENOR que neto + IVA ({_formatear_monto(esperado)}), lo cual es "
                "imposible — alguno de los tres se leyó mal"
            )
        elif neto > 0 and diferencia > max(_TOLERANCIA, neto * 0.15):
            # Una diferencia positiva chica es normal (percepciones,
            # impuestos internos), pero más del 15% del neto ya no se
            # explica con eso.
            advertencias.append(
                f"el total ({_formatear_monto(total)}) es bastante más grande que "
                f"neto + IVA ({_formatear_monto(esperado)}); si la factura no tiene "
                "percepciones o impuestos internos altos, algún monto se leyó mal"
            )

    # --- alícuota implícita del IVA ---
    if neto is not None and iva is not None and neto > 0:
        if iva / neto > 0.28:
            # La alícuota más alta que existe es 27%; con ítems exentos o
            # no gravados la efectiva solo puede BAJAR, nunca superar eso.
            advertencias.append(
                f"el IVA leído ({_formatear_monto(iva)}) es más del 28% del neto "
                f"({_formatear_monto(neto)}), y la alícuota máxima es 27% — "
                "probablemente uno de los dos campos se leyó mal"
            )

    # --- suma de ítems contra la cabecera ---
    items = resultado.get("items") or []
    if items:
        referencias = [v for v in (neto, cab.get("subtotal"), total) if v is not None]
        if referencias:
            # Se prueba primero la columna "neto" de los ítems y después
            # "subtotal" (que según el proveedor puede ser el importe de
            # línea con o sin IVA); por eso la suma se compara contra
            # TODAS las referencias de la cabecera y alcanza con que
            # coincida con alguna.
            for col in ("neto", "subtotal"):
                valores = [limpiar_numero(item.get(col)) for item in items]
                # Solo se compara si TODAS las filas tienen esa columna
                # legible: con filas incompletas la suma da corta seguro
                # y el aviso sería ruido (de filas/campos faltantes ya
                # avisan los otros mensajes de la app).
                if all(v is not None for v in valores):
                    suma = round(sum(valores), 2)
                    tolerancia = max(_TOLERANCIA, 0.02 * len(items))
                    if not any(abs(suma - ref) <= tolerancia for ref in referencias):
                        advertencias.append(
                            f"la suma de los importes de los ítems ({_formatear_monto(suma)}) "
                            "no coincide con el neto ni con el total de la cabecera — "
                            "puede faltar un ítem o haber un monto mal leído"
                        )
                    break

    return advertencias
