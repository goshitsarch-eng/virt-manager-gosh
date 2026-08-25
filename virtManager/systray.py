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


def _toggle_manager(*args, **kwargs):
    ignore = args
    ignore = kwargs
    from .manager import vmmManager

    manager = vmmManager.get_instance(None)
    if manager.is_visible():
        manager.close()
    else:
        manager.show()


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


class _SystrayIndicator(_Systray):  # pragma: no cover
    """
    UI backend for appindicator
    """

    def __init__(self):
        self._icon = AppIndicator3.Indicator.new(
            "virt-manager", "virt-manager", AppIndicator3.IndicatorCategory.APPLICATION_STATUS
        )

    def set_menu(self, menu):
        hide_item = Gtk.MenuItem.new_with_mnemonic(_("_Show Virtual Machine Manager"))
        hide_item.connect("activate", _toggle_manager)
        hide_item.show()
        menu.insert(hide_item, len(menu.get_children()) - 1)

        self._icon.set_menu(menu)
        self._icon.set_secondary_activate_target(hide_item)

    def is_embedded(self):
        if not self._icon.get_property("connected"):
            return False
        return self._icon.get_status() != AppIndicator3.IndicatorStatus.PASSIVE

    def show(self):
        self._icon.set_status(AppIndicator3.IndicatorStatus.ACTIVE)

    def hide(self):
        self._icon.set_status(AppIndicator3.IndicatorStatus.PASSIVE)


class _SystrayStatusIcon(_Systray):  # pragma: no cover
    """
    GTK 4 no longer has Gtk.StatusIcon. Show a compact tray window that
    still exposes the full connection/VM action menu.
    """

    def __init__(self):
        self._window = Gtk.Window()
        self._window.set_title(_("Virtual Machine Manager"))
        self._window.set_default_size(48, 48)
        self._window.set_decorated(True)
        self._window.set_resizable(False)
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

    def is_embedded(self):
        return self._visible

    def set_menu(self, menu):
        self._menu = menu

    def _popup_menu(self, *_args):
        if self._menu:
            self._menu.popup_at_widget(self._window)

    def show(self):
        self._visible = True
        self._window.set_visible(True)

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
  </interface>
</node>
"""


class _SystrayStatusNotifier(_Systray):  # pragma: no cover
    """
    Freedesktop/KDE StatusNotifierItem tray icon. This is the GTK 4
    replacement for Gtk.StatusIcon and AppIndicator3 menus.
    """

    def __init__(self):
        self._menu = None
        self._status = "Passive"
        self._bus = None
        self._owner_id = 0
        self._reg_id = 0
        self._window = Gtk.Window()
        self._window.set_title(_("Virtual Machine Manager"))
        self._window.set_default_size(1, 1)
        self._window.set_decorated(False)
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
            self._owner_id = Gio.bus_own_name_on_connection(
                self._bus,
                "org.kde.StatusNotifierItem-%s-1" % os.getpid(),
                Gio.BusNameOwnerFlags.NONE,
                self._on_name_acquired,
                None,
            )
        except Exception:
            log.debug("StatusNotifierItem setup failed", exc_info=True)

    def _on_name_acquired(self, connection, name):
        ignore = connection
        try:
            watcher = Gio.DBusProxy.new_sync(
                self._bus,
                0,
                None,
                "org.kde.StatusNotifierWatcher",
                "/StatusNotifierWatcher",
                "org.kde.StatusNotifierWatcher",
                None,
            )
            watcher.RegisterStatusNotifierItem("(s)", name)
        except Exception:
            try:
                watcher = Gio.DBusProxy.new_sync(
                    self._bus,
                    0,
                    None,
                    "org.freedesktop.StatusNotifierWatcher",
                    "/StatusNotifierWatcher",
                    "org.freedesktop.StatusNotifierWatcher",
                    None,
                )
                watcher.RegisterStatusNotifierItem("(s)", name)
            except Exception:
                log.debug("No StatusNotifierWatcher to register with")

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        ignore = params
        if method == "Activate":
            _toggle_manager()
        elif method in ("ContextMenu", "SecondaryActivate"):
            if self._menu:
                self._menu.popup_at_widget(self._window)
        invocation.return_value(None)

    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "virt-manager"),
            "Title": GLib.Variant("s", _("Virtual Machine Manager")),
            "Status": GLib.Variant("s", self._status),
            "WindowId": GLib.Variant("i", 0),
            "IconName": GLib.Variant("s", "virt-manager"),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/MenuBar"),
        }
        return values.get(name)

    def is_embedded(self):
        return self._status == "Active" and self._bus is not None

    def set_menu(self, menu):
        self._menu = menu

    def show(self):
        self._status = "Active"
        self._window.set_visible(True)

    def hide(self):
        self._status = "Passive"
        self._window.set_visible(False)


class _SystrayWindow(_Systray):
    """
    A mock systray implementation that shows its own top level window,
    so we can test more of the infrastructure in our ui tests
    """

    def __init__(self):
        self._window = None
        self._menu = None
        self._init_ui()

    def _init_ui(self):
        button = Gtk.Button(icon_name="list-add")
        gesture = Gtk.GestureClick()
        gesture.set_button(0)
        gesture.connect("pressed", self._popup_cb)
        button.add_controller(gesture)

        self._window = Gtk.Window()
        self._window.set_size_request(100, 100)
        from .lib import gtkcompat

        gtkcompat.set_accessible_name(self._window, "vmm-fake-systray")
        self._window.set_child(button)

    def is_embedded(self):
        return self._window.is_visible()

    def set_menu(self, menu):
        self._menu = menu

    def _popup_cb(self, gesture, _n, _x, _y):
        button = gesture.get_current_button()
        if button == 1:
            _toggle_manager()
        elif self._menu:
            self._menu.popup_at_widget(self._window)

    def show(self):
        self._window.set_visible(True)

    def hide(self):
        self._window.hide()


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
        if "WAYLAND_DISPLAY" not in os.environ:
            return
        if _USING_APPINDICATOR:
            return
        return _("AppIndicator not installed or usable, system tray icon not available on Wayland")

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
            elif _has_appindicator_dbus():  # pragma: no cover
                self._systray = _SystrayStatusNotifier()
            else:  # pragma: no cover
                self._systray = _SystrayStatusIcon()
            self._init_mainmenu()
            self._systray.set_menu(self._mainmenu.get_menu())
        self._systray.show()

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

    def _conn_removed_cb(self, src, conn):
        self._mainmenu.conn_remove(conn)

    def _conn_state_changed_cb(self, conn):
        self._mainmenu.conn_change(conn)

    def _vm_added_cb(self, conn, vm):
        vm.connect("state-changed", self._vm_state_changed_cb)
        self._mainmenu.vm_add(vm)

    def _vm_removed_cb(self, conn, vm):
        self._mainmenu.vm_remove(vm)

    def _vm_state_changed_cb(self, vm):
        self._mainmenu.vm_change(vm)
