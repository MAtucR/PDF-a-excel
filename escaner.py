"""
Detecta la hoja de la factura dentro de la foto (separándola del fondo:
mesa, teclado, etc.) y la endereza con una transformación de
perspectiva, como hace el modo "documento" de la cámara de un celular o
un scanner. A diferencia de un simple giro (deskew), esto corrige
también la distorsión de trapecio que deja una foto sacada en ángulo, y
de paso recorta el fondo.

Es "best effort" y CONSERVADOR a propósito: si no encuentra un
contorno de 4 lados con buena confianza (hoja clara sobre fondo más
oscuro, ocupando una porción importante del cuadro), devuelve la
imagen original SIN TOCAR. Nunca fuerza una corrección dudosa — ya
hubo un caso (ver historial de ocr_utils.py) donde una corrección
geométrica poco confiable rompió la alineación de filas de la tabla de
ítems mucho más de lo que ayudaba a la lectura.
"""
import cv2
import numpy as np
from PIL import Image

# Umbral mínimo de superficie del contorno encontrado (como fracción del
# área total de la foto) para animarnos a recortar/enderezar. Fotos
# donde la hoja no llega a ocupar una porción clara del cuadro no dan
# suficiente confianza como para no arriesgarnos a recortar mal.
AREA_MINIMA_FRACCION = 0.35

# Alto (en px) al que reducimos la imagen SOLO para buscar el contorno
# (más rápido); la transformación final se aplica sobre la foto
# original en su resolución real.
ALTO_BUSQUEDA = 800


def _ordenar_puntos(pts: np.ndarray) -> np.ndarray:
    """Ordena 4 puntos como (arriba-izq, arriba-der, abajo-der,
    abajo-izq), sin asumir en qué orden los devolvió OpenCV."""
    pts = pts.reshape(4, 2).astype("float32")
    suma = pts.sum(axis=1)
    diferencia = np.diff(pts, axis=1).reshape(-1)
    ordenado = np.zeros((4, 2), dtype="float32")
    ordenado[0] = pts[np.argmin(suma)]
    ordenado[2] = pts[np.argmax(suma)]
    ordenado[1] = pts[np.argmin(diferencia)]
    ordenado[3] = pts[np.argmax(diferencia)]
    return ordenado


def _encontrar_esquinas_hoja(pequena: np.ndarray):
    """Busca, en la versión reducida de la foto, el contorno de 4
    esquinas de la hoja: la separamos del fondo por brillo (la hoja es
    blanca; mesa/teclado son más oscuros), no por bordes (los bordes de
    Canny se confunden fácil con el propio texto de la factura)."""
    gris = cv2.cvtColor(pequena, cv2.COLOR_BGR2GRAY)
    gris = cv2.GaussianBlur(gris, (7, 7), 0)
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    kernel = np.ones((15, 15), np.uint8)
    limpia = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, kernel, iterations=2)
    limpia = cv2.morphologyEx(limpia, cv2.MORPH_OPEN, kernel, iterations=1)

    contornos, _ = cv2.findContours(limpia, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contornos:
        return None

    contorno_mayor = max(contornos, key=cv2.contourArea)
    area_total = pequena.shape[0] * pequena.shape[1]
    if cv2.contourArea(contorno_mayor) < AREA_MINIMA_FRACCION * area_total:
        return None

    hull = cv2.convexHull(contorno_mayor)
    perimetro = cv2.arcLength(hull, True)
    for eps_factor in (0.01, 0.02, 0.03, 0.05):
        approx = cv2.approxPolyDP(hull, eps_factor * perimetro, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype("float32")

    return None


def enderezar_documento(imagen: Image.Image) -> Image.Image:
    """Recibe una imagen PIL (ya orientada según EXIF) y devuelve la
    hoja de la factura recortada y enderezada por perspectiva, si pudo
    detectarla con confianza; si no, devuelve la imagen SIN CAMBIOS
    (nunca lanza una excepción hacia afuera: ante cualquier problema,
    el llamador sigue con la imagen original)."""
    try:
        original_rgb = np.array(imagen.convert("RGB"))
        original_bgr = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2BGR)

        alto_original = original_bgr.shape[0]
        escala = ALTO_BUSQUEDA / alto_original if alto_original > ALTO_BUSQUEDA else 1.0
        pequena = cv2.resize(original_bgr, None, fx=escala, fy=escala) if escala != 1.0 else original_bgr

        esquinas_chicas = _encontrar_esquinas_hoja(pequena)
        if esquinas_chicas is None:
            return imagen

        esquinas = esquinas_chicas / escala
        pts = _ordenar_puntos(esquinas)
        (tl, tr, br, bl) = pts

        ancho = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
        alto = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))

        # Sanity check: si el rectángulo resultante quedaría irrisorio
        # o desproporcionado respecto de la foto original, no confiamos
        # en la detección (mejor no tocar nada a arriesgarnos a recortar
        # mal la factura).
        if ancho < 0.3 * original_bgr.shape[1] or alto < 0.3 * original_bgr.shape[0]:
            return imagen
        if ancho == 0 or alto == 0:
            return imagen

        destino = np.array([
            [0, 0], [ancho - 1, 0], [ancho - 1, alto - 1], [0, alto - 1],
        ], dtype="float32")

        matriz = cv2.getPerspectiveTransform(pts, destino)
        recortada = cv2.warpPerspective(original_bgr, matriz, (ancho, alto))

        return Image.fromarray(cv2.cvtColor(recortada, cv2.COLOR_BGR2RGB))
    except Exception:
        return imagen
