"""
Diagnóstico de PaddleOCR.

Correr con el venv activado, desde la carpeta del proyecto:

    python diagnostico_paddle.py

Imprime qué versión hay instalada, qué forma de construir el objeto
PaddleOCR funciona en esa versión, y qué formato de salida devuelve.
Con eso se puede arreglar ocr_paddle.py sin adivinar.
"""
import sys
import traceback

print("=" * 60)
print("DIAGNOSTICO PADDLEOCR")
print("=" * 60)
print(f"Python: {sys.version.split()[0]}")

# --- 1. Los paquetes estan instalados? ---
try:
    import paddleocr
    print(f"paddleocr importado OK  -- version: {getattr(paddleocr, '__version__', 'desconocida')}")
except Exception as e:
    print(f"FALLO importando paddleocr: {type(e).__name__}: {e}")
    print("\n=> Instalar con:  pip install paddlepaddle paddleocr")
    sys.exit(1)

try:
    import paddle
    print(f"paddlepaddle importado OK -- version: {getattr(paddle, '__version__', 'desconocida')}")
except Exception as e:
    print(f"FALLO importando paddlepaddle: {type(e).__name__}: {e}")

# --- 2. Que firma de constructor acepta esta version? ---
from paddleocr import PaddleOCR

intentos = [
    ("lang='es', use_textline_orientation=True", dict(lang="es", use_textline_orientation=True)),
    ("lang='es', use_angle_cls=True",            dict(lang="es", use_angle_cls=True)),
    ("lang='es'",                                 dict(lang="es")),
    ("lang='en'",                                 dict(lang="en")),
    ("sin argumentos",                            dict()),
]

ocr = None
firma_ok = None
print("\n--- Probando formas de construir PaddleOCR ---")
for descripcion, kwargs in intentos:
    try:
        ocr = PaddleOCR(**kwargs)
        firma_ok = descripcion
        print(f"  OK     {descripcion}")
        break
    except Exception as e:
        msg = str(e).replace("\n", " ")[:160]
        print(f"  FALLA  {descripcion}  -> {type(e).__name__}: {msg}")

if ocr is None:
    print("\n=> Ninguna forma de construir PaddleOCR funciono.")
    print("   Si el error menciona 'model hosting' o 'network', es que no")
    print("   pudo descargar los modelos (hace falta internet la 1a vez).")
    sys.exit(1)

print(f"\n=> Constructor que funciona: {firma_ok}")

# --- 3. Que metodo de inferencia tiene, y que formato devuelve? ---
import numpy as np
imagen_prueba = np.full((200, 800, 3), 255, dtype=np.uint8)
try:
    import cv2
    cv2.putText(imagen_prueba, "CUIT 30-52952472-9  TOTAL 255748.34",
                (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
except Exception:
    pass

print("\n--- Probando inferencia ---")
salida = None
for metodo in ("predict", "ocr"):
    if not hasattr(ocr, metodo):
        print(f"  (no tiene metodo .{metodo}())")
        continue
    try:
        salida = getattr(ocr, metodo)(imagen_prueba)
        print(f"  OK con .{metodo}()")
        break
    except Exception as e:
        msg = str(e).replace("\n", " ")[:160]
        print(f"  FALLA .{metodo}()  -> {type(e).__name__}: {msg}")

if salida is None:
    print("\n=> No se pudo correr inferencia.")
    sys.exit(1)

print("\n--- Formato de la salida ---")
print(f"  tipo raiz: {type(salida).__name__}, largo: {len(salida) if hasattr(salida,'__len__') else '?'}")
if hasattr(salida, "__len__") and len(salida) > 0:
    primero = salida[0]
    print(f"  tipo salida[0]: {type(primero).__name__}")
    if isinstance(primero, dict):
        print(f"  claves: {sorted(primero.keys())[:20]}")
        for clave in ("rec_texts", "rec_scores", "rec_polys", "dt_polys"):
            if clave in primero:
                v = primero[clave]
                print(f"    {clave}: largo {len(v)}  ejemplo: {str(v[0])[:90] if len(v) else '(vacio)'}")
    elif isinstance(primero, list) and primero:
        print(f"  salida[0][0]: {str(primero[0])[:200]}")

# --- 4. Prueba contra el adaptador del proyecto ---
print("\n--- Probando el adaptador ocr_paddle.py del proyecto ---")
try:
    from PIL import Image
    import ocr_paddle
    ocr_paddle._OCR = ocr  # reusar la instancia que ya funciono
    datos = ocr_paddle.datos_estilo_tesseract(Image.fromarray(imagen_prueba))
    print(f"  palabras devueltas por el adaptador: {len(datos['text'])}")
    if datos["text"]:
        print(f"  ejemplo: {datos['text'][:8]}")
        print("  => EL ADAPTADOR FUNCIONA")
    else:
        print("  => el adaptador devolvio vacio (revisar _resultado_a_regiones)")
except Exception:
    print("  FALLA en el adaptador:")
    traceback.print_exc()

print("\n" + "=" * 60)
print("Pegale una captura / copia toda esta salida para poder arreglarlo.")
print("=" * 60)
