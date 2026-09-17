"""
Convierte fotos/imágenes de facturas (jpg, png, etc.) en un PDF con una
capa de texto OCR superpuesta, para poder reusar TAL CUAL el mismo
pipeline de extracción que ya existe en parser.py para PDFs digitales.

Requiere tener instalado el BINARIO de Tesseract OCR en el sistema
operativo (no alcanza con el paquete de Python `pytesseract`, que es
solo un wrapper que llama a ese binario), Y el paquete de idioma
español ('spa'). Sin el paquete de español, Tesseract reconoce el texto
en inglés por defecto, lo cual arruina bastante la lectura de facturas
en español (tildes, formato de números, etc.). Ver instrucciones en el
README, sección "OCR para fotos/imágenes".
"""
import pytesseract
from PIL import Image, ImageOps

# Extensiones de imagen que la app acepta además de .pdf
EXTENSIONES_IMAGEN = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")

# Ancho mínimo (en píxeles) que le pedimos a la imagen antes de mandarla a
# OCR. Fotos de celular sacadas de lejos, o comprimidas por WhatsApp,
# suelen quedar con poca resolución efectiva sobre las letras, y eso
# arruina el reconocimiento.
ANCHO_MINIMO_OCR = 2200


def es_imagen(nombre_archivo: str) -> bool:
    return nombre_archivo.lower().endswith(EXTENSIONES_IMAGEN)


def _hay_idioma_espanol() -> bool:
    try:
        return "spa" in pytesseract.get_languages(config="")
    except Exception:
        # Versiones viejas de tesseract pueden no soportar este chequeo;
        # asumimos que no está y dejamos que el try/except de más abajo
        # decida en tiempo real si "spa" funciona o no.
        return False


def _preprocesar(imagen: Image.Image) -> Image.Image:
    """Mejoras básicas para fotos sacadas con el celular:

    - respeta la orientación real (los celulares guardan la rotación
      como metadato EXIF, no rotando los píxeles)
    - pasa a escala de grises
    - sube el contraste automáticamente
    - agranda la imagen si quedó chica, para que el OCR tenga más
      píxeles por letra para trabajar
    """
    imagen = ImageOps.exif_transpose(imagen)
    imagen = imagen.convert("L")
    imagen = ImageOps.autocontrast(imagen)

    if imagen.width < ANCHO_MINIMO_OCR:
        factor = ANCHO_MINIMO_OCR / imagen.width
        nuevo_alto = int(imagen.height * factor)
        imagen = imagen.resize((ANCHO_MINIMO_OCR, nuevo_alto), Image.LANCZOS)

    return imagen


def convertir_imagen_a_pdf_ocr(ruta_imagen: str, ruta_pdf_salida: str, ruta_texto_debug: str = None):
    """
    Toma la ruta de una imagen, le corre OCR y genera en `ruta_pdf_salida`
    un PDF con la imagen + el texto reconocido superpuesto (un PDF
    "buscable"). Ese PDF se pasa directo a `parser.procesar_factura()`.

    Si se pasa `ruta_texto_debug`, también guarda ahí el texto plano que
    reconoció el OCR, útil para diagnosticar cuando fallan campos.

    Devuelve una tupla (ruta_pdf, advertencia). `advertencia` es None si
    todo salió bien, o un mensaje para mostrarle al usuario si el
    reconocimiento corrió en inglés por faltar el paquete de idioma
    español (lo cual explica que fallen muchos/todos los campos).
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
        imagen = _preprocesar(imagen_original)

        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                imagen, extension="pdf", lang=idioma or "eng"
            )
            texto = pytesseract.image_to_string(imagen, lang=idioma or "eng")
        except pytesseract.TesseractError:
            # Último recurso: dejar que tesseract use su idioma por defecto.
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(imagen, extension="pdf")
            texto = pytesseract.image_to_string(imagen)
            if advertencia is None:
                advertencia = (
                    "No se pudo usar el idioma español para el OCR (revisa "
                    "que 'spa' esté bien instalado); se usó el idioma por "
                    "defecto de Tesseract."
                )

    with open(ruta_pdf_salida, "wb") as f:
        f.write(pdf_bytes)

    if ruta_texto_debug:
        with open(ruta_texto_debug, "w", encoding="utf-8") as f:
            f.write(texto)

    return ruta_pdf_salida, advertencia
