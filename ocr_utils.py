"""
Convierte fotos/imágenes de facturas (jpg, png, etc.) en un PDF con una
capa de texto OCR superpuesta, para poder reusar TAL CUAL el mismo
pipeline de extracción que ya existe en parser.py para PDFs digitales.

Requiere tener instalado el BINARIO de Tesseract OCR en el sistema
operativo (no alcanza con el paquete de Python `pytesseract`, que es
solo un wrapper que llama a ese binario). Ver instrucciones en el
README, sección "OCR para fotos/imágenes".
"""
import pytesseract
from PIL import Image, ImageOps

# Extensiones de imagen que la app acepta además de .pdf
EXTENSIONES_IMAGEN = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff")


def es_imagen(nombre_archivo: str) -> bool:
    return nombre_archivo.lower().endswith(EXTENSIONES_IMAGEN)


def _preprocesar(imagen: Image.Image) -> Image.Image:
    """Mejoras básicas para fotos sacadas con el celular:

    - respeta la orientación real (los celulares guardan la rotación
      como metadato EXIF, no rotando los píxeles)
    - pasa a escala de grises
    - sube el contraste automáticamente

    Esto ayuda bastante a la precisión del OCR en fotos que no son
    perfectas (luz dispareja, celular no perfectamente derecho, etc.).
    """
    imagen = ImageOps.exif_transpose(imagen)
    imagen = imagen.convert("L")
    imagen = ImageOps.autocontrast(imagen)
    return imagen


def convertir_imagen_a_pdf_ocr(ruta_imagen: str, ruta_pdf_salida: str) -> str:
    """
    Toma la ruta de una imagen, le corre OCR y genera en `ruta_pdf_salida`
    un PDF con la imagen + el texto reconocido superpuesto (un PDF
    "buscable", con capa de texto invisible sobre la imagen original).

    Ese PDF resultante se pasa directo a `parser.procesar_factura()`,
    sin tocar nada del resto del pipeline (regex de cabecera, detección
    de tabla de ítems, etc.).
    """
    with Image.open(ruta_imagen) as imagen_original:
        imagen = _preprocesar(imagen_original)

        try:
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(
                imagen, extension="pdf", lang="spa"
            )
        except pytesseract.TesseractError:
            # Si no está instalado el paquete de idioma español
            # (tesseract-ocr-spa / spa.traineddata), reintentamos con el
            # idioma por defecto en vez de romper el flujo entero.
            pdf_bytes = pytesseract.image_to_pdf_or_hocr(imagen, extension="pdf")

    with open(ruta_pdf_salida, "wb") as f:
        f.write(pdf_bytes)

    return ruta_pdf_salida
