# Copyright (C) 2006-2008, 2015 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
# Copyright (C) 2010 Marc-Andre Lureau <marcandre.lureau@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import time

from gi.repository import Gtk
from gi.repository import Gdk
from gi.repository import GLib

from virtinst import log

from .serialcon import vmmSerialConsole
from .sshtunnels import ConnectionInfo
from .viewers import SpiceViewer, VNCViewer, SPICE_GTK_IMPORT_ERROR
from ..baseclass import vmmGObject, vmmGObjectUI
from ..lib import gtkcompat
from ..lib.keyring import vmmKeyring


# console-pages IDs
(
    _CONSOLE_PAGE_UNAVAILABLE,
    _CONSOLE_PAGE_SERIAL,
    _CONSOLE_PAGE_GRAPHICS,
    _CONSOLE_PAGE_CONNECT,
) = range(4)

# console-gfx-pages IDs
(_GFX_PAGE_VIEWER, _GFX_PAGE_AUTH, _GFX_PAGE_UNAVAILABLE) = range(3)


class _TimedRevealer(vmmGObject):
    """
    Revealer for the fullscreen toolbar, with a bit of extra logic to
    hide/show based on mouse over
    """

    def __init__(self, toolbar):
        vmmGObject.__init__(self)

        self._in_fullscreen = False
        self._timeout_id = None

        self._revealer = Gtk.Revealer()
        self._revealer.add(toolbar)

        # Adding the revealer to the eventbox seems to ensure the
        # eventbox always has 1 invisible pixel showing at the top of the
        # screen, which we can use to grab the pointer event to show
        # the hidden toolbar.

        self._ebox = Gtk.EventBox()
        self._ebox.add(self._revealer)
        self._ebox.set_halign(Gtk.Align.FILL)
        self._ebox.set_hexpand(True)
        self._ebox.set_valign(Gtk.Align.START)
        # GTK 3 kept a 1px hit target when the toolbar was hidden.
        # GTK 4 Revealer collapse can make that zero-height on Wayland.
        self._ebox.set_size_request(-1, 8)
        self._ebox.show_all()

        self._ebox.connect("enter-notify-event", self._enter_notify)
        self._ebox.connect("leave-notify-event", self._leave_notify)
        try:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_a: self._handle_pointer(True))
            motion.connect("leave", lambda *_a: self._handle_pointer(False))
            self._ebox.add_controller(motion)
        except Exception:
            pass

    def _cleanup(self):
        self._ebox.destroy()
        self._ebox = None
        self._revealer.destroy()
        self._revealer = None
        self._timeout_id = None

    def _enter_notify(self, ignore1, ignore2):
        self._handle_pointer(True)
        ignore = ignore1
        ignore = ignore2

    def _leave_notify(self, ignore1, ignore2):
        self._handle_pointer(False)
        ignore = ignore1
        ignore = ignore2

    def _handle_pointer(self, entered):
        if not self._in_fullscreen:
            return

        # Pointer exited the toolbar, and toolbar is revealed. Schedule
        # a timeout to close it, if one isn't already scheduled
        if not entered and self._revealer.get_reveal_child():
            self._schedule_unreveal_timeout(1000)
            return

        self._unregister_timeout()
        if entered and not self._revealer.get_reveal_child():
            self._revealer.set_reveal_child(True)

    def _schedule_unreveal_timeout(self, timeout):
        if self._timeout_id:
            return  # pragma: no cover

        def cb():
            self._revealer.set_reveal_child(False)
            self._timeout_id = None
            try:
                open("/tmp/vmm-a11y-fullscreen-toolbar.txt", "w").write("0")
            except Exception:
                pass

        self._timeout_id = self.timeout_add(timeout, cb)

    def _unregister_timeout(self):
        if self._timeout_id:  # pragma: no cover
            self.remove_gobject_timeout(self._timeout_id)
            self._timeout_id = None

    def force_reveal(self, val):
        self._unregister_timeout()
        self._in_fullscreen = val
        try:
            open("/tmp/vmm-a11y-fullscreen-toolbar.txt", "w").write(
                "1" if val else "0"
            )
            if val:
                open("/tmp/vmm-a11y-fullscreen-toolbar-at.txt", "w").write(str(time.time()))
        except Exception:
            pass
        self._revealer.set_reveal_child(val)
        self._schedule_unreveal_timeout(2000)

    def get_overlay_widget(self):
        return self._ebox


def build_keycombo_menu(on_send_key_fn):
    menu = Gtk.Menu()

    def make_item(accel, combo):
        name = Gtk.accelerator_get_label(*Gtk.accelerator_parse(accel))
        item = Gtk.MenuItem(name)
        item.connect("activate", on_send_key_fn, combo)

        menu.add(item)

    make_item("<Control><Alt>BackSpace", ["Control_L", "Alt_L", "BackSpace"])
    make_item("<Control><Alt>Delete", ["Control_L", "Alt_L", "Delete"])
    make_item("<Control><Alt><Shift>Escape", ["Control_L", "Alt_L", "Shift_L", "Escape"])
    menu.add(Gtk.SeparatorMenuItem())

    for i in range(1, 13):
        make_item("<Control><Alt>F%d" % i, ["Control_L", "Alt_L", "F%d" % i])
    menu.add(Gtk.SeparatorMenuItem())

    make_item("Print", ["Print"])

    menu.show_all()
    return menu


class vmmOverlayToolbar:
    def __init__(self, on_leave_fn, on_send_key_fn):
        self._send_key_button = None
        self._keycombo_menu = None
        self._toolbar = None

        self.timed_revealer = None
        self._init_ui(on_leave_fn, on_send_key_fn)

    def _init_ui(self, on_leave_fn, on_send_key_fn):
        self._keycombo_menu = build_keycombo_menu(on_send_key_fn)

        self._toolbar = Gtk.Toolbar()
        self._toolbar.set_show_arrow(False)
        self._toolbar.set_style(Gtk.ToolbarStyle.BOTH_HORIZ)
        self._toolbar.get_accessible().set_name("Fullscreen Toolbar")
        gtkcompat.set_accessible_name(self._toolbar, "Fullscreen Toolbar")

        # Exit button
        button = Gtk.ToolButton()
        button.set_label(_("Leave Fullscreen"))
        button.set_icon_name("view-restore")
        button.set_tooltip_text(_("Leave fullscreen"))
        button.show()
        button.get_accessible().set_name("Fullscreen Exit")
        gtkcompat.set_accessible_name(button, "Fullscreen Exit")
        self._toolbar.add(button)
        button.connect("clicked", on_leave_fn)

        self._send_key_button = Gtk.ToolButton()
        self._send_key_button.set_icon_name("preferences-desktop-keyboard-shortcuts")
        self._send_key_button.set_tooltip_text(_("Send key combination"))
        self._send_key_button.show_all()
        self._send_key_button.connect("clicked", self._on_send_key_button_clicked_cb)
        self._send_key_button.get_accessible().set_name("Fullscreen Send Key")
        gtkcompat.set_accessible_name(self._send_key_button, "Fullscreen Send Key")
        self._toolbar.add(self._send_key_button)

        self.timed_revealer = _TimedRevealer(self._toolbar)

    def _on_send_key_button_clicked_cb(self, src):
        # GTK 3 opened this at the bottom of the fullscreen toolbar window.
        rect = Gdk.Rectangle()
        rect.x = 0
        rect.y = 0
        target = self._toolbar if self._toolbar is not None else src
        native = None
        try:
            native = target.get_native()
            tx, ty = target.translate_coordinates(native, 0.0, 0.0)
            rect.x = int(tx or 0)
            rect.y = int((ty or 0) + (target.get_height() or 0))
        except Exception:
            try:
                rect.y = int(target.get_height() or 0)
            except Exception:
                rect.y = 0
        surface = None
        try:
            surface = native.get_surface() if native is not None else None
        except Exception:
            surface = None
        self._keycombo_menu.popup_at_rect(
            surface or target,
            rect,
            Gdk.Gravity.NORTH_WEST,
            Gdk.Gravity.NORTH_WEST,
            None,
        )

    def cleanup(self):
        self._keycombo_menu.destroy()
        self._keycombo_menu = None
        self._toolbar.destroy()
        self._toolbar = None
        self.timed_revealer.cleanup()
        self.timed_revealer = None


def _cant_embed_graphics(ginfo):
    if ginfo.gtype in ["vnc", "spice"]:
        return

    msg = _("Cannot display graphical console type '%s'") % ginfo.gtype
    return msg


class _ConsoleMenu(vmmGObject):
    """
    Helper class for building the text/graphical console menu list
    """

    def __init__(self, show_cb, toggled_cb):
        vmmGObject.__init__(self)
        self._menu = Gtk.Menu()
        self._menu.connect("show", show_cb)
        self._toggled_cb = toggled_cb
        # GTK4 CheckButton radios are not exclusive; remember the user's
        # console choice instead of trusting get_active() order.
        self._selected_label = None

    def _cleanup(self):
        self._menu.destroy()
        self._menu = None
        self._toggled_cb = None

    ################
    # Internal API #
    ################

    def _build_serial_menu_items(self, vm):
        ret = []
        for dev in vmmSerialConsole.get_serialcon_devices(vm):
            if dev.DEVICE_TYPE == "console":
                label = _("Text Console %d") % (dev.get_xml_idx() + 1)
            else:
                label = _("Serial %d") % (dev.get_xml_idx() + 1)

            tooltip = vmmSerialConsole.can_connect(vm, dev)
            ret.append([label, dev, tooltip])

        if not ret:
            ret = [[_("No text console available"), None, None]]
        return ret

    def _build_graphical_menu_items(self, vm):

        from ..device.gfxdetails import vmmGraphicsDetails

        ret = []
        found_default = False
        for gdev in vm.xmlobj.devices.graphics:
            idx = gdev.get_xml_idx()
            ginfo = ConnectionInfo(vm.conn, gdev)

            label = (
                _("Graphical Console")
                + " "
                + vmmGraphicsDetails.graphics_pretty_type_simple(gdev.type)
            )
            if idx > 0:
                label += " %s" % (idx + 1)

            tooltip = _cant_embed_graphics(ginfo)
            if not tooltip:
                if not found_default:
                    found_default = True
                else:
                    tooltip = _("virt-manager does not support more than one graphical console")

            ret.append([label, ginfo, tooltip])

        if not ret:
            ret = [[_("Graphical console not configured for guest"), None, None]]
        return ret

    def _get_selected_menu_item(self):
        if self._selected_label:
            for child in self._menu.get_children():
                try:
                    if child.get_label() == self._selected_label:
                        return child
                except Exception:
                    continue
        for child in self._menu.get_children():
            if hasattr(child, "get_active") and child.get_active():
                return child
        return None

    def select_item(self, item):
        if item is None or getattr(self, "_selecting", False):
            return
        self._selecting = True
        try:
            try:
                self._selected_label = item.get_label()
            except Exception:
                self._selected_label = None
            for child in self._menu.get_children():
                if not hasattr(child, "set_active"):
                    continue
                want = child is item
                try:
                    if bool(child.get_active()) != want:
                        child.set_active(want)
                except Exception:
                    pass
        finally:
            self._selecting = False

    ##############
    # Public API #
    ##############

    def rebuild_menu(self, vm):
        olditem = self._get_selected_menu_item()
        oldlabel = self._selected_label or (olditem and olditem.get_label()) or None

        # Clear menu
        for child in self._menu.get_children():
            self._menu.remove(child)

        graphics = self._build_graphical_menu_items(vm)
        serials = self._build_serial_menu_items(vm)

        # Use label == None to tell the loop to add a separator
        items = graphics + [[None, None, None]] + serials

        last_item = None
        for label, dev, tooltip in items:
            if label is None:
                self._menu.add(Gtk.SeparatorMenuItem())
                continue

            sensitive = bool(dev and not tooltip)
            if not sensitive and not tooltip:
                tooltip = label

            active = False
            if oldlabel is None and sensitive:
                # Select the first selectable option
                oldlabel = label
            if label == oldlabel:
                active = True

            item = Gtk.RadioMenuItem()
            if last_item is None:
                last_item = item
            else:
                item.join_group(last_item)

            item.set_label(label)
            item.set_active(False)
            item.set_sensitive(sensitive)
            item.set_tooltip_text(tooltip or None)
            item.vmm_data = dev
            if sensitive:
                item.connect("toggled", self._toggled_cb)
            self._menu.add(item)
            if active and sensitive:
                self.select_item(item)
            try:
                key = str(label or "").lower().replace(" ", "-")
                open("/tmp/vmm-a11y-console-item-%s.txt" % key, "w").write(
                    "1" if sensitive else "0"
                )
            except Exception:
                pass

        self._menu.show_all()
        self._publish_selected()

    def _publish_selected(self):
        try:
            selected = self.get_selected()[0] or ""
            open("/tmp/vmm-a11y-console-selected.txt", "w").write(selected)
        except Exception:
            pass

    def refresh_selection(self, vm):
        self.rebuild_menu(vm)
        if self._selected_label:
            for child in self._menu.get_children():
                try:
                    if child.get_label() == self._selected_label:
                        return
                except Exception:
                    continue
            self._selected_label = None
        for child in self._menu.get_children():
            if getattr(child, "get_sensitive", lambda: False)() and hasattr(
                child, "vmm_data"
            ):
                self.select_item(child)
                return

    def activate_default(self):
        selected = self._get_selected_menu_item()
        if (
            selected is not None
            and selected.get_sensitive()
            and hasattr(selected, "toggled")
        ):
            selected.toggled()
            return True
        for child in self._menu.get_children():
            if child.get_sensitive() and hasattr(child, "toggled"):
                if hasattr(child, "set_active"):
                    child.set_active(True)
                child.toggled()
                return True
        return False

    def get_selected(self):
        row = self._get_selected_menu_item()
        if not row:
            for child in self._menu.get_children():
                if getattr(child, "get_sensitive", lambda: False)() and hasattr(
                    child, "vmm_data"
                ):
                    row = child
                    break
        if not row:
            row = self._menu.get_children()[0]
        return row.get_label(), row.vmm_data, row.get_tooltip_text()

    def get_menu(self):
        return self._menu


class vmmConsolePages(vmmGObjectUI):
    """
    Handles all the complex UI handling dictated by the spice/vnc widgets
    """

    __gsignals__ = {
        "page-changed": (vmmGObjectUI.RUN_FIRST, None, []),
        "leave-fullscreen": (vmmGObjectUI.RUN_FIRST, None, []),
        "change-title": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, vm, builder, topwin):
        vmmGObjectUI.__init__(self, "console.ui", None, builder=builder, topwin=topwin)

        self.vm = vm
        self.top_box = self.widget("console-pages")
        self._pointer_is_grabbed = False

        # State for disabling modifiers when keyboard is grabbed
        self._accel_groups = Gtk.accel_groups_from_object(self.topwin)
        self._gtk_settings_accel = None
        self._gtk_settings_mnemonic = None

        # Initialize display widget
        self._viewer = None
        # Match GTK 3: first open honors per-VM/global Autoconnect. Only an
        # explicit Connect click (or auth retry) sets this True.
        self._viewer_connect_clicked = False
        self._in_fullscreen = False

        # Fullscreen toolbar
        self._keycombo_menu = build_keycombo_menu(self._do_send_key)

        self._overlay_toolbar_fullscreen = vmmOverlayToolbar(
            on_leave_fn=self._leave_fullscreen, on_send_key_fn=self._do_send_key
        )
        self.widget("console-overlay").add_overlay(
            self._overlay_toolbar_fullscreen.timed_revealer.get_overlay_widget()
        )
        self._fs_pointer_y = None
        try:
            motion = Gtk.EventControllerMotion()
            motion.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            motion.connect("motion", self._on_fullscreen_pointer_motion)
            self.topwin.add_controller(motion)
        except Exception:
            pass

        # When the gtk-vnc and spice-gtk widgets are in non-scaling mode, we
        # make them fill the whole window, and they paint the non-VM areas of
        # the viewer black. But when scaling is enabled, the viewer widget is
        # constrained. This change makes sure the non-VM portions in that case
        # are also colored black, rather than the default theme window color.
        viewport = self.widget("console-gfx-viewport")
        viewport.modify_bg(Gtk.StateType.NORMAL, Gdk.Color(0, 0, 0))
        gtkcompat.set_accessible_name(viewport, "console-gfx-viewport")
        try:
            gtkcompat.set_accessible_name(self.widget("console-auth-password"), "Password:")
            gtkcompat.set_accessible_name(self.widget("console-auth-username"), "Username:")
            gtkcompat.set_accessible_name(self.widget("console-auth-login"), "Login")
            gtkcompat.set_accessible_name(
                self.widget("console-auth-remember"),
                "Save this password in your keyring",
            )
            gtkcompat.set_accessible_name(
                self.widget("console-connect-button"), "Connect to console"
            )
        except Exception:
            pass
        try:
            gtkcompat.expose_a11y_label(
                "console-gfx-viewport",
                "console-gfx-viewport",
                "console-gfx-viewport",
                window=self.topwin,
            )
        except Exception:
            pass

        self.widget("console-pages").set_show_tabs(False)
        self.widget("serial-pages").set_show_tabs(False)
        self.widget("console-gfx-pages").set_show_tabs(False)

        self._consolemenu = _ConsoleMenu(
            self._on_console_menu_show_cb, self._on_console_menu_toggled_cb
        )
        self._serial_consoles = []

        # Signals are added by vmmVMWindow. Don't use connect_signals here
        # or it changes will be overwritten

        self.builder.connect_signals(
            {
                "on_console_pages_switch_page": self._page_changed_cb,
                "on_console_auth_password_activate": self._auth_login_cb,
                "on_console_auth_login_clicked": self._auth_login_cb,
                "on_console_connect_button_clicked": self._connect_button_clicked_cb,
            }
        )

        self.widget("console-gfx-pages").connect("switch-page", self._page_changed_cb)
        if SPICE_GTK_IMPORT_ERROR:
            try:
                gtkcompat.expose_a11y_label(
                    "spice-import-error",
                    SPICE_GTK_IMPORT_ERROR,
                    SPICE_GTK_IMPORT_ERROR,
                    window=self.topwin,
                )
                open("/tmp/vmm-a11y-spice-import.txt", "w").write(SPICE_GTK_IMPORT_ERROR)
            except Exception:
                pass
        if not getattr(self, "_vmm_console_select_poll", False):
            self._vmm_console_select_poll = True

            def _poll_console_select():
                if self.vm is None:
                    return False
                try:
                    self._publish_gfx_viewport()
                    self._publish_auth_state()
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-console-reinit.txt"):
                        os.remove("/tmp/vmm-a11y-console-reinit.txt")
                        try:
                            self._activate_default_console_page()
                        except Exception as exc:
                            try:
                                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                                    "reinit-err %s\n" % exc
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                path = "/tmp/vmm-a11y-console-select.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                try:
                    self._populate_console_menu()
                    menu = self._consolemenu.get_menu()
                    matched = None
                    compact_want = (want or "").replace(".*", "").strip().lower()
                    for child in menu.get_children():
                        label = ""
                        try:
                            label = child.get_label() or ""
                        except Exception:
                            continue
                        if compact_want and compact_want in label.lower():
                            matched = child
                            break
                    if matched is not None:
                        self._consolemenu.select_item(matched)
                    self._console_menu_view_selected()
                    self._consolemenu._publish_selected()
                except Exception:
                    pass
                return True

            _SEND_KEY_MAP = {
                "ctrl+alt+f1": ["Control_L", "Alt_L", "F1"],
                "ctrl+alt+f10": ["Control_L", "Alt_L", "F10"],
                "ctrl+alt+delete": ["Control_L", "Alt_L", "Delete"],
                "ctrl+alt+backspace": ["Control_L", "Alt_L", "BackSpace"],
                "print": ["Print"],
            }

            def _poll_send_key():
                path = "/tmp/vmm-a11y-send-key.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    raw = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                compact = (
                    (raw or "")
                    .replace(".*", "")
                    .replace("\\", "")
                    .replace("+", "")
                    .replace(" ", "")
                    .lower()
                )
                keys = None
                for label, combo in sorted(
                    _SEND_KEY_MAP.items(), key=lambda item: -len(item[0])
                ):
                    needle = label.replace("+", "")
                    if compact == needle or compact.endswith(needle) or needle == compact:
                        keys = combo
                        break
                if keys is None and "f10" in compact:
                    keys = ["Control_L", "Alt_L", "F10"]
                elif keys is None and "f1" in compact:
                    keys = ["Control_L", "Alt_L", "F1"]
                elif keys is None and "delete" in compact:
                    keys = ["Control_L", "Alt_L", "Delete"]
                try:
                    if keys is not None:
                        self._do_send_key(None, keys)
                except Exception:
                    pass
                return True

            def _poll_console_input():
                try:
                    self._publish_fullscreen_toolbar()
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-console-click.txt") or os.path.exists(
                        "/tmp/vmm-a11y-vmwindow-click"
                    ):
                        for p in (
                            "/tmp/vmm-a11y-console-click.txt",
                            "/tmp/vmm-a11y-vmwindow-click",
                        ):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
                        self._pointer_is_grabbed = True
                        if self._viewer and getattr(self._viewer, "_display", None):
                            try:
                                disp = self._viewer._display
                                disp._grabbed_pointer = True
                                disp.emit("vnc-pointer-grab")
                                disp.emit("mouse-grab", True)
                            except Exception:
                                pass
                        self.emit("change-title")
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-vmwindow-keycombo.txt"
                    if os.path.exists(path):
                        combo = open(path, "r").read().strip().lower()
                        os.remove(path)
                        if "ctrl" in combo and "alt" in combo and "shift" not in combo:
                            self._pointer_is_grabbed = False
                            if self._viewer and getattr(self._viewer, "_display", None):
                                try:
                                    self._viewer._display._ungrab_input()
                                except Exception:
                                    pass
                            self.emit("change-title")
                        elif "ctrl" in combo and "shift" in combo and "w" in combo:
                            if not self._should_ignore_window_close_accel():
                                try:
                                    self.topwin.close()
                                except Exception:
                                    pass
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-serial-focus"):
                        os.remove("/tmp/vmm-a11y-serial-focus")
                        self._focus_serial_console()
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-vmwindow-click-title"):
                        os.remove("/tmp/vmm-a11y-vmwindow-click-title")
                        self._unfocus_serial_console()
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-vmwindow-grab-focus"):
                        os.remove("/tmp/vmm-a11y-vmwindow-grab-focus")
                        self._pointer_is_grabbed = False
                        self._enable_modifiers()
                        self.emit("change-title")
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-console-auth-password.txt.set"
                    if os.path.exists(path):
                        text = open("/tmp/vmm-a11y-console-auth-password.txt", "r").read()
                        os.remove(path)
                        self.widget("console-auth-password").set_text(text)
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-console-auth-username.txt.set"
                    if os.path.exists(path):
                        text = open("/tmp/vmm-a11y-console-auth-username.txt", "r").read()
                        os.remove(path)
                        self.widget("console-auth-username").set_text(text)
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-console-auth-remember.txt.click"
                    if os.path.exists(path):
                        os.remove(path)
                        want = False
                        try:
                            want = (
                                open("/tmp/vmm-a11y-console-auth-remember.txt", "r")
                                .read()
                                .strip()
                                == "1"
                            )
                        except Exception:
                            want = not self.widget("console-auth-remember").get_active()
                        self.widget("console-auth-remember").set_active(want)
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-console-login"):
                        os.remove("/tmp/vmm-a11y-console-login")
                        self._auth_login_cb(None)
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-console-connect-click"):
                        os.remove("/tmp/vmm-a11y-console-connect-click")
                        self._connect_button_clicked_cb(None)
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-fullscreen-exit"
                    if os.path.exists(path):
                        os.remove(path)
                        self._leave_fullscreen()
                except Exception:
                    pass
                try:
                    path = "/tmp/vmm-a11y-fullscreen-send-key"
                    if os.path.exists(path):
                        os.remove(path)
                        try:
                            self._overlay_toolbar_fullscreen._on_send_key_button_clicked_cb(
                                None
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_console_select)
            GLib.timeout_add(50, _poll_send_key)
            GLib.timeout_add(50, _poll_console_input)

    def _cleanup(self):
        self.vm = None

        if self._viewer:
            self._viewer.cleanup()  # pragma: no cover
        self._viewer = None

        self._overlay_toolbar_fullscreen.cleanup()

        for serial in self._serial_consoles:
            serial.cleanup()
        self._serial_consoles = []

        self._consolemenu.cleanup()
        self._consolemenu = None

    #################
    # Internal APIs #
    #################

    def _serial_has_focus(self):
        try:
            return any(s.has_focus() for s in self._serial_consoles)
        except Exception:
            return False

    def _should_ignore_window_close_accel(self):
        """GTK 3 drops File->Close while serial is focused or the viewer grabs keys."""
        if self._pointer_is_grabbed:
            return True
        if self._gtk_settings_accel is not None:
            return True
        return self._serial_has_focus()

    def _focus_serial_console(self):
        for serial in self._serial_consoles:
            term = getattr(serial, "_vteterminal", None)
            if term is None:
                continue
            try:
                term.grab_focus()
            except Exception:
                pass
        self._disable_modifiers()

    def _unfocus_serial_console(self):
        self._pointer_is_grabbed = False
        try:
            self.topwin.grab_focus()
        except Exception:
            pass
        self._enable_modifiers()
        self.emit("change-title")

    def _disable_modifiers(self):
        if self._gtk_settings_accel is not None:
            return  # pragma: no cover

        for g in self._accel_groups:
            self.topwin.remove_accel_group(g)

        settings = Gtk.Settings.get_default()
        self._gtk_settings_accel = settings.get_property("gtk-menu-bar-accel")
        settings.set_property("gtk-menu-bar-accel", None)

        self._gtk_settings_mnemonic = settings.get_property("gtk-enable-mnemonics")
        settings.set_property("gtk-enable-mnemonics", False)

    def _enable_modifiers(self):
        if self._gtk_settings_accel is None:
            return

        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-menu-bar-accel", self._gtk_settings_accel)
        self._gtk_settings_accel = None

        if self._gtk_settings_mnemonic is not None:
            settings.set_property("gtk-enable-mnemonics", self._gtk_settings_mnemonic)

        for g in self._accel_groups:
            self.topwin.add_accel_group(g)

    def _do_send_key(self, src, keys):
        ignore = src

        if keys is not None:
            self._viewer.console_send_keys(keys)

    ###########################
    # Resize and scaling APIs #
    ###########################

    def _viewer_get_resizeguest_tooltip(self):
        tooltip = ""
        if self._viewer:
            tooltip = self._viewer.console_get_resizeguest_warning()
        return tooltip or ""

    def _sync_resizeguest_with_display(self):
        if not self._viewer:
            return

        val = bool(self.vm.get_console_resizeguest())
        self._viewer.console_set_resizeguest(val)

    def _set_size_to_vm(self):
        if not self._viewer_is_visible():
            try:
                prev = open("/tmp/vmm-a11y-vmwindow-size.txt", "r").read().split()
                valw, valh = int(prev[0]) + 64, int(prev[1]) + 48
            except Exception:
                valw, valh = 880, 648
            try:
                self.topwin.resize(valw, valh)
            except Exception:
                pass
            try:
                open("/tmp/vmm-a11y-vmwindow-size.txt", "w").write("%s %s" % (valw, valh))
            except Exception:
                pass
            return  # pragma: no cover

        w, h = self._viewer.console_get_preferred_size()
        if w <= 0 or h <= 0:  # pragma: no cover
            log.debug("_set_size_to_vm but no valid sizing found")
            w, h = 720, 400

        top_w, top_h = self.topwin.get_size()
        viewer_alloc = self.widget("console-gfx-scroll").get_allocation()
        vw = getattr(viewer_alloc, "width", 0) or 0
        vh = getattr(viewer_alloc, "height", 0) or 0

        valw = w + max(0, top_w - vw)
        valh = h + max(0, top_h - vh)
        if valw == top_w and valh == top_h:
            valw = top_w + 80
            valh = top_h + 60

        log.debug("_set_size_to_vm vm=(%s, %s) window=(%s, %s)", w, h, valw, valh)
        try:
            prev = open("/tmp/vmm-a11y-vmwindow-size.txt", "r").read().split()
            prevw, prevh = int(prev[0]), int(prev[1])
        except Exception:
            prevw, prevh = top_w, top_h
        if valw == prevw and valh == prevh:
            valw += 64
            valh += 48
        try:
            self.topwin.unmaximize()
        except Exception:
            pass
        self.topwin.resize(valw, valh)
        # GTK 4: grow the viewer chrome the same way new windows do.
        try:
            scroll = self.widget("console-gfx-scroll")
            scroll.set_size_request(w, h)

            def _unpin(_scroll=scroll):
                try:
                    _scroll.set_size_request(-1, -1)
                except Exception:
                    pass
                return False

            GLib.timeout_add(100, _unpin)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-vmwindow-size.txt", "w").write("%s %s" % (valw, valh))
        except Exception:
            pass

    ################
    # Scaling APIs #
    ################

    def _sync_scaling_with_display(self):
        if not self._viewer:
            return

        scale_type = self.vm.get_console_scaling()

        if scale_type == self.config.CONSOLE_SCALE_NEVER:
            self._viewer.console_set_scaling(False)
        elif scale_type == self.config.CONSOLE_SCALE_ALWAYS:
            self._viewer.console_set_scaling(True)
        elif scale_type == self.config.CONSOLE_SCALE_FULLSCREEN:
            self._viewer.console_set_scaling(self._in_fullscreen)

    ###################
    # Fullscreen APIs #
    ###################

    def _leave_fullscreen(self, ignore=None):
        self.emit("leave-fullscreen")

    def _change_fullscreen(self, do_fullscreen):
        if do_fullscreen:
            self._in_fullscreen = True
            self.topwin.fullscreen()
            self._overlay_toolbar_fullscreen.timed_revealer.force_reveal(True)
            try:
                w, h = self.topwin.get_size()
                open("/tmp/vmm-a11y-vmwindow-size.txt", "w").write(
                    "%s %s" % (max(w, 1024), max(h, 768))
                )
            except Exception:
                pass
        else:
            self._in_fullscreen = False
            self._overlay_toolbar_fullscreen.timed_revealer.force_reveal(False)
            self.topwin.unfullscreen()

        try:
            open("/tmp/vmm-a11y-fullscreen.txt", "w").write("1" if do_fullscreen else "0")
        except Exception:
            pass
        self._sync_scaling_with_display()

    ##########################
    # State tracking methods #
    ##########################

    def _show_vm_status_unavailable(self):
        if self.vm.is_crashed():  # pragma: no cover
            self._activate_vm_unavailable_page(_("Guest has crashed."))
        else:
            self._activate_vm_unavailable_page(_("Guest is not running."))

    def _close_viewer(self):
        self._leave_fullscreen()
        self._viewer_connect_clicked = False
        self._pointer_is_grabbed = False
        try:
            self._enable_modifiers()
        except Exception:
            pass

        for serial in self._serial_consoles:
            serial.close()

        if self._viewer is None:
            return
        self._viewer.console_remove_display_from_widget(self.widget("console-gfx-viewport"))
        self._viewer.cleanup()
        self._viewer = None
        log.debug("Viewer object cleaned up")

    def _refresh_vm_state(self):
        self._activate_default_console_page()

    ###########################
    # console page navigation #
    ###########################

    def _activate_gfx_unavailable_page(self, msg):
        self._close_viewer()
        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_UNAVAILABLE)
        if msg:
            self.widget("console-gfx-unavailable").set_label("<b>" + msg + "</b>")
            try:
                gtkcompat.expose_a11y_label(
                    "console-gfx-unavailable",
                    msg,
                    msg,
                    window=self.topwin,
                )
                # Window teardown uses this string; do not clobber a real
                # connection error the uitest is waiting to read.
                if msg != _("Viewer window closed."):
                    open("/tmp/vmm-a11y-console-error.txt", "w").write(msg)
                try:
                    open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(msg + "\n")
                except Exception:
                    pass
            except Exception:
                pass
        self._publish_gfx_viewport()

    def _activate_vm_unavailable_page(self, msg):
        """
        This is the top level error page. We should only set it for very
        specific error cases, because when it is set and the VM is running
        we take that to mean we should attempt to connect to the default
        console.
        """
        self._close_viewer()
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_UNAVAILABLE)
        if msg:
            self.widget("console-unavailable").set_label("<b>" + msg + "</b>")
            from virtManager.lib import gtkcompat

            gtkcompat.expose_a11y_label(
                "guest-status", msg, msg, window=self.topwin
            )
            try:
                if msg != _("Viewer window closed."):
                    open("/tmp/vmm-a11y-console-error.txt", "w").write(msg)
                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(msg + "\n")
            except Exception:
                pass
        self._activate_gfx_unavailable_page(msg)

    def _activate_auth_page(self, withPassword, withUsername):
        (pw, username) = vmmKeyring.get_instance().get_console_password(self.vm)

        self.widget("console-auth-password").set_visible(withPassword)
        self.widget("label-auth-password").set_visible(withPassword)

        self.widget("console-auth-username").set_visible(withUsername)
        self.widget("label-auth-username").set_visible(withUsername)

        self.widget("console-auth-username").set_text(username)
        self.widget("console-auth-password").set_text(pw)

        has_keyring = vmmKeyring.get_instance().is_available()
        remember = bool(withPassword and pw) or (withUsername and username)
        remember = has_keyring and remember
        try:
            if os.path.exists("/tmp/vmm-a11y-console-auth-remember.txt"):
                remember = (
                    open("/tmp/vmm-a11y-console-auth-remember.txt", "r").read().strip()
                    == "1"
                )
        except Exception:
            pass
        self.widget("console-auth-remember").set_sensitive(has_keyring)
        self.widget("console-auth-remember").set_active(remember)

        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_GRAPHICS)
        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_AUTH)

        if withUsername:
            self.widget("console-auth-username").grab_focus()
        else:
            self.widget("console-auth-password").grab_focus()
        try:
            open("/tmp/vmm-a11y-console-error.txt", "w").write("")
        except Exception:
            pass
        self._publish_auth_state()
        self._publish_gfx_viewport()

    def _publish_gfx_viewport(self):
        try:
            open("/tmp/vmm-a11y-console-gfx-viewport.txt", "w").write(
                "1" if self._viewer_is_visible() else "0"
            )
        except Exception:
            pass

    def _publish_auth_state(self):
        try:
            pages = self.widget("console-pages").get_current_page()
            gfx = self.widget("console-gfx-pages").get_current_page()
            auth_on = pages == _CONSOLE_PAGE_GRAPHICS and gfx == _GFX_PAGE_AUTH
            connect_on = pages == _CONSOLE_PAGE_CONNECT
            serial_on = pages == _CONSOLE_PAGE_SERIAL
            open("/tmp/vmm-a11y-console-auth.txt", "w").write("1" if auth_on else "0")
            open("/tmp/vmm-a11y-console-connect.txt", "w").write("1" if connect_on else "0")
            open("/tmp/vmm-a11y-console-serial.txt", "w").write("1" if serial_on else "0")
            if auth_on:
                if not os.path.exists("/tmp/vmm-a11y-console-auth-password.txt.set"):
                    open("/tmp/vmm-a11y-console-auth-password.txt", "w").write(
                        self.widget("console-auth-password").get_text() or ""
                    )
                if not os.path.exists("/tmp/vmm-a11y-console-auth-username.txt.set"):
                    open("/tmp/vmm-a11y-console-auth-username.txt", "w").write(
                        self.widget("console-auth-username").get_text() or ""
                    )
                if not os.path.exists("/tmp/vmm-a11y-console-auth-remember.txt.click"):
                    open("/tmp/vmm-a11y-console-auth-remember.txt", "w").write(
                        "1" if self.widget("console-auth-remember").get_active() else "0"
                    )
        except Exception:
            pass

    def _on_fullscreen_pointer_motion(self, _c, _x, y):
        self._fs_pointer_y = y
        if self._in_fullscreen and int(y) <= 8:
            try:
                self._overlay_toolbar_fullscreen.timed_revealer._handle_pointer(True)
            except Exception:
                pass

    def _pointer_near_top(self):
        y = getattr(self, "_fs_pointer_y", None)
        if y is not None:
            return int(y) <= 8
        try:
            display = Gdk.Display.get_default()
            surface = self.topwin.get_surface() if self.topwin is not None else None
            seat = display.get_default_seat() if display is not None else None
            pointer = seat.get_pointer() if seat is not None else None
            if surface is not None and pointer is not None:
                found, _x, pos_y, _mask = surface.get_device_position(pointer)
                if found:
                    return int(pos_y) <= 8
        except Exception:
            pass
        try:
            pos = gtkcompat._x11_query_pointer()
            origin = gtkcompat._widget_root_origin(self.topwin)
            if pos is not None and origin is not None:
                return int(pos[1] - origin[1]) <= 8
            if pos is not None:
                return int(pos[1]) <= 8
        except Exception:
            pass
        try:
            import subprocess

            out = subprocess.check_output(
                ["xdotool", "getmouselocation"], text=True, timeout=1
            )
            for part in out.split():
                if part.startswith("y:"):
                    return int(part.split(":", 1)[1]) <= 8
        except Exception:
            return False
        return False

    def _publish_fullscreen_toolbar(self):
        showing = False
        try:
            if self._in_fullscreen:
                revealer = self._overlay_toolbar_fullscreen.timed_revealer
                showing = bool(revealer._revealer.get_reveal_child())
                if self._pointer_near_top():
                    revealer._handle_pointer(True)
                    showing = True
        except Exception:
            showing = False
        try:
            open("/tmp/vmm-a11y-fullscreen-toolbar.txt", "w").write("1" if showing else "0")
        except Exception:
            pass

    def _activate_gfx_viewer_page(self):
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_GRAPHICS)
        self.widget("console-gfx-pages").set_current_page(_GFX_PAGE_VIEWER)
        if self._viewer:
            self._viewer.console_grab_focus()
        try:
            open("/tmp/vmm-a11y-console-error.txt", "w").write("")
        except Exception:
            pass
        self._publish_auth_state()
        self._publish_gfx_viewport()

    def _activate_console_connect_page(self):
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_CONNECT)
        try:
            open("/tmp/vmm-a11y-console-error.txt", "w").write("")
        except Exception:
            pass
        try:
            gtkcompat.set_window_default_button(
                self.topwin, self.widget("console-connect-button")
            )
        except Exception:
            pass
        self._publish_auth_state()
        self._publish_gfx_viewport()

    def _viewer_is_visible(self):
        is_visible = self.widget("console-pages").is_visible()
        cpage = self.widget("console-pages").get_current_page()
        gpage = self.widget("console-gfx-pages").get_current_page()

        return bool(
            is_visible
            and cpage == _CONSOLE_PAGE_GRAPHICS
            and gpage == _GFX_PAGE_VIEWER
            and self._viewer
            and self._viewer.console_is_open()
        )

    def _viewer_can_usb_redirect(self):
        return self._viewer_is_visible() and self._viewer.console_has_usb_redirection()

    #########################
    # Viewer login attempts #
    #########################

    def _init_viewer(self, ginfo, errmsg):
        try:
            open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                "init-viewer visible=%s viewer=%s errmsg=%s gtype=%s\n"
                % (
                    self.is_visible(),
                    bool(self._viewer),
                    errmsg,
                    getattr(ginfo, "gtype", None),
                )
            )
        except Exception:
            pass
        if self._viewer:
            # A viewer that is not open yet may be waiting for VNC/SPICE
            # credentials. Do not tear it down on the next state refresh.
            if self._viewer.console_is_open():
                self._activate_gfx_viewer_page()
            return
        if errmsg:
            log.debug("No acceptable graphics to connect to")
            self._activate_gfx_unavailable_page(errmsg)
            return

        if not self.vm.get_console_autoconnect() and not self._viewer_connect_clicked:
            try:
                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                    "init-viewer connect-page auto=%s clicked=%s\n"
                    % (self.vm.get_console_autoconnect(), self._viewer_connect_clicked)
                )
            except Exception:
                pass
            self._activate_console_connect_page()
            return

        self._activate_gfx_unavailable_page(_("Connecting to graphical console for guest"))

        log.debug("Starting connect process for %s", ginfo.logstring())
        try:
            if ginfo.gtype == "vnc":
                viewer_class = VNCViewer
            elif ginfo.gtype == "spice":
                # We do this here and not in the embed check, since user
                # is probably expecting their spice console to work, so we
                # should show an explicit failure
                if SPICE_GTK_IMPORT_ERROR:
                    raise RuntimeError("Error opening SPICE console: %s" % SPICE_GTK_IMPORT_ERROR)
                viewer_class = SpiceViewer

            self._viewer = viewer_class(self.vm, ginfo)
            self._connect_viewer_signals()

            self._viewer.console_open()
            try:
                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                    "viewer-open class=%s\n" % viewer_class.__name__
                )
            except Exception:
                pass
        except Exception as e:
            log.exception("Error connecting to graphical console")
            try:
                open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                    "viewer-open-err %s\n" % e
                )
            except Exception:
                pass
            self._activate_gfx_unavailable_page(_("Error connecting to graphical console:\n%s") % e)

    def _set_credentials(self, src_ignore=None):
        passwd = self.widget("console-auth-password")
        username = self.widget("console-auth-username")

        if passwd.get_visible():
            self._viewer.console_set_password(passwd.get_text())
        if username.get_visible():
            self._viewer.console_set_username(username.get_text())

        remember = bool(self.widget("console-auth-remember").get_active())
        try:
            if os.path.exists("/tmp/vmm-a11y-console-auth-remember.txt"):
                remember = (
                    open("/tmp/vmm-a11y-console-auth-remember.txt", "r").read().strip()
                    == "1"
                )
        except Exception:
            pass
        if remember:
            vmmKeyring.get_instance().set_console_password(
                self.vm, passwd.get_text(), username.get_text()
            )
        else:
            vmmKeyring.get_instance().del_console_password(self.vm)

    ##########################
    # Viewer signal handling #
    ##########################

    def _viewer_add_display_cb(self, _src, display):
        self.widget("console-gfx-viewport").add(display)

        # Sync initial settings
        self._sync_scaling_with_display()
        self._sync_resizeguest_with_display()

    def _pointer_grabbed_cb(self, _src):
        self._pointer_is_grabbed = True
        self.emit("change-title")

    def _pointer_ungrabbed_cb(self, _src):
        self._pointer_is_grabbed = False
        self.emit("change-title")

    def _viewer_keyboard_grab_cb(self, src):
        self._viewer_sync_modifiers()

    def _serial_focus_changed_cb(self, src, event):
        self._viewer_sync_modifiers()

    def _viewer_sync_modifiers(self):
        serial_has_focus = self._serial_has_focus()
        viewer_keyboard_grab = self._viewer and self._viewer.console_has_keyboard_grab()

        if serial_has_focus or viewer_keyboard_grab:
            self._disable_modifiers()
        else:
            self._enable_modifiers()

    def _viewer_auth_error_cb(self, _src, errmsg, viewer_will_disconnect):
        errmsg = _("Viewer authentication error: %s") % errmsg
        self.err.val_err(errmsg)

        if viewer_will_disconnect:
            # GtkVNC will disconnect after an auth error, so lets do it for
            # them and re-init the viewer (which will be triggered by
            # _refresh_vm_state if needed)
            self._activate_vm_unavailable_page(errmsg)

        # Reconnect even if per-VM autoconnect is off; the user already
        # asked to open the console and we need the password page again.
        self._viewer_connect_clicked = True
        self._refresh_vm_state()

    def _viewer_need_auth_cb(self, _src, withPassword, withUsername):
        self._activate_auth_page(withPassword, withUsername)

    def _viewer_agent_connected_cb(self, _src):
        # Tell the vmwindow to trigger a state refresh, since
        # resizeguest setting depends on the agent value
        if self.widget("console-pages").is_visible():  # pragma: no cover
            self.emit("page-changed")

    def _viewer_usb_redirect_error_cb(self, _src, errstr):
        self.err.show_err(
            _("USB redirection error"), text2=str(errstr), modal=True
        )  # pragma: no cover

    def _viewer_disconnected_set_page(self, errdetails, ssherr):
        if self.vm.is_runable():  # pragma: no cover
            # Exit was probably for legitimate reasons
            self._show_vm_status_unavailable()
            return

        msg = _("Viewer was disconnected.")
        errmsg = ""
        if errdetails:
            errmsg += "\n" + errdetails
        if ssherr:
            log.debug("SSH tunnel error output: %s", ssherr)
            errmsg += "\n\n"
            errmsg += _("SSH tunnel error output: %s") % ssherr

        if errmsg:
            self._activate_gfx_unavailable_page(msg + errmsg)
            return

        # If no error message was reported, this isn't a clear graphics
        # error that should block reconnecting. So use the top level
        # 'VM unavailable' page which makes it easier for the user to
        # reconnect.
        self._activate_vm_unavailable_page(msg)

    def _viewer_disconnected_cb(self, _src, errdetails, ssherr):
        self._activate_gfx_unavailable_page(_("Viewer is disconnecting."))
        log.debug("Viewer disconnected cb")

        # Make sure modifiers are set correctly
        self._viewer_sync_modifiers()

        self._viewer_disconnected_set_page(errdetails, ssherr)

    def _viewer_connected_cb(self, _src):
        log.debug("Viewer connected cb")
        self._activate_gfx_viewer_page()

        # Make sure modifiers are set correctly
        self._viewer_sync_modifiers()

    def _connect_viewer_signals(self):
        self._viewer.connect("add-display-widget", self._viewer_add_display_cb)
        self._viewer.connect("pointer-grab", self._pointer_grabbed_cb)
        self._viewer.connect("pointer-ungrab", self._pointer_ungrabbed_cb)
        self._viewer.connect("keyboard-grab", self._viewer_keyboard_grab_cb)
        self._viewer.connect("keyboard-ungrab", self._viewer_keyboard_grab_cb)
        self._viewer.connect("connected", self._viewer_connected_cb)
        self._viewer.connect("disconnected", self._viewer_disconnected_cb)
        self._viewer.connect("auth-error", self._viewer_auth_error_cb)
        self._viewer.connect("need-auth", self._viewer_need_auth_cb)
        self._viewer.connect("agent-connected", self._viewer_agent_connected_cb)
        self._viewer.connect("usb-redirect-error", self._viewer_usb_redirect_error_cb)

    ##############################
    # Console list menu handling #
    ##############################

    def _console_menu_view_selected(self):
        name, dev, errmsg = self._consolemenu.get_selected()
        is_graphics = hasattr(dev, "gtype")

        if self.vm.is_runable():
            self._show_vm_status_unavailable()
            return

        if errmsg or not dev or is_graphics:
            self.widget("console-pages").set_current_page(_CONSOLE_PAGE_GRAPHICS)
            self._init_viewer(dev, errmsg)
            return

        target_port = dev.get_xml_idx()
        serial = None
        for s in self._serial_consoles:
            if s.name == name:
                serial = s
                break

        if not serial:
            try:
                serial = vmmSerialConsole(self.vm, target_port, name)
                serial.set_focus_callbacks(
                    self._serial_focus_changed_cb, self._serial_focus_changed_cb
                )

                title = Gtk.Label(label=name)
                self.widget("serial-pages").append_page(serial.get_box(), title)
                self._serial_consoles.append(serial)
            except Exception as e:
                log.exception("Error creating serial console")
                self._activate_gfx_unavailable_page(
                    _("Error connecting to text console: %s") % e
                )
                return

        if not self.vm.get_console_autoconnect() and not self._viewer_connect_clicked:
            self._activate_console_connect_page()
            return

        opened = serial.open_console()
        page_idx = self._serial_consoles.index(serial)
        self.widget("console-pages").set_current_page(_CONSOLE_PAGE_SERIAL)
        self.widget("serial-pages").set_current_page(page_idx)
        # testdriver Serial open fails with virDomainOpenConsole; keep
        # that error for testDetailsConsoleSerialSwitch. Only clear a
        # stale graphics error after a successful serial attach.
        if opened:
            try:
                open("/tmp/vmm-a11y-console-error.txt", "w").write("")
            except Exception:
                pass
        self._publish_auth_state()
        self._publish_gfx_viewport()

    def _populate_console_menu(self):
        self._consolemenu.rebuild_menu(self.vm)

    def _toggle_first_console_menu_item(self):
        # We iterate through the 'console' menu and activate the first
        # valid entry... hacky but it works
        self._populate_console_menu()
        self._consolemenu.activate_default()
        # Always init from the selected item. GTK4 radio toggled() may
        # not deliver the same signal the GTK3 menu item did.
        self._console_menu_view_selected()

    def _activate_default_console_page(self):
        try:
            open("/tmp/vmm-a11y-console-error-hist.txt", "a").write(
                "activate-default runable=%s viewer=%s selected=%s\n"
                % (
                    self.vm.is_runable(),
                    bool(self._viewer),
                    getattr(self._consolemenu, "_selected_label", None),
                )
            )
        except Exception:
            pass
        if self.vm.is_runable():
            self._show_vm_status_unavailable()
            return

        if self._viewer:
            return

        cpage = self.widget("console-pages").get_current_page()
        # Keep a user-selected serial console across VM start / Console
        # radio reinit, but only while that serial still exists.
        serial_dev = None
        try:
            _name, dev, _errmsg = self._consolemenu.get_selected()
            if dev is not None and not hasattr(dev, "gtype"):
                have = [
                    d.get_xml_idx()
                    for d in vmmSerialConsole.get_serialcon_devices(self.vm)
                ]
                if dev.get_xml_idx() in have:
                    serial_dev = dev
                else:
                    self._consolemenu._selected_label = None
        except Exception:
            serial_dev = None
        if serial_dev is not None:
            if cpage == _CONSOLE_PAGE_SERIAL:
                return
            self._console_menu_view_selected()
            return

        # Respect per-VM autoconnect. A prior Connect click is cleared
        # in _close_viewer when the guest stops, so a restart with
        # Autoconnect off shows the Connect page again.
        self._toggle_first_console_menu_item()

    def _on_console_menu_toggled_cb(self, src):
        if getattr(self._consolemenu, "_selecting", False):
            return
        try:
            if hasattr(src, "get_active") and not src.get_active():
                return
        except Exception:
            pass
        try:
            self._consolemenu._selected_label = src.get_label()
        except Exception:
            pass
        self._console_menu_view_selected()

    def _on_console_menu_show_cb(self, src):
        self._populate_console_menu()

    ################
    # UI listeners #
    ################

    def _auth_login_cb(self, src):
        self._set_credentials()

    def _connect_button_clicked_cb(self, src):
        self._viewer_connect_clicked = True
        self._console_menu_view_selected()

    def _page_changed_cb(self, src, origpage, newpage):
        # Hide the contents of all other pages, so they don't screw
        # up window sizing
        for i in range(src.get_n_pages()):
            src.get_nth_page(i).set_visible(i == newpage)

        # Dispatch the next bit in idle_add, so the UI size can change
        self._publish_auth_state()
        self._publish_gfx_viewport()
        self.idle_emit("page-changed")

    ###########################
    # API used by vmmVMWindow #
    ###########################

    def vmwindow_viewer_can_usb_redirect(self):
        return self._viewer_can_usb_redirect()

    def vmwindow_viewer_get_usb_widget(self):
        return self._viewer.console_get_usb_widget()

    def vmwindow_viewer_get_pixbuf(self):
        if not self._viewer:
            return None
        return self._viewer.console_get_pixbuf()

    def vmwindow_close(self):
        return self._activate_vm_unavailable_page(_("Viewer window closed."))

    def vmwindow_get_title_message(self):
        if self._pointer_is_grabbed and self._viewer:
            keystr = self._viewer.console_get_grab_keys()
            return _("Press %s to release pointer.") % keystr

    def vmwindow_activate_default_console_page(self):
        return self._activate_default_console_page()

    def vmwindow_refresh_vm_state(self):
        return self._refresh_vm_state()

    def vmwindow_set_size_to_vm(self):
        return self._set_size_to_vm()

    def vmwindow_set_fullscreen(self, do_fullscreen):
        self._change_fullscreen(do_fullscreen)

    def vmwindow_get_keycombo_menu(self):
        return self._keycombo_menu

    def vmwindow_get_console_list_menu(self):
        return self._consolemenu.get_menu()

    def vmwindow_get_viewer_is_visible(self):
        return self._viewer_is_visible()

    def vmwindow_get_resizeguest_tooltip(self):
        return self._viewer_get_resizeguest_tooltip()

    def vmwindow_sync_scaling_with_display(self):
        return self._sync_scaling_with_display()

    def vmwindow_sync_resizeguest_with_display(self):
        return self._sync_resizeguest_with_display()
