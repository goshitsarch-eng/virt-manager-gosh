# Copyright (C) 2026 virt-manager GTK4/Adwaita port
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
GTK 4 console display widgets used when gtk-vnc / spice-gtk widgets
are not available. SPICE uses SpiceClientGLib (no GTK) and paints into
a DrawingArea. VNC uses a built-in RFB client against the same widget API
the rest of virt-manager already talks to.
"""

import io
import os
import socket
import struct
import threading

import gi
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

try:
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
except (ValueError, ImportError):  # pragma: no cover
    GdkPixbuf = None

from virtinst import log

# RFB / SPICE button bits: 1=left, 2=middle, 3=right, 4/5=wheel, 6/7=horiz
_BUTTON_BITS = {1: 1, 2: 2, 3: 4, 4: 8, 5: 16, 6: 32, 7: 64}
# spice-protocol VD_AGENT_CLIPBOARD_UTF8_TEXT
_SPICE_CLIP_UTF8 = 1
_SPICE_CLIP_SELECTION = 0
_SPICE_CLIP_PRIMARY = 1
_VNC_ENC_CORRE = 4
_VNC_ENC_TIGHTPNG = 20
# QEMU RFB client message + encoding for guest resize
_VNC_SET_DESKTOP_SIZE = 251
_VNC_ENC_DESKTOPSIZE = -223
_VNC_ENC_EXTENDED_DESKTOPSIZE = -308
_VNC_ENC_TIGHT = 7
_VNC_ENC_TRLE = 15
_VNC_ENC_ZRLE = 16
_VNC_ENC_CURSOR = -239
_VNC_ENC_XCURSOR = -232
_VNC_ENC_LASTRECT = -224
_VNC_ENC_DESKTOPNAME = -307
_VNC_ENC_QEMU_EXT_KEY = -258
_VNC_MSG_CLIENT_QEMU = 255
_VNC_QEMU_EXT_KEY = 0
_VNC_SEC_NONE = 1
_VNC_SEC_VNC = 2
_VNC_SEC_TLS = 18
_VNC_SEC_VENCRYPT = 19
_VNC_SEC_SASL = 20
_VNC_VENCRYPT_PLAIN = 256
_VNC_VENCRYPT_TLSNONE = 257
_VNC_VENCRYPT_TLSVNC = 258
_VNC_VENCRYPT_TLSPLAIN = 259
_VNC_VENCRYPT_X509NONE = 260
_VNC_VENCRYPT_X509VNC = 261
_VNC_VENCRYPT_X509PLAIN = 262
_VNC_VENCRYPT_TLSSASL = 263
_VNC_VENCRYPT_X509SASL = 264
_VNC_VENCRYPT_TLS = (
    _VNC_VENCRYPT_TLSNONE,
    _VNC_VENCRYPT_TLSVNC,
    _VNC_VENCRYPT_TLSPLAIN,
    _VNC_VENCRYPT_X509NONE,
    _VNC_VENCRYPT_X509VNC,
    _VNC_VENCRYPT_X509PLAIN,
    _VNC_VENCRYPT_TLSSASL,
    _VNC_VENCRYPT_X509SASL,
)
_VNC_VENCRYPT_PLAIN_AUTH = (
    _VNC_VENCRYPT_PLAIN,
    _VNC_VENCRYPT_TLSPLAIN,
    _VNC_VENCRYPT_X509PLAIN,
)
_VNC_VENCRYPT_VNC_AUTH = (_VNC_VENCRYPT_TLSVNC, _VNC_VENCRYPT_X509VNC)
_VNC_VENCRYPT_SASL_AUTH = (_VNC_VENCRYPT_TLSSASL, _VNC_VENCRYPT_X509SASL)
_SASL_MAX_MECHLIST = 300
_SASL_MAX_DATA = 1024 * 1024

# Linux evdev codes used by SpiceClientGLib.inputs_key_press and QEMU
# VNC extended key events. Gdk hardware keycodes on X11 are evdev + 8.
_SPICE_EVDEV = {
    Gdk.KEY_Escape: 1,
    Gdk.KEY_BackSpace: 14,
    Gdk.KEY_Tab: 15,
    Gdk.KEY_Return: 28,
    Gdk.KEY_KP_Enter: 96,
    Gdk.KEY_Control_L: 29,
    Gdk.KEY_Control_R: 97,
    Gdk.KEY_Shift_L: 42,
    Gdk.KEY_Shift_R: 54,
    Gdk.KEY_Alt_L: 56,
    Gdk.KEY_Alt_R: 100,
    Gdk.KEY_Meta_L: 125,
    Gdk.KEY_Meta_R: 126,
    Gdk.KEY_Super_L: 125,
    Gdk.KEY_Super_R: 126,
    Gdk.KEY_Menu: 127,
    Gdk.KEY_space: 57,
    Gdk.KEY_Caps_Lock: 58,
    Gdk.KEY_Num_Lock: 69,
    Gdk.KEY_Scroll_Lock: 70,
    Gdk.KEY_Print: 99,
    Gdk.KEY_Sys_Req: 99,
    Gdk.KEY_Home: 102,
    Gdk.KEY_Up: 103,
    Gdk.KEY_Page_Up: 104,
    Gdk.KEY_Left: 105,
    Gdk.KEY_Right: 106,
    Gdk.KEY_End: 107,
    Gdk.KEY_Down: 108,
    Gdk.KEY_Page_Down: 109,
    Gdk.KEY_Insert: 110,
    Gdk.KEY_Delete: 111,
    Gdk.KEY_minus: 12,
    Gdk.KEY_equal: 13,
    Gdk.KEY_bracketleft: 26,
    Gdk.KEY_bracketright: 27,
    Gdk.KEY_semicolon: 39,
    Gdk.KEY_apostrophe: 40,
    Gdk.KEY_grave: 41,
    Gdk.KEY_backslash: 43,
    Gdk.KEY_comma: 51,
    Gdk.KEY_period: 52,
    Gdk.KEY_slash: 53,
    Gdk.KEY_KP_Multiply: 55,
    Gdk.KEY_KP_Subtract: 74,
    Gdk.KEY_KP_Add: 78,
    Gdk.KEY_KP_Decimal: 83,
    Gdk.KEY_KP_Divide: 98,
}
for _i in range(1, 13):
    _SPICE_EVDEV[getattr(Gdk, "KEY_F%d" % _i)] = 58 + _i
for _digit, _code in (("1", 2), ("2", 3), ("3", 4), ("4", 5), ("5", 6),
                      ("6", 7), ("7", 8), ("8", 9), ("9", 10), ("0", 11)):
    _SPICE_EVDEV[getattr(Gdk, "KEY_%s" % _digit)] = _code
    _SPICE_EVDEV[getattr(Gdk, "KEY_KP_%s" % _digit)] = {
        "1": 79, "2": 80, "3": 81, "4": 75, "5": 76,
        "6": 77, "7": 71, "8": 72, "9": 73, "0": 82,
    }[_digit]
for _letter, _code in zip(
    "QWERTYUIOPASDFGHJKLZXCVBNM",
    (
        16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
        30, 31, 32, 33, 34, 35, 36, 37, 38,
        44, 45, 46, 47, 48, 49, 50,
    ),
):
    _SPICE_EVDEV[getattr(Gdk, "KEY_%s" % _letter)] = _code
    _SPICE_EVDEV[getattr(Gdk, "KEY_%s" % _letter.lower())] = _code


def _linux_scancode(keyval, keycode):
    mapped = _SPICE_EVDEV.get(int(keyval or 0))
    if mapped:
        return mapped
    if keycode and int(keycode) > 8:
        return int(keycode) - 8
    return int(keyval or 0)


try:
    gi.require_foreign("cairo")
    import cairo
except (ImportError, ValueError):  # pragma: no cover
    cairo = None

try:
    gi.require_version("SpiceClientGLib", "2.0")
    from gi.repository import SpiceClientGLib
except (ValueError, ImportError):  # pragma: no cover
    SpiceClientGLib = None


class GrabSequence:
    def __init__(self, keys=None):
        self._keys = list(keys or [])

    @classmethod
    def new(cls, keys):
        return cls(keys)

    def as_string(self):
        # GtkVnc.GrabSequence.as_string() uses key names so the VM
        # window title can show "Press Control_L+Alt_L to release pointer."
        names = []
        for k in self._keys:
            name = None
            try:
                name = Gdk.keyval_name(int(k))
            except Exception:
                name = None
            names.append(name or str(k))
        return "+".join(names)

    def get_keys(self):
        return list(self._keys)


class _DisplayBase(Gtk.DrawingArea):
    """
    Shared GTK4 framebuffer widget used by both VNC and SPICE viewers.
    """

    __gsignals__ = {
        "vnc-pointer-grab": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-pointer-ungrab": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-keyboard-grab": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-keyboard-ungrab": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-auth-credential": (GObject.SignalFlags.RUN_FIRST, None, [object]),
        "vnc-auth-failure": (GObject.SignalFlags.RUN_FIRST, None, [str]),
        "vnc-initialized": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-disconnected": (GObject.SignalFlags.RUN_FIRST, None, []),
        "vnc-desktop-resize": (GObject.SignalFlags.RUN_FIRST, None, [int, int]),
        "mouse-grab": (GObject.SignalFlags.RUN_FIRST, None, [bool]),
        "keyboard-grab": (GObject.SignalFlags.RUN_FIRST, None, [bool]),
        "size-allocate": (GObject.SignalFlags.RUN_FIRST, None, [object]),
    }

    scaling = GObject.Property(type=bool, default=True)
    resize_guest = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self._fb = None
        self._fb_size = (0, 0)
        self._open = False
        self._scaling = True
        self._keep_aspect = True
        self._pointer_grab = True
        self._grabbed_pointer = False
        self._grabbed_keyboard = False
        self._grab_keys = GrabSequence()
        self._force_size = False
        self._buttons = 0
        self._last_x = 0
        self._last_y = 0
        self._pressed_hwkeys = set()
        self._cursor_surface = None
        self._cursor_hot = (0, 0)
        self._cursor_pixels = None
        self.set_draw_func(self._on_draw)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)
        click = Gtk.GestureClick()
        click.set_button(0)
        click.connect("pressed", self._on_pressed)
        click.connect("released", self._on_released)
        self.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self.add_controller(keys)
        try:
            scroll = Gtk.EventControllerScroll()
            scroll.set_flags(
                Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
            )
            scroll.connect("scroll", self._on_scroll)
            self.add_controller(scroll)
        except Exception:
            pass
        try:
            self.connect("resize", self._on_widget_resize)
        except TypeError:
            pass
        self.connect("notify::scaling", self._on_scaling_prop)
        self.connect("notify::resize-guest", self._on_resize_prop)

    def _on_scaling_prop(self, *_args):
        self._scaling = bool(self.scaling)
        self.queue_draw()

    def _on_resize_prop(self, *_args):
        self._apply_resize_guest(bool(self.resize_guest))

    def _on_widget_resize(self, *args):
        self.emit("size-allocate", None)
        if self.resize_guest:
            self._apply_resize_guest(True)
        ignore = args

    def _apply_resize_guest(self, _val):
        return None

    def _update_buttons(self, button, pressed):
        bit = _BUTTON_BITS.get(int(button or 0), 0)
        if not bit:
            return
        if pressed:
            self._buttons |= bit
        else:
            self._buttons &= ~bit

    def _fb_dest_rect(self, width, height):
        fw, fh = self._fb_size
        if fw <= 0 or fh <= 0:
            return 0, 0, 0, 0
        if not self._scaling:
            return 0, 0, fw, fh
        if self._keep_aspect:
            scale = min(float(width) / fw, float(height) / fh)
            dw = fw * scale
            dh = fh * scale
            return (width - dw) / 2.0, (height - dh) / 2.0, dw, dh
        return 0, 0, width, height

    def _scale_pointer(self, x, y):
        fw, fh = self._fb_size
        dx, dy, dw, dh = self._fb_dest_rect(max(self.get_width(), 1), max(self.get_height(), 1))
        if dw <= 0 or dh <= 0 or fw <= 0 or fh <= 0:
            return 0, 0
        x = (x - dx) * fw / dw
        y = (y - dy) * fh / dh
        return max(0, min(fw - 1, int(x))), max(0, min(fh - 1, int(y)))

    def _matches_grab_sequence(self):
        keys = []
        if self._grab_keys is not None:
            keys = list(getattr(self._grab_keys, "_keys", []) or [])
        if not keys:
            return False
        return all(int(k) in self._pressed_hwkeys for k in keys)

    def _ungrab_input(self):
        if self._grabbed_pointer:
            self._grabbed_pointer = False
            self.emit("vnc-pointer-ungrab")
            self.emit("mouse-grab", False)
        if self._grabbed_keyboard:
            self._grabbed_keyboard = False
            self.emit("vnc-keyboard-ungrab")
            self.emit("keyboard-grab", False)

    def _on_draw(self, _area, cr, width, height, _data=None):
        if cairo is None or self._fb is None:
            cr.set_source_rgb(0, 0, 0)
            cr.rectangle(0, 0, width, height)
            cr.fill()
            return
        fw, fh = self._fb_size
        if fw <= 0 or fh <= 0:
            return
        cr.set_source_rgb(0, 0, 0)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        dx, dy, dw, dh = self._fb_dest_rect(width, height)
        if dw <= 0 or dh <= 0:
            return
        cr.save()
        cr.translate(dx, dy)
        cr.scale(dw / fw, dh / fh)
        cr.set_source_surface(self._fb, 0, 0)
        cr.paint()
        if self._cursor_surface is not None:
            fb_x, fb_y = self._scale_pointer(self._last_x, self._last_y)
            hx, hy = self._cursor_hot
            cr.set_source_surface(self._cursor_surface, fb_x - hx, fb_y - hy)
            cr.paint()
        cr.restore()

    def _set_framebuffer(self, surface, width, height):
        changed = self._fb_size != (width, height)
        self._fb = surface
        self._fb_size = (width, height)
        if self._force_size and not self._scaling:
            self.set_content_width(width)
            self.set_content_height(height)
        self.queue_draw()
        if changed:
            self.emit("vnc-desktop-resize", width, height)

    def _on_motion(self, _c, x, y):
        self._last_x, self._last_y = x, y
        self._send_pointer(x, y, 0, False)
        if self._cursor_surface is not None:
            self.queue_draw()

    def _on_pressed(self, gest, _n, x, y):
        self._last_x, self._last_y = x, y
        self._send_pointer(x, y, gest.get_current_button(), True)
        self.grab_focus()
        if self._pointer_grab and not self._grabbed_pointer:
            self._grabbed_pointer = True
            self.emit("vnc-pointer-grab")
            self.emit("mouse-grab", True)

    def _on_released(self, gest, _n, x, y):
        self._last_x, self._last_y = x, y
        self._send_pointer(x, y, gest.get_current_button(), False)

    def _on_scroll(self, _c, dx, dy):
        if dy < 0:
            button = 4
        elif dy > 0:
            button = 5
        elif dx < 0:
            button = 6
        elif dx > 0:
            button = 7
        else:
            return False
        self._send_pointer(self._last_x, self._last_y, button, True)
        self._send_pointer(self._last_x, self._last_y, button, False)
        return True

    def _on_key_pressed(self, _c, keyval, keycode, state):
        ignore = state
        # Preferences store Gdk keyvals (65507=Control_L). GTK 4 key
        # events also report hardware keycodes (37). Track both so the
        # default Ctrl+Alt grab release matches GtkVnc.
        if keycode:
            self._pressed_hwkeys.add(int(keycode))
        if keyval:
            self._pressed_hwkeys.add(int(keyval))
        if self._matches_grab_sequence() and (self._grabbed_pointer or self._grabbed_keyboard):
            self._ungrab_input()
            return True
        self._send_key(keyval, keycode, True)
        if not self._grabbed_keyboard:
            self._grabbed_keyboard = True
            self.emit("vnc-keyboard-grab")
            self.emit("keyboard-grab", True)
        return True

    def _on_key_released(self, _c, keyval, keycode, state):
        ignore = state
        if keycode:
            self._pressed_hwkeys.discard(int(keycode))
        if keyval:
            self._pressed_hwkeys.discard(int(keyval))
        self._send_key(keyval, keycode, False)
        return True

    def _send_pointer(self, x, y, button, pressed=False):
        raise NotImplementedError

    def _send_key(self, keyval, keycode, pressed):
        raise NotImplementedError

    def set_pointer_grab(self, val):
        self._pointer_grab = bool(val)

    def set_keep_aspect_ratio(self, val):
        self._keep_aspect = bool(val)
        self.queue_draw()

    def get_keep_aspect_ratio(self):
        return bool(self._keep_aspect)

    def set_scaling(self, val):
        self.scaling = bool(val)
        self._scaling = bool(val)
        self.queue_draw()

    def get_scaling(self):
        return self._scaling

    def set_force_size(self, val):
        self._force_size = bool(val)

    def set_allow_resize(self, val):
        self.set_property("resize-guest", bool(val))

    def get_allow_resize(self):
        return bool(self.get_property("resize-guest"))

    def set_grab_keys(self, seq):
        self._grab_keys = seq or GrabSequence()

    def get_grab_keys(self):
        return self._grab_keys

    def send_keys(self, keyvals, _event=None):
        for keyval in keyvals:
            self._send_key(keyval, 0, True)
        for keyval in reversed(keyvals):
            self._send_key(keyval, 0, False)

    def get_pixbuf(self):
        """
        Return a GdkPixbuf.Pixbuf of the current framebuffer so
        Virtual Machine -> Take Screenshot can call save_to_bufferv("png").
        """
        if GdkPixbuf is None:
            return None
        surface = self._fb
        w, h = self._fb_size
        if surface is None and cairo is not None:
            pixels = getattr(self, "_pixels", None)
            if pixels and w > 0 and h > 0:
                try:
                    surface = cairo.ImageSurface.create_for_data(
                        memoryview(pixels), cairo.FORMAT_ARGB32, w, h, w * 4
                    )
                except Exception:
                    surface = None
        if surface is None or w <= 0 or h <= 0:
            return None
        try:
            if hasattr(surface, "flush"):
                surface.flush()
            buf = io.BytesIO()
            surface.write_to_png(buf)
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(buf.getvalue())
            loader.close()
            return loader.get_pixbuf()
        except Exception as exc:
            log.debug("get_pixbuf failed: %s", exc)
            return None

    def get_preferred_width(self):
        return self._fb_size[0], self._fb_size[0]

    def get_preferred_height(self):
        return self._fb_size[1], self._fb_size[1]

    def is_open(self):
        return self._open

    def close(self):
        self._open = False


class VNCDisplay(_DisplayBase):
    """
    RFB/VNC client painted on a GTK 4 DrawingArea.

    Supports None, VNC-auth, VeNCrypt (including SASL subtypes), RFB SASL
    (PLAIN), and TLS; 32-bit pixels; and the encodings QEMU commonly
    sends: raw, CopyRect, RRE, Hextile, zlib, Tight, ZRLE, DesktopSize,
    and cursor.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._sock = None
        self._thread = None
        self._lock = threading.Lock()
        self._username = ""
        self._password = ""
        self._clientname = "libvirt-vnc"
        self._name = ""
        self._clip_from_guest = False
        self._bind_host_clipboard()
        self._stop = False
        self._auth_event = threading.Event()
        self._pixels = bytearray()
        self._zdec = None
        self._tight_z = [None, None, None, None]
        self._zrle_z = None
        self._buttons = 0
        self._qemu_ext_key = False
        self._shared = True
        self._bells = 0
        self._tls_ca = ""
        self._tls_client_cert = ""
        self._tls_client_key = ""
        self._host = ""

    def set_credential(self, cred, value):
        name = str(cred).upper()
        if cred == 1 or name.endswith("PASSWORD"):
            self._password = value or ""
        elif "CA" in name or name.endswith("CACERT") or name.endswith("CA_CERT"):
            self._tls_ca = value or ""
        elif "KEY" in name:
            self._tls_client_key = value or ""
        elif "CERT" in name and "CA" not in name:
            self._tls_client_cert = value or ""
        elif cred == 2 or name.endswith("CLIENTNAME") or name.endswith("CLIENT_NAME"):
            self._clientname = value or "libvirt-vnc"
        else:
            self._username = value or ""
        self._auth_event.set()

    def open_host(self, host, port):
        self._start_thread(lambda: self._connect_host(host, int(port)))

    def open_fd(self, fd):
        self._start_thread(lambda: self._connect_fd(fd))

    def close(self):
        self._stop = True
        self._open = False
        try:
            self._auth_event.set()
        except Exception:
            pass
        sock = self._sock
        if sock:
            try:
                sock.close()
            except Exception:
                pass
        self._sock = None

    def set_shared_flag(self, val):
        self._shared = bool(val)

    def get_shared_flag(self):
        return bool(getattr(self, "_shared", True))

    def _ring_bell(self):
        self._bells = getattr(self, "_bells", 0) + 1
        try:
            display = Gdk.Display.get_default()
            if display is not None:
                display.beep()
        except Exception:
            pass
        return False

    def _apply_resize_guest(self, val):
        if val:
            self._send_desktop_size()

    def _send_desktop_size(self, width=None, height=None):
        sock = self._sock
        if not sock or not self._open or not self.resize_guest:
            return
        w = int(width if width is not None else max(self.get_width(), 1))
        h = int(height if height is not None else max(self.get_height(), 1))
        if w < 16 or h < 16:
            return
        try:
            # QEMU / TigerVNC SetDesktopSize (client msg 251)
            sock.sendall(struct.pack("!BBHH", _VNC_SET_DESKTOP_SIZE, 1, w, h))
            sock.sendall(struct.pack("!IHHHHI", 0, 0, 0, w, h, 0))
        except Exception:
            pass

    def _send_pointer(self, x, y, button, pressed=False):
        if button:
            self._update_buttons(button, pressed)
        sock = self._sock
        if not sock or not self._open:
            return
        x, y = self._scale_pointer(x, y)
        try:
            sock.sendall(struct.pack("!BBHH", 5, self._buttons, x, y))
        except Exception:
            pass

    def _send_key(self, keyval, keycode, pressed):
        sock = self._sock
        if not sock or not self._open:
            return
        try:
            if self._qemu_ext_key:
                scancode = _linux_scancode(keyval, keycode)
                sock.sendall(
                    struct.pack(
                        "!BBHII",
                        _VNC_MSG_CLIENT_QEMU,
                        _VNC_QEMU_EXT_KEY,
                        1 if pressed else 0,
                        int(keyval or 0),
                        int(scancode),
                    )
                )
            else:
                sock.sendall(struct.pack("!BBxxI", 4, 1 if pressed else 0, int(keyval or 0)))
        except Exception:
            pass

    def _start_thread(self, fn):
        self._stop = False
        self._thread = threading.Thread(target=self._run, args=(fn,), daemon=True)
        self._thread.start()

    def _run(self, fn):
        try:
            fn()
        except Exception as exc:
            log.debug("VNC client error: %s", exc, exc_info=True)
            GLib.idle_add(self.emit, "vnc-disconnected")
            self._open = False

    def _connect_fd(self, fd):
        sock = socket.socket(fileno=int(fd))
        self._handshake(sock)

    def _connect_host(self, host, port):
        self._host = host or ""
        sock = socket.create_connection((host, port), timeout=15)
        self._handshake(sock)

    def _recv_n(self, sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise EOFError("VNC connection closed")
            buf += chunk
        return buf

    def _handshake(self, sock):
        self._sock = sock
        sock.settimeout(30)
        ver = self._recv_n(sock, 12)
        sock.sendall(b"RFB 003.008\n")
        ntypes = self._recv_n(sock, 1)[0]
        types = self._recv_n(sock, ntypes)
        try:
            open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                "vnc-sec-types %s\n" % list(types)
            )
        except Exception:
            pass
        if _VNC_SEC_VNC in types:
            sock.sendall(bytes([_VNC_SEC_VNC]))
            self._vnc_auth2(sock)
        elif _VNC_SEC_VENCRYPT in types:
            sock.sendall(bytes([_VNC_SEC_VENCRYPT]))
            sock = self._vencrypt(sock)
            self._sock = sock
        elif _VNC_SEC_SASL in types:
            sock.sendall(bytes([_VNC_SEC_SASL]))
            self._vnc_sasl(sock)
        elif _VNC_SEC_TLS in types:
            sock.sendall(bytes([_VNC_SEC_TLS]))
            sock = self._wrap_tls(sock, verify=bool(self._tls_ca_file()))
            self._sock = sock
        elif _VNC_SEC_NONE in types:
            sock.sendall(bytes([_VNC_SEC_NONE]))
        else:
            raise RuntimeError("Unsupported VNC security types: %s" % list(types))
        result = struct.unpack("!I", self._recv_n(sock, 4))[0]
        if result != 0:
            GLib.idle_add(self.emit, "vnc-auth-failure", "VNC authentication failed")
            # Do not raise: the except path emits vnc-disconnected, which
            # cleans up the viewer before vnc-auth-failure is delivered.
            return
        sock.sendall(struct.pack("!B", 1 if getattr(self, "_shared", True) else 0))
        width, height, _ppf = struct.unpack("!HH16s", self._recv_n(sock, 20))
        namelen = struct.unpack("!I", self._recv_n(sock, 4))[0]
        self._name = self._recv_n(sock, namelen).decode("utf-8", "replace")
        # SetPixelFormat: 32-bit little-endian true-colour (20 bytes)
        sock.sendall(
            struct.pack(
                "!BxxxBBBBHHHBBBxxx",
                0,  # type
                32,  # bits-per-pixel
                24,  # depth
                0,  # big-endian-flag
                1,  # true-colour-flag
                255,
                255,
                255,  # red/green/blue max
                16,
                8,
                0,  # red/green/blue shift
            )
        )
        # SetEncodings: nEncodings is U16. Advertise common QEMU encodings.
        encodings = (
            _VNC_ENC_QEMU_EXT_KEY,
            _VNC_ENC_LASTRECT,
            _VNC_ENC_DESKTOPNAME,
            _VNC_ENC_DESKTOPSIZE,
            _VNC_ENC_EXTENDED_DESKTOPSIZE,
            _VNC_ENC_CURSOR,
            _VNC_ENC_XCURSOR,
            0,  # raw first: Tight/ZRLE decode errors were dropping the session
            1,  # CopyRect
            2,  # RRE
            5,  # hextile
            6,  # zlib
            _VNC_ENC_CORRE,
            _VNC_ENC_TRLE,
            _VNC_ENC_ZRLE,
            _VNC_ENC_TIGHT,
            _VNC_ENC_TIGHTPNG,
        )
        self._qemu_ext_key = True
        sock.sendall(struct.pack("!BBH", 2, 0, len(encodings)))
        for enc in encodings:
            sock.sendall(struct.pack("!i", enc))
        self._alloc_pixels(width, height)
        self._open = True
        GLib.idle_add(self.emit, "vnc-initialized")
        GLib.idle_add(self.emit, "vnc-desktop-resize", width, height)
        self._request_update(sock, width, height)
        sock.settimeout(0.25)
        while not self._stop:
            try:
                msg = sock.recv(1)
            except socket.timeout:
                continue
            if not msg:
                break
            if msg[0] == 0:
                try:
                    width, height = self._read_fb_update(sock, width, height)
                except Exception as exc:
                    log.debug("VNC framebuffer update failed: %s", exc, exc_info=True)
                    self._request_update(sock, width, height)
                    continue
            elif msg[0] == 1:
                self._recv_n(sock, 3)
                n = struct.unpack("!H", self._recv_n(sock, 2))[0]
                self._recv_n(sock, n * 6)
            elif msg[0] == 2:
                self._recv_n(sock, 5)
                GLib.idle_add(self._ring_bell)
            elif msg[0] == 3:
                slen = struct.unpack("!xxxI", self._recv_n(sock, 7))[0]
                text = self._recv_n(sock, slen)
                self._apply_server_cut_text(text)
        GLib.idle_add(self.emit, "vnc-disconnected")
        self._open = False

    def _need_vnc_creds(self, username=False):
        class _Creds:
            def __init__(self, values):
                self._values = values
                self.n_values = len(values)

            def get_nth(self, idx):
                return self._values[idx]

        values = [1]
        if username:
            values = [0, 1]
        if (username and not self._username) or not self._password:
            try:
                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                    "vnc-need-creds username=%s\n" % username
                )
            except Exception:
                pass
            self._auth_event.clear()
            GLib.idle_add(self.emit, "vnc-auth-credential", _Creds(values))
            self._auth_event.wait(30)

    def _vnc_auth2(self, sock):
        challenge = self._recv_n(sock, 16)
        self._need_vnc_creds(False)
        sock.sendall(_vnc_auth_response(challenge, self._password))

    def _choose_vencrypt_subtype(self, subtypes):
        prefer = (
            _VNC_VENCRYPT_PLAIN,
            _VNC_VENCRYPT_TLSPLAIN,
            _VNC_VENCRYPT_TLSVNC,
            _VNC_VENCRYPT_TLSNONE,
            _VNC_VENCRYPT_X509PLAIN,
            _VNC_VENCRYPT_X509VNC,
            _VNC_VENCRYPT_X509NONE,
            _VNC_VENCRYPT_TLSSASL,
            _VNC_VENCRYPT_X509SASL,
        )
        for cand in prefer:
            if cand in subtypes:
                return cand
        return None

    def _sasl_choose_mech(self, mechlist):
        mechs = [m.strip() for m in str(mechlist or "").split(",") if m.strip()]
        for cand in ("PLAIN", "DIGEST-MD5"):
            if cand in mechs:
                return cand
        return None

    def _sasl_plain_clientout(self):
        user = (self._username or "").encode("utf-8")
        pw = (self._password or "").encode("utf-8")
        return b"\x00" + user + b"\x00" + pw

    def _sasl_write_payload(self, sock, payload):
        if payload is None:
            sock.sendall(struct.pack("!I", 0))
            return
        sock.sendall(struct.pack("!I", len(payload) + 1) + payload + b"\x00")

    def _sasl_read_server(self, sock):
        slen = struct.unpack("!I", self._recv_n(sock, 4))[0]
        if slen > _SASL_MAX_DATA:
            raise RuntimeError("SASL negotiation data too long: %s" % slen)
        data = b""
        if slen:
            data = self._recv_n(sock, slen)
            if data.endswith(b"\x00"):
                data = data[:-1]
        complete = self._recv_n(sock, 1)[0]
        return data, complete

    def _vnc_sasl(self, sock):
        """RFB security type 20 / VeNCrypt *SASL. GtkVnc PLAIN wire format."""
        mechlistlen = struct.unpack("!I", self._recv_n(sock, 4))[0]
        if mechlistlen > _SASL_MAX_MECHLIST:
            raise RuntimeError("SASL mechlist too long")
        mechlist = self._recv_n(sock, mechlistlen).decode("ascii", "replace")
        chosen = self._sasl_choose_mech(mechlist)
        if chosen is None:
            raise RuntimeError("SASL mechanisms unsupported: %s" % mechlist)
        self._need_vnc_creds(True)
        if chosen != "PLAIN":
            raise RuntimeError("SASL mechanism %s is not supported" % chosen)
        clientout = self._sasl_plain_clientout()
        sock.sendall(struct.pack("!I", len(chosen)) + chosen.encode("ascii"))
        self._sasl_write_payload(sock, clientout)
        _serverin, complete = self._sasl_read_server(sock)
        # PLAIN finishes in one client start. If the server is not done,
        # send an empty step like gtk-vnc's client-step loop.
        if not complete:
            self._sasl_write_payload(sock, None)
            _serverin, complete = self._sasl_read_server(sock)
        if not complete:
            raise RuntimeError("SASL negotiation did not complete")

    def _tls_ca_file(self):
        return (
            getattr(self, "_tls_ca", None)
            or os.environ.get("VNC_TLS_CA")
            or os.environ.get("SSL_CERT_FILE")
            or ""
        )

    def _wrap_tls(self, sock, verify=False):
        import ssl

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ca = self._tls_ca_file()
        cert = getattr(self, "_tls_client_cert", None) or os.environ.get("VNC_TLS_CERT")
        key = getattr(self, "_tls_client_key", None) or os.environ.get("VNC_TLS_KEY")
        do_verify = bool(verify or ca)
        if do_verify:
            ctx.verify_mode = ssl.CERT_REQUIRED
            try:
                if ca:
                    ctx.load_verify_locations(cafile=ca)
                else:
                    ctx.load_default_certs()
            except Exception:
                ctx.verify_mode = ssl.CERT_NONE
        else:
            ctx.verify_mode = ssl.CERT_NONE
        if cert:
            try:
                ctx.load_cert_chain(cert, key or None)
            except Exception:
                pass
        return ctx.wrap_socket(sock, server_hostname=self._host or None)

    def _send_plain_creds(self, sock):
        self._need_vnc_creds(True)
        user = (self._username or "").encode("utf-8")
        pw = (self._password or "").encode("utf-8")
        sock.sendall(struct.pack("!II", len(user), len(pw)) + user + pw)

    def _vencrypt(self, sock):
        # VeNCrypt 0.2: Plain, TLS/X509 + None/VNC/Plain, or TLS/X509 + SASL.
        _maj, _min = self._recv_n(sock, 2)
        sock.sendall(b"\x00\x02")
        ack = self._recv_n(sock, 1)[0]
        if ack != 0:
            raise RuntimeError("VeNCrypt version rejected")
        nsub = self._recv_n(sock, 1)[0]
        subtypes = []
        for _ in range(nsub):
            subtypes.append(struct.unpack("!I", self._recv_n(sock, 4))[0])
        chosen = self._choose_vencrypt_subtype(subtypes)
        if chosen is None:
            raise RuntimeError("VeNCrypt subtypes unsupported: %s" % subtypes)
        sock.sendall(struct.pack("!I", chosen))
        # QEMU writes 1 to accept the subtype and 0 to reject (the
        # version ack above is the opposite: 0 means version accepted).
        suback = self._recv_n(sock, 1)[0]
        if suback == 0:
            raise RuntimeError("VeNCrypt subtype rejected")
        if chosen in _VNC_VENCRYPT_TLS:
            sock = self._wrap_tls(sock, verify=bool(self._tls_ca_file()))
        if chosen in _VNC_VENCRYPT_PLAIN_AUTH:
            self._send_plain_creds(sock)
        elif chosen in _VNC_VENCRYPT_VNC_AUTH:
            self._vnc_auth2(sock)
        elif chosen in _VNC_VENCRYPT_SASL_AUTH:
            self._vnc_sasl(sock)
        return sock

    def _vencrypt_plain(self, sock):
        return self._vencrypt(sock)

    def _apply_server_cut_text(self, raw):
        text = raw.decode("latin1", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        self._clip_from_guest = True
        try:
            display = Gdk.Display.get_default()
            display.get_clipboard().set(text)
            if hasattr(display, "get_primary_clipboard"):
                display.get_primary_clipboard().set(text)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-clipboard.txt", "w").write(text)
        except Exception:
            pass
        GLib.timeout_add(250, self._clear_vnc_clip_from_guest)

    def _clear_vnc_clip_from_guest(self):
        self._clip_from_guest = False
        return False

    def _send_client_cut_text(self, text):
        sock = self._sock
        if not sock or not self._open or self._clip_from_guest:
            return
        payload = (text or "").encode("latin1", "replace")
        try:
            sock.sendall(struct.pack("!BxxxI", 6, len(payload)) + payload)
        except Exception:
            pass

    def _bind_host_clipboard(self):
        try:
            clip = Gdk.Display.get_default().get_clipboard()
            clip.connect("changed", self._on_host_clip_changed)
        except Exception:
            pass

    def _on_host_clip_changed(self, clip):
        if self._clip_from_guest or not self._open:
            return

        def _got(_src, result):
            try:
                text = clip.read_text_finish(result)
            except Exception:
                return
            if text:
                self._send_client_cut_text(text)

        try:
            clip.read_text_async(None, _got)
        except Exception:
            pass

    def _alloc_pixels(self, width, height):
        self._pixels = bytearray(max(width, 1) * max(height, 1) * 4)
        self._fb_size = (width, height)

    def _request_update(self, sock, width, height):
        sock.sendall(struct.pack("!BBHHHH", 3, 0, 0, 0, width, height))

    def _blit_raw(self, width, x, y, w, h, raw):
        for row in range(h):
            src = row * w * 4
            dst = ((y + row) * width + x) * 4
            self._pixels[dst : dst + w * 4] = raw[src : src + w * 4]

    def _fill_rect(self, width, x, y, w, h, pixel):
        rowbytes = pixel * w
        for row in range(h):
            dst = ((y + row) * width + x) * 4
            self._pixels[dst : dst + w * 4] = rowbytes

    def _copy_rect(self, width, height, x, y, w, h, srcx, srcy):
        src = bytearray(self._pixels)
        for row in range(h):
            s = ((srcy + row) * width + srcx) * 4
            d = ((y + row) * width + x) * 4
            if s < 0 or d < 0:
                continue
            self._pixels[d : d + w * 4] = src[s : s + w * 4]
        ignore = height

    def _read_hextile(self, sock, width, x, y, w, h):
        bg = b"\x00\x00\x00\x00"
        fg = b"\x00\x00\x00\x00"
        for ty in range(y, y + h, 16):
            th = min(16, y + h - ty)
            for tx in range(x, x + w, 16):
                tw = min(16, x + w - tx)
                sub = self._recv_n(sock, 1)[0]
                raw = bool(sub & 1)
                if sub & 2:
                    bg = self._recv_n(sock, 4)
                if sub & 4:
                    fg = self._recv_n(sock, 4)
                if raw:
                    self._blit_raw(width, tx, ty, tw, th, self._recv_n(sock, tw * th * 4))
                    continue
                self._fill_rect(width, tx, ty, tw, th, bg)
                if not (sub & 8):
                    continue
                nsub = self._recv_n(sock, 1)[0]
                coloured = bool(sub & 16)
                for _ in range(nsub):
                    pix = self._recv_n(sock, 4) if coloured else fg
                    xy = self._recv_n(sock, 1)[0]
                    wh = self._recv_n(sock, 1)[0]
                    sx = tx + ((xy >> 4) & 0xF)
                    sy = ty + (xy & 0xF)
                    sw = ((wh >> 4) & 0xF) + 1
                    sh = (wh & 0xF) + 1
                    self._fill_rect(width, sx, sy, sw, sh, pix)

    def _read_zlib(self, sock, width, x, y, w, h):
        import zlib

        n = struct.unpack("!I", self._recv_n(sock, 4))[0]
        if self._zdec is None:
            self._zdec = zlib.decompressobj()
        raw = self._zdec.decompress(self._recv_n(sock, n))
        self._blit_raw(width, x, y, w, h, raw)

    def _read_rre(self, sock, width, x, y, w, h):
        nsub = struct.unpack("!I", self._recv_n(sock, 4))[0]
        bg = self._recv_n(sock, 4)
        self._fill_rect(width, x, y, w, h, bg)
        for _ in range(nsub):
            pix = self._recv_n(sock, 4)
            sx, sy, sw, sh = struct.unpack("!HHHH", self._recv_n(sock, 8))
            self._fill_rect(width, x + sx, y + sy, sw, sh, pix)

    def _read_corre(self, sock, width, x, y, w, h):
        nsub = struct.unpack("!I", self._recv_n(sock, 4))[0]
        bg = self._recv_n(sock, 4)
        self._fill_rect(width, x, y, w, h, bg)
        for _ in range(nsub):
            pix = self._recv_n(sock, 4)
            sx, sy, sw, sh = struct.unpack("!BBBB", self._recv_n(sock, 4))
            self._fill_rect(width, x + sx, y + sy, sw, sh, pix)

    def _tight_compact_len(self, sock):
        b0 = self._recv_n(sock, 1)[0]
        if b0 < 128:
            return b0
        b1 = self._recv_n(sock, 1)[0]
        if b1 < 128:
            return (b0 & 0x7F) | (b1 << 7)
        b2 = self._recv_n(sock, 1)[0]
        return (b0 & 0x7F) | ((b1 & 0x7F) << 7) | (b2 << 14)

    def _cpixel_to_bgra(self, rgb):
        if len(rgb) >= 4:
            return rgb[:4]
        r, g, b = rgb[0], rgb[1], rgb[2]
        return bytes((b, g, r, 0))

    def _tight_read_data(self, sock, stream_id, expect):
        if expect < 12:
            return self._recv_n(sock, expect)
        n = self._tight_compact_len(sock)
        rawz = self._recv_n(sock, n)
        import zlib

        if self._tight_z[stream_id] is None:
            self._tight_z[stream_id] = zlib.decompressobj()
        return self._tight_z[stream_id].decompress(rawz)

    def _blit_rgb24(self, width, x, y, w, h, data):
        need = w * h * 3
        if len(data) < need:
            return
        row = bytearray()
        for i in range(0, need, 3):
            row.extend(self._cpixel_to_bgra(data[i : i + 3]))
        self._blit_raw(width, x, y, w, h, bytes(row))

    def _read_tight_palette(self, sock, width, x, y, w, h, stream_id):
        # Tight palette is 1 bit/pixel when there are two colors, else
        # 8 bits/pixel. This is not the ZRLE 1/2/4/8 packing.
        ncolors = self._recv_n(sock, 1)[0] + 1
        palette = [self._cpixel_to_bgra(self._recv_n(sock, 3)) for _ in range(ncolors)]
        if ncolors <= 2:
            rowsize = (w + 7) // 8
            data = self._tight_read_data(sock, stream_id, rowsize * h)
            for row in range(h):
                packed = data[row * rowsize : (row + 1) * rowsize]
                for col in range(w):
                    if col // 8 >= len(packed):
                        break
                    idx = (packed[col // 8] >> (7 - (col % 8))) & 1
                    pix = palette[idx] if idx < len(palette) else palette[0]
                    self._fill_rect(width, x + col, y + row, 1, 1, pix)
            return
        data = self._tight_read_data(sock, stream_id, w * h)
        for i, idx in enumerate(data[: w * h]):
            pix = palette[idx] if idx < len(palette) else palette[0]
            self._fill_rect(width, x + (i % w), y + (i // w), 1, 1, pix)

    def _read_tight_gradient(self, sock, width, x, y, w, h, stream_id):
        # Tight gradient: each RGB sample is a delta from
        # left + above - above-left, matching TigerVNC FilterGradient24.
        expect = w * h * 3
        data = self._tight_read_data(sock, stream_id, expect)
        if len(data) < expect:
            return
        prev = [0] * (w * 3)
        out = bytearray()
        for row in range(h):
            this = [0] * (w * 3)
            for col in range(w):
                src = (row * w + col) * 3
                for c in range(3):
                    if col == 0:
                        val = (prev[c] + data[src + c]) & 0xFF
                    else:
                        est = (
                            int(prev[col * 3 + c])
                            + int(this[(col - 1) * 3 + c])
                            - int(prev[(col - 1) * 3 + c])
                        )
                        est = 0 if est < 0 else 255 if est > 255 else est
                        val = (est + data[src + c]) & 0xFF
                    this[col * 3 + c] = val
                out.extend(self._cpixel_to_bgra(bytes(this[col * 3 : col * 3 + 3])))
            prev = this
        self._blit_raw(width, x, y, w, h, bytes(out))

    def _read_tight(self, sock, width, x, y, w, h):
        ctrl = self._recv_n(sock, 1)[0]
        for stream in range(4):
            if ctrl & (1 << stream):
                self._tight_z[stream] = None
        # After the four reset bits, bits 4-5 are the zlib stream and
        # bit 6 means a filter-id byte follows. JPEG/PNG/fill use the
        # remaining high values 0x08-0x0A.
        kind = ctrl >> 4
        if kind == 0x08:
            pix = self._cpixel_to_bgra(self._recv_n(sock, 3))
            self._fill_rect(width, x, y, w, h, pix)
            return
        if kind in (0x09, 0x0A):
            n = self._tight_compact_len(sock)
            payload = self._recv_n(sock, n)
            if GdkPixbuf is None:
                return
            try:
                loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg" if kind == 0x09 else "png")
                loader.write(payload)
                loader.close()
                pixbuf = loader.get_pixbuf()
                if pixbuf is None:
                    return
                raw = pixbuf.get_pixels()
                rowstride = pixbuf.get_rowstride()
                nch = pixbuf.get_n_channels()
                for row in range(min(h, pixbuf.get_height())):
                    src = row * rowstride
                    dst = ((y + row) * width + x) * 4
                    for col in range(min(w, pixbuf.get_width())):
                        i = src + col * nch
                        r, g, b = raw[i], raw[i + 1], raw[i + 2]
                        self._pixels[dst + col * 4 : dst + col * 4 + 4] = bytes((b, g, r, 0))
            except Exception:
                pass
            return
        stream_id = kind & 0x03
        filt = 0
        if kind & 0x04:
            filt = self._recv_n(sock, 1)[0]
        if filt == 1:
            self._read_tight_palette(sock, width, x, y, w, h, stream_id)
            return
        if filt == 2:
            self._read_tight_gradient(sock, width, x, y, w, h, stream_id)
            return
        data = self._tight_read_data(sock, stream_id, w * h * 3)
        if len(data) >= w * h * 3:
            self._blit_rgb24(width, x, y, w, h, data)

    def _decode_rle_tiles(self, width, x, y, w, h, take):
        # Shared ZRLE/TRLE 64x64 tile decoder. `take(n)` returns n bytes.
        for ty in range(y, y + h, 64):
            th = min(64, y + h - ty)
            for tx in range(x, x + w, 64):
                tw = min(64, x + w - tx)
                raw = take(1)
                if not raw:
                    return
                sub = raw[0]
                if sub == 0:
                    self._blit_raw(width, tx, ty, tw, th, take(tw * th * 4))
                elif sub == 1:
                    pix = take(4)
                    self._fill_rect(width, tx, ty, tw, th, pix)
                elif 2 <= sub <= 16:
                    palette = [take(4) for _ in range(sub)]
                    bits = 1 if sub <= 2 else 2 if sub <= 4 else 4
                    for row in range(th):
                        packed = take((tw * bits + 7) // 8)
                        bitpos = 0
                        for col in range(tw):
                            byte = packed[bitpos // 8]
                            shift = 8 - bits - (bitpos % 8)
                            idx = (byte >> shift) & ((1 << bits) - 1)
                            bitpos += bits
                            pix = palette[idx] if idx < len(palette) else palette[0]
                            self._fill_rect(width, tx + col, ty + row, 1, 1, pix)
                elif sub == 128:
                    count = 0
                    while count < tw * th:
                        pix = take(4)
                        run = 1
                        while True:
                            b = take(1)[0]
                            run += b
                            if b != 255:
                                break
                        for _ in range(run):
                            col = count % tw
                            row = count // tw
                            self._fill_rect(width, tx + col, ty + row, 1, 1, pix)
                            count += 1
                            if count >= tw * th:
                                break
                elif 130 <= sub <= 255:
                    ncolors = sub - 128
                    palette = [take(4) for _ in range(ncolors)]
                    count = 0
                    while count < tw * th:
                        idx = take(1)[0]
                        run = 1
                        if idx & 0x80:
                            idx &= 0x7F
                            while True:
                                b = take(1)[0]
                                run += b
                                if b != 255:
                                    break
                        pix = palette[idx] if idx < len(palette) else palette[0]
                        for _ in range(run):
                            col = count % tw
                            row = count // tw
                            self._fill_rect(width, tx + col, ty + row, 1, 1, pix)
                            count += 1
                            if count >= tw * th:
                                break

    def _read_zrle(self, sock, width, x, y, w, h):
        import zlib

        n = struct.unpack("!I", self._recv_n(sock, 4))[0]
        rawz = self._recv_n(sock, n)
        if self._zrle_z is None:
            self._zrle_z = zlib.decompressobj()
        try:
            data = self._zrle_z.decompress(rawz)
        except Exception:
            self._zrle_z = zlib.decompressobj()
            data = self._zrle_z.decompress(rawz)
        pos = 0

        def _take(n):
            nonlocal pos
            out = data[pos : pos + n]
            pos += n
            return out

        self._decode_rle_tiles(width, x, y, w, h, _take)

    def _read_trle(self, sock, width, x, y, w, h):
        def _take(n):
            return self._recv_n(sock, n)

        self._decode_rle_tiles(width, x, y, w, h, _take)

    def _read_cursor(self, sock, hotx, hoty, w, h):
        raw = self._recv_n(sock, max(w, 0) * max(h, 0) * 4)
        mask = self._recv_n(sock, ((max(w, 0) + 7) // 8) * max(h, 0))
        if w <= 0 or h <= 0 or cairo is None:
            self._cursor_surface = None
            self._cursor_pixels = None
            return
        pixels = bytearray(w * h * 4)
        rowmask = (w + 7) // 8
        for row in range(h):
            for col in range(w):
                src = (row * w + col) * 4
                visible = False
                if row * rowmask + (col // 8) < len(mask):
                    visible = bool((mask[row * rowmask + col // 8] >> (7 - (col % 8))) & 1)
                if visible and src + 4 <= len(raw):
                    pixels[src : src + 4] = raw[src : src + 4]
                    pixels[src + 3] = 255
        self._cursor_pixels = pixels
        self._cursor_hot = (int(hotx), int(hoty))
        try:
            self._cursor_surface = cairo.ImageSurface.create_for_data(
                memoryview(pixels), cairo.FORMAT_ARGB32, w, h, w * 4
            )
        except Exception:
            self._cursor_surface = None
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("none"))
        except Exception:
            pass
        GLib.idle_add(self.queue_draw)

    def _read_xcursor(self, sock, hotx, hoty, w, h):
        fg = self._recv_n(sock, 3)
        bg = self._recv_n(sock, 3)
        rowmask = (max(w, 0) + 7) // 8
        bitmap = self._recv_n(sock, rowmask * max(h, 0))
        mask = self._recv_n(sock, rowmask * max(h, 0))
        if w <= 0 or h <= 0 or cairo is None:
            self._cursor_surface = None
            self._cursor_pixels = None
            return

        def _bit(data, row, col):
            idx = row * rowmask + (col // 8)
            if idx >= len(data):
                return False
            return bool((data[idx] >> (7 - (col % 8))) & 1)

        pixels = bytearray(w * h * 4)
        for row in range(h):
            for col in range(w):
                if not _bit(mask, row, col):
                    continue
                src = (row * w + col) * 4
                color = fg if _bit(bitmap, row, col) else bg
                # cairo ARGB32 little-endian stores B,G,R,A
                pixels[src] = color[2]
                pixels[src + 1] = color[1]
                pixels[src + 2] = color[0]
                pixels[src + 3] = 255
        self._cursor_pixels = pixels
        self._cursor_hot = (int(hotx), int(hoty))
        try:
            self._cursor_surface = cairo.ImageSurface.create_for_data(
                memoryview(pixels), cairo.FORMAT_ARGB32, w, h, w * 4
            )
        except Exception:
            self._cursor_surface = None
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("none"))
        except Exception:
            pass
        GLib.idle_add(self.queue_draw)

    def _publish_fb(self, width, height):
        if cairo is None:
            return
        surface = cairo.ImageSurface.create_for_data(
            memoryview(self._pixels), cairo.FORMAT_ARGB32, width, height, width * 4
        )
        GLib.idle_add(self._set_framebuffer, surface, width, height)

    def _read_fb_update(self, sock, width, height):
        self._recv_n(sock, 1)
        nrects = struct.unpack("!H", self._recv_n(sock, 2))[0]
        for _ in range(nrects):
            x, y, w, h, enc = struct.unpack("!HHHHi", self._recv_n(sock, 12))
            if enc == _VNC_ENC_LASTRECT:
                break
            if enc == _VNC_ENC_DESKTOPNAME:
                nlen = struct.unpack("!I", self._recv_n(sock, 4))[0]
                self._name = self._recv_n(sock, nlen).decode("utf-8", "replace")
                continue
            if enc == _VNC_ENC_QEMU_EXT_KEY:
                self._qemu_ext_key = True
                continue
            if enc == _VNC_ENC_XCURSOR:
                self._read_xcursor(sock, x, y, w, h)
                continue
            if enc == _VNC_ENC_DESKTOPSIZE:
                width, height = w, h
                self._alloc_pixels(width, height)
                GLib.idle_add(self.emit, "vnc-desktop-resize", width, height)
                continue
            if enc == _VNC_ENC_EXTENDED_DESKTOPSIZE:
                nscreens = self._recv_n(sock, 1)[0]
                self._recv_n(sock, 3)
                self._recv_n(sock, nscreens * 16)
                if w and h:
                    width, height = w, h
                    self._alloc_pixels(width, height)
                    GLib.idle_add(self.emit, "vnc-desktop-resize", width, height)
                continue
            if enc == 0:
                self._blit_raw(width, x, y, w, h, self._recv_n(sock, w * h * 4))
            elif enc == 1:
                srcx, srcy = struct.unpack("!HH", self._recv_n(sock, 4))
                self._copy_rect(width, height, x, y, w, h, srcx, srcy)
            elif enc == 2:
                self._read_rre(sock, width, x, y, w, h)
            elif enc == _VNC_ENC_CORRE:
                self._read_corre(sock, width, x, y, w, h)
            elif enc == 5:
                self._read_hextile(sock, width, x, y, w, h)
            elif enc == 6:
                self._read_zlib(sock, width, x, y, w, h)
            elif enc in (_VNC_ENC_TIGHT, _VNC_ENC_TIGHTPNG):
                self._read_tight(sock, width, x, y, w, h)
            elif enc == _VNC_ENC_TRLE:
                self._read_trle(sock, width, x, y, w, h)
            elif enc == _VNC_ENC_ZRLE:
                self._read_zrle(sock, width, x, y, w, h)
            elif enc == _VNC_ENC_CURSOR:
                self._read_cursor(sock, x, y, w, h)
            elif enc < 0:
                log.debug("Ignoring VNC pseudo-encoding %s", enc)
            else:
                # GtkVnc skips unknown encodings instead of tearing down
                # the RFB session. Keep the framebuffer and wait for the
                # next update.
                log.debug("Ignoring unsupported VNC encoding %s", enc)
        self._publish_fb(width, height)
        try:
            self._request_update(sock, width, height)
        except Exception:
            pass
        return width, height


def _vnc_bit_reverse_key(password):
    key = (password or "").encode("latin1")[:8].ljust(8, b"\x00")
    return bytes(int("{:08b}".format(b)[::-1], 2) for b in key)


def _vnc_auth_response(challenge, password):
    """VNC d3des-style auth (bit-reversed DES-ECB of the 16-byte challenge)."""
    rev = _vnc_bit_reverse_key(password)
    try:
        from Crypto.Cipher import DES  # type: ignore

        cipher = DES.new(rev, DES.MODE_ECB)
        return cipher.encrypt(challenge)
    except Exception:
        pass
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        # TripleDES(K,K,K) is single DES. cryptography no longer exports DES.
        cipher = Cipher(algorithms.TripleDES(rev * 3), modes.ECB())
        encryptor = cipher.encryptor()
        return encryptor.update(challenge) + encryptor.finalize()
    except Exception:
        log.debug("VNC DES encrypt failed; authentication will fail", exc_info=True)
        return (password or "").encode("latin1")[:16].ljust(16, b"\x00")


class SpiceDisplay(_DisplayBase):
    """
    GTK 4 DrawingArea that renders a SpiceClientGLib DisplayChannel.
    """

    def __init__(self, session, channel_id=0, **kwargs):
        super().__init__(**kwargs)
        self._session = session
        self._channel_id = channel_id
        self._channel = None
        self._inputs = None
        self._main = None
        self._cursor_channel = None
        self._buttons = 0
        self._open = True
        self._clip_from_guest = False
        self._bind_session_channels()
        try:
            drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
            drop.connect("drop", self._on_file_drop)
            self.add_controller(drop)
        except Exception:
            pass

    def attach_channels(self, display_channel, inputs_channel):
        self._channel = display_channel
        self._inputs = inputs_channel
        if display_channel is not None:
            display_channel.connect("notify::width", self._on_primary)
            try:
                display_channel.connect("display-primary-create", self._on_primary_create)
            except TypeError:
                pass
            try:
                display_channel.connect("display-invalidate", self._on_invalidate)
            except TypeError:
                pass
            try:
                display_channel.connect("display-mark", self._on_invalidate)
            except TypeError:
                pass
            try:
                display_channel.connect("notify::gl-scanout", self._on_invalidate)
            except TypeError:
                pass
            try:
                display_channel.connect("gl-scanout", self._on_invalidate)
            except TypeError:
                pass
            self._refresh_primary()
        self._bind_session_channels()

    def _bind_session_channels(self):
        if self._session is None or SpiceClientGLib is None:
            return
        try:
            self._session.connect("channel-new", self._on_session_channel)
        except Exception:
            pass
        self._bind_main(self._find_main_channel())

    def _on_session_channel(self, _session, channel):
        if SpiceClientGLib is not None and isinstance(channel, SpiceClientGLib.MainChannel):
            self._bind_main(channel)
        if (
            SpiceClientGLib is not None
            and isinstance(channel, SpiceClientGLib.InputsChannel)
            and self._inputs is None
        ):
            self._inputs = channel
        if SpiceClientGLib is not None and isinstance(
            channel, SpiceClientGLib.CursorChannel
        ):
            self.attach_cursor_channel(channel)

    def _find_main_channel(self):
        if self._main is not None:
            return self._main
        if not self._session or SpiceClientGLib is None:
            return None
        try:
            for ch in self._session.get_channels() or []:
                if isinstance(ch, SpiceClientGLib.MainChannel):
                    return ch
        except Exception:
            return None
        return None

    def _bind_main(self, channel):
        if not channel or self._main is channel:
            return
        self._main = channel
        for sig, handler in (
            ("clipboard-selection", self._on_spice_clip_data),
            ("clipboard-selection-grab", self._on_spice_clip_grab),
            ("clipboard-selection-request", self._on_spice_clip_request),
            ("clipboard-selection-release", self._on_spice_clip_release),
            ("clipboard-grab", self._on_spice_clip_grab_old),
            ("clipboard-request", self._on_spice_clip_request_old),
            ("clipboard-release", self._on_spice_clip_release),
        ):
            try:
                channel.connect(sig, handler)
            except (TypeError, RuntimeError):
                pass
        self._bind_gdk_clipboard()
        if self.resize_guest:
            self._push_monitor_config()

    def _on_primary_create(self, *_args):
        self._refresh_primary()

    def _on_primary(self, *_args):
        self._refresh_primary()

    def _on_invalidate(self, *_args):
        self._refresh_primary()

    def attach_cursor_channel(self, channel):
        """Follow SpiceClientGLib CursorChannel like SpiceClientGtk."""
        if self._cursor_channel is channel:
            return
        if self._cursor_channel is not None:
            for handler in (
                self._on_cursor_set,
                self._on_cursor_hide,
                self._on_cursor_reset,
                self._on_cursor_move,
            ):
                try:
                    self._cursor_channel.disconnect_by_func(handler)
                except Exception:
                    pass
        self._cursor_channel = channel
        if channel is None:
            return
        for sig, handler in (
            ("cursor-set", self._on_cursor_set),
            ("cursor-hide", self._on_cursor_hide),
            ("cursor-reset", self._on_cursor_reset),
            ("cursor-move", self._on_cursor_move),
        ):
            try:
                channel.connect(sig, handler)
            except (TypeError, RuntimeError):
                pass

    def _on_cursor_set(self, _channel, *args):
        for arg in args:
            if hasattr(arg, "width") and hasattr(arg, "data"):
                self._apply_spice_cursor_shape(arg)
                return
        if len(args) >= 5:
            width, height, hotx, hoty, data = args[:5]
            shape = type(
                "_CursorShape",
                (),
                {
                    "width": width,
                    "height": height,
                    "hot_spot_x": hotx,
                    "hot_spot_y": hoty,
                    "data": data,
                },
            )()
            self._apply_spice_cursor_shape(shape)

    def _on_cursor_hide(self, *_args):
        self._cursor_surface = None
        self._cursor_pixels = None
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("none"))
        except Exception:
            try:
                self.set_cursor(None)
            except Exception:
                pass
        self.queue_draw()

    def _on_cursor_reset(self, *_args):
        self._cursor_surface = None
        self._cursor_pixels = None
        try:
            self.set_cursor(None)
        except Exception:
            pass
        self.queue_draw()

    def _on_cursor_move(self, _channel, *args):
        if len(args) >= 2:
            self._last_x, self._last_y = args[0], args[1]
            self.queue_draw()

    def _apply_spice_cursor_shape(self, shape):
        width = int(getattr(shape, "width", 0) or 0)
        height = int(getattr(shape, "height", 0) or 0)
        data = getattr(shape, "data", None)
        if width <= 0 or height <= 0 or data is None or cairo is None:
            self._on_cursor_hide()
            return
        try:
            pixels = bytearray(data)
        except Exception:
            self._on_cursor_hide()
            return
        need = width * height * 4
        if len(pixels) < need:
            pixels.extend(b"\x00" * (need - len(pixels)))
        self._cursor_pixels = pixels
        self._cursor_hot = (
            int(getattr(shape, "hot_spot_x", 0) or 0),
            int(getattr(shape, "hot_spot_y", 0) or 0),
        )
        try:
            self._cursor_surface = cairo.ImageSurface.create_for_data(
                memoryview(pixels), cairo.FORMAT_ARGB32, width, height, width * 4
            )
        except Exception:
            self._cursor_surface = None
        try:
            self.set_cursor(Gdk.Cursor.new_from_name("none"))
        except Exception:
            pass
        self.queue_draw()

    def _try_gl_scanout(self):
        """Paint Spice GL scanout (gtk-spice GL guests) when available."""
        if not self._channel or SpiceClientGLib is None:
            return False
        scanout = None
        try:
            if hasattr(self._channel, "get_gl_scanout"):
                scanout = self._channel.get_gl_scanout()
            elif hasattr(SpiceClientGLib, "display_get_gl_scanout"):
                scanout = SpiceClientGLib.display_get_gl_scanout(self._channel)
        except Exception:
            return False
        if not scanout or not getattr(scanout, "width", 0):
            return False
        surface = _cairo_from_gl_scanout(scanout)
        if surface is None:
            return False
        self._set_framebuffer(surface, int(scanout.width), int(scanout.height))
        try:
            if hasattr(self._channel, "gl_draw_done"):
                self._channel.gl_draw_done()
            elif hasattr(SpiceClientGLib, "display_gl_draw_done"):
                SpiceClientGLib.display_gl_draw_done(self._channel)
        except Exception:
            pass
        return True

    def _refresh_primary(self):
        if not self._channel or SpiceClientGLib is None:
            return
        if self._try_gl_scanout():
            return
        primary = SpiceClientGLib.DisplayPrimary()
        try:
            ok = self._channel.display_channel_get_primary(0, primary)
        except Exception:
            ok = False
        if not ok or not primary.width:
            return
        surface = _cairo_from_spice_primary(primary)
        if surface is not None:
            self._set_framebuffer(surface, primary.width, primary.height)

    def _apply_resize_guest(self, val):
        if self._session is not None:
            try:
                self._session.set_property("enable-audio", True)
            except Exception:
                pass
        if val:
            self._push_monitor_config()

    def _push_monitor_config(self, width=None, height=None):
        main = self._find_main_channel()
        if not main or SpiceClientGLib is None:
            return
        w = int(width if width is not None else max(self.get_width(), 1))
        h = int(height if height is not None else max(self.get_height(), 1))
        if w < 16 or h < 16:
            return
        try:
            SpiceClientGLib.main_update_display_enabled(main, 0, True, False)
            SpiceClientGLib.main_update_display(main, 0, 0, 0, w, h, True)
            SpiceClientGLib.main_send_monitor_config(main)
        except Exception as exc:
            log.debug("spice resize-guest failed: %s", exc)

    def _send_pointer(self, x, y, button, pressed=False):
        if button:
            self._update_buttons(button, pressed)
        if not self._inputs or SpiceClientGLib is None:
            return
        x, y = self._scale_pointer(x, y)
        try:
            if button and pressed:
                SpiceClientGLib.inputs_button_press(self._inputs, int(button), self._buttons)
            SpiceClientGLib.inputs_position(self._inputs, int(x), int(y), 0, self._buttons)
            if button and not pressed:
                SpiceClientGLib.inputs_button_release(self._inputs, int(button), self._buttons)
        except Exception:
            pass

    def _gdk_clipboard(self, selection=_SPICE_CLIP_SELECTION):
        display = Gdk.Display.get_default()
        if display is None:
            return None
        if int(selection or 0) == _SPICE_CLIP_PRIMARY and hasattr(display, "get_primary_clipboard"):
            return display.get_primary_clipboard()
        return display.get_clipboard()

    def _bind_gdk_clipboard(self):
        display = Gdk.Display.get_default()
        if display is None:
            return
        for selection, getter in (
            (_SPICE_CLIP_SELECTION, "get_clipboard"),
            (_SPICE_CLIP_PRIMARY, "get_primary_clipboard"),
        ):
            if not hasattr(display, getter):
                continue
            try:
                clip = getattr(display, getter)()
                clip.connect(
                    "changed",
                    lambda c, sel=selection: self._on_gdk_clip_changed(c, sel),
                )
            except Exception:
                pass

    def _clear_clip_from_guest(self):
        self._clip_from_guest = False
        return False

    def _spice_clip_notify(self, channel, selection, typ, data):
        if channel is None or SpiceClientGLib is None:
            return
        payload = list(data or b"")
        try:
            SpiceClientGLib.main_clipboard_selection_notify(channel, selection, typ, payload)
            return
        except Exception:
            pass
        try:
            SpiceClientGLib.main_clipboard_notify(channel, typ, payload)
        except Exception as exc:
            log.debug("spice clipboard notify failed: %s", exc)

    def _spice_clip_grab(self, channel, selection, types):
        if channel is None or SpiceClientGLib is None:
            return
        try:
            SpiceClientGLib.main_clipboard_selection_grab(channel, selection, list(types or [_SPICE_CLIP_UTF8]))
            return
        except Exception:
            pass
        try:
            SpiceClientGLib.main_clipboard_grab(channel, list(types or [_SPICE_CLIP_UTF8]))
        except Exception as exc:
            log.debug("spice clipboard grab failed: %s", exc)

    def _on_gdk_clip_changed(self, clip, selection=_SPICE_CLIP_SELECTION):
        if self._clip_from_guest or self._main is None:
            return

        def _got(_src, result):
            try:
                text = clip.read_text_finish(result)
            except Exception:
                return
            if text is None:
                return
            data = text.encode("utf-8")
            self._spice_clip_grab(self._main, selection, [_SPICE_CLIP_UTF8])
            self._spice_clip_notify(self._main, selection, _SPICE_CLIP_UTF8, data)

        try:
            clip.read_text_async(None, _got)
        except Exception:
            pass

    def _on_spice_clip_grab(self, channel, selection, types, *_args):
        ignore = types
        try:
            SpiceClientGLib.main_clipboard_selection_request(channel, selection, _SPICE_CLIP_UTF8)
        except Exception:
            try:
                SpiceClientGLib.main_clipboard_request(channel, _SPICE_CLIP_UTF8)
            except Exception:
                pass

    def _on_spice_clip_grab_old(self, channel, types, *_args):
        self._on_spice_clip_grab(channel, _SPICE_CLIP_SELECTION, types)

    def _on_spice_clip_request(self, channel, selection, typ, *_args):
        clip = self._gdk_clipboard(selection)
        if clip is None:
            return

        def _got(_src, result):
            try:
                text = clip.read_text_finish(result)
            except Exception:
                text = ""
            self._spice_clip_notify(channel, selection, typ, (text or "").encode("utf-8"))

        try:
            clip.read_text_async(None, _got)
        except Exception:
            pass

    def _on_spice_clip_request_old(self, channel, typ, *_args):
        self._on_spice_clip_request(channel, _SPICE_CLIP_SELECTION, typ)

    def _on_spice_clip_release(self, *_args):
        return None

    def _on_spice_clip_data(self, _channel, _selection, typ, data, *_args):
        if typ not in (_SPICE_CLIP_UTF8, None) and typ != 1:
            return
        try:
            if isinstance(data, (bytes, bytearray, memoryview)):
                text = bytes(data).decode("utf-8", "replace")
            elif isinstance(data, str):
                text = data
            elif isinstance(data, (list, tuple)):
                text = bytes(data).decode("utf-8", "replace")
            else:
                return
        except Exception:
            return
        clip = self._gdk_clipboard(_selection)
        if clip is None:
            return
        self._clip_from_guest = True
        try:
            try:
                clip.set(text)
            except Exception:
                clip.set_content(Gdk.ContentProvider.new_for_value(text))
        except Exception as exc:
            log.debug("host clipboard set failed: %s", exc)
        GLib.idle_add(self._clear_clip_from_guest)

    def _on_file_drop(self, _target, value, _x, _y):
        main = self._find_main_channel()
        if not main or SpiceClientGLib is None:
            return False
        files = []
        try:
            if hasattr(value, "get_files"):
                files = list(value.get_files() or [])
            elif isinstance(value, (list, tuple)):
                files = list(value)
        except Exception:
            return False
        if not files:
            return False
        try:
            SpiceClientGLib.main_file_copy_async(main, files, 0)
            return True
        except Exception as exc:
            log.debug("spice file transfer failed: %s", exc)
            return False

    def _spice_scancode(self, keyval, keycode):
        return _linux_scancode(keyval, keycode)

    def _send_key(self, keyval, keycode, pressed):
        if not self._inputs or SpiceClientGLib is None:
            return
        scancode = self._spice_scancode(keyval, keycode)
        try:
            if pressed:
                SpiceClientGLib.inputs_key_press(self._inputs, int(scancode))
            else:
                SpiceClientGLib.inputs_key_release(self._inputs, int(scancode))
        except Exception:
            pass

    def close(self):
        self._open = False
        self.attach_cursor_channel(None)
        self._channel = None
        self._inputs = None
        self._main = None


def _cairo_from_gl_scanout(scanout):
    """Import a Spice GL dmabuf scanout into a cairo surface."""
    if cairo is None or GdkPixbuf is None:
        return None
    try:
        fd = int(getattr(scanout, "fd", -1))
        width = int(getattr(scanout, "width", 0) or 0)
        height = int(getattr(scanout, "height", 0) or 0)
        stride = int(getattr(scanout, "stride", 0) or width * 4)
        fourcc = int(getattr(scanout, "format", 0) or 0)
    except Exception:
        return None
    if fd < 0 or width <= 0 or height <= 0:
        return None
    try:
        builder = Gdk.DmabufTextureBuilder.new()
        display = Gdk.Display.get_default()
        if display is not None:
            builder.set_display(display)
        builder.set_width(width)
        builder.set_height(height)
        builder.set_fourcc(fourcc)
        builder.set_n_planes(1)
        builder.set_fd(0, fd)
        builder.set_stride(0, stride)
        modifier = int(getattr(scanout, "modifier", 0) or 0)
        if hasattr(builder, "set_modifier"):
            try:
                builder.set_modifier(modifier)
            except Exception:
                pass
        texture = builder.build()
        buf = bytearray(width * height * 4)
        texture.download(buf, width * 4)
        return cairo.ImageSurface.create_for_data(
            memoryview(buf), cairo.FORMAT_ARGB32, width, height, width * 4
        )
    except Exception as exc:
        log.debug("Failed to import spice GL scanout: %s", exc)
        return None


def _cairo_from_spice_primary(primary):
    if cairo is None:
        return None
    width = int(primary.width)
    height = int(primary.height)
    stride = int(primary.stride) if primary.stride else width * 4
    data = primary.data
    if data is None:
        return None
    try:
        if isinstance(data, (bytes, bytearray, memoryview)):
            buf = bytearray(data)
        else:
            import ctypes

            addr = int(data)
            buf = (ctypes.c_char * (stride * height)).from_address(addr)
            buf = bytearray(buf)
    except Exception as exc:
        log.debug("Failed to map spice primary: %s", exc)
        return None
    try:
        return cairo.ImageSurface.create_for_data(
            memoryview(buf), cairo.FORMAT_ARGB32, width, height, stride
        )
    except Exception as exc:
        log.debug("Failed to create cairo surface: %s", exc)
        return None


class UsbDeviceWidget(Gtk.Box):
    """
    GTK 4 USB redirection list using SpiceClientGLib.UsbDeviceManager.
    """

    __gsignals__ = {
        "connect-failed": (GObject.SignalFlags.RUN_FIRST, None, [object, str]),
    }

    def __init__(self, session, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6, **kwargs)
        self._session = session
        self._manager = None
        if SpiceClientGLib is not None and session is not None:
            try:
                self._manager = SpiceClientGLib.UsbDeviceManager.get(session)
            except Exception:
                self._manager = None
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._spice_cd = None
        self._toggling = False
        self.append(Gtk.Label(label=_("USB devices"), xalign=0))
        self.append(self._list)
        if self._manager is not None:
            for sig in ("device-added", "device-removed"):
                try:
                    self._manager.connect(sig, lambda *_a: GLib.idle_add(self._refresh))
                except Exception:
                    pass
        self._refresh()

    @classmethod
    def new(cls, session, _unused=None):
        return cls(session)

    def _dev_label(self, dev):
        try:
            if hasattr(dev, "get_description"):
                desc = dev.get_description(None)
                if desc:
                    return str(desc)
        except Exception:
            pass
        return str(dev)

    def _refresh(self):
        child = self._list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list.remove(child)
            child = nxt
        self._spice_cd = None
        if not self._manager:
            self._list.append(Gtk.Label(label=_("USB redirection is not available"), xalign=0))
            self._append_spice_cd(sensitive=False)
            return
        try:
            devices = list(self._manager.get_devices() or [])
        except Exception:
            devices = []
        self._append_spice_cd(sensitive=True)
        if not devices:
            self._list.append(Gtk.Label(label=_("No USB devices"), xalign=0))
            return
        for dev in devices:
            shared = False
            try:
                shared = bool(self._manager.is_device_shared_cd(dev))
            except Exception:
                shared = False
            if shared:
                if self._spice_cd is not None:
                    self._toggling = True
                    self._spice_cd.set_active(True)
                    self._toggling = False
                continue
            connected = False
            try:
                connected = bool(self._manager.is_device_connected(dev))
            except Exception:
                connected = False
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label=self._dev_label(dev), xalign=0, hexpand=True)
            btn = Gtk.CheckButton(label=_("Redirect"))
            btn.set_active(connected)
            btn.connect("toggled", self._on_toggle, dev)
            row.append(label)
            row.append(btn)
            self._list.append(row)

    def _append_spice_cd(self, sensitive=True):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=_("SPICE CD"), xalign=0, hexpand=True)
        btn = Gtk.CheckButton(label=_("SPICE CD"))
        btn.set_name("SPICE CD")
        try:
            btn.update_property([Gtk.AccessibleProperty.LABEL], ["SPICE CD"])
        except Exception:
            pass
        btn.set_sensitive(sensitive)
        btn.connect("toggled", self._on_spice_cd_toggled)
        row.append(label)
        row.append(btn)
        self._list.append(row)
        self._spice_cd = btn

    def _on_toggle(self, btn, dev):
        if self._toggling or not self._manager:
            return
        if btn.get_active():
            self._connect_dev(dev, btn)
        else:
            self._disconnect_dev(dev, btn)

    def _connect_dev(self, dev, btn=None):
        def _done(_src, result):
            try:
                self._manager.connect_device_finish(result)
            except Exception as exc:
                if btn is not None:
                    self._toggling = True
                    btn.set_active(False)
                    self._toggling = False
                self.emit("connect-failed", dev, str(exc))

        try:
            self._manager.connect_device_async(dev, None, _done)
        except Exception as exc:
            if btn is not None:
                self._toggling = True
                btn.set_active(False)
                self._toggling = False
            self.emit("connect-failed", dev, str(exc))

    def _disconnect_dev(self, dev, btn=None):
        def _fail(exc):
            if btn is not None:
                self._toggling = True
                btn.set_active(True)
                self._toggling = False
            self.emit("connect-failed", dev, str(exc))

        try:
            if hasattr(self._manager, "disconnect_device_async"):

                def _done(_src, result):
                    try:
                        self._manager.disconnect_device_finish(result)
                    except Exception as exc:
                        _fail(exc)

                self._manager.disconnect_device_async(dev, None, _done)
            else:
                self._manager.disconnect_device(dev)
        except Exception as exc:
            _fail(exc)

    def _on_spice_cd_toggled(self, btn):
        if self._toggling:
            return
        if not btn.get_active():
            self._disconnect_shared_cds()
            return
        if not self._manager:
            self._toggling = True
            btn.set_active(False)
            self._toggling = False
            return
        self._choose_iso()

    def _choose_iso(self):
        from virtManager.lib import gtkcompat

        parent = self.get_root()
        try:
            path = gtkcompat.browse_local(
                parent,
                _("Select CDROM / ISO image"),
                _type=("iso", _("ISO files")),
                dialog_type=Gtk.FileChooserAction.OPEN,
            )
        except Exception as exc:
            self.emit("connect-failed", None, str(exc))
            path = None
        if not path:
            self._toggling = True
            if self._spice_cd is not None:
                self._spice_cd.set_active(False)
            self._toggling = False
            return
        try:
            ok = self._manager.create_shared_cd_device(path)
        except Exception as exc:
            self.emit("connect-failed", None, str(exc))
            ok = False
        if not ok:
            self._toggling = True
            if self._spice_cd is not None:
                self._spice_cd.set_active(False)
            self._toggling = False
        else:
            GLib.idle_add(self._refresh)

    def _disconnect_shared_cds(self):
        if not self._manager:
            return
        try:
            devices = list(self._manager.get_devices() or [])
        except Exception:
            devices = []
        for dev in devices:
            try:
                if self._manager.is_device_shared_cd(dev):
                    self._disconnect_dev(dev)
            except Exception:
                pass

    def _on_redirect(self, _btn, dev):
        # Kept for callers/tests that click the older Redirect action.
        self._connect_dev(dev)
