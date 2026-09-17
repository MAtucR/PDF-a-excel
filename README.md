# Facturas PDF/Foto → Excel

App local en Flask que sube facturas AFIP (en PDF o como foto/imagen) y
las vuelca a un Excel (una hoja de cabeceras + una hoja de ítems), sin
usar ninguna LLM: todo extracción determinística con `pdfplumber`
(texto + tablas) y regex. Las fotos/imágenes se convierten primero a un
PDF con OCR (Tesseract) y de ahí en más pasan por el mismo pipeline.

## Instalación paso a paso (Windows)

1. **Instalar Python**: entrá a https://www.python.org/downloads/windows/
   y descargá la última versión estable (3.11 o superior). Al correr el
   instalador, **IMPORTANTE**: tildá la casilla "Add python.exe to PATH"
   que aparece abajo del todo antes de darle a Install — si no la tildás,
   después Windows no va a reconocer el comando `python` en la terminal.

2. **Verificar la instalación**: abrí PowerShell (buscá "PowerShell" en
   el menú inicio) y escribí:
   ```powershell
   python --version
   ```
   Debería mostrarte algo como `Python 3.12.x`. Si te dice que no
   reconoce el comando, reiniciá la PC (a veces el PATH tarda en
   actualizarse) o reinstalá tildando la casilla del paso 1.

3. **Descargar el proyecto**: cloná el repo con git (si lo tenés
   instalado):
   ```powershell
   git clone https://github.com/MAtucR/PDF-a-excel.git
   ```
   Si no tenés git, entrá al repo en el navegador, botón verde "Code" →
   "Download ZIP", y descomprimilo donde quieras.

4. **Abrir la carpeta en la terminal**: navegá hasta la carpeta
   descomprimida/clonada, por ejemplo:
   ```powershell
   cd Downloads\PDF-a-excel
   ```
   (ajustá la ruta según dónde lo hayas guardado).

5. **Crear el entorno virtual**: esto crea una carpeta `venv` que aísla
   las librerías de este proyecto del resto de tu sistema, para no
   pisar otras instalaciones de Python que tengas.
   ```powershell
   python -m venv venv
   ```

6. **Activar el entorno virtual**:
   ```powershell
   venv\Scripts\activate
   ```
   Vas a ver que el prompt de la terminal cambia y ahora empieza con
   `(venv)`. Si PowerShell te tira un error de "execution policy",
   corré antes:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```
   confirmá con `S`, y volvé a intentar activar.

7. **Instalar las dependencias de Python**: con el entorno activado:
   ```powershell
   pip install -r requirements.txt
   ```
   Esto instala Flask, pdfplumber, openpyxl, pytesseract, Pillow y
   OpenCV (puede tardar 2-4 minutos la primera vez, OpenCV pesa unos
   40-60MB — no hace falta ningún compilador ni instalación aparte,
   todo viene ya compilado en el paquete de pip).

8. **Instalar Tesseract OCR** (necesario solo para procesar fotos/
   imágenes; si solo vas a subir PDFs, podés saltear este paso). Ver la
   sección **"OCR para fotos/imágenes"** más abajo.

9. **Correr la aplicación**:
   ```powershell
   python app.py
   ```
   Vas a ver un mensaje tipo `Running on http://127.0.0.1:5000`. Dejá
   esa ventana de la terminal abierta y abrí esa dirección en tu
   navegador (Chrome, Edge, el que uses) — ahí ya podés subir tu
   primera factura (PDF o foto).

**Para usarla de nuevo más adelante** no hace falta repetir todos los
pasos: alcanza con abrir PowerShell en la carpeta del proyecto y correr:
```powershell
venv\Scripts\activate
python app.py
```

## Instalación (Mac/Linux)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## OCR para fotos/imágenes

Para que la app pueda leer fotos/imágenes (no solo PDFs) hace falta
tener instalado el **programa** Tesseract OCR en la compu, además del
paquete de Python `pytesseract` (que ya quedó en `requirements.txt`).
`pytesseract` es solo un wrapper: si el binario no está instalado, la
app te va a avisar con un mensaje de error al subir una foto.

**Windows**: descargá el instalador desde
https://github.com/UB-Mannheim/tesseract/wiki (es el build de Windows
más usado). Durante la instalación, en la lista de "Additional language
data", tildá **Spanish** para que reconozca mejor los textos en
español. Si después la app no lo encuentra, puede que necesites agregar
la carpeta de instalación (por defecto algo como
`C:\Program Files\Tesseract-OCR`) al PATH del sistema, o setear en
`ocr_utils.py`:
```python
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

**Mac**:
```bash
brew install tesseract tesseract-lang
```

**Linux (Debian/Ubuntu)**:
```bash
sudo apt install tesseract-ocr tesseract-ocr-spa
```

## Uso

Subís el PDF o la foto de la factura y listo. Cada factura que subís se
va agregando como fila nueva al mismo `output/facturas.xlsx` (no lo
pisa). Podés descargarlo con el botón, o reiniciarlo si querés empezar
de cero.

## Cómo funciona (parser.py)

1. **Cabecera** (CUIT, fecha, N° factura, condición IVA, CAE, etc.):
   regex sobre el texto plano extraído con `pdfplumber`. Están escritos
   para tolerar variaciones de espaciado/mayúsculas entre proveedores.

2. **Totales generales** (Neto/IVA/Subtotal/Total): se buscan primero en
   la tabla de totales del PDF (más confiable que el texto corrido) y
   si no aparece se cae al regex sobre el texto.

3. **Tabla de ítems**: usa `pdfplumber.extract_tables()`, que detecta
   tablas por los bordes/líneas reales del PDF. Busca automáticamente
   la fila de encabezado (por palabras clave como "descripción",
   "cantidad", "unitario") y corta al llegar a la fila "Totales".

## Cómo funciona con fotos/imágenes (ocr_utils.py)

Cuando el archivo subido no es un `.pdf` sino una imagen (jpg, png,
webp, bmp, tif), `ocr_utils.convertir_imagen_a_pdf_ocr`:

1. Corrige la orientación según el metadato EXIF de la foto, y busca la
   hoja de la factura dentro de la foto para separarla del fondo (mesa,
   teclado, etc.) y enderezarla por perspectiva — como el modo
   "documento" de la cámara de un celular o un scanner (ver
   `escaner.py`). Es best-effort: si no encuentra la hoja con confianza
   (por ejemplo, si el fondo alrededor de la hoja es muy parecido en
   color, o la hoja ocupa muy poco del cuadro), sigue con la foto
   original sin recortar.
2. Pasa a escala de grises, sube el contraste, corrige rotaciones de
   90°/180°/270° (vía OSD de Tesseract) y agranda la imagen si quedó
   chica, para que el OCR tenga más píxeles por letra.
3. Le corre OCR con Tesseract y genera un PDF "buscable" (la imagen +
   una capa de texto invisible superpuesta con lo que Tesseract
   reconoció).
4. Ese PDF se pasa **tal cual** a `parser.procesar_factura()` — el
   mismo código que ya procesa PDFs digitales, sin ningún cambio. Además,
   `ocr_items.py`/`ocr_totales.py` reconstruyen la tabla de ítems y la
   fila de totales por posición de palabras (ver sus propios docstrings).

**Limitación a tener en cuenta**: incluso con la hoja bien recortada y
derecha, si la foto original tiene poca resolución (por ejemplo, viene
de WhatsApp, que recomprime bastante) el texto chico —cabecera, columna
de precios— puede quedar ilegible. Ninguna corrección geométrica puede
"inventar" detalle que la compresión ya descartó; en esos casos conviene
mandar la foto por otro medio (sin recomprimir) o sacarla más de cerca.

## Si tus facturas no matchean bien

Los patrones en `HEADER_PATTERNS` (dict al principio de `parser.py`) y
las palabras clave en `ITEM_HEADER_HINTS` son el lugar donde ajustar
cuando aparezca un proveedor con un formato distinto. Si la factura
sube pero faltan campos, la app te avisa cuáles no pudo detectar — con
eso podés ir agregando el patrón puntual que falte.
