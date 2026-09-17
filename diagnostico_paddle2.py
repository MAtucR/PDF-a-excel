"""
Busca una configuracion de PaddleOCR que SI pueda correr inferencia en
esta maquina.

Contexto: con paddleocr 3.7 + paddlepaddle 3.3.1 la inferencia puede
fallar con:

    NotImplementedError: ConvertPirAttribute2RuntimeAttribute not
    support [pir::ArrayAttribute<pir::DoubleAttribute>]

que es un problema del motor de ejecucion de Paddle con los modelos
PP-OCRv6, no del codigo de este proyecto. Las salidas posibles son
usar modelos mas viejos, apagar oneDNN (MKLDNN), o apagar PIR.

Cada combinacion se prueba en un SUBPROCESO aparte, porque los flags
de Paddle (FLAGS_*) solo tienen efecto si se setean ANTES de importar
paddle, y una vez importado no se puede deshacer.

Uso:

    python diagnostico_paddle2.py

Al final imprime que combinacion funciono, para poder fijarla en
ocr_paddle.py.
"""
import json
import os
import subprocess
import sys

# (nombre, variables de entorno, kwargs del constructor)
COMBOS = [
    ("por defecto (v6)",                  {}, {}),
    ("sin oneDNN",                        {}, {"enable_mkldnn": False}),
    ("modelos PP-OCRv5",                  {}, {"ocr_version": "PP-OCRv5"}),
    ("modelos PP-OCRv4",                  {}, {"ocr_version": "PP-OCRv4"}),
    ("PP-OCRv4 sin oneDNN",               {}, {"ocr_version": "PP-OCRv4", "enable_mkldnn": False}),
    ("PP-OCRv5 sin oneDNN",               {}, {"ocr_version": "PP-OCRv5", "enable_mkldnn": False}),
    ("sin PIR en executor",               {"FLAGS_enable_pir_in_executor": "0"}, {}),
    ("sin PIR api",                       {"FLAGS_enable_pir_api": "0"}, {}),
    ("sin PIR + PP-OCRv4",                {"FLAGS_enable_pir_in_executor": "0"}, {"ocr_version": "PP-OCRv4"}),
]


HIJO = r'''
import json, os, sys, warnings
warnings.filterwarnings("ignore")
kwargs = json.loads(sys.argv[1])
try:
    import numpy as np
    from paddleocr import PaddleOCR
    base = {"lang": "es", "use_textline_orientation": True}
    base.update(kwargs)
    try:
        ocr = PaddleOCR(**base)
    except TypeError:
        # alguna version no acepta algun kwarg: reintentar sin los extras
        ocr = PaddleOCR(lang="es")
    img = np.full((200, 800, 3), 255, dtype=np.uint8)
    try:
        import cv2
        cv2.putText(img, "CUIT 30-52952472-9 TOTAL 255748.34",
                    (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    except Exception:
        pass
    salida = ocr.predict(img) if hasattr(ocr, "predict") else ocr.ocr(img)
    textos = []
    if salida and isinstance(salida[0], dict):
        textos = list(salida[0].get("rec_texts") or [])
    elif salida and isinstance(salida[0], list):
        textos = [e[1][0] for e in salida[0]]
    print("RESULTADO_OK::" + json.dumps(textos[:6], ensure_ascii=False))
except Exception as e:
    print("RESULTADO_FALLA::" + type(e).__name__ + ": " + str(e).replace("\n", " ")[:180])
'''


def probar(nombre, env_extra, kwargs):
    env = dict(os.environ)
    env.update(env_extra)
    env["PYTHONWARNINGS"] = "ignore"
    try:
        proc = subprocess.run(
            [sys.executable, "-c", HIJO, json.dumps(kwargs)],
            env=env, capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout (>10 min)"

    salida = (proc.stdout or "") + (proc.stderr or "")
    for linea in salida.splitlines():
        if linea.startswith("RESULTADO_OK::"):
            return True, linea[len("RESULTADO_OK::"):]
        if linea.startswith("RESULTADO_FALLA::"):
            return False, linea[len("RESULTADO_FALLA::"):]
    return False, "sin resultado (revisar salida cruda)"


def main():
    print("=" * 66)
    print("BUSCANDO UNA CONFIGURACION DE PADDLEOCR QUE FUNCIONE")
    print("=" * 66)
    print("Cada prueba puede tardar 1-2 min (carga los modelos). Paciencia.\n")

    funcionaron = []
    for nombre, env_extra, kwargs in COMBOS:
        detalle = []
        if env_extra:
            detalle.append(" ".join(f"{k}={v}" for k, v in env_extra.items()))
        if kwargs:
            detalle.append(" ".join(f"{k}={v}" for k, v in kwargs.items()))
        print(f"-> Probando: {nombre}  [{'; '.join(detalle) or 'sin cambios'}]")
        ok, info = probar(nombre, env_extra, kwargs)
        if ok:
            print(f"   FUNCIONA. Texto reconocido: {info}\n")
            funcionaron.append((nombre, env_extra, kwargs))
        else:
            print(f"   falla: {info}\n")

    print("=" * 66)
    if funcionaron:
        nombre, env_extra, kwargs = funcionaron[0]
        print(f"PRIMERA QUE FUNCIONA: {nombre}")
        print(f"  variables de entorno: {env_extra or 'ninguna'}")
        print(f"  kwargs del constructor: {kwargs or 'ninguno'}")
        if len(funcionaron) > 1:
            print(f"\n  (tambien funcionaron: {', '.join(n for n, _, _ in funcionaron[1:])})")
    else:
        print("NINGUNA COMBINACION FUNCIONO.")
        print("La salida mas probable es bajar la version de paddlepaddle:")
        print("    pip install paddlepaddle==3.0.0")
        print("y volver a correr este script.")
    print("=" * 66)
    print("Copia toda esta salida para poder fijar la configuracion en el codigo.")


if __name__ == "__main__":
    main()
