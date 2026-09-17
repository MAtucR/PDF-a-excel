# Facturas PDF → Excel

App local en Flask que sube facturas AFIP en PDF y las vuelca a un Excel
(una hoja de cabeceras + una hoja de ítems), sin usar ninguna LLM: todo
extracción determinística con `pdfplumber` (texto + tablas) y regex.

## Instalación (Windows)

1. Instalá Python 3.11+ desde https://www.python.org/downloads/windows/
   (**importante**: en el instalador tildá la casilla "Add python.exe to PATH").
2. Abrí la carpeta del proyecto en PowerShell o CMD.
3. Creá y activá un entorno virtual:
   ```powershell
   python -m venv venv
   venv\Scripts\activate
   ```
4. Instalá las dependencias:
   ```powershell
   pip install -r requirements.txt
   ```
5. Corré la app:
   ```powershell
   python app.py
   ```
6. Abrí http://localhost:5000 en el navegador.

## Instalación (Mac/Linux)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

## Uso

Subís el PDF y listo. Cada factura que subís se va agregando como fila
nueva al mismo `output/facturas.xlsx` (no lo pisa). Podés descargarlo
con el botón, o reiniciarlo si querés empezar de cero.

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

## Si tus facturas no matchean bien

Los patrones en `HEADER_PATTERNS` (dict al principio de `parser.py`) y
las palabras clave en `ITEM_HEADER_HINTS` son el lugar donde ajustar
cuando aparezca un proveedor con un formato distinto. Si la factura
sube pero faltan campos, la app te avisa cuáles no pudo detectar — con
eso podés ir agregando el patrón puntual que falte.

**Importante**: esto solo funciona con PDFs que tienen capa de texto
(la gran mayoría de las facturas electrónicas AFIP). Si alguna vez te
llega una factura escaneada como imagen, vas a necesitar sumar OCR
(`pytesseract`) antes de este pipeline — avisame si llega ese caso.
