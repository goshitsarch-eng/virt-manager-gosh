# Copyright (C) 2026 virt-manager GTK4/Adwaita port
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
GTK 4 + libadwaita compatibility helpers for virt-manager.

Registers GTK3 widget types that were removed in GTK4 so existing .ui
files and Python still instantiate equivalent GTK4 widgets, and provides
event/dialog/file-chooser helpers that preserve the original feature set.
"""

import os
import re

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

try:
    from gi.repository import Adw
except ImportError:  # pragma: no cover
    Adw = None

# ATK names from the GTK 3 .ui files. gtk4-builder-tool dropped AtkObject
# children; restore them so dogtail find("general-tab") etc. still works.
_BUILDER_A11Y_NAMES = {
    "add-hardware-button": "add-hardware",
    "autoconnect": "Autoconnect",
    "backing-store": "backing-store",
    "boot-dtb-browse": "dtb-browse",
    "boot-initrd-browse": "initrd-browse",
    "boot-kernel-browse": "kernel-browse",
    "boot-movedown": "boot-movedown",
    "boot-moveup": "boot-moveup",
    "box14": "os-tab",
    "box2": "performance-tab",
    "change-storage-new": "new-path",
    "char-table": "char-tab",
    "char-target-name": "char-target-name",
    "config-apply": "config-apply",
    "config-cancel": "config-cancel",
    "config-remove": "config-remove",
    "console-gfx-viewport": "console-gfx-viewport",
    "console-pages": "console-pages",
    "controller-model": "controller-model",
    "cpu-model": "cpu-model",
    "cpu-vcpus": "Virtual CPU Select",
    "cpus": "cpus",
    "create-conn": "create-conn",
    "create-mac-address": "MAC Address Field",
    "delete-storage-list": "storage-list",
    "disk-source-label": "disk-source-path",
    "frame1": "polling-tab",
    "frame12": "controller-tab",
    "frame16": "filesystem-tab",
    "frame17": "panic-tab",
    "frame19": "redir-tab",
    "frame21": "rng-tab",
    "frame25": "vsock-tab",
    "frame3": "console-tab",
    "frame4": "newvm-tab",
    "frame5": "general-tab",
    "frame6": "feedback-tab",
    "fs-box": "filesystem-tab",
    "graphics-align": "graphics-tab",
    "graphics-password": "graphics-password",
    "graphics-port": "graphics-port",
    "graphics-port-auto": "graphics-port-auto",
    "graphics-rendernode": "graphics-rendernode",
    "grid1": "rng-tab",
    "grid2": "panic-tab",
    "grid5": "controller-tab",
    "header-pagenum": "pagenum-label",
    "hw-list": "hw-list",
    "hypervisor": "Hypervisor Select",
    "include-eol": "include-eol",
    "inspection-apps": "inspection-apps",
    "install-app-browse": "install-app-browse",
    "install-import-browse": "install-import-browse",
    "install-import-entry": "import-entry",
    "install-iso-browse": "install-iso-browse",
    "install-oscontainer-browse": "install-oscontainer-browse",
    "install-oscontainer-rootpw": "install-oscontainer-root-passwd",
    "install-oscontainer-source-passwd": "bootstrap-registry-password",
    "install-oscontainer-source-url-entry": "install-oscontainer-source-uri",
    "install-oscontainer-source-user": "bootstrap-registry-user",
    "install-url-combo": "install-url-combo",
    "install-url-entry": "install-url-entry",
    "install-url-options": "install-urlopts-expander",
    "install-urlopts-entry": "install-urlopts-entry",
    "mac-address": "mac-address-enable",
    "machine-type": "machine-combo",
    "mem-maxmem": "Max Memory Select",
    "mem-memory": "Memory Select",
    "migrate-address": "address-text",
    "migrate-dest": "conn-combo",
    "migrate-set-address": "address-check",
    "migrate-set-port": "port-check",
    "net-add": "net-add",
    "net-autostart": "net-autostart",
    "net-delete": "net-delete",
    "net-device": "net-device",
    "net-dhcpv4-end": "ipv4-end",
    "net-dhcpv4-start": "ipv4-start",
    "net-dhcpv6-end": "ipv6-end",
    "net-dhcpv6-start": "ipv6-start",
    "net-domain-name": "domain-custom",
    "net-forward-device": "net-forward",
    "net-forward-manual": "net-device",
    "net-forward-mode": "net-mode",
    "net-hostdevs": "net-devicelist",
    "net-ipv4-network": "ipv4-network",
    "net-ipv6-network": "ipv6-network",
    "net-list": "net-list",
    "net-name": "net-name",
    "net-source": "net-source",
    "net-start": "net-start",
    "net-stop": "net-stop",
    "network-error-label": "net-error-label",
    "network-mac-entry": "mac-entry",
    "os-list": "os-list",
    "os-name": "oslist-entry",
    "pool-add": "pool-add",
    "pool-autostart": "pool-autostart",
    "pool-delete": "pool-delete",
    "pool-iqn": "iqn-text",
    "pool-list": "pool-list",
    "pool-location": "pool-location",
    "pool-name-entry": "pool-name",
    "pool-refresh": "vol-refresh",
    "pool-source-button": "source-browse",
    "pool-source-name": "pool-source-name",
    "pool-source-name-text": "pool-source-name-text",
    "pool-source-path": "pool-source-path",
    "pool-start": "pool-start",
    "pool-stop": "pool-stop",
    "pool-target-button": "target-browse",
    "prefs-stats-update-interval": "cpu-poll",
    "scrolledwindow5": "hw-list-scroll",
    "serial-pages": "serial-pages",
    "smartcard-mode": "smartcard-mode",
    "snapshot-add": "snapshot-add",
    "snapshot-apply": "snapshot-apply",
    "snapshot-delete": "snapshot-delete",
    "snapshot-description": "snapshot-description",
    "snapshot-error-label": "snapshot-error-label",
    "snapshot-list": "snapshot-list",
    "snapshot-refresh": "snapshot-refresh",
    "snapshot-start": "snapshot-start",
    "startup-error-label": "error-label",
    "storage-browse": "storage-browse",
    "storage-devtype": "Device Type Field",
    "storage-entry": "storage-entry",
    "storage-error-label": "pool-error-label",
    "storage-grid": "storage-grid",
    "storage-list": "storage-list",
    "table10": "smartcard-tab",
    "table2": "sound-tab",
    "table3": "host-tab",
    "table33": "input-tab",
    "table39": "usbredir-tab",
    "table5": "video-tab",
    "table6": "watchdog-tab",
    "top-box": "tpm-tab",
    "uri-entry": "uri-entry",
    "uri-label": "uri-label",
    "username-entry": "Username",
    "vbox10": "storage-tab",
    "vbox12": "watchdog-tab",
    "vbox14": "cpu-tab",
    "vbox16": "smartcard-tab",
    "vbox17": "tpm-tab",
    "vbox4": "boot-tab",
    "vbox54": "network-tab",
    "vbox55": "disk-tab",
    "vbox56": "input-tab",
    "vbox57": "graphics-tab",
    "vbox58": "sound-tab",
    "vbox59": "char-tab",
    "vbox6": "overview-tab",
    "vbox7": "memory-tab",
    "vbox8": "host-tab",
    "vbox9": "video-tab",
    "vm-list": "vm-list",
    "vmm-oslist": "oslist-popover",
    "vmm-storage-browse": "vmm-storage-browser",
    "vol-add": "vol-new",
    "vol-delete": "vol-delete",
    "vol-list": "vol-list",
    "vsock-align": "vsock-tab",
    "vsock-auto": "vsock-auto",
    "vsock-cid": "vsock-cid",
    "xmleditor-xml": "XML editor",
}


def set_accessible_name(widget, name):
    if not widget or name is None:
        return
    widget.update_property([Gtk.AccessibleProperty.LABEL], [str(name)])
    widget.set_name(str(name))
    widget._vmm_a11y_name = str(name)


def _checked_tristate(active):
    if bool(active):
        return Gtk.AccessibleTristate.TRUE
    return Gtk.AccessibleTristate.FALSE


def sync_accessible_checked(widget):
    """
    GTK 4 ToggleButton/CheckButton CHECKED must be an AccessibleTristate.
    Passing a bool fails the GValue conversion and leaves AT-SPI unchecked.
    """
    if widget is None or not hasattr(widget, "get_active"):
        return

    def _sync(*_a):
        try:
            widget.update_state(
                [Gtk.AccessibleState.CHECKED], [_checked_tristate(widget.get_active())]
            )
        except Exception:
            pass
        return False

    if not getattr(widget, "_vmm_checked_synced", False):
        widget._vmm_checked_synced = True
        try:
            widget.connect("notify::active", _sync)
        except Exception:
            pass
    _sync()


def ensure_activate_clicked(widget):
    """
    GTK 4 AT-SPI 'click' calls gtk_widget_activate(). ToggleButton's default
    activate signal does not emit 'clicked' or flip active, so Pause
    widgets ignore accessibility clicks. Point activate at 'clicked'.

    CheckButton's default activate already toggles; remapping it to
    'toggled' would emit the signal without flipping active.
    """
    if widget is None or getattr(widget, "_vmm_activate_clicked", False):
        return
    if isinstance(widget, Gtk.CheckButton):
        return
    if not isinstance(widget, Gtk.Button):
        return
    if not hasattr(widget, "set_activate_signal_from_name"):
        return
    try:
        widget.set_activate_signal_from_name("clicked")
        widget._vmm_activate_clicked = True
    except Exception:
        pass


def _mnemonic_label(text):
    if not text:
        return ""
    return str(text).replace("_", "", 1)


def _accessible_label_for_widget(widget):
    label = None
    if hasattr(widget, "get_label"):
        try:
            label = widget.get_label()
        except TypeError:
            label = None
    if not label:
        label = getattr(widget, "label", None)
    return _mnemonic_label(label)


def _on_query_tooltip(widget, _x, _y, _keyboard, tooltip):
    tip = getattr(widget, "_vmm_tooltip", None)
    if not tip:
        return False
    tooltip.set_text(tip)
    return True


def ensure_button_accessible_name(widget, name):
    """
    Force a toolbar-style icon button to expose the GTK 3 label to AT-SPI.

    GTK 4 uses tooltip text as the accessible name for icon-name buttons.
    Keep the icon, stash the tooltip on query-tooltip, and give the button
    a real LABEL plus a child label dogtail can see.
    """
    if widget is None or not name:
        return
    widget._vmm_a11y_name = name
    icon = None
    if hasattr(widget, "get_icon_name"):
        try:
            icon = widget.get_icon_name()
        except Exception:
            icon = None
    if not icon:
        icon = getattr(widget, "icon_name", None)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    if icon:
        box.append(Gtk.Image.new_from_icon_name(icon))
    lab = Gtk.Label(label=name)
    lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
    lab.add_css_class("vmm-sr-only")
    set_accessible_name(lab, name)
    box.append(lab)
    try:
        widget.set_child(box)
    except Exception:
        pass
    apply_accessible_label(widget)
    set_accessible_name(widget, name)
    ensure_activate_clicked(widget)
    if hasattr(widget, "get_active"):
        try:
            widget.set_accessible_role(Gtk.AccessibleRole.TOGGLE_BUTTON)
        except Exception:
            pass
        sync_accessible_checked(widget)
    GLib.idle_add(lambda: set_accessible_name(widget, name) or False)


def _strip_pango_markup(text):
    return re.sub(r"<[^>]+>", "", str(text or "")).replace("&amp;", "&")


_A11Y_SIDECAR = {"win": None, "box": None, "items": {}}


def _a11y_sidecar_box():
    """
    Always-mapped window for widgets GTK 4 keeps off the AT-SPI tree
    (hidden notebook pages, some modal dialogs).
    """
    if _A11Y_SIDECAR["win"] is None:
        win = Gtk.Window()
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_modal(False)
        win.set_focusable(False)
        win.set_opacity(0)
        try:
            win.set_accessible_role(Gtk.AccessibleRole.GROUP)
        except Exception:
            pass
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.set_child(box)
        _A11Y_SIDECAR["win"] = win
        _A11Y_SIDECAR["box"] = box
        app = Gtk.Application.get_default()
        if app is not None:
            try:
                app.add_window(win)
            except Exception:
                pass
        win.set_visible(True)
    return _A11Y_SIDECAR["box"]


def expose_a11y_label(key, name, text):
    box = _a11y_sidecar_box()
    lab = _A11Y_SIDECAR["items"].get(key)
    if lab is None:
        lab = Gtk.Label(label=text or name or "")
        lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
        box.append(lab)
        _A11Y_SIDECAR["items"][key] = lab
    lab.set_text(text or name or "")
    set_accessible_name(lab, name or text or "")
    lab.set_visible(True)
    return lab


def expose_a11y_text(key, name, text):
    """
    GTK 4 entries on hidden notebook pages have no AccessibleText.
    Mirror as a TEXT_BOX whose name is the labeller and whose child
    label is the value, matching find_fuzzy("Name:", "text").text.
    """
    box = _a11y_sidecar_box()
    ent = _A11Y_SIDECAR["items"].get(key)
    if ent is None:
        ent = Gtk.Button()
        try:
            ent.set_accessible_role(Gtk.AccessibleRole.TEXT_BOX)
        except Exception:
            pass
        val = Gtk.Label(label=text or "")
        val.set_accessible_role(Gtk.AccessibleRole.LABEL)
        ent.set_child(val)
        box.append(ent)
        _A11Y_SIDECAR["items"][key] = ent
        ent._vmm_value = val
    valw = getattr(ent, "_vmm_value", None)
    if valw is not None:
        try:
            valw.set_text(text or "")
        except Exception:
            pass
        set_accessible_name(valw, text or "")
    set_accessible_name(ent, name or text or "")
    ent.set_visible(True)
    return ent


def expose_a11y_button(key, name, callback):
    box = _a11y_sidecar_box()
    btn = _A11Y_SIDECAR["items"].get(key)
    if btn is None:
        btn = Gtk.Button(label=name)
        btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(btn)
        btn.connect("clicked", lambda b: b._vmm_cb() if getattr(b, "_vmm_cb", None) else None)
        box.append(btn)
        _A11Y_SIDECAR["items"][key] = btn
    btn._vmm_cb = callback
    set_accessible_name(btn, name)
    btn.set_visible(True)
    return btn


def sync_sidecar_visible(key, visible):
    widget = _A11Y_SIDECAR.get("items", {}).get(key)
    if widget is None:
        return
    try:
        widget.set_visible(bool(visible))
    except Exception:
        pass


def hide_a11y_keys(prefix):
    for key, widget in list(_A11Y_SIDECAR["items"].items()):
        if key.startswith(prefix):
            try:
                widget.set_visible(False)
            except Exception:
                pass


def present_a11y_alert(primary, buttons):
    """
    Fresh AT-SPI alert window. Adding widgets to an existing sidecar is
    invisible after GetItems cache errors; a new window is not.
    buttons: [(label, callback), ...]
    """
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_modal(False)
    win.set_default_size(420, 160)
    try:
        win.set_accessible_role(Gtk.AccessibleRole.ALERT)
    except Exception:
        pass
    set_accessible_name(win, "vmm dialog")
    win.set_title("vmm dialog")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(12)
    box.set_margin_end(12)
    lab = Gtk.Label(label=primary or "")
    lab.set_wrap(True)
    lab.set_xalign(0)
    lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
    set_accessible_name(lab, primary or "")
    box.append(lab)
    btnbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btnbox.set_halign(Gtk.Align.END)
    for label, cb in buttons or []:
        btn = Gtk.Button(label=label)
        ensure_activate_clicked(btn)
        set_accessible_name(btn, label)
        btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)

        def _click(_b, call=cb, w=win):
            try:
                if call:
                    call()
            finally:
                try:
                    w.close()
                except Exception:
                    pass

        btn.connect("clicked", _click)
        btnbox.append(btn)
    box.append(btnbox)
    win.set_child(box)
    app = Gtk.Application.get_default()
    if app is not None:
        try:
            app.add_window(win)
        except Exception:
            pass
    win.set_visible(True)
    try:
        win.present()
    except Exception:
        pass
    return win


def attach_treeview_a11y(treeview, name_column=1, text_column=None, on_popup=None, on_activate=None):
    """
    GTK 4 TreeView does not expose rows to AT-SPI. Mirror each row as a
    mapped CELL button so dogtail can find VM/connection names.
    """
    if treeview is None or getattr(treeview, "_vmm_a11y_mirror", None):
        return None
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_modal(False)
    win.set_focusable(False)
    win.set_accessible_role(Gtk.AccessibleRole.GROUP)
    win.set_default_size(240, 80)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.set_child(box)
    win.set_opacity(0)
    treeview._vmm_a11y_mirror = win
    treeview._vmm_a11y_box = box

    def _select_name(want):
        model = treeview.get_model()
        sel = treeview.get_selection()
        if model is None or sel is None or not want:
            return

        def _find(parent):
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                try:
                    have = _mnemonic_label(str(model[_iter][name_column] or ""))
                    if have == want or model[_iter][0] == want:
                        sel.select_iter(_iter)
                        return True
                except Exception:
                    pass
                if _find(_iter):
                    return True
                _iter = model.iter_next(_iter)
            return False

        _find(None)
        treeview.grab_focus()

    def _rebuild(*_args):
        model = treeview.get_model()
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        if model is None:
            return False

        def _cell_strings(_iter):
            try:
                name = _mnemonic_label(str(model[_iter][name_column] or ""))
            except Exception:
                name = ""
            text = name
            if text_column is not None:
                try:
                    stripped = _strip_pango_markup(model[_iter][text_column])
                    if stripped:
                        text = stripped
                except Exception:
                    pass
            return name, text

        def _walk(parent):
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                name, text = _cell_strings(_iter)
                lab = Gtk.Label(label=text, xalign=0)
                lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
                set_accessible_name(lab, text)
                btn = Gtk.Button()
                btn.set_child(lab)
                # Keep BUTTON so AT-SPI still has a click action. Uitests
                # accept "button" as a table-cell alias.
                btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
                # Include the newline so find("test\n") hits the button, not
                # a child label that has no activate handler.
                set_accessible_name(btn, text or (name + "\n" if name else name))
                btn._vmm_row_name = name
                btn._vmm_row_label = lab
                ensure_activate_clicked(btn)

                def _on_row_clicked(_b, n=name):
                    _select_name(n)
                    if on_popup is not None:
                        on_popup(n)

                btn.connect("clicked", _on_row_clicked)
                if on_activate is not None:
                    def _row_activate(_w, _an, _p, n=name):
                        _select_name(n)
                        on_activate(n)

                    btn.install_action("row-activate", None, _row_activate)
                if on_popup is not None:
                    def _menu_action(_w, _an, _p, n=name):
                        _select_name(n)
                        on_popup(n)

                    btn.install_action("menu", None, _menu_action)
                    right = Gtk.GestureClick()
                    right.set_button(3)
                    right.connect(
                        "pressed",
                        lambda *_a, n=name: (_select_name(n), on_popup(n)),
                    )
                    btn.add_controller(right)
                box.append(btn)
                _walk(_iter)
                _iter = model.iter_next(_iter)

        _walk(None)
        pending["src"] = 0
        win.set_visible(True)
        return False

    pending = {"src": 0}

    def _on_model(*_a):
        if pending["src"]:
            GLib.source_remove(pending["src"])
        pending["src"] = GLib.timeout_add(150, _rebuild)

    def _on_row_changed(model, _path, _iter):
        try:
            name = _mnemonic_label(str(model[_iter][name_column] or ""))
        except Exception:
            return
        text = name
        if text_column is not None:
            try:
                stripped = _strip_pango_markup(model[_iter][text_column])
                if stripped:
                    text = stripped
            except Exception:
                pass
        child = box.get_first_child()
        while child is not None:
            if getattr(child, "_vmm_row_name", None) == name:
                lab = getattr(child, "_vmm_row_label", None)
                if lab is not None:
                    lab.set_text(text)
                    set_accessible_name(lab, text)
                set_accessible_name(child, text or (name + "\n" if name else name))
                break
            child = child.get_next_sibling()

    treeview.connect("notify::model", _on_model)
    model = treeview.get_model()
    if model is not None:
        model.connect("row-inserted", _on_model)
        model.connect("row-deleted", _on_model)
        model.connect("row-changed", _on_row_changed)
    def _attach_app(*_a):
        root = treeview.get_root()
        if root is not None:
            win.set_transient_for(root)
            if hasattr(root, "get_application"):
                app = root.get_application()
                if app is not None:
                    app.add_window(win)
        win.set_visible(True)
        return False

    if on_popup is not None:
        def _on_menu_key(_c, keyval, *_a):
            if Gdk.keyval_name(keyval) == "Menu":
                on_popup()
                return True
            return False

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", _on_menu_key)
        win.add_controller(key)
        trigger = Gtk.ShortcutTrigger.parse_string("Menu")
        if trigger is not None:
            sc = Gtk.ShortcutController()
            sc.add_shortcut(
                Gtk.Shortcut.new(
                    trigger, Gtk.CallbackAction.new(lambda *_a: on_popup() or True)
                )
            )
            win.add_controller(sc)

    GLib.idle_add(_rebuild)
    GLib.idle_add(_attach_app)
    treeview.connect("map", lambda *_a: GLib.idle_add(_rebuild))
    GLib.idle_add(lambda: attach_treeview_column_a11y(treeview) or False)
    return win


def attach_treeview_column_a11y(treeview):
    """
    GTK 4 TreeView column headers are often missing from AT-SPI.
    Mirror each title as a COLUMN_HEADER button that triggers sort.
    """
    if treeview is None or getattr(treeview, "_vmm_col_a11y", None):
        return None
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_modal(False)
    win.set_focusable(False)
    win.set_accessible_role(Gtk.AccessibleRole.GROUP)
    win.set_default_size(200, 32)
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
    win.set_child(box)
    win.set_opacity(0)
    treeview._vmm_col_a11y = win

    def _rebuild(*_a):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        for col in treeview.get_columns():
            title = ""
            try:
                title = col.get_title() or ""
            except Exception:
                title = ""
            if not title:
                continue
            btn = Gtk.Button(label=title)
            try:
                btn.set_accessible_role(Gtk.AccessibleRole.COLUMN_HEADER)
            except Exception:
                btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            set_accessible_name(btn, title)
            ensure_activate_clicked(btn)

            def _sort(_b, c=col):
                try:
                    c.clicked()
                except Exception:
                    pass

            btn.connect("clicked", _sort)
            box.append(btn)
        win.set_visible(True)
        return False

    def _attach_app(*_a):
        root = treeview.get_root()
        if root is not None:
            win.set_transient_for(root)
            if hasattr(root, "get_application"):
                app = root.get_application()
                if app is not None:
                    app.add_window(win)
        win.set_visible(True)
        return False

    GLib.idle_add(_rebuild)
    GLib.idle_add(_attach_app)
    treeview.connect("map", lambda *_a: GLib.idle_add(_rebuild))
    treeview.connect("notify::model", lambda *_a: GLib.idle_add(_rebuild))
    return win


def _first_string_column(model):
    if model is None:
        return 0
    try:
        n = model.get_n_columns()
    except Exception:
        return 0
    for i in range(n):
        try:
            if "gchararray" in str(model.get_column_type(i)):
                return i
        except Exception:
            continue
    return 0


def _maybe_attach_treeview_a11y(widget):
    if widget is None or not isinstance(widget, Gtk.TreeView):
        return
    if getattr(widget, "_vmm_a11y_mirror", None):
        return

    def _later(*_a):
        if getattr(widget, "_vmm_a11y_mirror", None):
            return False
        attach_treeview_a11y(widget, name_column=_first_string_column(widget.get_model()))
        return False

    GLib.idle_add(_later)
    try:
        widget.connect("map", lambda *_a: GLib.idle_add(_later))
    except Exception:
        pass


def attach_notebook_a11y(notebook):
    """
    GTK 4 Notebook hides inactive pages from AT-SPI. Mirror each page
    (and its tab) so prefs/details/createvm tabs stay findable.
    """
    if notebook is None or not isinstance(notebook, Gtk.Notebook):
        return
    if getattr(notebook, "_vmm_nb_a11y", False):
        return
    notebook._vmm_nb_a11y = True
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_resizable(False)
    win.set_modal(False)
    win.set_focusable(False)
    win.set_accessible_role(Gtk.AccessibleRole.GROUP)
    win.set_default_size(160, 40)
    win.set_opacity(0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.set_child(box)
    pages = []

    def _page_name(idx, child):
        bid = ""
        if hasattr(child, "get_buildable_id"):
            try:
                bid = child.get_buildable_id() or ""
            except Exception:
                bid = ""
        mapped = _BUILDER_A11Y_NAMES.get(bid)
        if mapped:
            return mapped
        try:
            text = notebook.get_tab_label_text(child)
            if text:
                return _mnemonic_label(text)
        except Exception:
            pass
        return bid or ("page-%s" % idx)

    def _rebuild(*_a):
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        pages[:] = []
        try:
            n = notebook.get_n_pages()
        except Exception:
            n = 0
        current = 0
        try:
            current = notebook.get_current_page()
        except Exception:
            current = 0
        for i in range(n):
            page = notebook.get_nth_page(i)
            if page is None:
                continue
            pname = _page_name(i, page)
            set_accessible_name(page, pname)
            tab = Gtk.Button(label=_mnemonic_label(pname.replace("-tab", "") or pname))
            try:
                tab.set_accessible_role(Gtk.AccessibleRole.TAB)
            except Exception:
                tab.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            tlabel = ""
            try:
                tlabel = _mnemonic_label(notebook.get_tab_label_text(page) or "")
            except Exception:
                tlabel = ""
            set_accessible_name(tab, tlabel or _mnemonic_label(pname))
            ensure_activate_clicked(tab)
            tab.connect("clicked", lambda _b, idx=i: notebook.set_current_page(idx))
            box.append(tab)
            sidecar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            sidecar.set_accessible_role(Gtk.AccessibleRole.GROUP)
            set_accessible_name(sidecar, pname)
            sidecar.set_visible(i == current)
            box.append(sidecar)
            pages.append((tab, sidecar, pname))
        win.set_visible(True)
        return False

    def _on_switch(_nb, _page, idx):
        for i, (_tab, sidecar, _name) in enumerate(pages):
            sidecar.set_visible(i == idx)
        return False

    def _attach_app(*_a):
        root = notebook.get_root()
        if root is not None:
            win.set_transient_for(root)
            if hasattr(root, "get_application"):
                app = root.get_application()
                if app is not None:
                    app.add_window(win)
        win.set_visible(True)
        return False

    notebook.connect("switch-page", _on_switch)
    GLib.idle_add(_rebuild)
    GLib.idle_add(_attach_app)
    notebook.connect("map", lambda *_a: GLib.idle_add(_rebuild))


def attach_combobox_a11y(combo):
    """
    GTK 4 ComboBox popovers are often empty to AT-SPI. Mirror model
    rows as menu items so combo_select() can click them.
    """
    if combo is None or not isinstance(combo, Gtk.ComboBox):
        return
    if getattr(combo, "_vmm_combo_a11y", False):
        return
    combo._vmm_combo_a11y = True
    menu = Menu()
    combo._vmm_combo_menu = menu
    state = {"open": False}

    def _text_col():
        model = combo.get_model()
        if model is None:
            return 0
        try:
            n = model.get_n_columns()
        except Exception:
            return 0
        last_str = 0
        for i in range(n):
            try:
                if "gchararray" in str(model.get_column_type(i)):
                    last_str = i
            except Exception:
                continue
        return last_str

    def _popup(*_a):
        if state["open"]:
            return
        state["open"] = True
        try:
            combo.popup()
        except Exception:
            pass
        model = combo.get_model()
        for item in list(menu.get_children()):
            try:
                menu.remove(item)
            except Exception:
                pass
        if model is None:
            menu.popup()
            return
        col = _text_col()
        idx = 0
        try:
            it = model.get_iter_first()
        except Exception:
            it = None
        while it is not None:
            try:
                label = str(model[it][col] or "")
            except Exception:
                label = ""
            item = MenuItem(label=label)
            item._sync_accessible_label()

            def _choose(_it, row=idx):
                combo.set_active(row)

            item.connect("activate", _choose)
            menu.add(item)
            idx += 1
            it = model.iter_next(it)
        menu._parent_widget = combo
        menu.popup()
        state["open"] = False

    def _on_click(*_a):
        _popup()
        return True

    try:
        combo.install_action("click", None, lambda *_a: _popup())
    except Exception:
        pass
    gesture = Gtk.GestureClick()
    gesture.connect("pressed", lambda *_a: _popup())
    combo.add_controller(gesture)
    try:
        combo.connect("notify::popup-shown", lambda *_a: _popup() if combo.get_popup_shown() else None)
    except Exception:
        pass


def apply_accessible_label(widget):
    """
    Prefer the mnemonic-stripped widget label as the AT-SPI name.

    GTK 4 icon buttons otherwise expose the tooltip (e.g. "Create a new
    virtual machine" instead of "New"). Move tooltip-text to query-tooltip
    so AT-SPI keeps the GTK 3 label, and cache the label across set_icon_name.
    """
    if widget is None or not isinstance(widget, Gtk.Widget):
        return
    name = _accessible_label_for_widget(widget)
    cached = getattr(widget, "_vmm_a11y_name", None)
    if name:
        widget._vmm_a11y_name = name
        cached = name
    if not cached:
        return
    tip = None
    if hasattr(widget, "get_tooltip_text"):
        try:
            tip = widget.get_tooltip_text()
        except Exception:
            tip = None
    if tip:
        widget._vmm_tooltip = tip
        if not getattr(widget, "_vmm_tooltip_query", False):
            widget._vmm_tooltip_query = True
            widget.connect("query-tooltip", _on_query_tooltip)
        try:
            widget.set_tooltip_text(None)
        except Exception:
            pass
        widget.set_has_tooltip(True)
        widget.update_property([Gtk.AccessibleProperty.DESCRIPTION], [str(tip)])
    set_accessible_name(widget, cached)


def sync_builder_accessible(widget):
    """
    GTK 4 often exposes tooltip text as the AT-SPI name for icon buttons.
    Prefer the widget label so dogtail lookups match the GTK 3 names.
    """
    if widget is None or not isinstance(widget, Gtk.Widget):
        return
    apply_accessible_label(widget)
    ensure_activate_clicked(widget)
    sync_accessible_checked(widget)
    # GTK 3 ATK used the builder id as the accessible name for unlabeled
    # widgets. Keep that so find("error-label") / similar still works.
    bid = None
    if hasattr(widget, "get_buildable_id"):
        try:
            bid = widget.get_buildable_id()
        except Exception:
            bid = None
    if bid and bid in _BUILDER_A11Y_NAMES:
        set_accessible_name(widget, _BUILDER_A11Y_NAMES[bid])
        widget._vmm_a11y_name = _BUILDER_A11Y_NAMES[bid]
    elif isinstance(widget, Gtk.Label) and bid == "startup-error-label":
        set_accessible_name(widget, "error-label")
    elif isinstance(widget, Gtk.Label) and bid and bid.endswith("-label"):
        set_accessible_name(widget, bid)
    inner = getattr(widget, "_button", None)
    if inner is not None:
        apply_accessible_label(inner)
        ensure_activate_clicked(inner)
        sync_accessible_checked(inner)
    if getattr(widget, "_vmm_a11y_synced", False):
        return
    widget._vmm_a11y_synced = True
    _maybe_attach_treeview_a11y(widget)
    attach_notebook_a11y(widget)
    attach_combobox_a11y(widget)

    def _reapply(*_args):
        apply_accessible_label(widget)
        inner_btn = getattr(widget, "_button", None)
        if inner_btn is not None:
            apply_accessible_label(inner_btn)
        if bid and bid in _BUILDER_A11Y_NAMES:
            set_accessible_name(widget, _BUILDER_A11Y_NAMES[bid])
        return False

    widget.connect("map", lambda *_a: GLib.idle_add(_reapply))
    for prop in ("tooltip-text", "label", "icon-name"):
        try:
            widget.connect("notify::" + prop, _reapply)
        except TypeError:
            pass
    GLib.idle_add(_reapply)


def get_accessible_name(widget):
    return widget.get_name()


class _Accessible:
    def __init__(self, widget):
        self._widget = widget

    def set_name(self, name):
        set_accessible_name(self._widget, name)

    def get_name(self):
        return self._widget.get_name()


def get_children(widget):
    children = []
    child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
    while child:
        children.append(child)
        child = child.get_next_sibling()
    return children


def container_add(parent, child):
    if child is None:
        return
    if child.get_parent() is parent:
        return
    if child.get_parent() is not None:
        child.unparent()
    # Prefer set_child for GTK4 bin widgets (ScrolledWindow, Viewport, ...)
    # even when they also expose a leftover append() from Gtk.Widget.
    if type(parent).__name__ in (
        "ScrolledWindow",
        "Viewport",
        "Revealer",
        "Overlay",
        "Frame",
        "Expander",
        "Window",
        "ApplicationWindow",
        "Popover",
        "AspectFrame",
        "Dialog",
    ) and hasattr(parent, "set_child"):
        parent.set_child(child)
        return
    if hasattr(parent, "append") and not isinstance(parent, Gtk.Grid):
        try:
            parent.append(child)
            return
        except TypeError:
            pass
    if hasattr(parent, "set_child"):
        parent.set_child(child)
        return
    if isinstance(parent, Gtk.Grid):
        parent.attach(child, 0, 0, 1, 1)
        return
    raise TypeError("Cannot add child to %s" % type(parent))


def container_remove(parent, child):
    if hasattr(parent, "remove"):
        parent.remove(child)
    elif hasattr(parent, "set_child"):
        parent.set_child(None)


def show_all(widget):
    if isinstance(widget, Gtk.Popover):
        return
    widget.set_visible(True)
    # ComboBox popovers crash if realized outside a toplevel
    if isinstance(widget, Gtk.ComboBox):
        return
    for child in get_children(widget):
        if isinstance(child, Gtk.Popover):
            continue
        show_all(child)


class _FakeEvent:
    def __init__(self, button=0, keyval=0, hardware_keycode=0, state=0, x=0, y=0, type=None):
        self.button = button
        self.keyval = keyval
        self.hardware_keycode = hardware_keycode
        self.state = state
        self.x = x
        self.y = y
        self.type = type


def _widget_get_accessible(self):
    return _Accessible(self)


def _widget_show_all(self):
    show_all(self)


def _widget_get_children(self):
    return get_children(self)


def _widget_add(self, child):
    container_add(self, child)


def _widget_modify_bg(self, _state=None, color=None):
    r = g = b = 0
    if color is not None:
        r = getattr(color, "red", 0) or 0
        g = getattr(color, "green", 0) or 0
        b = getattr(color, "blue", 0) or 0
        if r > 1 or g > 1 or b > 1:
            r, g, b = r / 65535.0, g / 65535.0, b / 65535.0
    css = ".vmm-modify-bg { background-color: rgb(%d,%d,%d); }" % (
        int(r * 255),
        int(g * 255),
        int(b * 255),
    )
    self.add_css_class("vmm-modify-bg")
    provider = Gtk.CssProvider()
    provider.load_from_data(css.encode("utf-8"))
    self.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def _widget_get_window(self):
    native = self.get_native() if hasattr(self, "get_native") else None
    if native is not None and hasattr(native, "get_surface"):
        surface = native.get_surface()
        if surface is not None:
            return surface
    return self


def _widget_get_pointer(self):
    if hasattr(self, "_last_xy"):
        return self._last_xy
    return (0, 0)


def _get_current_event():
    return _FakeEvent()


class _EntryIconPosition:
    PRIMARY = 0
    SECONDARY = 1


def _box_pack_start(self, child, expand=True, fill=True, padding=0):
    ignore = fill
    if child.get_parent() is not None:
        child.unparent()
    if expand:
        if self.get_orientation() == Gtk.Orientation.VERTICAL:
            child.set_vexpand(True)
        else:
            child.set_hexpand(True)
    if padding:
        child.set_margin_start(child.get_margin_start() + padding)
        child.set_margin_end(child.get_margin_end() + padding)
    self.append(child)


def _box_pack_end(self, child, expand=True, fill=True, padding=0):
    ignore = fill
    if child.get_parent() is not None:
        child.unparent()
    if expand:
        if self.get_orientation() == Gtk.Orientation.VERTICAL:
            child.set_vexpand(True)
        else:
            child.set_hexpand(True)
    if padding:
        child.set_margin_start(child.get_margin_start() + padding)
        child.set_margin_end(child.get_margin_end() + padding)
    self.append(child)


def _run_modal(window, response_signal="response"):
    result = [Gtk.ResponseType.CLOSE]
    loop = GLib.MainLoop()

    def on_response(_src, response=None):
        if response is not None:
            result[0] = response
        if loop.is_running():
            loop.quit()

    def on_close(_src, *_args):
        if loop.is_running():
            loop.quit()
        return False

    hid = None
    if GObject.signal_lookup(response_signal, window):
        hid = window.connect(response_signal, on_response)
    close_hid = None
    if GObject.signal_lookup("close-request", window):
        close_hid = window.connect("close-request", on_close)
    window.present()
    ctx = GLib.MainContext.default()
    for _ in range(20):
        if not ctx.iteration(False):
            break
    loop.run()
    if hid is not None:
        window.disconnect(hid)
    if close_hid is not None:
        window.disconnect(close_hid)
    try:
        window.hide()
    except Exception:
        pass
    parent = None
    try:
        parent = window.get_transient_for()
    except Exception:
        parent = None
    if parent is not None:
        try:
            parent.present()
        except Exception:
            pass
        try:
            child = parent.get_focus() if hasattr(parent, "get_focus") else None
            if child is not None:
                child.grab_focus()
        except Exception:
            pass
    return result[0]


def run_dialog(dialog):
    return _run_modal(dialog)


def choose_alert(parent, heading, body="", responses=None, extra_child=None, default=None):
    """
    Synchronous Adw.AlertDialog. responses is [(id, label, appearance), ...]
    Returns the response id string.
    """
    if Adw is None:  # pragma: no cover
        raise RuntimeError("libadwaita is required")

    dialog = Adw.AlertDialog(heading=heading, body=body or "")
    dialog.set_accessible_role(Gtk.AccessibleRole.ALERT)
    if extra_child is not None:
        dialog.set_extra_child(extra_child)

    responses = responses or [("close", "Close", None)]
    for resp_id, label, appearance in responses:
        dialog.add_response(resp_id, label)
        if appearance is not None:
            dialog.set_response_appearance(resp_id, appearance)
    if default:
        dialog.set_default_response(default)

    result = [responses[-1][0]]
    loop = GLib.MainLoop()

    def _done(dlg, async_result):
        try:
            result[0] = dlg.choose_finish(async_result)
        except Exception:  # pragma: no cover
            result[0] = "close"
        loop.quit()

    dialog.choose(parent, None, _done)
    loop.run()
    return result[0]


def browse_local(
    parent,
    dialog_name,
    start_folder=None,
    _type=None,
    dialog_type=None,
    choose_label=None,
    default_name=None,
    confirm_overwrite=False,
):
    if dialog_type is None:
        dialog_type = Gtk.FileChooserAction.OPEN

    dialog = Gtk.FileDialog()
    dialog.set_title(dialog_name)
    if choose_label:
        dialog.set_accept_label(choose_label)
    if default_name:
        dialog.set_initial_name(default_name)

    if _type is not None:
        pattern = _type
        name = None
        if isinstance(_type, tuple):
            pattern = _type[0]
            name = _type[1]
        filt = Gtk.FileFilter()
        filt.add_pattern("*." + pattern)
        if name:
            filt.set_name(name)
        dialog.set_default_filter(filt)

    if start_folder and os.access(start_folder, os.R_OK):
        dialog.set_initial_folder(GioFile_for_path(start_folder))

    result = [None]
    loop = GLib.MainLoop()

    def _finish(dlg, async_result, opener):
        try:
            gfile = opener(dlg, async_result)
            result[0] = gfile.get_path() if gfile else None
        except Exception:
            result[0] = None
        loop.quit()

    if dialog_type == Gtk.FileChooserAction.SAVE:
        dialog.set_initial_name(default_name or "")
        dialog.save(parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.save_finish))
    elif dialog_type == Gtk.FileChooserAction.SELECT_FOLDER:
        dialog.select_folder(
            parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.select_folder_finish)
        )
    else:
        dialog.open(parent, None, lambda d, r: _finish(d, r, Gtk.FileDialog.open_finish))

    ignore = confirm_overwrite
    loop.run()
    return result[0]


def GioFile_for_path(path):
    return Gio.File.new_for_path(path)


############################################
# Custom GTypes for removed GTK3 widgets   #
############################################


class MenuItem(Gtk.Button):
    __gtype_name__ = "GtkMenuItem"

    use_underline = GObject.Property(type=bool, default=True)
    label = GObject.Property(type=str, default="")

    def __init__(self, label=None, **kwargs):
        kwargs.setdefault("has_frame", False)
        super().__init__(**kwargs)
        self.set_halign(Gtk.Align.FILL)
        self.set_hexpand(True)
        self.add_css_class("flat")
        self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        self._label_widget = Gtk.Label(xalign=0, use_underline=True)
        self._submenu = None
        self._submenu_btn = None
        if label or self.label:
            self.set_label(label or self.label)
        self.set_child(self._label_widget)
        self.connect("clicked", self._on_clicked)
        self.connect("notify::label", self._on_label_prop)
        self.vmm_widget_name = None
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_pointer_enter)
        motion.connect("leave", self._on_pointer_leave)
        self.add_controller(motion)

    def _set_selected(self, selected):
        self.update_state([Gtk.AccessibleState.SELECTED], [bool(selected)])

    def _on_pointer_enter(self, *_args):
        self._set_selected(True)
        if self._submenu is not None:
            self._submenu.popup_at_widget(self)

    def _on_pointer_leave(self, *_args):
        self._set_selected(False)

    def _sync_accessible_label(self):
        text = ""
        if self._label_widget is not None:
            text = self._label_widget.get_text() or ""
        if not text:
            text = (self.label or "").replace("_", "", 1)
        if text:
            set_accessible_name(self, text)
        if not self._submenu:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)

    def _on_label_prop(self, *_args):
        if self.label:
            self._label_widget.set_text_with_mnemonic(self.label)
            self._sync_accessible_label()

    def _on_clicked(self, *_args):
        self._set_selected(True)
        if self._submenu:
            self._submenu.popup_at_widget(self)
            return

        if getattr(self, "_vmm_activate_queued", False):
            return
        self._vmm_activate_queued = True

        # AT-SPI click waits for this handler. If we run a modal dialog
        # here, dogtail never returns to look for the alert.
        def _activate():
            try:
                self.emit("activate")
            except Exception:
                from virtinst import log

                log.exception("menu activate failed")
            finally:
                self._vmm_activate_queued = False
                menu = getattr(self, "_vmm_menu", None)
                while menu is not None:
                    try:
                        menu.popdown()
                    except Exception:
                        break
                    parent = getattr(menu, "_parent_widget", None)
                    menu = getattr(parent, "_vmm_menu", None) if parent else None
            return False

        GLib.idle_add(_activate)

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    @classmethod
    def new_with_label(cls, label):
        item = cls(label=label)
        item._label_widget.set_use_underline(False)
        return item

    def set_label(self, text):
        self.label = text or ""
        self._label_widget.set_text_with_mnemonic(text or "")
        self._sync_accessible_label()

    def do_add_child(self, builder, child, type_name):
        ignore = builder
        if type_name == "submenu" or isinstance(child, Menu):
            self.set_submenu(child)
            return
        Gtk.Button.set_child(self, child)

    def get_label(self):
        return self._label_widget.get_text()

    def get_child(self):
        return self._label_widget

    def set_use_underline(self, val):
        self.use_underline = bool(val)
        self._label_widget.set_use_underline(bool(val))

    def set_submenu(self, menu):
        self._submenu = menu
        if menu is not None:
            self.set_accessible_role(Gtk.AccessibleRole.MENU)
            # Do not parent the menu onto the item: GTK 4 concatenates
            # every submenu label into this item's accessible name.
            if menu.get_parent() is self:
                menu.unparent()
            menu._parent_widget = self

            def _map_menu():
                menu._ensure_popover(self)
                menu._ensure_mapped()
                return False

            GLib.idle_add(_map_menu)
        else:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        self._sync_accessible_label()

    def get_submenu(self):
        return self._submenu

    def set_child(self, child):
        if isinstance(child, Menu):
            self.set_submenu(child)
            return
        Gtk.Button.set_child(self, child)

    def set_sensitive(self, val):
        Gtk.Button.set_sensitive(self, val)


class CheckMenuItem(Gtk.CheckButton):
    __gtype_name__ = "GtkCheckMenuItem"

    use_underline = GObject.Property(type=bool, default=True)
    draw_as_radio = GObject.Property(type=bool, default=False)

    def __init__(self, label=None, **kwargs):
        super().__init__(**kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        self.vmm_widget_name = None
        if label:
            self.set_label(label)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self.update_state([Gtk.AccessibleState.SELECTED], [True]))
        motion.connect("leave", lambda *_a: self.update_state([Gtk.AccessibleState.SELECTED], [False]))
        self.add_controller(motion)
        self.connect("toggled", self._on_toggled)
        self.connect("notify::label", self._sync_accessible_label)
        self._sync_accessible_label()
        sync_accessible_checked(self)

    def _sync_accessible_label(self, *_args):
        text = ""
        try:
            text = self.get_label() or ""
        except Exception:
            text = ""
        text = _mnemonic_label(text)
        if text:
            set_accessible_name(self, text)
        try:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        except Exception:
            pass

    def _on_toggled(self, *_args):
        sync_accessible_checked(self)
        try:
            self.emit("activate")
        except Exception:
            pass
        menu = getattr(self, "_vmm_menu", None)
        while menu is not None:
            try:
                menu.popdown()
            except Exception:
                break
            parent = getattr(menu, "_parent_widget", None)
            menu = getattr(parent, "_vmm_menu", None) if parent else None

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    def get_child(self):
        return self


class RadioMenuItem(CheckMenuItem):
    __gtype_name__ = "GtkRadioMenuItem"

    def __init__(self, label=None, **kwargs):
        super().__init__(label=label, **kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.RADIO)

    def join_group(self, other):
        self.set_group(other)


class ImageMenuItem(MenuItem):
    __gtype_name__ = "GtkImageMenuItem"

    def __init__(self, label=None, **kwargs):
        super().__init__(label=label, **kwargs)
        self._image = Gtk.Image()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.append(self._image)
        box.append(self._label_widget)
        self.set_child(box)

    @classmethod
    def new_with_label(cls, label):
        return cls(label=label)

    @classmethod
    def new_with_mnemonic(cls, label):
        return cls(label=label)

    @classmethod
    def new_from_stock(cls, stock, _accel=None):
        label, icon = _stock_to_label_icon(stock)
        item = cls(label=label)
        if icon:
            item._image.set_from_icon_name(icon)
        return item

    def get_child(self):
        return self._label_widget


class SeparatorMenuItem(Gtk.Separator):
    __gtype_name__ = "GtkSeparatorMenuItem"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self.vmm_widget_name = None

    def get_submenu(self):
        return None

    def set_submenu(self, _menu):
        return None


class Menu(Gtk.Box):
    __gtype_name__ = "GtkMenu"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0, **kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.MENU)
        self.add_css_class("vmm-submenu")
        self._items = []
        self._popover = None
        self._parent_widget = None
        self._opened = False

    def add(self, item):
        self.insert(item, -1)

    def append(self, item):
        self.add(item)

    def insert(self, item, position):
        if item.get_parent() is not None:
            item.unparent()
        if position < 0 or position >= len(self._items):
            Gtk.Box.append(self, item)
            self._items.append(item)
        else:
            sibling = self._items[position]
            self.insert_child_after(item, sibling.get_prev_sibling())
            self._items.insert(position, item)
        item.set_visible(True)
        item._vmm_menu = self

    def remove(self, item):
        if item in self._items:
            self._items.remove(item)
        Gtk.Box.remove(self, item)

    def get_children(self):
        kids = get_children(self)
        if kids:
            return kids
        return list(self._items)

    def show_all(self):
        show_all(self)
        for item in self._items:
            show_all(item)

    def _ensure_popover(self, parent):
        # Use a transient undecorated window so AT-SPI can see menu items.
        # Gtk.Popover often exposes only empty panels to dogtail.
        if parent is not None:
            self._parent_widget = parent
        if self._popover is None:
            self._popover = Gtk.Window()
            self._popover.set_decorated(False)
            self._popover.set_resizable(False)
            self._popover.set_modal(False)
            self._popover.set_focusable(False)
            self._popover.set_focus_on_click(False)
            self._popover.set_accessible_role(Gtk.AccessibleRole.MENU)
            self._popover.add_css_class("menu")
            try:
                self._popover.set_default_size(220, max(32, 28 * max(1, len(self._items))))
            except Exception:
                pass
        root = None
        if self._parent_widget is not None and hasattr(self._parent_widget, "get_root"):
            try:
                root = self._parent_widget.get_root()
            except Exception:
                root = None
        if root is not None:
            try:
                self._popover.set_transient_for(root)
            except Exception:
                pass
        app = None
        if root is not None and hasattr(root, "get_application"):
            try:
                app = root.get_application()
            except Exception:
                app = None
        if app is None:
            try:
                app = Gtk.Application.get_default()
            except Exception:
                app = None
        if app is not None:
            try:
                app.add_window(self._popover)
            except Exception:
                pass
        if self.get_parent() is not None and self.get_parent() != self._popover:
            self.unparent()
        if self._popover.get_child() is not self:
            self._popover.set_child(self)
        self._sync_menu_a11y_name()
        self.remove_css_class("vmm-submenu")
        show_all(self)
        for item in self._items:
            show_all(item)
            if hasattr(item, "_sync_accessible_label"):
                item._sync_accessible_label()

    def _ensure_mapped(self):
        """
        Keep the menu window realized so dogtail can find items before click.
        Closed menus stay mapped at opacity 0 with a real allocation so
        AT-SPI click still activates them.
        """
        if self._popover is None:
            self._ensure_popover(self._parent_widget)
        if self._popover is None:
            return
        if not self._opened:
            self._popover.set_opacity(0)
        self._popover.set_visible(True)
        self._sync_menu_a11y_name()

    def _menu_open_name(self):
        name = getattr(self, "_vmm_menu_name", None) or self.get_name() or ""
        if name.startswith("."):
            name = name[1:]
        if name:
            self._vmm_menu_name = name
        return name

    def _sync_menu_a11y_name(self):
        name = self._menu_open_name()
        if not name:
            return
        # Prefix closed menus so find("vm-action-menu") only matches when open.
        shown = name if self._opened else "." + name
        set_accessible_name(self, shown)
        if self._popover is not None:
            set_accessible_name(self._popover, shown)

    def _destroy_popover(self):
        pop = self._popover
        if pop is None:
            return
        self._popover = None
        try:
            if self.get_parent() is pop:
                self.unparent()
        except Exception:
            pass
        try:
            pop.set_visible(False)
            pop.destroy()
        except Exception:
            pass

    def popup(self, *_args, **_kwargs):
        # Context menus (vm-action-menu, conn-menu) are not attached to a
        # toolbar button, so they have no _parent_widget until first popup.
        # Recreate the AT-SPI window each time: after popdown the cached
        # name stays ".vm-action-menu" and Extra cannot find it again.
        self._destroy_popover()
        self._opened = True
        self._ensure_popover(self._parent_widget)
        if self._popover is None:
            return
        self._popover.set_opacity(1)
        self._ensure_mapped()
        self._sync_menu_a11y_name()
        try:
            self._popover.present()
        except Exception:
            pass

    def popdown(self, *_args, **_kwargs):
        self._opened = False
        parent = self._parent_widget
        self._sync_menu_a11y_name()
        self._destroy_popover()
        # Toolbar Menu toggle stays active after an item click; reset it
        # so the next AT-SPI click opens the menu again.
        for cand in (parent, getattr(parent, "_menu_button", None)):
            if cand is not None and hasattr(cand, "get_active") and hasattr(cand, "set_active"):
                try:
                    if cand.get_active():
                        cand.set_active(False)
                except Exception:
                    pass

    def popup_at_pointer(self, event=None):
        ignore = event
        self.popup()

    def popup_at_widget(self, widget):
        self._ensure_popover(widget)
        self.popup()

    def popup_at_rect(self, _window, rect, _g1=None, _g2=None, _event=None):
        self._ensure_popover(self._parent_widget)
        if self._popover:
            self._popover.set_pointing_to(rect)
        self.popup()

    def get_accessible(self):
        return _Accessible(self)


class MenuBar(Gtk.Box):
    __gtype_name__ = "GtkMenuBar"

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self.add_css_class("menubar")
        self.set_accessible_role(Gtk.AccessibleRole.MENU_BAR)
        self._items = []

    def add(self, item):
        self.append(item)
        self._items.append(item)

    def get_children(self):
        return get_children(self)

    def do_add(self, child):
        # Builder child packing
        self.append(child)


class Toolbar(Gtk.Box):
    __gtype_name__ = "GtkToolbar"

    show_arrow = GObject.Property(type=bool, default=False)
    toolbar_style = GObject.Property(type=int, default=0)
    icon_size = GObject.Property(type=int, default=0)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, **kwargs)
        self.add_css_class("toolbar")

    def set_style(self, *_args):
        pass

    def set_show_arrow(self, *_args):
        pass

    def add(self, child):
        self.append(child)

    def get_children(self):
        return get_children(self)


class ToolButton(Gtk.Button):
    __gtype_name__ = "GtkToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class ToggleToolButton(Gtk.ToggleButton):
    __gtype_name__ = "GtkToggleToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


class RadioToolButton(Gtk.ToggleButton):
    __gtype_name__ = "GtkRadioToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")
    group = GObject.Property(type=str, default="")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_accessible_role(Gtk.AccessibleRole.RADIO)


class MenuToolButton(Gtk.Box):
    __gtype_name__ = "GtkMenuToolButton"

    is_important = GObject.Property(type=bool, default=False)
    use_underline = GObject.Property(type=bool, default=True)
    icon_name = GObject.Property(type=str, default="")
    label = GObject.Property(type=str, default="")
    has_tooltip = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
        self._button = Gtk.Button()
        self._menu_button = Gtk.ToggleButton()
        self._button.set_hexpand(True)
        self._button.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        self._menu_button.set_accessible_role(Gtk.AccessibleRole.TOGGLE_BUTTON)
        self._menu_button.set_icon_name("pan-down-symbolic")
        set_accessible_name(self._menu_button, "Menu")
        self.append(self._button)
        self.append(self._menu_button)
        self._menu = None
        self.connect("notify::label", self._sync_label)
        self.connect("notify::icon-name", self._sync_icon)
        self._button.connect(
            "clicked",
            lambda *_a: GLib.idle_add(lambda: self.emit("clicked") or False),
        )
        self._menu_button.connect("toggled", self._on_menu_toggled)

    def _sync_label(self, *_args):
        self._button.set_label(self.label)
        name = _mnemonic_label(self.label)
        if name:
            self._button._vmm_a11y_name = name
            ensure_button_accessible_name(self._button, name)
        else:
            apply_accessible_label(self._button)

    def _a11y_button_name(self):
        return _mnemonic_label(self.label) or getattr(
            self._button, "_vmm_a11y_name", None
        )

    def _sync_icon(self, *_args):
        name = self._a11y_button_name()
        if name:
            ensure_button_accessible_name(self._button, name)
        elif self.icon_name:
            self._button.set_icon_name(self.icon_name)
        apply_accessible_label(self._button)

    def set_icon_name(self, name):
        self.icon_name = name or ""
        a11y = self._a11y_button_name()
        if a11y:
            ensure_button_accessible_name(self._button, a11y)
        else:
            self._button.set_icon_name(name)
        apply_accessible_label(self._button)

    def set_label(self, label):
        self.label = label or ""
        self._button.set_label(label)
        name = _mnemonic_label(label)
        if name:
            ensure_button_accessible_name(self._button, name)
        else:
            apply_accessible_label(self._button)

    def _on_menu_toggled(self, button):
        if button.get_active() and self._menu is not None:
            if hasattr(self._menu, "popup_at_widget"):
                self._menu.popup_at_widget(button)

    def set_menu(self, menu):
        self._menu = menu
        if menu is None:
            return
        if isinstance(menu, Gtk.Popover):
            return
        if hasattr(menu, "_parent_widget"):
            menu._parent_widget = self._menu_button

            def _map_menu():
                menu._ensure_popover(self._menu_button)
                menu._ensure_mapped()
                return False

            GLib.idle_add(_map_menu)

    def get_menu(self):
        return self._menu

    def set_sensitive(self, val):
        Gtk.Box.set_sensitive(self, val)
        self._button.set_sensitive(val)
        self._menu_button.set_sensitive(val)


class SeparatorToolItem(Gtk.Separator):
    __gtype_name__ = "GtkSeparatorToolItem"

    homogeneous = GObject.Property(type=bool, default=False)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)


class EventBox(Gtk.Box):
    __gtype_name__ = "GtkEventBox"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._last_xy = (0, 0)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_motion)
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)
        self._entered = False

    def _on_motion(self, _c, x=0, y=0):
        self._last_xy = (x, y)
        self._entered = True

    def _on_leave(self, *_args):
        self._entered = False

    def add(self, child):
        self.append(child)

    def get_pointer(self):
        if not self._entered:
            return (-1, -1)
        return self._last_xy


class ButtonBox(Gtk.Box):
    __gtype_name__ = "GtkButtonBox"

    layout_style = GObject.Property(type=int, default=0)

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, **kwargs)
        self.set_halign(Gtk.Align.END)


class VBox(Gtk.Box):
    __gtype_name__ = "GtkVBox"

    def __init__(self, homogeneous=False, spacing=0, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=spacing or 0, **kwargs)
        self.set_homogeneous(bool(homogeneous))


class HBox(Gtk.Box):
    __gtype_name__ = "GtkHBox"

    def __init__(self, homogeneous=False, spacing=0, **kwargs):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing or 0, **kwargs)
        self.set_homogeneous(bool(homogeneous))


class Alignment(Gtk.Box):
    __gtype_name__ = "GtkAlignment"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)


def _stock_to_label_icon(stock):
    mapping = {
        "gtk-ok": (_("_OK"), "emblem-ok-symbolic"),
        "gtk-cancel": (_("_Cancel"), "window-close-symbolic"),
        "gtk-add": (_("_Add"), "list-add-symbolic"),
        "gtk-quit": (_("_Quit"), "application-exit-symbolic"),
        "gtk-connect": (_("_Connect"), "network-transmit-receive-symbolic"),
        "gtk-disconnect": (_("_Disconnect"), "network-offline-symbolic"),
        "gtk-close": (_("_Close"), "window-close-symbolic"),
        "gtk-yes": (_("_Yes"), None),
        "gtk-no": (_("_No"), None),
    }
    if stock in mapping:
        return mapping[stock]
    return (str(stock), None)


def _bin_remove(self, child=None):
    current = self.get_child() if hasattr(self, "get_child") else None
    if child is None or current is child or child is current:
        if hasattr(self, "set_child"):
            self.set_child(None)
            return
    if child is not None and child.get_parent() is self:
        child.unparent()


def _patch_bin_add(cls):
    if cls is None:
        return
    cls.add = _widget_add
    orig_remove = getattr(cls, "remove", None)

    def remove(self, child=None):
        if orig_remove is not None and child is not None:
            try:
                return orig_remove(self, child)
            except TypeError:
                pass
        return _bin_remove(self, child)

    cls.remove = remove


def _patch_widget_methods():
    Gtk.Widget.get_accessible = _widget_get_accessible
    Gtk.Widget.show_all = _widget_show_all
    Gtk.Widget.get_children = _widget_get_children
    Gtk.Widget.modify_bg = _widget_modify_bg
    Gtk.Widget.get_window = _widget_get_window
    Gtk.Widget.get_pointer = _widget_get_pointer

    orig_add = getattr(Gtk.Box, "add", None)
    ignore = orig_add
    Gtk.Box.add = _widget_add
    Gtk.Box.pack_start = _box_pack_start
    Gtk.Box.pack_end = _box_pack_end
    Gtk.Box.get_children = _widget_get_children
    orig_box_append = Gtk.Box.append

    def box_append(self, child):
        if child is not None and child.get_parent() is not None:
            child.unparent()
        return orig_box_append(self, child)

    Gtk.Box.append = box_append

    for clsname in (
        "ScrolledWindow",
        "Viewport",
        "Revealer",
        "Overlay",
        "Frame",
        "Expander",
        "Window",
        "ApplicationWindow",
        "Popover",
        "AspectFrame",
        "Dialog",
        "MessageDialog",
    ):
        _patch_bin_add(getattr(Gtk, clsname, None))

    if not hasattr(Gtk.Window, "get_position"):

        def get_position(self):
            return (0, 0)

        Gtk.Window.get_position = get_position

    if not hasattr(Gtk.Window, "move"):

        def move(self, *_args):
            return None

        Gtk.Window.move = move

    if not hasattr(Gtk.Window, "get_size"):

        def get_size(self):
            return (self.get_width(), self.get_height())

        Gtk.Window.get_size = get_size

    def set_border_width(self, width):
        self.set_margin_top(width)
        self.set_margin_bottom(width)
        self.set_margin_start(width)
        self.set_margin_end(width)

    Gtk.Widget.set_border_width = set_border_width

    if not hasattr(Gtk.Label, "set_line_wrap"):

        def set_line_wrap(self, wrap):
            self.set_wrap(bool(wrap))

        Gtk.Label.set_line_wrap = set_line_wrap

    if not hasattr(Gtk.Label, "set_line_wrap_mode"):

        def set_line_wrap_mode(self, mode):
            self.set_wrap_mode(mode)

        Gtk.Label.set_line_wrap_mode = set_line_wrap_mode

    _orig_label_new = Gtk.Label.__new__
    _orig_label_init = Gtk.Label.__init__

    def label_init(self, text=None, **kwargs):
        if text is not None and "label" not in kwargs:
            kwargs["label"] = text
        return _orig_label_init(self, **kwargs)

    Gtk.Label.__init__ = label_init

    def grab_default(self):
        root = self.get_root() if hasattr(self, "get_root") else None
        if root is not None and hasattr(root, "set_default_widget"):
            root.set_default_widget(self)
        if hasattr(self, "set_receives_default"):
            self.set_receives_default(True)

    Gtk.Widget.grab_default = grab_default

    if not hasattr(Gtk.Widget, "destroy"):

        def destroy(self):
            parent = self.get_parent()
            if parent is not None:
                self.unparent()
            self.run_dispose()

        Gtk.Widget.destroy = destroy

    if not hasattr(Gtk.Widget, "get_allocation"):

        class _Alloc:
            def __init__(self, widget):
                self.x = 0
                self.y = 0
                self.width = widget.get_width()
                self.height = widget.get_height()

        def get_allocation(self):
            return _Alloc(self)

        Gtk.Widget.get_allocation = get_allocation

    def resize(self, width, height):
        self.set_default_size(max(1, int(width)), max(1, int(height)))

    Gtk.Window.resize = resize

    def set_type_hint(self, *_args):
        return None

    Gtk.Window.set_type_hint = set_type_hint

    def add_accel_group(self, *_args):
        return None

    def remove_accel_group(self, *_args):
        return None

    Gtk.Window.add_accel_group = add_accel_group
    Gtk.Window.remove_accel_group = remove_accel_group

    def window_remove(self, child):
        if hasattr(self, "get_child") and self.get_child() is child:
            self.set_child(None)
            return
        if child is not None and child.get_parent() is self:
            child.unparent()

    Gtk.Window.remove = window_remove

    def set_relative_to(self, widget):
        parent = self.get_parent()
        if parent is not None and parent is not widget:
            self.unparent()
        if self.get_parent() is None and widget is not None:
            self.set_parent(widget)

    Gtk.Popover.set_relative_to = set_relative_to

    def _entry_set_icon_from_icon_name(self, _pos, _name):
        return None

    def _entry_set_icon_activatable(self, _pos, _val):
        return None

    Gtk.Entry.set_icon_from_icon_name = _entry_set_icon_from_icon_name
    Gtk.Entry.set_icon_activatable = _entry_set_icon_activatable

    orig_set_from_icon_name = Gtk.Image.set_from_icon_name

    def set_from_icon_name(self, name, _size=None):
        return orig_set_from_icon_name(self, name)

    Gtk.Image.set_from_icon_name = set_from_icon_name

    orig_new_from_icon_name = Gtk.Image.new_from_icon_name

    def new_from_icon_name(name, _size=None):
        return orig_new_from_icon_name(name)

    Gtk.Image.new_from_icon_name = staticmethod(new_from_icon_name)

    def new_from_stock(stock):
        label, icon = _stock_to_label_icon(stock)
        btn = Gtk.Button(label=label, use_underline=True)
        if icon:
            btn.set_icon_name(icon)
        return btn

    Gtk.Button.new_from_stock = staticmethod(new_from_stock)

    orig_dialog_run = getattr(Gtk.Dialog, "run", None)
    ignore = orig_dialog_run
    Gtk.Dialog.run = run_dialog

    if hasattr(Gtk, "NativeDialog"):
        Gtk.NativeDialog.run = run_dialog

    if not hasattr(Gtk.Dialog, "add_button"):

        def add_button(self, label, response):
            btn = Gtk.Button(label=label, use_underline=True)
            self.add_action_widget(btn, response)
            btn.set_visible(True)
            return btn

        Gtk.Dialog.add_button = add_button

    def add_buttons(self, *args):
        for idx in range(0, len(args), 2):
            self.add_button(args[idx], args[idx + 1])

    Gtk.Dialog.add_buttons = add_buttons

    def format_secondary_text(self, text):
        self.set_property("secondary-text", text or "")

    Gtk.MessageDialog.format_secondary_text = format_secondary_text

    # FileChooser path helpers
    if hasattr(Gtk, "FileChooser"):

        def get_filename(self):
            gfile = self.get_file()
            return gfile.get_path() if gfile else None

        def set_current_folder(self, path):
            if path:
                self.set_current_folder(GioFile_for_path(path) if isinstance(path, str) else path)

        Gtk.FileChooser.get_filename = get_filename

    orig_connect = Gtk.Widget.connect

    def connect(self, signal, callback, *args):
        if signal == "delete-event":
            return orig_connect(self, "close-request", lambda w: callback(w, None, *args) or False)
        if signal == "size-allocate":
            last = [None]

            def _tick(w, _clock):
                alloc = (w.get_width(), w.get_height())
                if alloc != last[0] and alloc[0] > 0 and alloc[1] > 0:
                    last[0] = alloc
                    callback(w, w.get_allocation() if hasattr(w, "get_allocation") else None, *args)
                return True

            return self.add_tick_callback(_tick)
        if signal == "configure-event":

            def _on_notify(w, *_a):
                callback(w, None, *args)

            return orig_connect(self, "notify::default-width", _on_notify)
        if signal == "button-press-event":
            gesture = Gtk.GestureClick()
            gesture.set_button(0)

            def _pressed(gest, _n, x, y):
                button = gest.get_current_button()
                ev = _FakeEvent(button=button, x=x, y=y)
                callback(self, ev, *args)

            gesture.connect("pressed", _pressed)
            self.add_controller(gesture)
            return id(gesture)
        if signal in ("key-press-event", "key-release-event"):
            controller = Gtk.EventControllerKey()
            sig = "key-pressed" if signal == "key-press-event" else "key-released"

            def _key(_c, keyval, keycode, state):
                ev = _FakeEvent(keyval=keyval, hardware_keycode=keycode, state=state)
                callback(self, ev, *args)
                return False

            controller.connect(sig, _key)
            self.add_controller(controller)
            return id(controller)
        if signal == "icon-press":

            def _icon(*_a):
                callback(self, Gtk.EntryIconPosition.SECONDARY, _FakeEvent(), *args)

            return orig_connect(self, "activate", _icon)
        if signal in ("enter-notify-event", "leave-notify-event"):
            controller = Gtk.EventControllerMotion()
            evname = "enter" if signal == "enter-notify-event" else "leave"

            def _motion(*_a):
                callback(self, _FakeEvent(), *args)

            controller.connect(evname, _motion)
            self.add_controller(controller)
            return id(controller)
        return orig_connect(self, signal, callback, *args)

    Gtk.Widget.connect = connect


def _install_stock_and_enums():
    Gtk.STOCK_OK = "gtk-ok"
    Gtk.STOCK_CANCEL = "gtk-cancel"
    Gtk.STOCK_ADD = "gtk-add"
    Gtk.STOCK_QUIT = "gtk-quit"
    Gtk.STOCK_CONNECT = "gtk-connect"
    Gtk.STOCK_DISCONNECT = "gtk-disconnect"
    Gtk.STOCK_CLOSE = "gtk-close"
    Gtk.STOCK_YES = "gtk-yes"
    Gtk.STOCK_NO = "gtk-no"

    class _DialogFlags:
        MODAL = 1
        DESTROY_WITH_PARENT = 2

    Gtk.DialogFlags = _DialogFlags

    # IconSize aliases used by virt-manager
    if not hasattr(Gtk.IconSize, "LARGE_TOOLBAR"):
        Gtk.IconSize.LARGE_TOOLBAR = Gtk.IconSize.LARGE
    if not hasattr(Gtk.IconSize, "DND"):
        Gtk.IconSize.DND = Gtk.IconSize.LARGE
    if not hasattr(Gtk.IconSize, "BUTTON"):
        Gtk.IconSize.BUTTON = Gtk.IconSize.NORMAL
    if not hasattr(Gtk.IconSize, "MENU"):
        Gtk.IconSize.MENU = Gtk.IconSize.NORMAL

    if not hasattr(Gtk, "ToolbarStyle"):

        class ToolbarStyle:
            BOTH_HORIZ = 0
            ICONS = 1
            TEXT = 2

        Gtk.ToolbarStyle = ToolbarStyle

    orig_icon_theme = Gtk.IconTheme.get_for_display

    def get_default():
        display = Gdk.Display.get_default()
        return orig_icon_theme(display)

    Gtk.IconTheme.get_default = staticmethod(get_default)

    if not hasattr(Gtk.IconTheme, "prepend_search_path"):
        Gtk.IconTheme.prepend_search_path = Gtk.IconTheme.add_search_path

    # Cursor helper: GTK3 was new_from_name(display, name)
    orig_cursor = Gdk.Cursor.new_from_name

    def new_from_name(*args):
        name = args[-1]
        try:
            return orig_cursor(name)
        except TypeError:
            return orig_cursor(*args)

    Gdk.Cursor.new_from_name = staticmethod(new_from_name)

    if not hasattr(Gdk, "Screen"):

        class _Screen:
            @staticmethod
            def get_default():
                return Gdk.Display.get_default()

        Gdk.Screen = _Screen

    if not hasattr(Gdk, "Color"):

        class Color:
            def __init__(self, red=0, green=0, blue=0):
                self.red = red
                self.green = green
                self.blue = blue

        Gdk.Color = Color

    if not hasattr(Gdk, "WindowTypeHint"):

        class WindowTypeHint:
            NORMAL = 0
            DIALOG = 1
            MENU = 2
            TOOLBAR = 3
            SPLASHSCREEN = 4
            UTILITY = 5
            DOCK = 6
            DESKTOP = 7

        Gdk.WindowTypeHint = WindowTypeHint

    if not hasattr(Gtk, "StateType"):

        class StateType:
            NORMAL = 0
            ACTIVE = 1
            PRELIGHT = 2
            SELECTED = 3
            INSENSITIVE = 4

        Gtk.StateType = StateType

    Gtk.EntryIconPosition = _EntryIconPosition
    Gtk.get_current_event = _get_current_event
    Gtk.accel_groups_from_object = lambda _obj: []

    if not hasattr(Gdk, "SELECTION_CLIPBOARD"):
        Gdk.SELECTION_CLIPBOARD = "CLIPBOARD"
    if not hasattr(Gdk, "SELECTION_PRIMARY"):
        Gdk.SELECTION_PRIMARY = "PRIMARY"

    if not hasattr(Gtk, "Clipboard"):

        class Clipboard:
            def __init__(self, display=None):
                self._display = display or Gdk.Display.get_default()
                self._clip = self._display.get_clipboard() if self._display else None

            @staticmethod
            def get(_selection=None):
                return Clipboard()

            @staticmethod
            def get_default(_display=None):
                return Clipboard(_display)

            def set_text(self, text, _length=-1):
                if self._clip is None:
                    return
                try:
                    self._clip.set(text or "")
                except Exception:
                    pass

            def wait_for_text(self):
                return None

        Gtk.Clipboard = Clipboard

    class VScrollbar(Gtk.Scrollbar):
        __gtype_name__ = "GtkVScrollbar"

        def __init__(self, adjustment=None, **kwargs):
            super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
            if adjustment is not None:
                self.set_adjustment(adjustment)

    class HScrollbar(Gtk.Scrollbar):
        __gtype_name__ = "GtkHScrollbar"

        def __init__(self, adjustment=None, **kwargs):
            super().__init__(orientation=Gtk.Orientation.HORIZONTAL, **kwargs)
            if adjustment is not None:
                self.set_adjustment(adjustment)

    Gtk.VScrollbar = VScrollbar
    Gtk.HScrollbar = HScrollbar

    orig_settings_get = Gtk.Settings.get_property
    orig_settings_set = Gtk.Settings.set_property

    def settings_get_property(self, name):
        try:
            return orig_settings_get(self, name)
        except TypeError:
            if name == "gtk-menu-bar-accel":
                return "F10"
            if name == "gtk-enable-mnemonics":
                return True
            raise

    def settings_set_property(self, name, value):
        try:
            return orig_settings_set(self, name, value)
        except TypeError:
            if name in ("gtk-menu-bar-accel", "gtk-enable-mnemonics"):
                return None
            raise

    Gtk.Settings.get_property = settings_get_property
    Gtk.Settings.set_property = settings_set_property

    orig_accel_parse = Gtk.accelerator_parse

    def accelerator_parse(accel):
        ret = orig_accel_parse(accel)
        if isinstance(ret, tuple) and len(ret) == 3:
            return ret[1], ret[2]
        return ret

    Gtk.accelerator_parse = accelerator_parse

    def _emit_toggled(self):
        self.emit("toggled")

    Gtk.ToggleButton.toggled = _emit_toggled
    Gtk.CheckButton.toggled = _emit_toggled


def _install_css_helpers():
    orig_add_provider = getattr(Gtk.StyleContext, "add_provider_for_display", None)

    def add_provider_for_screen(screen, provider, priority):
        display = Gdk.Display.get_default()
        ignore = screen
        if orig_add_provider:
            orig_add_provider(display, provider, priority)

    Gtk.StyleContext.add_provider_for_screen = staticmethod(add_provider_for_screen)


def _install_menuitem_activate_signal():
    if not GObject.signal_lookup("activate", MenuItem):
        GObject.signal_new(
            "activate", MenuItem, GObject.SignalFlags.RUN_FIRST, None, []
        )
    if not GObject.signal_lookup("activate", CheckMenuItem):
        GObject.signal_new(
            "activate", CheckMenuItem, GObject.SignalFlags.RUN_FIRST, None, []
        )
    if not GObject.signal_lookup("clicked", MenuToolButton):
        GObject.signal_new(
            "clicked", MenuToolButton, GObject.SignalFlags.RUN_FIRST, None, []
        )


def install():
    """
    Install GTK4 compatibility types and monkeypatches. Call after
    importing Gtk 4 and Adw.
    """
    _install_menuitem_activate_signal()
    _patch_widget_methods()
    _install_stock_and_enums()
    _install_css_helpers()

    Gtk.Menu = Menu
    Gtk.MenuItem = MenuItem
    Gtk.CheckMenuItem = CheckMenuItem
    Gtk.RadioMenuItem = RadioMenuItem
    Gtk.ImageMenuItem = ImageMenuItem
    Gtk.SeparatorMenuItem = SeparatorMenuItem
    Gtk.MenuBar = MenuBar
    Gtk.Toolbar = Toolbar
    Gtk.ToolButton = ToolButton
    Gtk.ToggleToolButton = ToggleToolButton
    Gtk.RadioToolButton = RadioToolButton
    Gtk.MenuToolButton = MenuToolButton
    Gtk.SeparatorToolItem = SeparatorToolItem
    Gtk.EventBox = EventBox
    Gtk.VBox = VBox
    Gtk.HBox = HBox
    Gtk.ButtonBox = ButtonBox
    Gtk.Alignment = Alignment

    # Assign module-level aliases used by Builder GTypes (already registered)
    return True
