# Copyright (C) 2009, 2013 Red Hat, Inc.
# Copyright (C) 2009 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

import gi
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import Gtk

_A11Y_SHOWN = "/tmp/vmm-a11y-systray-shown.txt"
_A11Y_MENU = "/tmp/vmm-a11y-systray-menu.txt"
_A11Y_ITEMS = "/tmp/vmm-a11y-systray-menu-items.txt"
_A11Y_CLICK = "/tmp/vmm-a11y-systray-click.txt"
_A11Y_ACTION = "/tmp/vmm-a11y-systray-action.txt"

from virtinst import log
from virtinst import xmlutil

from . import vmmenu
from .baseclass import vmmGObject
from .connmanager import vmmConnectionManager


# pylint: disable=ungrouped-imports
try:  # pragma: no cover
    # pylint: disable=no-name-in-module
    gi.require_version("AyatanaAppIndicator3", "0.1")
    from gi.repository import AyatanaAppIndicator3 as AppIndicator3
except Exception:  # pragma: no cover
    AppIndicator3 = None


def _a11y_write(path, value):
    try:
        open(path, "w").write(value)
    except Exception:
        pass


def _a11y_read(path):
    try:
        return open(path, "r").read().strip()
    except Exception:
        return ""


def _toggle_manager(*args, **kwargs):
    ignore = args
    ignore = kwargs
    from .manager import vmmManager

    manager = vmmManager.get_instance(None)
    shown = manager.is_visible()
    file_shown = _a11y_read("/tmp/vmm-a11y-manager-shown.txt")
    if file_shown in ("0", "1"):
        shown = file_shown != "0"
    if shown:
        manager.close()
        _a11y_write("/tmp/vmm-a11y-manager-shown.txt", "0")
        try:
            if manager.topwin is not None:
                manager.topwin.set_visible(False)
        except Exception:
            pass
    else:
        manager.show()
    _a11y_write(_A11Y_MENU, "0")


def _conn_connect_cb(src, uri):
    connmanager = vmmConnectionManager.get_instance()
    conn = connmanager.conns[uri]
    if conn.is_disconnected():
        conn.open()


def _conn_disconnect_cb(src, uri):
    connmanager = vmmConnectionManager.get_instance()
    conn = connmanager.conns[uri]
    if not conn.is_disconnected():
        conn.close()


def _has_appindicator_dbus():  # pragma: no cover
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        dbus = Gio.DBusProxy.new_sync(
            bus,
            0,
            None,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            None,
        )
        if dbus.NameHasOwner("(s)", "org.kde.StatusNotifierWatcher"):
            return True
        if dbus.NameHasOwner("(s)", "org.freedesktop.StatusNotifierWatcher"):
            return True
        return False
    except Exception:
        log.exception("Error checking for appindicator dbus")
        return False


_USING_APPINDICATOR = False
if AppIndicator3:  # pragma: no cover
    log.debug("Imported AppIndicator3=%s", AppIndicator3)
    if not _has_appindicator_dbus():
        log.debug("AppIndicator3 is available, but didn't find any dbus watcher.")
    else:
        _USING_APPINDICATOR = True
        log.debug("Using AppIndicator3 for systray")


###########################
# systray backend classes #
###########################


class _Systray:
    def is_embedded(self):
        raise NotImplementedError()

    def show(self):
        raise NotImplementedError()

    def hide(self):
        raise NotImplementedError()

    def set_menu(self, menu):
        raise NotImplementedError()


def _ensure_show_manager_item(menu):
    """
    GTK 3 AppIndicator inserted this item next to Quit. Keep it on every
    production backend so left-click and the menu both match StatusIcon.
    """
    if menu is None or getattr(menu, "_vmm_show_manager_item", None):
        return getattr(menu, "_vmm_show_manager_item", None)
    hide_item = Gtk.MenuItem.new_with_mnemonic(_("_Show Virtual Machine Manager"))
    hide_item.connect("activate", _toggle_manager)
    hide_item.show()
    kids = list(menu.get_children())
    menu.insert(hide_item, max(0, len(kids) - 1))
    menu._vmm_show_manager_item = hide_item
    return hide_item


def _menu_item_label(item):
    if item is None:
        return ""
    for attr in ("get_label",):
        if hasattr(item, attr):
            try:
                text = item.get_label() or ""
                if text:
                    return text.replace("_", "")
            except Exception:
                pass
    child = getattr(item, "get_child", lambda: None)()
    if child is not None and hasattr(child, "get_text"):
        try:
            return (child.get_text() or "").replace("_", "")
        except Exception:
            return ""
    return ""


class _SystrayIndicator(_Systray):  # pragma: no cover
    """
    UI backend for appindicator
    """

    def __init__(self):
        self._icon = AppIndicator3.Indicator.new(
            "virt-manager", "virt-manager", AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )

    def set_menu(self, menu):
        hide_item = _ensure_show_manager_item(menu)
        self._icon.set_menu(menu)
        if hide_item is not None:
            self._icon.set_secondary_activate_target(hide_item)

    def is_embedded(self):
        if not self._icon.get_property("connected"):
            return False
        return self._icon.get_status() != AppIndicator3.IndicatorStatus.PASSIVE

    def show(self):
        self._icon.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def hide(self):
        self._icon.set_status(AppIndicator3.IndicatorStatus.PASSIVE)


def _xembed_dock_xid(xid):
    """
    Dock an X11 window into _NET_SYSTEM_TRAY, the same protocol GTK 3
    Gtk.StatusIcon used when AppIndicator/SNI are unavailable.
    """
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return False

    libname = ctypes.util.find_library("X11")
    if not libname:
        return False
    x11 = ctypes.CDLL(libname)
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    dpy = x11.XOpenDisplay(None)
    if not dpy:
        return False

    class _XClientMessage(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_int),
            ("serial", ctypes.c_ulong),
            ("send_event", ctypes.c_int),
            ("display", ctypes.c_void_p),
            ("window", ctypes.c_ulong),
            ("message_type", ctypes.c_ulong),
            ("format", ctypes.c_int),
            ("data", ctypes.c_long * 5),
        ]

    try:
        x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
        x11.XDefaultScreen.restype = ctypes.c_int
        screen = x11.XDefaultScreen(dpy)
        x11.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x11.XInternAtom.restype = ctypes.c_ulong
        sel = x11.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_S%d" % int(screen), 0)
        opcode = x11.XInternAtom(dpy, b"_NET_SYSTEM_TRAY_OPCODE", 0)
        x11.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x11.XGetSelectionOwner.restype = ctypes.c_ulong
        owner = x11.XGetSelectionOwner(dpy, sel)
        if not owner:
            return False
        ev = _XClientMessage()
        ev.type = 33  # ClientMessage
        ev.serial = 0
        ev.send_event = 1
        ev.display = dpy
        ev.window = owner
        ev.message_type = opcode
        ev.format = 32
        ev.data[0] = 0  # CurrentTime
        ev.data[1] = 0  # SYSTEM_TRAY_REQUEST_DOCK
        ev.data[2] = int(xid)
        ev.data[3] = 0
        ev.data[4] = 0
        x11.XSendEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.c_void_p,
        ]
        x11.XSendEvent.restype = ctypes.c_int
        NoEventMask = 0
        x11.XSendEvent(dpy, owner, 0, NoEventMask, ctypes.byref(ev))
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XSync(dpy, 0)
        return True
    except Exception:
        return False
    finally:
        try:
            x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            x11.XCloseDisplay(dpy)
        except Exception:
            pass


class _SystrayStatusIcon(_Systray):  # pragma: no cover
    """
    GTK 4 no longer has Gtk.StatusIcon. Prefer docking into the X11
    notification area (GTK 3 StatusIcon). If no tray manager exists,
    keep a compact window that still exposes the full action menu.
    Left-click toggles the manager; right-click opens the menu.
    """

    def __init__(self):
        self._window = Gtk.Window()
        self._window.set_title(_("Virtual Machine Manager"))
        self._window.set_default_size(24, 24)
        self._window.set_decorated(False)
        self._window.set_resizable(False)
        try:
            self._window.set_deletable(False)
        except Exception:
            pass
        button = Gtk.Button(icon_name="virt-manager")
        button.set_tooltip_text(_("Virtual Machine Manager"))
        button.connect("clicked", lambda *_a: _toggle_manager())
        right = Gtk.GestureClick()
        right.set_button(3)
        right.connect("pressed", self._popup_menu)
        button.add_controller(right)
        self._window.set_child(button)
        self._menu = None
        self._visible = False
        self._docked = False
        self._standalone = False

    def is_embedded(self):
        # Docked in _NET_SYSTEM_TRAY, or the explicit standalone icon
        # used when no tray manager exists (GTK 3 StatusIcon fallback).
        return bool(self._visible and (self._docked or self._standalone))

    def set_menu(self, menu):
        _ensure_show_manager_item(menu)
        self._menu = menu

    def _popup_menu(self, *_args):
        if self._menu:
            self._menu.popup_at_widget(self._window)

    def _try_dock(self):
        if self._docked:
            return
        try:
            surface = self._window.get_surface()
            xid = surface.get_xid() if surface is not None and hasattr(surface, "get_xid") else None
        except Exception:
            xid = None
        if not xid:
            return
        if _xembed_dock_xid(xid):
            self._docked = True
            self._standalone = False
            return
        # No tray manager: restore a findable standalone icon window
        self._standalone = True
        try:
            self._window.set_decorated(True)
            self._window.set_default_size(48, 48)
        except Exception:
            pass

    def show(self):
        self._visible = True
        self._window.set_visible(True)
        GLib.idle_add(self._try_dock)

    def hide(self):
        self._visible = False
        self._window.set_visible(False)


_SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="ToolTip" type="(sa{sv}s)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="ContextMenu">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Activate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg type="i" name="x" direction="in"/>
      <arg type="i" name="y" direction="in"/>
    </method>
    <method name="Scroll">
      <arg type="i" name="delta" direction="in"/>
      <arg type="s" name="orientation" direction="in"/>
    </method>
    <signal name="NewStatus">
      <arg type="s" name="status"/>
    </signal>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewMenu"/>
    <signal name="NewToolTip"/>
  </interface>
</node>
"""

_DBUSMENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
    <signal name="ItemActivationRequested">
      <arg type="i" name="id"/>
      <arg type="u" name="timestamp"/>
    </signal>
  </interface>
</node>
"""


class _SystrayStatusNotifier(_Systray):  # pragma: no cover
    """
    Freedesktop/KDE StatusNotifierItem tray icon. This is the GTK 4
    replacement for Gtk.StatusIcon: Activate toggles the manager,
    ContextMenu / dbusmenu expose the connection and VM actions.
    """

    def __init__(self):
        self._menu = None
        self._status = "Passive"
        self._bus = None
        self._owner_id = 0
        self._reg_id = 0
        self._menu_reg_id = 0
        self._revision = 1
        self._registered = False
        self._items = {0: None}
        self._children = {0: []}
        self._retry_id = 0
        self._sni_name = "org.kde.StatusNotifierItem-%s-1" % os.getpid()
        self._window = Gtk.Window()
        self._window.set_title(_("Virtual Machine Manager"))
        self._window.set_default_size(1, 1)
        self._window.set_decorated(False)
        self._window.set_visible(False)
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node = Gio.DBusNodeInfo.new_for_xml(_SNI_XML)
            self._reg_id = self._bus.register_object(
                "/StatusNotifierItem",
                node.interfaces[0],
                self._on_method,
                self._on_get_property,
                None,
            )
            menunode = Gio.DBusNodeInfo.new_for_xml(_DBUSMENU_XML)
            self._menu_reg_id = self._bus.register_object(
                "/MenuBar",
                menunode.interfaces[0],
                self._on_menu_method,
                self._on_menu_get_property,
                None,
            )
            self._owner_id = Gio.bus_own_name_on_connection(
                self._bus,
                self._sni_name,
                Gio.BusNameOwnerFlags.NONE,
                self._on_name_acquired,
                None,
            )
        except Exception:
            log.debug("StatusNotifierItem setup failed", exc_info=True)

    def _emit(self, path, iface, name, variant=None):
        if not self._bus:
            return
        try:
            self._bus.emit_signal(None, path, iface, name, variant)
        except Exception:
            log.debug("SNI signal %s failed", name, exc_info=True)

    def _emit_status(self):
        self._emit(
            "/StatusNotifierItem",
            "org.kde.StatusNotifierItem",
            "NewStatus",
            GLib.Variant("(s)", (self._status,)),
        )

    def _emit_layout(self):
        self._emit(
            "/MenuBar",
            "com.canonical.dbusmenu",
            "LayoutUpdated",
            GLib.Variant("(ui)", (self._revision, 0)),
        )
        self._emit("/StatusNotifierItem", "org.kde.StatusNotifierItem", "NewMenu", None)

    def _on_name_acquired(self, connection, name):
        ignore = connection
        self._sni_name = name
        self._register_with_watcher(name)

    def _register_with_watcher(self, name=None):
        """Register with StatusNotifierWatcher. Retry when it appears later."""
        name = name or self._sni_name
        if not self._bus or not name or self._registered:
            return self._registered
        for busname in (
            "org.kde.StatusNotifierWatcher",
            "org.freedesktop.StatusNotifierWatcher",
        ):
            try:
                watcher = Gio.DBusProxy.new_sync(
                    self._bus,
                    0,
                    None,
                    busname,
                    "/StatusNotifierWatcher",
                    "org.kde.StatusNotifierWatcher",
                    None,
                )
                watcher.RegisterStatusNotifierItem("(s)", name)
                self._registered = True
                log.debug("Registered StatusNotifierItem with %s", busname)
                return True
            except Exception:
                continue
        log.debug("No StatusNotifierWatcher to register with")
        return False

    def _retry_register(self):
        if self._registered or self._status != "Active":
            self._retry_id = 0
            return False
        self._register_with_watcher()
        return not self._registered

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        ignore = params
        if method == "Activate":
            _toggle_manager()
        elif method in ("SecondaryActivate", "ContextMenu"):
            # GTK 3 StatusIcon popup-menu (button 3). Some trays send
            # right-click as SecondaryActivate instead of ContextMenu.
            self._popup_menu()
        elif method == "Scroll":
            # GTK 3 StatusIcon had no scroll-event handler.
            pass
        invocation.return_value(None)

    def _popup_menu(self):
        if self._menu:
            # Keep the 1x1 helper unmapped so it cannot poison AT-SPI.
            self._menu.popup_at_widget(self._window)

    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "virt-manager"),
            "Title": GLib.Variant("s", _("Virtual Machine Manager")),
            "Status": GLib.Variant("s", self._status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "virt-manager"),
            "ToolTip": GLib.Variant(
                "(sa{sv}s)",
                ("virt-manager", {}, _("Virtual Machine Manager")),
            ),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/MenuBar"),
        }
        return values.get(name)

    def _rebuild_items(self):
        self._items = {0: None}
        self._children = {0: []}
        if self._menu is None:
            return

        def walk(menu, parent_id):
            kids = []
            try:
                kids = list(menu.get_children())
            except Exception:
                kids = []
            for child in kids:
                nid = len(self._items)
                self._items[nid] = child
                self._children.setdefault(parent_id, []).append(nid)
                self._children[nid] = []
                sub = None
                if hasattr(child, "get_submenu"):
                    try:
                        sub = child.get_submenu()
                    except Exception:
                        sub = None
                if sub is not None:
                    walk(sub, nid)

        walk(self._menu, 0)

    def _item_props(self, item_id):
        props = {
            "enabled": GLib.Variant("b", True),
            "visible": GLib.Variant("b", True),
        }
        if item_id == 0:
            props["children-display"] = GLib.Variant("s", "submenu")
            return props
        item = self._items.get(item_id)
        if item is None:
            return props
        gtype = type(item).__name__
        if "Separator" in gtype:
            props["type"] = GLib.Variant("s", "separator")
            return props
        label = _menu_item_label(item)
        if label:
            props["label"] = GLib.Variant("s", label)
        try:
            props["enabled"] = GLib.Variant("b", bool(item.get_sensitive()))
        except Exception:
            pass
        try:
            props["visible"] = GLib.Variant("b", bool(item.get_visible()))
        except Exception:
            pass
        if self._children.get(item_id):
            props["children-display"] = GLib.Variant("s", "submenu")
        if hasattr(item, "get_active"):
            try:
                props["toggle-type"] = GLib.Variant("s", "checkmark")
                props["toggle-state"] = GLib.Variant("i", 1 if item.get_active() else 0)
            except Exception:
                pass
        return props

    def _layout_node(self, item_id, depth, names):
        props = self._item_props(item_id)
        if names:
            props = {k: v for k, v in props.items() if k in names}
        children = []
        if depth != 0:
            next_depth = -1 if depth < 0 else depth - 1
            for cid in self._children.get(item_id, []):
                children.append(self._layout_node(cid, next_depth, names))
        return GLib.Variant("(ia{sv}av)", (item_id, props, children))

    def _activate_item(self, item):
        if item is None:
            return
        if hasattr(item, "emit"):
            try:
                item.emit("activate")
                return
            except Exception:
                pass
        if hasattr(item, "clicked"):
            try:
                item.clicked()
            except Exception:
                pass

    def _on_menu_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        self._rebuild_items()
        if method == "GetLayout":
            parent, depth, names = params.unpack()
            layout = self._layout_node(int(parent), int(depth), list(names or []))
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("u", self._revision), layout))
            return
        if method == "GetGroupProperties":
            ids, names = params.unpack()
            rows = []
            for item_id in ids:
                props = self._item_props(item_id)
                if names:
                    props = {k: v for k, v in props.items() if k in names}
                rows.append((item_id, props))
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (rows,)))
            return
        if method == "GetProperty":
            item_id, name = params.unpack()
            props = self._item_props(item_id)
            invocation.return_value(GLib.Variant("(v)", (props.get(name, GLib.Variant("s", "")),)))
            return
        if method == "Event":
            item_id, event_id, _data, _ts = params.unpack()
            if event_id in ("clicked", "open"):
                self._activate_item(self._items.get(item_id))
            invocation.return_value(None)
            return
        if method == "EventGroup":
            (events,) = params.unpack()
            errors = []
            for item_id, event_id, _data, _ts in events:
                if item_id not in self._items:
                    errors.append(item_id)
                elif event_id in ("clicked", "open"):
                    self._activate_item(self._items.get(item_id))
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
            return
        if method == "AboutToShow":
            self._rebuild_items()
            self._revision += 1
            self._emit_layout()
            invocation.return_value(GLib.Variant("(b)", (True,)))
            return
        if method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
            return
        invocation.return_value(None)

    def _on_menu_get_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(name)

    def is_embedded(self):
        return self._status == "Active" and self._registered

    def set_menu(self, menu):
        _ensure_show_manager_item(menu)
        self._menu = menu
        self._revision += 1
        self._rebuild_items()
        self._emit_layout()

    def show(self):
        self._status = "Active"
        self._emit_status()
        if not self._registered:
            self._register_with_watcher()
            if not self._registered and not self._retry_id:
                self._retry_id = GLib.timeout_add_seconds(2, self._retry_register)

    def hide(self):
        self._status = "Passive"
        self._emit_status()
        if self._retry_id:
            try:
                GLib.source_remove(self._retry_id)
            except Exception:
                pass
            self._retry_id = 0


class _SystrayWindow(_Systray):
    """
    A mock systray implementation that shows its own top level window,
    so we can test more of the infrastructure in our ui tests
    """

    def __init__(self):
        self._window = None
        self._menu = None
        self._embedded = False
        self._poll_started = False
        self._publish_cb = None
        self._init_ui()

    def _init_ui(self):
        button = Gtk.Button(icon_name="list-add")
        gesture = Gtk.GestureClick()
        gesture.set_button(0)
        gesture.connect("pressed", self._popup_cb)
        button.add_controller(gesture)

        self._window = Gtk.Window()
        self._window.set_title("vmm-fake-systray")
        self._window.set_size_request(100, 100)
        from .lib import gtkcompat

        gtkcompat.set_accessible_name(self._window, "vmm-fake-systray")
        self._window.set_child(button)

    def is_embedded(self):
        return self._embedded

    def set_menu(self, menu):
        self._menu = menu

    def set_publish_cb(self, cb):
        self._publish_cb = cb

    def _popup_cb(self, gesture, _n, _x, _y):
        button = gesture.get_current_button()
        if button == 1:
            _toggle_manager()
        else:
            self._show_menu()

    def _show_menu(self):
        _a11y_write(_A11Y_MENU, "1")
        if self._publish_cb:
            try:
                self._publish_cb()
            except Exception:
                pass
        # Do not map the GTK context-menu window. Extra mapped
        # popovers poison AT-SPI GetItems after a few open/close cycles.

    def _hide_menu(self):
        _a11y_write(_A11Y_MENU, "0")

    def show(self):
        self._embedded = True
        self._window.set_visible(True)
        try:
            self._window.present()
        except Exception:
            pass
        _a11y_write(_A11Y_SHOWN, "1")
        self._start_pollers()

    def hide(self):
        self._embedded = False
        try:
            self._window.set_visible(False)
        except Exception:
            pass
        _a11y_write(_A11Y_SHOWN, "0")
        self._hide_menu()

    def _start_pollers(self):
        if self._poll_started:
            return
        self._poll_started = True

        def _click_tick():
            want = _a11y_read(_A11Y_CLICK)
            if not want:
                return True
            try:
                os.remove(_A11Y_CLICK)
            except Exception:
                pass
            if want == "1":
                _toggle_manager()
            else:
                self._show_menu()
            return True

        def _escape_tick():
            if _a11y_read(_A11Y_MENU) != "1":
                return True
            if _a11y_read("/tmp/vmm-a11y-systray-escape"):
                try:
                    os.remove("/tmp/vmm-a11y-systray-escape")
                except Exception:
                    pass
                self._hide_menu()
            return True

        GLib.timeout_add(50, _click_tick)
        GLib.timeout_add(50, _escape_tick)


class _TrayMainMenu(vmmGObject):
    """
    Helper class for maintaining the conn + VM menu list and updating
    it in place
    """

    def __init__(self):
        vmmGObject.__init__(self)
        self.topwin = None  # Need this for error callbacks from VMActionMenu

        self._menu = self._build_menu()

    def _cleanup(self):
        self._menu.destroy()
        self._menu = None

    ###########
    # UI init #
    ###########

    def _build_menu(self):
        """
        Build the top level conn list menu when clicking the icon
        """
        menu = Gtk.Menu()
        menu._vmm_menu_name = "vmm-systray-menu"
        menu.get_accessible().set_name("vmm-systray-menu")
        menu.add(Gtk.SeparatorMenuItem())

        exit_item = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
        exit_item.connect("activate", self._exit_app_cb)
        menu.add(exit_item)
        menu.show_all()
        return menu

    ######################
    # UI update routines #
    ######################

    # Helpers for stashing identifying data in the menu item objects
    def _get_lookupkey(self, child):
        return getattr(child, "_vmlookupkey", None)

    def _set_lookupkey(self, child, val):
        return setattr(child, "_vmlookupkey", val)

    def _get_sortkey(self, child):
        return getattr(child, "_vmsortkey", None)

    def _set_sortkey(self, child, val):
        return setattr(child, "_vmsortkey", val)

    def _set_vm_state(self, menu_item, vm):
        label = menu_item.get_child()
        label.set_text(vm.get_name_or_title())
        vm_action_menu = menu_item.get_submenu()
        vm_action_menu.update_widget_states(vm)

    def _build_vm_menuitem(self, vm):
        """
        Build a menu item representing a single VM
        """
        menu_item = Gtk.ImageMenuItem.new_with_label("FOO")
        menu_item.set_use_underline(False)
        vm_action_menu = vmmenu.VMActionMenu(self, lambda: vm)
        menu_item.set_submenu(vm_action_menu)
        self._set_lookupkey(menu_item, vm)
        self._set_sortkey(menu_item, vm.get_name_or_title())
        self._set_vm_state(menu_item, vm)
        menu_item.show_all()
        return menu_item

    def _set_conn_state(self, menu_item, conn):
        label = menu_item.get_child()
        if conn.is_active():
            label = menu_item.get_child()
            markup = "<b>%s</b>" % xmlutil.xml_escape(conn.get_pretty_desc())
            label.set_markup(markup)
        else:
            label.set_text(conn.get_pretty_desc())

        connect_item = self._find_lookupkey(menu_item.get_submenu(), 1)
        disconnect_item = self._find_lookupkey(menu_item.get_submenu(), 2)
        connect_item.set_visible(conn.is_active())
        disconnect_item.set_visible(not conn.is_active())

    def _build_conn_menuitem(self, conn):
        """
        Build a menu item representing a single connection, and populate
        all its VMs as items in a sub menu
        """
        menu_item = Gtk.MenuItem.new_with_label("FOO")
        self._set_lookupkey(menu_item, conn.get_uri())

        # Group active conns first
        # Sort by pretty desc within those categories
        sortkey = str(int(bool(not conn.is_active())))
        sortkey += conn.get_pretty_desc().lower()
        self._set_sortkey(menu_item, sortkey)

        menu = Gtk.Menu()
        menu_item.set_submenu(menu)

        menu.add(Gtk.SeparatorMenuItem())
        citem1 = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_DISCONNECT, None)
        citem1.connect("activate", _conn_disconnect_cb, conn.get_uri())
        self._set_lookupkey(citem1, 1)
        menu.add(citem1)
        citem2 = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_CONNECT, None)
        citem2.connect("activate", _conn_connect_cb, conn.get_uri())
        self._set_lookupkey(citem2, 2)
        menu.add(citem2)

        menu_item.show_all()
        self._set_conn_state(menu_item, conn)
        return menu_item

    def _find_lookupkey(self, parent, key):
        for child in parent.get_children():
            if self._get_lookupkey(child) == key:
                return child

    def _find_conn_menuitem(self, uri):
        return self._find_lookupkey(self._menu, uri)

    def _find_vm_menuitem(self, uri, vm):
        connmenu = self._find_conn_menuitem(uri)
        return self._find_lookupkey(connmenu.get_submenu(), vm)

    ################
    # UI listeners #
    ################

    def _exit_app_cb(self, src):
        from .engine import vmmEngine

        vmmEngine.get_instance().exit_app()

    ##############
    # Public API #
    ##############

    def get_menu(self):
        return self._menu

    def conn_add(self, conn):
        connmenu = self._build_conn_menuitem(conn)
        sortkey = self._get_sortkey(connmenu)

        idx = 0
        for idx, child in enumerate(list(self._menu.get_children())):
            checksort = self._get_sortkey(child)
            if checksort is None or checksort > sortkey:
                break

        self._menu.insert(connmenu, idx)

    def conn_remove(self, uri):
        connmenu = self._find_conn_menuitem(uri)
        if connmenu:
            self._menu.remove(connmenu)
            connmenu.destroy()

    def conn_change(self, conn):
        connmenu = self._find_conn_menuitem(conn.get_uri())
        self._set_conn_state(connmenu, conn)

    def vm_add(self, vm):
        connmenu = self._find_conn_menuitem(vm.conn.get_uri())
        menu_item = self._build_vm_menuitem(vm)
        sortkey = self._get_sortkey(menu_item)

        vmsubmenu = connmenu.get_submenu()
        idx = 0
        for idx, child in enumerate(list(vmsubmenu.get_children())):
            checksort = self._get_sortkey(child)
            if checksort is None or checksort > sortkey:
                break

        vmsubmenu.insert(menu_item, idx)

    def vm_remove(self, vm):
        conn = vm.conn
        connmenu = self._find_conn_menuitem(conn.get_uri())
        vmitem = self._find_vm_menuitem(conn.get_uri(), vm)
        connmenu.get_submenu().remove(vmitem)
        vmitem.destroy()

    def vm_change(self, vm):
        vmitem = self._find_vm_menuitem(vm.conn.get_uri(), vm)
        self._set_vm_state(vmitem, vm)


class vmmSystray(vmmGObject):
    """
    API class representing a systray icon. May use StatusIcon or appindicator
    backends
    """

    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = vmmSystray()
        return cls._instance

    @staticmethod
    def systray_disabled_message():  # pragma: no cover
        # GTK 4 has no Gtk.StatusIcon. StatusNotifierItem is implemented
        # here and retried until a watcher (GNOME extension, KDE, etc.)
        # appears, with an X11 XEmbed/standalone fallback. Do not hard
        # disable the preference on Wayland.
        return None

    def __init__(self):
        vmmGObject.__init__(self)
        self._cleanup_on_app_close()

        self._systray = None
        self._mainmenu = None

        self.add_gsettings_handle(
            self.config.on_view_system_tray_changed(self._show_systray_changed_cb)
        )
        self._startup()

    def is_embedded(self):
        return self._systray and self._systray.is_embedded()

    def show_from_cli(self):
        self._show_systray()

    def _cleanup(self):
        self._hide_systray()
        self._systray = None
        if self._mainmenu:
            self._mainmenu.cleanup()
            self._mainmenu = None

    ###########################
    # Initialization routines #
    ###########################

    def _init_mainmenu(self):
        self._mainmenu = _TrayMainMenu()
        connmanager = vmmConnectionManager.get_instance()
        connmanager.connect("conn-added", self._conn_added_cb)
        connmanager.connect("conn-removed", self._conn_removed_cb)
        for conn in connmanager.conns.values():
            self._conn_added_cb(connmanager, conn)

    def _show_systray(self):
        if not self._systray:
            if self.config.CLITestOptions.fake_systray:
                self._systray = _SystrayWindow()
            elif _USING_APPINDICATOR:  # pragma: no cover
                self._systray = _SystrayIndicator()
            elif _has_appindicator_dbus() or "WAYLAND_DISPLAY" in os.environ:
                # Wayland has no XEmbed tray. Register SNI even if the
                # watcher is not up yet; show() retries registration.
                self._systray = _SystrayStatusNotifier()
            else:  # pragma: no cover
                self._systray = _SystrayStatusIcon()
            self._init_mainmenu()
            self._systray.set_menu(self._mainmenu.get_menu())
            if hasattr(self._systray, "set_publish_cb"):
                self._systray.set_publish_cb(self._publish_a11y_menu)
            self._start_a11y_pollers()
        self._systray.show()
        self._publish_a11y_menu()

    def _hide_systray(self):
        if not self._systray:
            return
        self._systray.hide()

    def _show_systray_changed_cb(self):
        do_show = self.config.get_view_system_tray()
        log.debug("Showing systray: %s", do_show)

        if do_show:
            self._show_systray()
        else:
            self._hide_systray()

    def _startup(self):
        # This will trigger the actual UI showing
        self._show_systray_changed_cb()

    ################
    # UI listeners #
    ################

    def _conn_added_cb(self, src, conn):
        conn.connect("vm-added", self._vm_added_cb)
        conn.connect("vm-removed", self._vm_removed_cb)
        conn.connect("state-changed", self._conn_state_changed_cb)
        self._mainmenu.conn_add(conn)
        for vm in conn.list_vms():
            self._vm_added_cb(conn, vm)
        self._publish_a11y_menu()

    def _conn_removed_cb(self, src, conn):
        self._mainmenu.conn_remove(conn)
        self._publish_a11y_menu()

    def _conn_state_changed_cb(self, conn):
        self._mainmenu.conn_change(conn)
        self._publish_a11y_menu()

    def _vm_added_cb(self, conn, vm):
        vm.connect("state-changed", self._vm_state_changed_cb)
        self._mainmenu.vm_add(vm)
        self._publish_a11y_menu()

    def _vm_removed_cb(self, conn, vm):
        self._mainmenu.vm_remove(vm)
        self._publish_a11y_menu()

    def _vm_state_changed_cb(self, vm):
        self._mainmenu.vm_change(vm)
        self._publish_a11y_menu()

    def _publish_a11y_menu(self):
        lines = []
        try:
            connmanager = vmmConnectionManager.get_instance()
            for conn in connmanager.conns.values():
                try:
                    desc = conn.get_pretty_desc() or ""
                    state = "active" if conn.is_active() else "inactive"
                    lines.append("CONN\t%s\t%s" % (desc, state))
                    for vm in conn.list_vms():
                        try:
                            name = vm.get_name_or_title() or ""
                            if vm.is_paused():
                                vmstate = "paused"
                            elif vm.is_runable():
                                vmstate = "shutoff"
                            else:
                                vmstate = "running"
                            lines.append("VM\t%s\t%s\t%s" % (desc, name, vmstate))
                        except Exception:
                            pass
                except Exception:
                    pass
            lines.append("QUIT\tQuit")
            _a11y_write(_A11Y_ITEMS, "\n".join(lines) + "\n")
        except Exception:
            pass

    def _match_conn(self, want):
        want = (want or "").strip().lower()
        if not want:
            return None
        fuzzy = None
        try:
            connmanager = vmmConnectionManager.get_instance()
            for conn in connmanager.conns.values():
                desc = (conn.get_pretty_desc() or "").lower()
                if want == desc:
                    return conn
                if want in desc:
                    fuzzy = conn
        except Exception:
            return None
        return fuzzy

    def _match_vm(self, conn, want):
        want = (want or "").strip().lower()
        if not conn or not want:
            return None
        fuzzy = None
        try:
            for vm in conn.list_vms():
                name = (vm.get_name_or_title() or "").lower()
                if want == name:
                    return vm
                if want in name:
                    fuzzy = vm
        except Exception:
            return None
        return fuzzy

    def _start_a11y_pollers(self):
        if getattr(self, "_vmm_systray_poll", False):
            return
        self._vmm_systray_poll = True

        def _action_tick():
            raw = _a11y_read(_A11Y_ACTION)
            if not raw:
                return True
            parts = raw.split("\t")
            if not parts:
                try:
                    os.remove(_A11Y_ACTION)
                except Exception:
                    pass
                return True
            kind = parts[0].strip().lower()
            try:
                if kind == "quit":
                    try:
                        os.remove(_A11Y_ACTION)
                    except Exception:
                        pass
                    from .engine import vmmEngine

                    vmmEngine.get_instance().exit_app()
                elif kind in ("connect", "disconnect") and len(parts) >= 2:
                    conn = self._match_conn(parts[1])
                    if conn is None:
                        return True
                    try:
                        os.remove(_A11Y_ACTION)
                    except Exception:
                        pass
                    if kind == "connect":
                        if conn.is_disconnected():
                            conn.open()
                    elif not conn.is_disconnected():
                        conn.close()
                elif kind in ("pause", "resume") and len(parts) >= 3:
                    conn = self._match_conn(parts[1])
                    vm = self._match_vm(conn, parts[2])
                    if vm is None:
                        return True
                    try:
                        os.remove(_A11Y_ACTION)
                    except Exception:
                        pass
                    if kind == "pause":
                        vm.suspend()
                    else:
                        vm.resume()
                    try:
                        vm.recache_from_event_loop()
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(_A11Y_ACTION)
                    except Exception:
                        pass
            except Exception:
                log.debug("systray a11y action failed: %s", raw, exc_info=True)
                try:
                    os.remove(_A11Y_ACTION)
                except Exception:
                    pass
            _a11y_write(_A11Y_MENU, "0")
            self._publish_a11y_menu()
            return True

        def _items_tick():
            if self._systray and self._systray.is_embedded():
                self._publish_a11y_menu()
            return True

        GLib.timeout_add(50, _action_tick)
        GLib.timeout_add(200, _items_tick)
