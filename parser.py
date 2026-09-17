"""
Parser de facturas argentinas (AFIP) a partir de PDFs con capa de texto.
No usa LLM: regex para cabecera + detección de tablas de pdfplumber para ítems.

Cada campo de cabecera puede tener una o más expresiones regulares
alternativas (se prueban en orden, se usa la primera que matchee), porque
distintos proveedores/sistemas de facturación usan etiquetas distintas
para el mismo dato (por ej. "Razón Social:" vs "Sres:").
"""
import re
import pdfplumber


# ---------------------------------------------------------------------------
# 1. CABECERA — regex sobre el texto plano
# ---------------------------------------------------------------------------

HEADER_PATTERNS = {
    "tipo_comprobante": [
        # Caso típico: la palabra (FACTURA/...) seguida de la letra A/B/C.
        r"\b(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO)\b[\s\S]{0,15}?\b([ABC])\b",
        # Facturas pre-impresas de distribuidoras: la letra viene en un
        # recuadro que el OCR a veces lee ANTES que la palabra.
        r"\b([ABC])\b[\s\S]{0,40}?\b(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO)\b",
    ],
    "numero_factura": [
        r"(?:FACTURA|NOTA DE CR[EÉ]DITO|NOTA DE D[EÉ]BITO|RECIBO)\s+[ABC]\s+([\d\-]{5,20})",
        # Formato "0003-01084580" solo (punto de venta + número), sin el
        # tipo pegado justo adelante.
        r"\b(\d{4}-\d{7,8})\b",
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
        r"IVA\s*:?\s*(RESPONSABLE INSCRIPTO|MONOTRIBUTISTA|EXENTO|CONSUMIDOR FINAL)",
        r"IVA\s*:?\s*(CONS\.?\s*FINAL)",
    ],
    "neto": [r"\bNeto\b[^\d\n]{0,10}([\d\.]+,\d{2})"],
    "iva": [r"\bI\.?V\.?A\.?\b[^\d\n]{0,10}([\d\.]+,\d{2})"],
    "subtotal": [r"\bSubTotal\b[^\d\n]{0,10}([\d\.]+,\d{2})"],
    "total": [r"\bTotal\b[^\d\n]{0,10}([\d\.]+,\d{2})(?!\s*\d)"],
    "cae": [
        r"CAE[A]?\s*Nro\.?\s*:?\s*(\d{10,15})",
        r"N[uú]mero\s*(?:de\s*)?CAE\s*:?\s*(\d{10,15})",
    ],
    "vencimiento_cae": [
        r"Vencimiento\s*:?\s*(\d{2}[/-]\d{2}[/-]\d{4})",
        # Formato AAAAMMDD sin separadores ("20260925").
        r"Vencimiento\s*:?\s*(\d{8})\b",
    ],
    "punto_venta": [r"\b(\d{4})-\d{7,8}\b"],
}


def _clean_number(value: str):
    """Convierte '13.175,99' -> 13175.99"""
    if not value:
        return None
    try:
        return float(value.replace(".", "").replace(",", "."))
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
        resultado[campo_monto] = _clean_number(resultado.get(campo_monto))

    # Post-proceso: normalizar fechas y condición de IVA del cliente
    for campo_fecha in ("fecha_facturacion", "vencimiento_cae"):
        resultado[campo_fecha] = _normalizar_fecha(resultado.get(campo_fecha))
    resultado["condicion_iva_cliente"] = _normalizar_condicion_iva(
        resultado.get("condicion_iva_cliente")
    )

    return resultado


# ---------------------------------------------------------------------------
# 2. TABLA DE ÍTEMS — pdfplumber extract_table + fallback heurístico
# ---------------------------------------------------------------------------

# Encabezados típicos que suelen aparecer en la fila de título de la tabla
ITEM_HEADER_HINTS = ["sku", "codigo", "código", "descripcion", "descripción",
                     "cantidad", "cant", "unitario", "precio", "subtotal", "iva"]


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
                                resultado[key] = _clean_number((valor or "").strip())
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
        "descuento": "descuento",
        "neto": "neto",
        "interno": "interno", "internos": "interno",
        "iva": "iva",
        "subtotal": "subtotal",
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
