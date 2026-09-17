# Facturas PDF → Excel

App local en Flask que sube facturas AFIP en PDF y las vuelca a un Excel
(una hoja de cabeceras + una hoja de ítems), sin usar ninguna LLM: todo
extracción determinística con `pdfplumber` (texto + tablas) y regex.

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

7. **Instalar las dependencias**: con el entorno activado:
   ```powershell
   pip install -r requirements.txt
   ```
   Esto instala Flask, pdfplumber y openpyxl (puede tardar 1-2 minutos).

8. **Correr la aplicación**:
   ```powershell
   python app.py
   ```
   Vas a ver un mensaje tipo `Running on http://127.0.0.1:5000`. Dejá
   esa ventana de la terminal abierta y abrí esa dirección en tu
   navegador (Chrome, Edge, el que uses) — ahí ya podés subir tu
   primera factura PDF.

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
