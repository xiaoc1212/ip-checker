"""Read Windows icons into content-addressed transparent PNGs. No executable is loaded as code."""
import ctypes
from ctypes import wintypes
import hashlib
import io
import os
from pathlib import Path
import re
import threading

from PIL import Image, ImageChops


class IconCache:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'static' / 'icons'
        self.lock = threading.Lock()
        self.memo = {}

    def extract(self, source, index=0):
        if not source:
            return ''
        source = os.path.expandvars(str(source).strip('"'))
        key = (source, index)
        with self.lock:
            if key in self.memo:
                return self.memo[key]
            result = ''
            handle = None
            import pythoncom
            pythoncom.CoInitialize()
            try:
                import win32gui
                from win32com.shell import shell, shellcon
                if not source.startswith('shell:AppsFolder\\'):
                    from launcher_store import local_path
                    local_path(source, '图标来源')
                    if not Path(source).exists():
                        return ''
                    user = ctypes.WinDLL('user32', use_last_error=True)
                    user.PrivateExtractIconsW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int,
                        ctypes.c_int, ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT), wintypes.UINT, wintypes.UINT]
                    user.PrivateExtractIconsW.restype = wintypes.UINT
                    output, icon_id = wintypes.HICON(), wintypes.UINT()
                    count = user.PrivateExtractIconsW(source, index, 128, 128, ctypes.byref(output), ctypes.byref(icon_id), 1, 0)
                    if count == 1 and output.value:
                        handle = output.value
                if not handle:
                    value = source
                    flags = shellcon.SHGFI_ICON | shellcon.SHGFI_LARGEICON
                    if source.startswith('shell:AppsFolder\\'):
                        value = shell.SHParseDisplayName(source, 0)[0]
                        flags |= shellcon.SHGFI_PIDL
                    _, info = shell.SHGetFileInfo(value, 0, flags)
                    handle = info[0]
                if handle:
                    image = self._image(handle, 128)
                    stream = io.BytesIO(); image.save(stream, format='PNG')
                    raw = stream.getvalue()
                    name = hashlib.sha256(raw).hexdigest() + '.png'
                    self.directory.mkdir(parents=True, exist_ok=True)
                    path = self.directory / name
                    if not path.exists():
                        temp = path.with_suffix('.tmp')
                        temp.write_bytes(raw); os.replace(temp, path)
                    result = 'static/icons/' + name
            except Exception:
                result = ''
            finally:
                if handle:
                    import win32gui
                    win32gui.DestroyIcon(handle)
                pythoncom.CoUninitialize()
            self.memo[key] = result
            return result

    @staticmethod
    def _image(handle, size):
        import win32gui, win32ui, win32con
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p]
        gdi.CreateDCW.restype = wintypes.HDC
        display = gdi.CreateDCW('DISPLAY', None, None, None)
        if not display:
            raise OSError('Cannot create icon rendering context')
        dc = win32ui.CreateDCFromHandle(display)
        memory = dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(dc, size, size)
        old = memory.SelectObject(bitmap)
        try:
            memory.FillSolidRect((0, 0, size, size), 0)
            win32gui.DrawIconEx(memory.GetSafeHdc(), 0, 0, handle, size, size, 0, 0, win32con.DI_NORMAL)
            black = Image.frombuffer('RGBA', (size, size), bitmap.GetBitmapBits(True), 'raw', 'BGRA', 0, 1).convert('RGB')
            memory.FillSolidRect((0, 0, size, size), 0xffffff)
            win32gui.DrawIconEx(memory.GetSafeHdc(), 0, 0, handle, size, size, 0, 0, win32con.DI_NORMAL)
            white = Image.frombuffer('RGBA', (size, size), bitmap.GetBitmapBits(True), 'raw', 'BGRA', 0, 1).convert('RGB')
            difference = ImageChops.subtract(white, black)
            r, g, b = difference.split()
            alpha = ImageChops.invert(ImageChops.lighter(ImageChops.lighter(r, g), b))
            pixels = bytearray()
            for color, a in zip(black.getdata(), alpha.getdata()):
                pixels.extend([min(255, round(channel * 255 / a)) if a else 0 for channel in color])
                pixels.append(a)
            return Image.frombytes('RGBA', (size, size), bytes(pixels))
        finally:
            memory.SelectObject(old)
            win32gui.DeleteObject(bitmap.GetHandle())
            memory.DeleteDC(); dc.DeleteDC()

    def read(self, name):
        if not re.fullmatch(r'[a-f0-9]{64}\.png', name):
            raise FileNotFoundError
        return (self.directory / name).read_bytes()
