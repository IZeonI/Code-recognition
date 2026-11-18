# Code Capture

Selecciona una región de la pantalla (como Recortes de Windows), reconoce el código que aparece y lo copia automáticamente al portapapeles.

Usa el OCR integrado de Windows 10/11: no necesita internet ni instalar Tesseract.

## Instalación

```bash
pip install -r requirements.txt
```

## Uso

1. Ejecuta `iniciar.bat` (o `python code_capture.py`). Aparece un icono `</>` en la bandeja del sistema.
2. Pulsa **Alt+Q** (o haz doble clic en el icono) y arrastra para seleccionar el código.
3. El texto queda en el portapapeles. Pégalo con Ctrl+V.
4. Justo después, el programa pulsa **Alt+Tab**, **Ctrl+V** y **Enter** por ti (cambia de ventana, pega el código y lo envía). Para desactivarlo: clic derecho en el icono → desmarca *Tras copiar: Alt+Tab + Ctrl+V + Enter*. Para que arranque desactivado, pon `AUTO_SWITCH = False` al principio de `code_capture.py`.

- **Esc** o **clic derecho** cancelan la selección.
- **Desactivar el atajo sin cerrar**: clic derecho en el icono → desmarca *Atajo activo*. Así la tecla vuelve a funcionar en las demás aplicaciones; márcalo de nuevo para reactivarlo.
- **Cerrar el programa**: clic derecho en el icono → *Salir* (o termina `pythonw.exe` en el Administrador de tareas).
- **Que no arranque con Windows**: borra el acceso directo de `shell:startup` si lo creaste.
- **Cambiar el atajo**: clic derecho en el icono → *Cambiar atajo...* y pulsa la tecla o combinación nueva. Puede ser una sola tecla (por ejemplo F8) o una combinación (Ctrl+Shift+S). Esc cancela. Se guarda en `config.json` y se mantiene al reiniciar.
- **Volver a Alt+Q**: clic derecho en el icono → *Restablecer atajo (ALT+Q)*, o borra `config.json`.
- Si eliges una sola tecla, esa tecla deja de funcionar en las demás aplicaciones mientras el atajo esté activo. Elige una que no uses (F8, F9, Pausa...).
- Para probar con una imagen: `python code_capture.py captura.png`.

## Vigilar "CODIGO:" automáticamente

Clic derecho en el icono → marca *Vigilar CODIGO: (abajo a la derecha)*.

- Revisa unas 15-20 veces por segundo el cuadrante inferior derecho del monitor principal.
- Busca `CODIGO:` seguido de 12 caracteres separados por guiones (por ejemplo `XK7P-QM4T-R9WZ`). También acepta `CÓDIGO` y los grupos pueden tener cualquier tamaño.
- Al encontrarlo, copia el código y, si está activada la opción, pulsa Alt+Tab, Ctrl+V y Enter.
- Es de un solo uso: se apaga sola al encontrar un código, o a los 15 minutos si no aparece ninguno. Puedes apagarla antes desmarcando la opción.

### Refrescar el stream

Mientras *Vigilar CODIGO* está activo, el programa pulsa **Espacio dos veces** (pausa y play) a las 11:40, 11:50, 12:00… (cada 10 minutos desde las 11:40). Así el stream vuelve al directo con el menor retraso posible.

- Se activa y desactiva en el menú: *Refrescar stream (Espacio x2...)*. Viene activado.
- El Espacio va a la ventana que tengas delante, así que deja el stream en primer plano mientras vigilas.
- Ajustes: `REFRESH` (valor al arrancar), `REFRESH_FROM` (hora de inicio), `REFRESH_EVERY` (minutos) y `REFRESH_GAP` (segundos entre los dos Espacios).

Ajustes al principio de `code_capture.py`: `CODE_LEN` (número de caracteres), `WATCH_TIMEOUT` (segundos) y `WATCH_SCALE` (súbelo a 1.5 si el texto es muy pequeño y no lo detecta; es algo más lento).

Ojo: el OCR puede confundir caracteres parecidos (`1`/`I`/`l`, `0`/`O`).

## Qué hace con el código

- Mantiene la sangría y las líneas vacías a partir de la posición del texto.
- Funciona con temas oscuros (invierte la imagen) y amplía el texto pequeño.
- Cambia las comillas tipográficas (“ ” ‘ ’) por comillas normales.

## Limitaciones

El OCR de Windows a veces omite guiones bajos (`__name__`) y algunos corchetes. Revisa esos símbolos al pegar.

Para que arranque con Windows, crea un acceso directo a `iniciar.bat` en `shell:startup`.
