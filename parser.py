"""
Parser de facturas argentinas (AFIP) a partir de PDFs con capa de texto.
No usa LLM: regex para cabecera + detección de tablas de pdfplumber para ítems.

Cada campo de cabecera puede tener una o más expresiones regulares
alternativas (se prueban en orden, se usa la primera que matchee), porque
distintos proveedores/sistemas de facturación usan etiquetas distintas
para el mismo dato (por ej. "Razón Social:" vs "Sres:", o "CAE Nro" vs
"CAE N°:").
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
    ],
    "numero_factura": [
        r"(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO|REMITO)\s+[ABCR]\s+([\d\-]{5,20})",
        # Formato "0003-01084580" (punto de venta de 4 dígitos + número
        # de 7/8), sin el tipo pegado justo adelante.
        r"\b(\d{4}-\d{7,8})\b",
        # Otros formatos con distinta cantidad de dígitos a cada lado del
        # guión (ej. remitos: "90099-00004158").
        r"\b(\d{3,6}-\d{6,9})\b",
    ],
    "fecha_facturacion": [
        r"Fecha\s*(?:de\s*)?facturaci[oó]n\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        # Formato simple "FECHA:15/09/2026" de facturas pre-impresas.
        r"\bFECHA\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
    ],
    "cuit_emisor": [
        r"C\.?U\.?I\.?T\.?\s*:?\s*(\d{2}[-\s]?\d{8}[-\s]?\d{1})",
    ],
    "condicion_iva_emisor": [
        r"(Responsable Inscripto|Monotributista|Exento|Consumidor Final)",
    ],
    "razon_social_cliente": [
        r"Raz[oó]n Social\s*:?\s*(.+)",
        r"Sres\.?\s*:?\s*(.+)",
    ],
    "cuit_cliente": [
        r"CUIT\s*:?\s*(\d{2}[-\s]?\d{8}[-\s]?\d{1})(?!.*C\.U\.I\.T)",
        # Algunos comprobantes de consumidor final ponen un código más
        # corto (no un CUIT completo de 11 dígitos) bajo la misma etiqueta.
        r"CUIT\s*:?\s*(\d{6,11})(?!.*C\.U\.I\.T)",
    ],
    "cliente_codigo": [
        r"Cliente\s*:?\s*(\d{3,})",
    ],
    "condicion_iva_cliente": [
        # Más específico primero: "Condicion IVA: Monotributo" (común en
        # facturas donde también aparece la condición del EMISOR con la
        # etiqueta genérica "IVA", lo que confundiría al patrón genérico
        # de abajo si se probara primero).
        r"Condici[oó]n(?:\s+de)?\s+IVA\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|MONOTRIBUTO|EXENTO|CONSUMIDOR FINAL)",
        r"IVA\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|MONOTRIBUTO|EXENTO|CONSUMIDOR FINAL)",
        r"IVA\s*:?\s*(CONS\.?\s*FINAL)",
    ],
    "neto": [
        r"\bNeto\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)",
        # Formato "Neto 21.00% $143287.43": hay que saltar la alícuota
        # (que también tiene forma de monto decimal) antes de llegar al
        # importe real.
        r"\bNeto\b[^\d\n]{0,15}\d+[,.]\d{1,2}%\s*[^\d\n]{0,10}([\d\.]+[,.]\d{2})",
    ],
    "iva": [
        r"\bI\.?V\.?A\.?\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)",
        r"\bI\.?V\.?A\.?\b[^\d\n]{0,15}\d+[,.]\d{1,2}%\s*[^\d\n]{0,10}([\d\.]+[,.]\d{2})",
    ],
    "subtotal": [r"\bSubTotal\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*%)"],
    "total": [r"\bTotal\b[^\d\n]{0,10}([\d\.]+[,.]\d{2})(?!\s*\d)(?!\s*%)"],
    "cae": [
        r"CAE[A]?\s*N[°ºro.]{1,4}\.?\s*:?\s*(\d{10,15})",
        r"N[uú]mero\s*(?:de\s*)?CAE\s*:?\s*(\d{10,15})",
        # "CAE N°: 86373284712148" (con símbolo de grado en vez de "Nro").
        r"CAE\s*N[°º]\s*:?\s*(\d{10,15})",
    ],
    "vencimiento_cae": [
        r"Vencimiento\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        # Formato AAAAMMDD sin separadores ("20260925").
        r"Vencimiento\s*:?\s*(\d{8})\b",
        # "Vto CAE: 25/09/2026" (abreviatura común en facturas prolijas).
        r"Vto\.?\s*CAE\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
    ],
    "punto_venta": [r"\b(\d{4})-\d{7,8}\b"],
}


def limpiar_numero(value: str):
    """Convierte un monto de texto a float, detectando automáticamente si
    usa el formato argentino (punto de miles, coma decimal: '13.175,99')
    o el formato con punto decimal ('143287.43', común en algunos
    sistemas de facturación aunque no sea lo habitual en Argentina).
    """
    if not value:
        return None
    v = value.strip()
    try:
        if "," in v and "." in v:
            # Tiene los dos separadores: el que aparece último es el
            # decimal (ej. '13.175,99' -> coma decimal; '13,175.99' ->
            # punto decimal).
            if v.rfind(",") > v.rfind("."):
                v = v.replace(".", "").replace(",", ".")
            else:
                v = v.replace(",", "")
        elif "," in v:
            # Solo coma: la tomamos como decimal (formato argentino).
            v = v.replace(".", "").replace(",", ".")
        elif "." in v:
            # Solo punto: si termina en exactamente 2 dígitos, es decimal
            # (ej. '143287.43'); si no, probablemente sea separador de
            # miles (ej. '1.234' sin centavos).
            partes = v.split(".")
            if len(partes[-1]) != 2:
                v = v.replace(".", "")
        return float(v)
    except ValueError:
        return None


def _normalizar_fecha(valor):
    """Si la fecha vino en formato AAAAMMDD (sin separadores), la pasa a
    DD/MM/AAAA para que quede consistente en el Excel con el resto."""
    if not valor:
        return valor
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", valor)
    if m:
        anio, mes, dia = m.groups()
        return f"{dia}/{mes}/{anio}"
    return valor


def _normalizar_condicion_iva(valor):
    """Uniforma abreviaturas tipo 'CONS. FINAL' / 'CONS FINAL' a
    'CONSUMIDOR FINAL', para que no queden como valores distintos en el
    Excel según el proveedor."""
    if not valor:
        return valor
    v = valor.strip().rstrip(".")
    if re.fullmatch(r"CONS\.?\s*FINAL", v, re.IGNORECASE):
        return "CONSUMIDOR FINAL"
    return valor.strip()


def extraer_cabecera(texto: str) -> dict:
    resultado = {}
    for campo, patrones in HEADER_PATTERNS.items():
        if isinstance(patrones, str):
            patrones = [patrones]

        valor = None
        for patron in patrones:
            m = re.search(patron, texto, re.IGNORECASE)
            if m:
                valor = m.group(m.lastindex).strip()
                break
        resultado[campo] = valor

    # Post-proceso: montos a float
    for campo_monto in ("neto", "iva", "subtotal", "total"):
        resultado[campo_monto] = limpiar_numero(resultado.get(campo_monto))

    # Post-proceso: normalizar fechas y condición de IVA del cliente
    for campo_fecha in ("fecha_facturacion", "vencimiento_cae"):
        resultado[campo_fecha] = _normalizar_fecha(resultado.get(campo_fecha))
    resultado["condicion_iva_cliente"] = _normalizar_condicion_iva(
        resultado.get("condicion_iva_cliente")
    )

    # Fallback para vencimiento_cae: en fotos, el OCR a veces parte la
    # palabra "Vencimiento" en dos (ej. "Vencimien" ... "to: 20260925" en
    # puntos distintos del texto), y ningún patrón de arriba matchea.
    # Como último recurso, buscamos una fecha de 8 dígitos pegada cerca
    # del número de CAE ya encontrado (suelen ir juntos en el documento).
    if not resultado.get("vencimiento_cae") and resultado.get("cae"):
        m = re.search(re.escape(resultado["cae"]) + r"[\s\S]{0,60}?(\d{8})\b", texto)
        if m:
            resultado["vencimiento_cae"] = _normalizar_fecha(m.group(1))

    # Salvavidas: si emisor y cliente terminaron con el MISMO CUIT, es
    # casi seguro un choque (el CUIT real de una de las dos partes no se
    # pudo leer del texto, y el patrón genérico terminó usando el único
    # "CUIT:" que sí encontró para ambos campos). Dos partes de una
    # factura prácticamente nunca comparten CUIT, así que preferimos
    # anular los dos antes que mostrar uno con confianza estando mal.
    if (
        resultado.get("cuit_emisor")
        and resultado.get("cuit_emisor") == resultado.get("cuit_cliente")
    ):
        resultado["cuit_emisor"] = None
        resultado["cuit_cliente"] = None

    return resultado


# ---------------------------------------------------------------------------
# 2. TABLA DE ÍTEMS — pdfplumber extract_table + fallback heurístico
# ---------------------------------------------------------------------------

# Encabezados típicos que suelen aparecer en la fila de título de la tabla.
# También los usa ocr_items.py para detectar la fila de encabezado en
# fotos (a partir de palabras sueltas reconocidas por OCR).
ITEM_HEADER_HINTS = ["sku", "codigo", "código", "descripcion", "descripción",
                     "cantidad", "cant", "unitario", "precio", "subtotal", "iva",
                     "importe"]


def _fila_es_encabezado(fila: list) -> bool:
    texto_fila = " ".join([c or "" for c in fila]).lower()
    hits = sum(1 for hint in ITEM_HEADER_HINTS if hint in texto_fila)
    return hits >= 2


def _fila_es_totales(fila: list) -> bool:
    texto_fila = " ".join([c or "" for c in fila]).lower()
    return "total" in texto_fila and not any(c and c.strip().isdigit() for c in fila[:1])


def extraer_items(pdf_path: str) -> list:
    items = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            tablas = page.extract_tables()
            for tabla in tablas:
                if not tabla or len(tabla) < 2:
                    continue

                # Buscar la fila de encabezado dentro de la tabla detectada
                header_idx = None
                for i, fila in enumerate(tabla):
                    if _fila_es_encabezado(fila):
                        header_idx = i
                        break
                if header_idx is None:
                    continue  # esta tabla no parece ser la de ítems

                headers = [(_normalizar_header(c)) for c in tabla[header_idx]]

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
    """Busca, entre todas las tablas del PDF, una fila de encabezado tipo
    'Neto | Interno | I.V.A. | SubTotal | ... | Total' seguida de su fila
    de valores, y devuelve un dict {campo: monto}."""
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
    if not nombre:
        return None
    n = nombre.strip().lower().replace(".", "")
    mapa = {
        "sku": "sku", "codigo": "sku", "código": "sku",
        "descripcion": "descripcion", "descripción": "descripcion",
        "cantidad": "cantidad", "cant": "cantidad", "cant.": "cantidad",
        "unitario": "precio_unitario", "precio unitario": "precio_unitario",
        "unit": "precio_unitario",
        # Facturas pre-impresas de distribuidoras suelen tener una sola
        # columna "Precio" (sin la palabra "unitario") y "Dto" en vez de
        # "Descuento"; otras usan "Importe" para el total de la línea en
        # vez de "Subtotal".
        "precio": "precio_unitario",
        "descuento": "descuento", "dto": "descuento",
        "neto": "neto",
        "interno": "interno", "internos": "interno",
        "iva": "iva",
        "subtotal": "subtotal", "importe": "subtotal",
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

    # Los totales son más confiables desde la tabla que desde el texto corrido;
    # solo usamos el valor del regex si la tabla no dio nada.
    totales_tabla = extraer_totales_tabla(pdf_path)
    for campo in ("neto", "iva", "subtotal", "total"):
        if totales_tabla.get(campo) is not None:
            cabecera[campo] = totales_tabla[campo]

    return {
        "cabecera": cabecera,
        "items": items,
        "texto_crudo": texto_completo,  # útil para debug si algo no matcheó
    }
