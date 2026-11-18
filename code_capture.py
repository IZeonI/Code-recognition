"""Code Capture: selecciona una región de la pantalla, reconoce el código con el
OCR integrado de Windows y lo copia al portapapeles.

Atajo por defecto: Alt+Q  (Esc cancela la selección).
Icono en la bandeja del sistema con opciones "Capturar" y "Salir".
"""
import asyncio
import ctypes
import io
import json
import queue
import re
import statistics
import time
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from pathlib import Path

import keyboard
import mss
import pystray
from PIL import Image, ImageDraw, ImageEnhance, ImageGrab, ImageOps, ImageTk
from winrt.windows.graphics.imaging import BitmapDecoder
from winrt.windows.globalization import Language
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

HOTKEY = "alt+q"  # atajo por defecto; el elegido en el menú se guarda en config.json
CONFIG = Path(__file__).with_name("config.json")
AUTO_SWITCH = True  # tras copiar, pulsa Alt+Tab, Ctrl+V y Enter (desactivable en el menú)
OCR_LANG = "en-US"  # inglés reconoce mejor símbolos y palabras clave de código

# Coordenadas en píxeles reales aunque haya escalado de pantalla (125%, 150%...)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()


# ---------------------------------------------------------------- OCR ------
def _make_engine():
    lang = Language(OCR_LANG)
    if OcrEngine.is_language_supported(lang):
        return OcrEngine.try_create_from_language(lang)
    return OcrEngine.try_create_from_user_profile_languages()


ENGINE = _make_engine()


def preprocess(img: Image.Image) -> tuple[Image.Image, float]:
    """Escala de grises, fondo claro y ampliada para texto pequeño."""
    img = img.convert("L")
    if statistics.mean(img.getdata()) < 128:  # tema oscuro -> invertir
        img = ImageOps.invert(img)
    img = ImageEnhance.Contrast(img).enhance(1.5)
    scale = 3.0 if img.height < 400 else 2.0
    max_dim = OcrEngine.max_image_dimension
    scale = min(scale, max_dim / img.width, max_dim / img.height)
    if scale > 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img.convert("RGB"), scale


async def _ocr_async(img: Image.Image):
    buf = io.BytesIO()
    img.save(buf, format="BMP")  # BMP: ~5 ms frente a ~100 ms de PNG
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream)
    writer.write_bytes(buf.getvalue())
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    return await ENGINE.recognize_async(bitmap)


def lines_to_code(result) -> str:
    """Reconstruye sangría y líneas en blanco a partir de las posiciones."""
    # Windows OCR a veces parte una misma línea visual en varias: se agrupan
    # todas las palabras por su altura en la imagen.
    words = [w for line in result.lines for w in line.words]
    if not words:
        return ""
    line_h = statistics.median(w.bounding_rect.height for w in words)
    words.sort(key=lambda w: w.bounding_rect.y + w.bounding_rect.height / 2)
    rows = []
    for w in words:
        cy = w.bounding_rect.y + w.bounding_rect.height / 2
        if rows and abs(cy - rows[-1]["cy"]) < line_h * 0.6:
            rows[-1]["words"].append(w)
        else:
            rows.append({"cy": cy, "words": [w]})
    for r in rows:
        r["words"].sort(key=lambda w: w.bounding_rect.x)
        r["x"] = r["words"][0].bounding_rect.x
        r["y"] = min(w.bounding_rect.y for w in r["words"])
        r["h"] = max(w.bounding_rect.height for w in r["words"])

    char_ws = [w.bounding_rect.width / len(w.text) for w in words if len(w.text) >= 3]
    char_w = statistics.median(char_ws) if char_ws else 10
    min_x = min(r["x"] for r in rows)

    # distancia típica entre líneas consecutivas; un salto mayor = líneas vacías
    pitches = [b["cy"] - a["cy"] for a, b in zip(rows, rows[1:])]
    pitch = min(statistics.median(pitches), min(pitches) * 1.5) if pitches else line_h

    out, prev_cy = [], None
    for r in rows:
        if prev_cy is not None:
            blanks = int(round((r["cy"] - prev_cy) / pitch)) - 1
            out.extend([""] * max(0, min(blanks, 3)))
        prev_cy = r["cy"]
        indent = int(round((r["x"] - min_x) / char_w))
        # respeta espacios grandes entre palabras (alineaciones, comentarios)
        text, prev_end = "", None
        for w in r["words"]:
            br = w.bounding_rect
            if prev_end is not None:
                text += " " * max(0, int(round((br.x - prev_end) / char_w)))
            text += w.text
            prev_end = br.x + br.width
        out.append(" " * indent + text)
    return fix_symbols("\n".join(out))


def fix_symbols(text: str) -> str:
    for a, b in {"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "´": "'",
                 "—": "-", "–": "-", "…": "...", " ": " "}.items():
        text = text.replace(a, b)
    return text


def recognize(img: Image.Image) -> str:
    prepared, _ = preprocess(img)
    result = asyncio.run(_ocr_async(prepared))
    return lines_to_code(result)


# -------------------------------------------------------- Selección --------
def virtual_screen():
    u = ctypes.windll.user32
    return u.GetSystemMetrics(76), u.GetSystemMetrics(77), u.GetSystemMetrics(78), u.GetSystemMetrics(79)


class Selector:
    def __init__(self, root, on_done):
        self.on_done = on_done
        vx, vy, vw, vh = virtual_screen()
        self.shot = ImageGrab.grab(bbox=(vx, vy, vx + vw, vy + vh), all_screens=True)
        dim = Image.blend(self.shot, Image.new("RGB", self.shot.size, "black"), 0.45)

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{vw}x{vh}+{vx}+{vy}")
        self.canvas = tk.Canvas(self.win, cursor="cross", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.bg = ImageTk.PhotoImage(dim)
        self.bright = None
        self.canvas.create_image(0, 0, image=self.bg, anchor="nw")
        self.rect = self.canvas.create_rectangle(0, 0, 0, 0, outline="#00b7ff", width=2)
        self.crop_img = self.canvas.create_image(0, 0, anchor="nw")
        self.canvas.tag_raise(self.rect)
        self.start = None

        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda e: self._finish(None))
        self.win.bind("<ButtonPress-3>", lambda e: self._finish(None))
        self.win.focus_force()
        self.win.after(50, self.win.focus_force)

    def _box(self, e):
        x0, y0 = self.start
        return min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y)

    def _press(self, e):
        self.start = (e.x, e.y)

    def _drag(self, e):
        if not self.start:
            return
        x0, y0, x1, y1 = self._box(e)
        self.canvas.coords(self.rect, x0, y0, x1, y1)
        if x1 - x0 > 2 and y1 - y0 > 2:  # muestra la zona elegida sin oscurecer
            self.bright = ImageTk.PhotoImage(self.shot.crop((x0, y0, x1, y1)))
            self.canvas.itemconfigure(self.crop_img, image=self.bright)
            self.canvas.coords(self.crop_img, x0, y0)
            self.canvas.tag_raise(self.rect)

    def _release(self, e):
        if not self.start:
            return
        x0, y0, x1, y1 = self._box(e)
        self._finish(self.shot.crop((x0, y0, x1, y1)) if x1 - x0 > 5 and y1 - y0 > 5 else None)

    def _finish(self, img):
        self.win.destroy()
        self.on_done(img)


# --------------------------------------------------- Vigilar CODIGO --------
CODE_LEN = 12            # caracteres del código sin contar los guiones
WATCH_TIMEOUT = 15 * 60  # segundos; la vigilancia se apaga sola pasado este tiempo
WATCH_SCALE = 1.0        # sube a 1.5 o 2 si el texto es pequeño (más preciso, más lento)

# Mientras se vigila, pulsa Espacio 2 veces (pausa + play) para que el stream
# vuelva al directo y tenga el menor retraso posible.
REFRESH = True           # valor al arrancar; se cambia en el menú
REFRESH_FROM = "11:40"   # primera hora del día en que refresca
REFRESH_EVERY = 10       # minutos entre refrescos: 11:40, 11:50, 12:00...
REFRESH_GAP = 0.3        # segundos entre los dos Espacios


def next_refresh_time(now: datetime) -> datetime:
    h, m = map(int, REFRESH_FROM.split(":"))
    base = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if now <= base:
        return base
    step = timedelta(minutes=REFRESH_EVERY)
    return base + step * -(-(now - base) // step)  # redondeo hacia arriba


def refresh_stream():
    """Espacio (pausa) + Espacio (play) en la ventana activa, que debe ser el stream."""
    keyboard.send("space")
    time.sleep(REFRESH_GAP)
    keyboard.send("space")
LABEL_RE = re.compile(r"C\s*[OÓ0]\s*D\s*[IÍ1l]\s*G\s*[OÓ0]\s*[:;.]?", re.IGNORECASE)


def find_code(text: str) -> str | None:
    """Busca 'CODIGO: XXXX-XXXX-XXXX' (12 caracteres separados por guiones)."""
    for line in text.splitlines():
        for m in LABEL_RE.finditer(line):
            code, count = "", 0
            for ch in line[m.end():]:
                if ch.isalnum():
                    code += ch  # tal cual: el código puede distinguir mayúsculas
                    count += 1
                    if count == CODE_LEN:
                        break
                elif ch == "-":
                    if not code or code.endswith("-"):
                        break
                    code += "-"
                elif ch != " ":  # el OCR a veces mete espacios dentro del código
                    break
            if count == CODE_LEN and "-" in code:
                return code
    return None


def watch_region():
    """Cuadrante inferior derecho del monitor principal."""
    u = ctypes.windll.user32
    w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    return w // 2, h // 2, w, h


def prepare_fast(img: Image.Image) -> Image.Image:
    img = img.convert("L")
    if statistics.mean(img.resize((64, 36)).getdata()) < 128:
        img = ImageOps.invert(img)
    if WATCH_SCALE != 1:
        img = img.resize((int(img.width * WATCH_SCALE), int(img.height * WATCH_SCALE)))
    return img.convert("RGB")


# ----------------------------------------------------------- Config --------
def load_hotkey() -> str:
    try:
        hk = json.loads(CONFIG.read_text(encoding="utf-8")).get("hotkey") or HOTKEY
        keyboard.parse_hotkey(hk)  # valida que sea un atajo correcto
        return hk
    except Exception:
        return HOTKEY


def save_hotkey(hk: str):
    try:
        CONFIG.write_text(json.dumps({"hotkey": hk}, indent=2), encoding="utf-8")
    except OSError:
        pass


# -------------------------------------------------------------- App --------
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.events = queue.Queue()
        self.busy = False

        self.enabled = True
        self.auto_switch = AUTO_SWITCH
        self.rebinding = False
        self.watching = False
        self.refresh = REFRESH
        self.hotkey = load_hotkey()
        self._register()
        self.tray = pystray.Icon(
            "code_capture", self._icon(), self._title(),
            menu=pystray.Menu(
                pystray.MenuItem("Capturar", lambda: self.events.put("capture"), default=True),
                pystray.MenuItem(lambda item: f"Atajo activo ({self.hotkey.upper()})", self._toggle,
                                 checked=lambda item: self.enabled),
                pystray.MenuItem("Cambiar atajo...", self._rebind),
                pystray.MenuItem(f"Restablecer atajo ({HOTKEY.upper()})", self._reset_hotkey,
                                 enabled=lambda item: self.hotkey != HOTKEY),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Vigilar CODIGO: (abajo a la derecha)", self._toggle_watch,
                                 checked=lambda item: self.watching),
                pystray.MenuItem(f"Refrescar stream (Espacio x2 cada {REFRESH_EVERY} min desde {REFRESH_FROM})",
                                 self._toggle_refresh, checked=lambda item: self.refresh),
                pystray.MenuItem("Tras copiar: Alt+Tab + Ctrl+V + Enter", self._toggle_auto,
                                 checked=lambda item: self.auto_switch),
                pystray.MenuItem("Salir", lambda: self.events.put("quit")),
            ),
        )
        self.tray.run_detached()
        self.root.after(100, self._poll)
        self.notify(f"Activo. Pulsa {self.hotkey.upper()} para capturar código.")

    def _title(self):
        return f"Code Capture ({self.hotkey.upper()})"

    def _register(self):
        # suppress=True: la tecla no llega a la aplicación que tengas abierta
        keyboard.add_hotkey(self.hotkey, self._hotkey, suppress=True)

    def _unregister(self):
        try:
            keyboard.remove_hotkey(self.hotkey)
        except (KeyError, ValueError):
            pass

    def _set_hotkey(self, new):
        if self.enabled:
            self._unregister()
        self.hotkey = new
        if self.enabled:
            self._register()
        save_hotkey(new)
        self.tray.title = self._title()
        self.tray.update_menu()
        self.notify(f"Nuevo atajo: {new.upper()}")

    def _rebind(self, icon, item):
        if self.rebinding:
            return
        self.rebinding = True
        self.notify("Pulsa la tecla o combinación nueva (Esc cancela).")

        def wait_keys():
            if self.enabled:
                self._unregister()  # que el atajo actual no se dispare mientras eliges
            try:
                new = keyboard.read_hotkey(suppress=False)
            finally:
                if self.enabled:
                    self._register()
                self.rebinding = False
            if new == "esc":
                self.notify(f"Cancelado. El atajo sigue siendo {self.hotkey.upper()}.")
            else:
                self._set_hotkey(new)

        threading.Thread(target=wait_keys, daemon=True).start()

    def _reset_hotkey(self, icon, item):
        self._set_hotkey(HOTKEY)

    @staticmethod
    def _icon():
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((2, 2, 62, 62), 12, fill="#1e1e2e")
        d.text((9, 16), "</>", fill="#00b7ff", font_size=26)
        return img

    def _hotkey(self):
        if self.enabled:
            self.events.put("capture")

    def _toggle(self, icon, item):
        self.enabled = not self.enabled
        if self.enabled:
            self._register()
        else:
            self._unregister()  # libera la tecla para las demás apps
        self.notify("Atajo activado." if self.enabled else "Atajo desactivado.")

    def _toggle_watch(self, icon, item):
        if self.watching:
            self.watching = False  # el hilo lo ve y termina
            self.notify("Vigilancia detenida.")
            return
        self.watching = True
        self.notify("Vigilando la esquina inferior derecha en busca de CODIGO:")
        threading.Thread(target=self._watch_loop, daemon=True).start()

    def _watch_loop(self):
        loop = asyncio.new_event_loop()
        x0, y0, x1, y1 = watch_region()
        area = {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
        sct = mss.MSS()  # captura ~3 veces más rápida que ImageGrab
        deadline = time.monotonic() + WATCH_TIMEOUT
        last = None
        next_refresh = next_refresh_time(datetime.now())
        try:
            while self.watching:
                if self.refresh and datetime.now() >= next_refresh:
                    threading.Thread(target=refresh_stream, daemon=True).start()
                    next_refresh = next_refresh_time(datetime.now() + timedelta(seconds=1))
                if time.monotonic() > deadline:
                    self.watching = False
                    self.notify("Vigilancia apagada: no apareció ningún código.")
                    break
                g = sct.grab(area)
                shot = Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")
                thumb = shot.resize((160, 90)).tobytes()
                if thumb == last:  # la pantalla no cambió: no repite el OCR
                    time.sleep(0.02)
                    continue
                last = thumb
                result = loop.run_until_complete(_ocr_async(prepare_fast(shot)))
                code = find_code(lines_to_code(result)) if self.watching else None
                if code:
                    self.watching = False  # un solo uso: se apaga al encontrarlo
                    self.events.put(("found", code))
        except Exception as ex:
            self.watching = False
            self.notify(f"Error vigilando: {ex}")
        finally:
            loop.close()
            sct.close()
            self.tray.update_menu()

    def _toggle_refresh(self, icon, item):
        self.refresh = not self.refresh
        self.notify("Refrescar stream: " + ("activado." if self.refresh else "desactivado."))

    def _toggle_auto(self, icon, item):
        self.auto_switch = not self.auto_switch
        self.notify("Alt+Tab + Ctrl+V + Enter tras copiar: " + ("activado." if self.auto_switch else "desactivado."))

    def _send_keys(self):
        threading.Thread(target=self._key_sequence, daemon=True).start()

    @staticmethod
    def _key_sequence():
        user32 = ctypes.windll.user32
        for k in ("alt", "shift", "ctrl"):  # por si aún se mantiene pulsada alguna
            keyboard.release(k)
        before = user32.GetForegroundWindow()
        keyboard.send("alt+tab")
        # en vez de esperar un tiempo fijo, espera justo hasta que cambie la ventana
        end = time.perf_counter() + 0.6
        while user32.GetForegroundWindow() == before and time.perf_counter() < end:
            time.sleep(0.005)
        time.sleep(0.04)  # margen para que la ventana acepte teclas
        keyboard.send("ctrl+v")
        time.sleep(0.05)
        keyboard.send("enter")

    def notify(self, msg):
        try:
            self.tray.notify(msg, "Code Capture")
        except Exception:
            print(msg)

    def _poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev == "capture" and not self.busy:
                    self.busy = True
                    Selector(self.root, self._selected)
                elif isinstance(ev, tuple) and ev[0] == "found":
                    self._paste_code(ev[1])
                elif ev == "quit":
                    self.watching = False
                    self.tray.stop()
                    keyboard.unhook_all()
                    self.root.destroy()
                    return
                elif isinstance(ev, tuple) and ev[0] == "text":
                    self._copy(ev[1])
        except queue.Empty:
            pass
        self.root.after(15, self._poll)  # rápido para no retrasar el pegado del código

    def _paste_code(self, code):
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        self.root.update()
        if self.auto_switch:
            self._send_keys()  # primero las teclas, la notificación después
        self.notify(f"Código encontrado y copiado: {code}")

    def _selected(self, img):
        if img is None:
            self.busy = False
            return

        def work():
            try:
                text = recognize(img)
            except Exception as ex:
                text = None
                self.notify(f"Error de OCR: {ex}")
            self.events.put(("text", text))

        threading.Thread(target=work, daemon=True).start()

    def _copy(self, text):
        self.busy = False
        if not text:
            self.notify("No se reconoció texto en la región.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        n = text.count("\n") + 1
        self.notify(f"Copiado al portapapeles ({n} línea{'s' if n != 1 else ''}).")
        if self.auto_switch:
            self.root.after(150, self._send_keys)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:  # modo prueba: python code_capture.py imagen.png
        print(recognize(Image.open(sys.argv[1])))
    else:
        App().run()
