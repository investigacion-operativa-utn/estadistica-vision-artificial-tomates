# Tomates IO - Vision + Estadistica

MVP educativo para subir un video de una cinta transportadora, detectar tomates y calcular indicadores descriptivos de diametro (media y desvio estandar).

## Alcance actual

- Subida de video por web (`mp4/mov/avi/mkv`).
- Procesamiento asincrono en background.
- Deteccion con modos configurables:
  - `auto`: usa Ultralytics como detector principal y cae a `basic` solo si el modelo no esta disponible o no devuelve resultados.
  - `yolo`: usa Ultralytics solamente, sin fallback.
  - `basic`: fallback legado por segmentacion simple de color rojo para comparacion/debug.
- Motor principal actual: `yolov8s-world.pt` con prompt `tomato` mediante Ultralytics.
- Tabla de observaciones (frame, timestamp, diametro, confianza).
- Tabla de observaciones con `track_id` estimado (tracking simple por IoU).
- Indicadores: media y desvio estandar en px y mm.
- Visualizaciones: histograma de diametros y serie temporal de diametro medio por frame.
- Reproductor de video embebido con overlays (bounding boxes + diametro) y linea vertical de conteo.
- Medicion por cruce: la tabla se completa cuando cada tomate cruza la linea durante la reproduccion.
- Export CSV de resultados.

## Estructura

- `app/main.py`: API + render de interfaz.
- `app/pipeline.py`: procesamiento del video por frames.
- `app/detector.py`: detector YOLO/fallback.
- `app/db.py`: persistencia SQLite de jobs y mediciones.
- `app/templates/index.html`: UI.
- `app/static/*`: JS + CSS.

## Requisitos

- Python 3.9+
- FFmpeg no es obligatorio en este MVP (OpenCV lee archivos directos).

## Ejecutar local

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abrir `http://localhost:8000`.

## Configuracion del detector

Variables de entorno soportadas:

- `TOMATO_DETECTION_MODE`: selecciona el modo de deteccion. Valores: `auto`, `yolo`, `basic`. El valor legado `classic` se interpreta como `auto`.
- `TOMATO_MODEL_WEIGHTS`: ruta a pesos custom para usar un modelo Ultralytics propio.
- `TOMATO_YOLO_MODEL`: nombre o ruta del modelo Ultralytics a usar por defecto. Valor inicial recomendado: `yolov8s-world.pt`.
- `TOMATO_WORLD_PROMPT`: prompt textual para modelos open-vocabulary tipo world. Valor inicial: `tomato`.
- `TOMATO_YOLO_CONFIDENCE`: umbral de confianza de inferencia.
- `TOMATO_YOLO_IMGSZ`: tamano de imagen para inferencia.
- `TOMATO_ROI_TOP_RATIO` y `TOMATO_ROI_BOTTOM_RATIO`: recorte vertical util de la cinta.
- `TOMATO_FALLBACK_MODE`: fallback temporal cuando `auto` no puede usar Ultralytics o no obtiene detecciones. Valor actual recomendado: `basic`.

Recomendaciones:

- Usar `auto` para el flujo normal mientras se valida el modelo Ultralytics elegido.
- Usar `basic` solo para comparar contra el fallback viejo.
- Usar `yolo` cuando quieras forzar inferencia solo con Ultralytics, sin fallback.
- Si usas un modelo `*-world.pt`, el entorno necesita `git+https://github.com/ultralytics/CLIP.git`, que ya queda incluido en `requirements.txt`.

## Railway

Este repo ya incluye `Procfile` para levantar uvicorn.

1. Crear proyecto en Railway conectado a este repo.
2. Railway detecta Python e instala `requirements.txt`.
3. Definir variables de entorno si aplica:
   - `TOMATO_MODEL_WEIGHTS`: ruta a pesos custom dentro del contenedor (opcional).
4. Deploy.

## Nota metodologica

La conversion a mm depende de calibracion (`mm por pixel`) y de que la camara permanezca fija respecto a la cinta.
Los resultados deben presentarse como estimaciones para analisis estadistico, no como metrologia de alta precision.

## Siguientes mejoras sugeridas

- Histograma y serie temporal en la UI.
- Cola de trabajos separada (worker dedicado) si aumenta la concurrencia.
- Integrar pesos YOLO entrenados especificamente para tomate.

## Detalle de tracking del MVP

El `track_id` actual usa un enfoque liviano por IoU de bounding boxes entre frames muestreados.
Es suficiente para reducir duplicados en una demo de clase, pero no reemplaza un tracker robusto (SORT/DeepSORT/ByteTrack).
