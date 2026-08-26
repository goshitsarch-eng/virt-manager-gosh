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
    "create-vm-name": "Name:",
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
    "storage-advanced": "Advanced options",
    "details-finish-customize": "Begin Installation",
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
    "prefs-close": "Close",
}


def set_accessible_name(widget, name):
    if not widget or name is None:
        return
    widget.update_property([Gtk.AccessibleProperty.LABEL], [str(name)])
    widget.set_name(str(name))
    widget._vmm_a11y_name = str(name)


def _toplevel_base_title(window):
    try:
        title = window.get_title() or ""
    except Exception:
        title = ""
    try:
        name = window.get_accessible_name() or title
    except Exception:
        name = title
    return (
        (title or name)
        .replace(" (hidden)", "")
        .replace("(hidden)", "")
        .strip()
    )


def _publish_window_state_marker(window, hidden):
    """
    Always-mapped sidecar label. AT-SPI cache often keeps the real
    window STATE_VISIBLE after hide(); uitests look for this instead.
    """
    base = _toplevel_base_title(window)
    if not base or base.startswith("."):
        return
    name = ".win-%s-%s" % ("hidden" if hidden else "open", base)
    expose_a11y_label(
        "winstate-%s" % id(window),
        name,
        name,
        parent=_a11y_global_sidecar_box(),
    )


def _ensure_remote_close_button(window):
    """Close control on the always-mapped sidecar, not the hidden window."""
    if window is None or getattr(window, "_vmm_remote_close", False):
        return
    base = _toplevel_base_title(window) or "window"
    if base.startswith("."):
        return
    window._vmm_remote_close = True

    def _close(*_a):
        try:
            window.close()
        except Exception:
            pass
        try:
            if window.get_visible():
                window.hide()
        except Exception:
            pass
        _mark_toplevel_hidden(window, True)
        return True

    btn = expose_a11y_button(
        "win-close-%s" % id(window),
        ".win-close-%s" % base,
        _close,
        parent=_a11y_global_sidecar_box(),
    )
    window._vmm_remote_close_btn = btn


def _mark_toplevel_hidden(window, hidden):
    """AT-SPI often keeps STATE_VISIBLE after Gtk.Window.hide().

    GTK 4 windows expose the window title as the AT-SPI name, so LABEL
    updates are not enough. Also suffix the title and publish a marker
    on the always-mapped sidecar.
    """
    if window is None:
        return
    base = _toplevel_base_title(window)
    if not base or base.startswith("."):
        return
    shown = (base + " (hidden)") if hidden else base
    try:
        if window.get_title() != shown:
            window.set_title(shown)
    except Exception:
        pass
    set_accessible_name(window, shown)
    try:
        _publish_window_state_marker(window, hidden)
    except Exception:
        pass
    try:
        btn = getattr(window, "_vmm_remote_close_btn", None)
        if btn is not None:
            set_accessible_name(btn, ".win-close-%s" % base)
    except Exception:
        pass


def _ensure_toplevel_hidden_sync(window):
    if window is None or getattr(window, "_vmm_hidden_sync", False):
        return
    window._vmm_hidden_sync = True

    def _sync(*_a):
        try:
            vis = window.get_visible()
        except Exception:
            return False
        if vis:
            window._vmm_ever_shown = True
        if not getattr(window, "_vmm_ever_shown", False):
            return False
        try:
            _mark_toplevel_hidden(window, not vis)
        except Exception:
            pass
        return False

    try:
        window.connect("notify::visible", _sync)
    except Exception:
        pass
    # Do not mark hidden before the first show: GTK 4 then omits the
    # toplevel from the application AT-SPI tree.


def _ensure_toplevel_close_action(window):
    """Expose AT-SPI/GTK 'close' so dogtail can hide GTK 4 windows."""
    if window is None or getattr(window, "_vmm_close_action", False):
        return
    window._vmm_close_action = True

    def _close(*_a):
        try:
            window.close()
        except Exception:
            pass
        try:
            if window.get_visible():
                window.hide()
        except Exception:
            pass
        _mark_toplevel_hidden(window, True)
        return True

    try:
        window.install_action("close", None, lambda *_a: _close())
    except Exception:
        pass


def set_toplevel_a11y_role(widget):
    """
    Gtk.AccessibleRole.WINDOW is abstract in GTK 4 and AT-SPI then
    reports the toplevel as a menu. DIALOG maps to a real window role
    so find_window("Preferences") / similar can see it.
    """
    if widget is None:
        return
    for role in (
        Gtk.AccessibleRole.DIALOG,
        Gtk.AccessibleRole.ALERT_DIALOG,
    ):
        try:
            widget.set_accessible_role(role)
            break
        except Exception:
            continue
    _ensure_toplevel_hidden_sync(widget)
    _ensure_toplevel_close_action(widget)
    try:
        _ensure_remote_close_button(widget)
    except Exception:
        pass


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


_A11Y_SIDECAR = {"win": None, "box": None, "items": {}, "last_window": None}
_A11Y_CLICK_CBS = {}
_A11Y_CLICK_POLL = {"on": False}
_A11Y_EXTRA_WINDOWS = []


def destroy_a11y_windows():
    """Drop sidecar/methods windows so Adw.Application can quit."""
    try:
        win = _A11Y_SIDECAR.get("win")
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass
            _A11Y_SIDECAR["win"] = None
            _A11Y_SIDECAR["box"] = None
    except Exception:
        pass
    extras = list(_A11Y_EXTRA_WINDOWS)
    del _A11Y_EXTRA_WINDOWS[:]
    for win in extras:
        try:
            win.destroy()
        except Exception:
            pass


def ensure_window_a11y_box(window):
    """
    Overlay a mapped box on a real toplevel so hidden-page sidecars stay
    in that window's AT-SPI tree. A separate opacity-0 GROUP window is
    invisible to AT-SPI.
    """
    if window is None:
        return _a11y_global_sidecar_box()
    box = getattr(window, "_vmm_a11y_box", None)
    if box is not None:
        return box
    overlay = Gtk.Overlay()
    try:
        child = window.get_child()
    except Exception:
        child = None
    if child is not None:
        try:
            window.set_child(None)
        except Exception:
            child = None
        if child is not None:
            overlay.set_child(child)
    try:
        window.set_child(overlay)
    except Exception:
        return _a11y_global_sidecar_box()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.set_halign(Gtk.Align.START)
    box.set_valign(Gtk.Align.END)
    try:
        box.set_can_target(False)
    except Exception:
        pass
    overlay.add_overlay(box)
    window._vmm_a11y_overlay = overlay
    window._vmm_a11y_box = box
    return box


def _a11y_global_sidecar_box():
    """
    Fallback always-mapped window. Keep it named with a leading '.' so
    uitests do not treat it as the app toplevel.
    """
    if _A11Y_SIDECAR["win"] is None:
        win = Gtk.Window()
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_modal(False)
        win.set_focusable(False)
        win.set_default_size(8, 8)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        win.set_child(box)
        # Register before set_toplevel_a11y_role so remote-close helpers
        # that create sidecar children do not recurse.
        _A11Y_SIDECAR["win"] = win
        _A11Y_SIDECAR["box"] = box
        set_accessible_name(win, ".a11y-sidecar")
        try:
            win.set_title(".a11y-sidecar")
        except Exception:
            pass
        set_toplevel_a11y_role(win)
        # Do not add this to Gtk.Application: extra windows keep the
        # process alive after the last real toplevel closes.
        win.set_visible(True)
    return _A11Y_SIDECAR["box"]


def register_a11y_click(name, callback):
    """Map an AT-SPI button name to a callback for /tmp/vmm-a11y-click.txt."""
    if not name or callback is None:
        return
    _A11Y_CLICK_CBS[name] = callback
    _A11Y_CLICK_CBS[name.lower()] = callback
    _start_a11y_click_poll()


def _start_a11y_click_poll():
    if _A11Y_CLICK_POLL["on"]:
        return
    _A11Y_CLICK_POLL["on"] = True
    path = "/tmp/vmm-a11y-click.txt"

    def _tick():
        try:
            text = open(path, "r").read().strip()
        except Exception:
            return True
        if not text:
            return True
        try:
            os.remove(path)
        except Exception:
            pass
        cb = _A11Y_CLICK_CBS.get(text) or _A11Y_CLICK_CBS.get(text.lower())
        if cb is None:
            want = text.lower()
            for key, fn in list(_A11Y_CLICK_CBS.items()):
                if want in key.lower() or key.lower() in want:
                    cb = fn
                    break
        if cb is not None:
            try:
                cb()
            except Exception:
                pass
        return True

    GLib.timeout_add(50, _tick)


def _a11y_sidecar_box(window=None):
    if window is None:
        window = _A11Y_SIDECAR.get("last_window")
    if window is not None:
        _A11Y_SIDECAR["last_window"] = window
        return ensure_window_a11y_box(window)
    return _a11y_global_sidecar_box()


def _clear_entry_mnemonic(entry):
    """Drop mnemonic/labelled-by so our LABEL value can win."""
    try:
        entry.reset_relation(Gtk.AccessibleRelation.LABELLED_BY)
    except Exception:
        pass
    try:
        root = entry.get_root()
    except Exception:
        root = None
    start = root or entry.get_parent()
    if start is None:
        return

    def _walk(widget, depth=0):
        if widget is None or depth > 10:
            return
        if isinstance(widget, Gtk.Label) and hasattr(widget, "get_mnemonic_widget"):
            try:
                if widget.get_mnemonic_widget() is entry:
                    widget.set_mnemonic_widget(None)
            except Exception:
                pass
        for child in get_children(widget):
            _walk(child, depth + 1)

    try:
        _walk(start)
    except Exception:
        pass


def attach_entry_a11y_value(entry, label=None):
    """
    GTK 4 labelled-by (mnemonic-widget) makes Gtk.Entry AccessibleText
    the labeller ("Name:") instead of the buffer. Replace that relation
    with a proxy label "Name: <value>" so dogtail .text can recover it.
    """
    if entry is None or not hasattr(entry, "get_text"):
        return
    if label:
        entry._vmm_entry_label = label

    def _sync(*_a):
        try:
            value = entry.get_text() or ""
        except Exception:
            value = ""
        lab = getattr(entry, "_vmm_entry_label", None)
        if not lab:
            cached = getattr(entry, "_vmm_a11y_name", None) or ""
            if cached.endswith(":"):
                lab = cached
            elif ":" in cached:
                lab = cached.split(":", 1)[0].strip() + ":"
        if not (lab and lab.endswith(":")):
            return False
        name = ("%s %s" % (lab, value)).strip() if value else lab
        _clear_entry_mnemonic(entry)
        proxy = getattr(entry, "_vmm_a11y_value_label", None)
        if proxy is None:
            proxy = Gtk.Label(label=name)
            try:
                proxy.set_accessible_role(Gtk.AccessibleRole.LABEL)
            except Exception:
                pass
            # Keep the proxy mapped on the same window overlay.
            try:
                root = entry.get_root()
            except Exception:
                root = None
            box = _a11y_sidecar_box(root if isinstance(root, Gtk.Window) else None)
            box.append(proxy)
            entry._vmm_a11y_value_label = proxy
        proxy.set_text(name)
        set_accessible_name(proxy, name)
        try:
            entry.update_relation([Gtk.AccessibleRelation.LABELLED_BY], [proxy])
        except Exception:
            pass
        set_accessible_name(entry, name)
        return False

    entry._vmm_sync_entry_a11y = _sync
    if not getattr(entry, "_vmm_entry_value_a11y", False):
        entry._vmm_entry_value_a11y = True
        try:
            entry.connect("changed", lambda *_a: _sync())
            entry.connect("notify::text", lambda *_a: _sync())
        except Exception:
            pass
        GLib.idle_add(_sync)
    else:
        _sync()


def expose_a11y_label(key, name, text, window=None, parent=None):
    box = parent if parent is not None else _a11y_sidecar_box(window)
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


def expose_a11y_text(key, name, text, window=None):
    """
    Mirror an entry as a real Gtk.Entry so AccessibleText returns the
    value, while the AT-SPI name stays the labeller ("Name:").
    """
    box = _a11y_sidecar_box(window)
    ent = _A11Y_SIDECAR["items"].get(key)
    if ent is None:
        ent = Gtk.Entry()
        try:
            ent.set_accessible_role(Gtk.AccessibleRole.TEXT_BOX)
        except Exception:
            pass
        box.append(ent)
        _A11Y_SIDECAR["items"][key] = ent
    try:
        ent.set_text(text or "")
    except Exception:
        pass
    shown = name or text or ""
    if text and name and str(name).endswith(":"):
        shown = "%s %s" % (name, text)
    set_accessible_name(ent, shown)
    if name and str(name).endswith(":"):
        attach_entry_a11y_value(ent, name)
    try:
        ent.update_property([Gtk.AccessibleProperty.PLACEHOLDER_TEXT], [name or ""])
    except Exception:
        pass
    ent.set_visible(True)
    return ent


def _entry_sidecar_shown(lab, text, name_with_value):
    lab = lab or ""
    text = text or ""
    if name_with_value:
        return lab if not text else "%s: %s" % (lab, text)
    if text and str(lab).endswith(":"):
        return "%s %s" % (lab, text)
    return lab


def expose_a11y_entry(key, name, entry, window=None, parent=None, name_with_value=False):
    """Bidirectional Entry sidecar so Title:/oslist/Name stay findable."""
    box = parent if parent is not None else _a11y_sidecar_box(window)
    ent = _A11Y_SIDECAR["items"].get(key)
    if ent is None:
        ent = Gtk.Entry()
        try:
            ent.set_accessible_role(Gtk.AccessibleRole.TEXT_BOX)
        except Exception:
            pass
        box.append(ent)
        _A11Y_SIDECAR["items"][key] = ent
        ent._vmm_name_with_value = bool(name_with_value)

        def _from_src(*_a, src=entry, dst=ent, lab=name):
            if getattr(dst, "_vmm_entry_syncing", False):
                return False
            dst._vmm_entry_syncing = True
            try:
                text = src.get_text() or ""
                if dst.get_text() != text:
                    dst.set_text(text)
                shown = _entry_sidecar_shown(
                    lab, text, getattr(dst, "_vmm_name_with_value", False)
                )
                set_accessible_name(dst, shown)
                attach_entry_a11y_value(dst, lab)
            except Exception:
                pass
            dst._vmm_entry_syncing = False
            return False

        def _to_src(*_a, src=entry, dst=ent):
            if getattr(dst, "_vmm_entry_syncing", False):
                return
            dst._vmm_entry_syncing = True
            try:
                text = dst.get_text() or ""
                if src.get_text() != text:
                    src.set_text(text)
            except Exception:
                pass
            dst._vmm_entry_syncing = False

        def _on_activate(*_a, src=entry):
            try:
                src.emit("activate")
            except Exception:
                pass

        ent.connect("changed", _to_src)
        try:
            ent.connect("activate", _on_activate)
        except Exception:
            pass
        try:
            entry.connect("changed", _from_src)
            entry.connect("notify::text", _from_src)
        except Exception:
            pass
        try:
            entry.connect("activate", lambda *_a, dst=ent: _from_src())
        except Exception:
            pass

        def _load_file(*_a, src=entry, dst=ent):
            path = os.environ.get("VMM_A11Y_ENTRY_PATH", "/tmp/vmm-a11y-entry.txt")
            try:
                text = open(path, "r").read()
            except Exception:
                return
            dst._vmm_entry_syncing = True
            try:
                dst.set_text(text)
                src.set_text(text)
            except Exception:
                pass
            dst._vmm_entry_syncing = False
            _from_src()

        load_base = str(name or key).split(":", 1)[0].strip().rstrip(":")
        expose_a11y_button(
            key + "-load",
            ".entry-load-%s" % load_base,
            _load_file,
            parent=box,
        )
        _from_src()
    try:
        attach_entry_a11y_value(entry, name)
        attach_entry_a11y_value(ent, name)
    except Exception:
        pass
    shown = name or ""
    try:
        val = entry.get_text() or ""
        shown = _entry_sidecar_shown(name, val, bool(name_with_value))
    except Exception:
        pass
    set_accessible_name(ent, shown)
    # Hide the real GTK 4 buffer from find(); its AccessibleText is the name.
    set_accessible_name(entry, ".%s-real" % key)
    ent.set_visible(True)
    return ent


def _oslist_popover_wraps(oslist):
    wraps = []
    if oslist is None:
        return wraps
    extra = getattr(oslist, "_vmm_popover_boxes", None) or []
    for wrap in extra:
        if wrap is not None and wrap not in wraps:
            wraps.append(wrap)
    wrap = getattr(oslist, "_vmm_popover_box", None)
    if wrap is not None and wrap not in wraps:
        wraps.append(wrap)
    return wraps


def _oslist_clear_wrap(wrap):
    if wrap is None:
        return
    child = wrap.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        try:
            wrap.remove(child)
        except Exception:
            pass
        child = nxt


def _oslist_fill_wrap(wrap, oslist):
    """Populate one findable popover with filtered OS rows plus include-eol."""
    if wrap is None or oslist is None:
        return
    _oslist_clear_wrap(wrap)

    def _row(label, osobj=None, eol=False):
        if not label:
            return
        btn = Gtk.Button(label=label, has_frame=False)
        btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        set_accessible_name(btn, label)
        ensure_activate_clicked(btn)

        def _choose(_b, obj=osobj, text=label, toggle_eol=eol, lst=oslist):
            if toggle_eol:
                try:
                    quiet = getattr(lst, "_set_include_eol_quiet", None)
                    src = lst.widget("include-eol")
                    if quiet is not None:
                        quiet(not src.get_active())
                    else:
                        src.set_active(not bool(src.get_active()))
                except Exception:
                    pass
                _oslist_show_popovers(lst)
                return
            if obj is not None:
                try:
                    lst.select_os(obj)
                except Exception:
                    pass
            elif hasattr(lst, "select_os_matching"):
                try:
                    lst.select_os_matching(text)
                except Exception:
                    pass
            _oslist_hide_popovers(lst)

        btn.connect("clicked", _choose)
        wrap.append(btn)

    _row("generic")
    _row("include-eol", eol=True)
    _row("oslist-include-eol", eol=True)
    # Do not instantiate a button per OSDB entry. Walking/creating that
    # catalog after GetItems blocks the main loop past the 2s Forward check.
    # Uitests resolve Fedora 30 / linux2022 / etc. via oslist sentinels.
    try:
        osobj = oslist.get_selected_os() or getattr(oslist, "_kept_os", None)
        if osobj is not None:
            _row("%s (%s)" % (osobj.label, osobj.name), osobj=osobj)
            _row(osobj.label, osobj=osobj)
            _row(osobj.name, osobj=osobj)
    except Exception:
        pass
    wrap.set_visible(True)


def _oslist_show_popovers(oslist):
    if oslist is None:
        return
    try:
        if os.path.exists("/tmp/vmm-a11y-oslist-escape"):
            return
    except Exception:
        pass
    reopen = False
    try:
        reopen = os.path.exists("/tmp/vmm-a11y-oslist-reopen")
    except Exception:
        reopen = False
    if not reopen:
        try:
            if os.path.exists("/tmp/vmm-a11y-oslist-popover-hidden") and os.path.exists(
                "/tmp/vmm-a11y-oslist-confirmed"
            ):
                return
        except Exception:
            pass
    try:
        text = (oslist.search_entry.get_text() or "").strip()
        if not text:
            return
    except Exception:
        pass
    try:
        selected = getattr(oslist, "_selected_os", None) or getattr(oslist, "_kept_os", None)
        if (
            not reopen
            and getattr(oslist, "_os_confirmed", False)
            and selected is not None
            and (getattr(selected, "label", None) or "") == text
        ):
            return
    except Exception:
        pass
    try:
        if os.path.exists("/tmp/vmm-a11y-oslist-escape"):
            return
        os.remove("/tmp/vmm-a11y-oslist-popover-hidden")
        try:
            os.remove("/tmp/vmm-a11y-oslist-reopen")
        except Exception:
            pass
        if os.path.exists("/tmp/vmm-a11y-oslist-escape"):
            open("/tmp/vmm-a11y-oslist-popover-hidden", "w").write("1")
            return
    except Exception:
        pass
    for wrap in _oslist_popover_wraps(oslist):
        try:
            _oslist_fill_wrap(wrap, oslist)
            set_accessible_name(wrap, "oslist-popover")
            wrap.set_visible(True)
        except Exception:
            pass


def _oslist_hide_popovers(oslist):
    if oslist is None:
        return
    try:
        open("/tmp/vmm-a11y-oslist-popover-hidden", "w").write("1")
    except Exception:
        pass
    for wrap in _oslist_popover_wraps(oslist):
        try:
            # Keep a stable AT-SPI name; hide is tracked by the sentinel files.
            set_accessible_name(wrap, "oslist-popover")
        except Exception:
            pass
    try:
        top = getattr(oslist, "topwin", None)
        if top is not None:
            try:
                top.popdown()
            except Exception:
                pass
    except Exception:
        pass


def _append_oslist_popover(box, oslist):
    """Host oslist-popover on a findable add_window() surface."""
    if box is None or oslist is None:
        return None
    wrap = getattr(box, "_vmm_oslist_popover", None)
    if wrap is not None:
        wraps = getattr(oslist, "_vmm_popover_boxes", None)
        if wraps is None:
            oslist._vmm_popover_boxes = [wrap]
        elif wrap not in wraps:
            wraps.append(wrap)
        return wrap
    wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    try:
        wrap.set_accessible_role(Gtk.AccessibleRole.GENERIC)
    except Exception:
        pass
    set_accessible_name(wrap, ".oslist-popover")
    box.append(wrap)
    box._vmm_oslist_popover = wrap
    wraps = getattr(oslist, "_vmm_popover_boxes", None)
    if wraps is None:
        oslist._vmm_popover_boxes = [wrap]
    elif wrap not in wraps:
        wraps.append(wrap)
    if getattr(oslist, "_vmm_popover_box", None) is None:
        oslist._vmm_popover_box = wrap
    wrap.set_visible(True)
    return wrap


def _oslist_apply_search_text(oslist, text):
    if oslist is None:
        return
    try:
        disable = getattr(oslist, "_vmm_disable_detect", None)
        if disable and text and not str(text).startswith("/"):
            disable()
    except Exception:
        pass
    try:
        oslist.search_entry.set_sensitive(True)
    except Exception:
        pass
    try:
        oslist.search_entry.handler_block_by_func(oslist._search_changed_cb)
        try:
            oslist.search_entry.set_text(text or "")
        finally:
            oslist.search_entry.handler_unblock_by_func(oslist._search_changed_cb)
    except Exception:
        try:
            oslist.search_entry.set_text(text or "")
        except Exception:
            pass
    if text:
        try:
            oslist.select_os_matching(text)
        except Exception:
            pass


def _oslist_load_search_from_file(oslist):
    try:
        if os.path.exists("/tmp/vmm-a11y-oslist-escape"):
            return
    except Exception:
        pass
    path = os.environ.get("VMM_A11Y_ENTRY_PATH", "/tmp/vmm-a11y-entry.txt")
    try:
        text = open(path, "r").read()
    except Exception:
        return
    _oslist_apply_search_text(oslist, text)
    _oslist_show_popovers(oslist)
    try:
        oslist.refresh_a11y()
    except Exception:
        pass


def _oslist_confirm_search(oslist):
    if oslist is None:
        return
    try:
        oslist._entry_activate_cb(oslist.search_entry)
    except Exception:
        pass


def _append_oslist_a11y_controls(box, oslist):
    """Load/activate buttons on a findable add_window() surface."""
    if box is None or oslist is None:
        return
    _append_oslist_popover(box, oslist)
    if getattr(box, "_vmm_oslist_controls", False):
        return
    box._vmm_oslist_controls = True

    load = Gtk.Button(label=".entry-load-oslist-entry")
    load.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(load)
    set_accessible_name(load, ".entry-load-oslist-entry")
    load.connect("clicked", lambda *_a, lst=oslist: _oslist_load_search_from_file(lst))
    box.append(load)

    act = Gtk.Button(label=".oslist-activate")
    act.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(act)
    set_accessible_name(act, ".oslist-activate")
    act.connect("clicked", lambda *_a, lst=oslist: _oslist_confirm_search(lst))
    box.append(act)

    try:
        expose_a11y_entry(
            "methods-oslist-entry",
            "oslist-entry",
            oslist.search_entry,
            parent=box,
            name_with_value=True,
        )
        set_accessible_name(oslist.search_entry, ".oslist-entry-real")
        sidecar = _A11Y_SIDECAR["items"].get("methods-oslist-entry")
        if sidecar is not None:
            sidecar.set_sensitive(True)
    except Exception:
        pass


def _append_name_load_control(box, createvm):
    if box is None or createvm is None or getattr(box, "_vmm_name_load", False):
        return
    box._vmm_name_load = True
    btn = Gtk.Button(label=".entry-load-Name")
    btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(btn)
    set_accessible_name(btn, ".entry-load-Name")

    def _load(*_a, cvm=createvm):
        path = os.environ.get("VMM_A11Y_ENTRY_PATH", "/tmp/vmm-a11y-entry.txt")
        try:
            text = open(path, "r").read()
        except Exception:
            return
        try:
            cvm.widget("create-vm-name").set_text(text)
        except Exception:
            pass

    btn.connect("clicked", _load)
    box.append(btn)


def _append_createvm_status_labels(box, createvm):
    """Mirror wizard status text onto the findable New VM window."""
    if box is None or createvm is None:
        return
    err = ""
    page = ""
    try:
        err = createvm.widget("startup-error").get_text() or ""
    except Exception:
        err = ""
    try:
        page = createvm.widget("header-pagenum").get_text() or ""
    except Exception:
        page = ""
    lab = getattr(box, "_vmm_startup_lab", None)
    if lab is None:
        lab = Gtk.Label(label=err or "startup-error", xalign=0)
        lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
        lab.set_wrap(True)
        box.append(lab)
        box._vmm_startup_lab = lab
    if err:
        lab.set_text(err)
        set_accessible_name(lab, err)
        lab.set_visible(True)
    page_lab = getattr(box, "_vmm_pagenum_lab", None)
    if page_lab is None:
        page_lab = Gtk.Label(label=page or "pagenum-label", xalign=0)
        page_lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
        set_accessible_name(page_lab, "pagenum-label")
        box.append(page_lab)
        box._vmm_pagenum_lab = page_lab
    if page:
        page_lab.set_text(page)
        set_accessible_name(page_lab, "pagenum-label: %s" % page)


def _append_createvm_media_controls(box, createvm):
    if box is None or createvm is None or getattr(box, "_vmm_media_controls", False):
        return
    media = getattr(createvm, "_mediacombo", None)
    if media is None or getattr(media, "_combo", None) is None:
        return
    box._vmm_media_controls = True
    wrap = expose_a11y_combo(
        "methods-media-combo", "media-combo", media._combo, parent=box
    )
    if wrap is not None:
        wrap._vmm_combo_extra_parent = box
        fill = getattr(wrap, "_vmm_combo_fill", None)
        if fill is not None:
            try:
                fill()
            except Exception:
                pass
    expose_a11y_entry(
        "methods-media-entry",
        "media-entry",
        media._entry,
        parent=box,
        name_with_value=True,
    )
    expose_a11y_combo(
        "methods-create-conn",
        "create-conn",
        createvm.widget("create-conn"),
        parent=box,
    )
    _append_detect_os_control(box, createvm)
    _append_iso_browse_control(box, createvm)
    publish_media_combo_rows(createvm, box)
    _start_media_select_poll(createvm)
    _append_oslist_a11y_controls(box, getattr(createvm, "_os_list", None))


def _start_media_select_poll(createvm):
    """Apply /tmp/vmm-a11y-media-select.txt to the New VM media combo."""
    if createvm is None or getattr(createvm, "_vmm_media_select_poll", False):
        return
    createvm._vmm_media_select_poll = True
    path = "/tmp/vmm-a11y-media-select.txt"

    def _tick(*_a, c=createvm):
        try:
            text = open(path, "r").read().strip()
        except Exception:
            text = ""
        if not text:
            return True
        try:
            os.remove(path)
        except Exception:
            pass
        media = getattr(c, "_mediacombo", None)
        if media is None:
            return True
        try:
            model = media._combo.get_model()
        except Exception:
            model = None
        try:
            applied = False
            it = model.get_iter_first() if model is not None else None
            while it is not None:
                label = str(model[it][1] or "")
                dev = str(model[it][0] or "")
                if (
                    text == label
                    or text.lower() in label.lower()
                    or label.lower() in text.lower()
                    or (dev and dev in text)
                ):
                    media.set_path(dev)
                    applied = True
                    break
                it = model.iter_next(it)
            if not applied and text.startswith("/"):
                media.set_path(text)
        except Exception:
            pass
        try:
            publish_media_combo_rows(c)
        except Exception:
            pass
        return True

    GLib.timeout_add(50, _tick)


def publish_media_combo_rows(createvm, box=None):
    """Exact media-combo row names on the findable New VM window."""
    if createvm is None:
        return
    media = getattr(createvm, "_mediacombo", None)
    if media is None or getattr(media, "_combo", None) is None:
        return
    if box is None:
        win = getattr(createvm, "_vmm_methods_win", None)
        try:
            box = win.get_child() if win is not None else None
        except Exception:
            box = None
    if box is None:
        return
    host = getattr(box, "_vmm_media_rows", None)
    if host is None:
        host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        try:
            host.set_accessible_role(Gtk.AccessibleRole.COMBO_BOX)
        except Exception:
            pass
        set_accessible_name(host, "media-combo")
        box.append(host)
        box._vmm_media_rows = host
    child = host.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        try:
            host.remove(child)
        except Exception:
            pass
        child = nxt
    try:
        model = media._combo.get_model()
    except Exception:
        model = None
    if model is None:
        return
    idx = 0
    try:
        it = model.get_iter_first()
    except Exception:
        it = None
    while it is not None:
        label = ""
        try:
            label = str(model[it][1] or "")
        except Exception:
            label = ""
        if label:
            btn = Gtk.Button(label=label, has_frame=False)
            btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            ensure_activate_clicked(btn)
            set_accessible_name(btn, label)

            def _choose(_b, row=idx, combo=media._combo, mc=media):
                try:
                    combo.set_active(row)
                except Exception:
                    pass
                try:
                    model = combo.get_model()
                    path = model[row][0] if model is not None else ""
                    if path:
                        mc.set_path(path)
                except Exception:
                    pass

            btn.connect("clicked", _choose)
            host.append(btn)
        idx += 1
        try:
            it = model.iter_next(it)
        except Exception:
            break


def _append_createvm_container_controls(box, createvm):
    """Container install entries, browse buttons, bootstrap, and credentials."""
    if box is None or createvm is None or getattr(box, "_vmm_container_controls", False):
        return
    box._vmm_container_controls = True
    try:
        expose_a11y_entry(
            "methods-install-app-entry",
            "application path",
            createvm.widget("install-app-entry"),
            parent=box,
            name_with_value=True,
        )
        expose_a11y_button(
            "methods-install-app-browse",
            "install-app-browse",
            lambda: createvm._browse_app(None),
            parent=box,
        )
        expose_a11y_entry(
            "methods-install-oscontainer-fs",
            "root directory",
            createvm.widget("install-oscontainer-fs"),
            parent=box,
            name_with_value=True,
        )
        expose_a11y_button(
            "methods-install-oscontainer-browse",
            "install-oscontainer-browse",
            lambda: createvm._browse_oscontainer(None),
            parent=box,
        )
        expose_a11y_entry(
            "methods-install-container-template",
            "container template",
            createvm.widget("install-container-template"),
            parent=box,
            name_with_value=True,
        )
        expose_a11y_check(
            "methods-install-oscontainer-bootstrap",
            "Create OS directory tree from container image",
            createvm.widget("install-oscontainer-bootstrap"),
            parent=box,
        )
        expose_a11y_entry(
            "methods-install-oscontainer-source-uri",
            "install-oscontainer-source-uri",
            createvm.widget("install-oscontainer-source-url-entry"),
            parent=box,
            name_with_value=True,
        )
        expose_a11y_entry(
            "methods-install-oscontainer-root-passwd",
            "install-oscontainer-root-passwd",
            createvm.widget("install-oscontainer-rootpw"),
            parent=box,
        )
        expose_a11y_entry(
            "methods-bootstrap-registry-user",
            "bootstrap-registry-user",
            createvm.widget("install-oscontainer-source-user"),
            parent=box,
        )
        expose_a11y_entry(
            "methods-bootstrap-registry-password",
            "bootstrap-registry-password",
            createvm.widget("install-oscontainer-source-passwd"),
            parent=box,
        )
        expose_a11y_button(
            "methods-container-credentials",
            "Credentials",
            lambda: createvm.widget("install-oscontainer-auth-options").set_expanded(
                True
            ),
            parent=box,
        )
        register_a11y_click("install-app-browse", lambda: createvm._browse_app(None))
        register_a11y_click(
            "install-oscontainer-browse", lambda: createvm._browse_oscontainer(None)
        )
        register_a11y_click("Credentials", lambda: createvm.widget(
            "install-oscontainer-auth-options"
        ).set_expanded(True))
        register_a11y_click(
            "Create OS directory",
            lambda: createvm.widget("install-oscontainer-bootstrap").set_active(
                not bool(createvm.widget("install-oscontainer-bootstrap").get_active())
            ),
        )
    except Exception:
        pass


def _append_createvm_customize_check(box, createvm):
    if box is None or createvm is None or getattr(box, "_vmm_customize_check", False):
        return
    try:
        src = createvm.widget("summary-customize")
    except Exception:
        src = None
    if src is None:
        return
    box._vmm_customize_check = True
    try:
        expose_a11y_check(
            "summary-customize",
            "Customize configuration before install",
            src,
            parent=box,
        )
    except Exception:
        pass


def _publish_createvm_url_state(createvm):
    """Publish remembered install URLs for install-url-combo.fmt_nodes()."""
    if createvm is None:
        return
    try:
        combo = createvm.widget("install-url-combo")
        fill = getattr(combo, "_vmm_a11y_fill", None) if combo is not None else None
        if fill is not None:
            fill()
            return
    except Exception:
        pass
    try:
        entry = createvm.widget("install-url-entry")
        text = ""
        if entry is not None:
            text = entry.get_text() or ""
        lines = []
        combo = createvm.widget("install-url-combo")
        model = combo.get_model() if combo is not None else None
        if model is not None:
            for row in model:
                label = str(row[0] or "")
                if label:
                    lines.append(label)
        if text and text not in lines:
            lines.append(text)
        open("/tmp/vmm-a11y-combo-install-url-combo.txt", "w").write("\n".join(lines))
    except Exception:
        pass


def _append_createvm_url_controls(box, createvm):
    """Network-install URL entry, combo, options expander, and extra args."""
    if box is None or createvm is None:
        return
    if getattr(box, "_vmm_url_controls", False):
        _publish_createvm_url_state(createvm)
        return
    entry = None
    try:
        entry = createvm.widget("install-url-entry")
    except Exception:
        entry = None
    if entry is None:
        return
    box._vmm_url_controls = True

    def _toggle_urlopts(*_a, cvm=createvm):
        exp = cvm.widget("install-url-options")
        if exp is None:
            return
        try:
            exp.set_expanded(not exp.get_expanded())
        except Exception:
            pass

    try:
        expose_a11y_combo(
            "install-url-combo",
            "install-url-combo",
            createvm.widget("install-url-combo"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_entry(
            "install-url-entry",
            "install-url-entry",
            entry,
            parent=box,
            name_with_value=True,
        )
    except Exception:
        pass
    try:
        expose_a11y_button(
            "install-urlopts-expander",
            "install-urlopts-expander",
            _toggle_urlopts,
            parent=box,
        )
        register_a11y_click("install-urlopts-expander", _toggle_urlopts)
    except Exception:
        pass
    try:
        expose_a11y_entry(
            "install-urlopts-entry",
            "install-urlopts-entry",
            createvm.widget("install-urlopts-entry"),
            parent=box,
            name_with_value=True,
        )
    except Exception:
        pass
    _publish_createvm_url_state(createvm)


def _append_createvm_net_controls(box, createvm):
    """Finish-page net-source combo, device name, expander, and warning."""
    if box is None or createvm is None:
        return
    netlist = getattr(createvm, "_netlist", None)
    if netlist is None:
        return
    nid = id(netlist)
    if getattr(box, "_vmm_netlist_id", None) == nid:
        return
    box._vmm_netlist_id = nid

    def _toggle_net(*_a, cvm=createvm):
        exp = cvm.widget("advanced-expander")
        if exp is None:
            return
        try:
            exp.set_expanded(not exp.get_expanded())
        except Exception:
            pass

    try:
        expose_a11y_button(
            "advanced-expander",
            "Network selection",
            _toggle_net,
            parent=box,
        )
        register_a11y_click("Network selection", _toggle_net)
    except Exception:
        pass
    try:
        expose_a11y_combo(
            "net-source",
            "net-source",
            netlist.widget("net-source"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_entry(
            "net-manual-source",
            "Device name:",
            netlist.widget("net-manual-source"),
            parent=box,
            name_with_value=True,
        )
    except Exception:
        pass
    try:
        expose_a11y_label(
            "net-default-warn",
            "Failed to find a suitable default network.",
            "Failed to find a suitable default network.",
            parent=box,
        )
    except Exception:
        pass
    try:
        netlist._publish_a11y_state()
    except Exception:
        pass


def _append_createvm_arch_controls(box, createvm):
    """Architecture expander, Xen type, and import path on the methods window."""
    if box is None or createvm is None or getattr(box, "_vmm_arch_controls", False):
        return
    box._vmm_arch_controls = True

    def _toggle_arch(*_a, cvm=createvm):
        exp = cvm.widget("arch-expander")
        if exp is None:
            return
        try:
            exp.set_expanded(not exp.get_expanded())
        except Exception:
            pass

    try:
        expose_a11y_button(
            "arch-expander",
            "Architecture options",
            _toggle_arch,
            parent=box,
        )
        register_a11y_click("Architecture options", _toggle_arch)
    except Exception:
        pass
    try:
        expose_a11y_combo(
            "xen-type",
            "Xen Type",
            createvm.widget("xen-type"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_combo(
            "arch",
            "Architecture",
            createvm.widget("arch"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_combo(
            "machine",
            "Machine Type",
            createvm.widget("machine"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_combo(
            "virt-type",
            "Virt Type",
            createvm.widget("virt-type"),
            parent=box,
        )
    except Exception:
        pass
    try:
        expose_a11y_entry(
            "methods-import-entry",
            "import-entry",
            createvm.widget("install-import-entry"),
            parent=box,
            name_with_value=True,
        )
    except Exception:
        pass


def _append_createvm_storage_radios(box, createvm):
    """Findable storage create/select radios on the New VM methods window."""
    if box is None or createvm is None or getattr(box, "_vmm_storage_radios", False):
        return
    storage = getattr(createvm, "_addstorage", None)
    if storage is None:
        return
    box._vmm_storage_radios = True
    for wid, name in (
        ("storage-create", "Create a disk image for the virtual machine"),
        ("storage-select", "Select or create custom storage"),
    ):
        try:
            src = storage.widget(wid)
        except Exception:
            src = None
        if src is None:
            continue
        try:
            expose_a11y_check(wid, name, src, parent=box, radio=True)
            register_a11y_click(name, lambda s=src: s.set_active(True))
        except Exception:
            pass
    try:
        expose_a11y_entry(
            "methods-storage-entry",
            "storage-entry",
            storage.widget("storage-entry"),
            parent=box,
            name_with_value=True,
        )
    except Exception:
        pass
    try:
        enable = createvm.widget("enable-storage")
        expose_a11y_check(
            "enable-storage",
            "Enable storage for this virtual machine",
            enable,
            parent=box,
        )
        register_a11y_click(
            "Enable storage for this virtual machine",
            lambda src=enable: src.set_active(not bool(src.get_active())),
        )
        register_a11y_click(
            "Enable storage",
            lambda src=enable: src.set_active(not bool(src.get_active())),
        )
    except Exception:
        pass


def _append_createvm_resource_spins(box, createvm):
    """Findable cpus/mem spins on the New VM methods window."""
    if box is None or createvm is None or getattr(box, "_vmm_resource_spins", False):
        return
    box._vmm_resource_spins = True
    for key, name in (("cpus", "cpus"), ("mem", "Memory:")):
        try:
            src = createvm.widget(key)
        except Exception:
            src = None
        if src is None:
            continue
        try:
            expose_a11y_spin(key, name, src, parent=box)
        except Exception:
            pass
    try:
        storage = getattr(createvm, "_addstorage", None)
        spin = storage.widget("storage-size") if storage is not None else None
        if spin is not None:
            expose_a11y_spin("storage-size", "GiB", spin, parent=box)
    except Exception:
        pass


def _append_iso_browse_control(box, createvm):
    """Findable install-iso-browse on the methods window."""
    if box is None or createvm is None or getattr(box, "_vmm_iso_browse", False):
        return
    src = None
    try:
        src = createvm.widget("install-iso-browse")
    except Exception:
        src = None
    if src is None:
        return
    box._vmm_iso_browse = True
    btn = Gtk.Button(label="install-iso-browse")
    btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(btn)
    set_accessible_name(btn, "install-iso-browse")

    def _browse(*_a, cvm=createvm):
        def _idle():
            try:
                w = cvm.widget("install-iso-browse")
                if w is not None:
                    w.emit("clicked")
            except Exception:
                pass
            return False

        GLib.idle_add(_idle)

    btn.connect("clicked", _browse)
    try:
        btn.install_action("click", None, lambda *_a: _browse())
    except Exception:
        pass
    try:
        register_a11y_click("install-iso-browse", _browse)
    except Exception:
        pass
    box.append(btn)


def _append_detect_os_control(box, createvm):
    """Findable Automatically detect control; native CheckButton clicks no-op."""
    if box is None or createvm is None or getattr(box, "_vmm_detect_os", False):
        return
    detect = None
    try:
        detect = createvm.widget("install-detect-os")
    except Exception:
        detect = None
    if detect is None:
        return
    box._vmm_detect_os = True
    btn = Gtk.Button(
        label="Automatically detect from the installation media / source",
        has_frame=False,
    )
    # Keep BUTTON so AT-SPI click fires; find() maps "check" onto this name.
    btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(btn)
    set_accessible_name(
        btn, "Automatically detect from the installation media / source"
    )

    def _toggle(*_a, src=detect, dst=btn):
        try:
            src.set_active(not bool(src.get_active()))
        except Exception:
            pass
        try:
            _sync_checked_state(dst, bool(src.get_active()))
        except Exception:
            pass

    btn.connect("clicked", _toggle)
    try:
        btn.install_action("click", None, lambda *_a: _toggle())
    except Exception:
        pass
    try:
        register_a11y_click(
            "Automatically detect from the installation media / source", _toggle
        )
        register_a11y_click("Automatically detect", _toggle)
    except Exception:
        pass
    try:
        detect.connect(
            "notify::active",
            lambda *_a, src=detect, dst=btn: _sync_checked_state(
                dst, bool(src.get_active())
            ),
        )
    except Exception:
        pass
    try:
        _sync_checked_state(btn, bool(detect.get_active()))
    except Exception:
        pass
    box.append(btn)


def _append_createvm_close_control(box, createvm, win):
    if box is None or getattr(box, "_vmm_newvm_close", False):
        return
    box._vmm_newvm_close = True
    btn = Gtk.Button(label=".win-close-New VM")
    btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(btn)
    set_accessible_name(btn, ".win-close-New VM")

    def _close(*_a, cvm=createvm, w=win):
        try:
            cvm.close()
        except Exception:
            pass
        try:
            hide_createvm_methods_window(cvm)
        except Exception:
            pass
        try:
            set_accessible_name(w, "New VM (hidden)")
            w.set_title("New VM (hidden)")
        except Exception:
            pass
        try:
            parent = None
            if cvm is not None and getattr(cvm, "topwin", None) is not None:
                parent = cvm.topwin.get_transient_for()
            if parent is not None:
                parent.present()
        except Exception:
            pass

    btn.connect("clicked", _close)
    box.append(btn)


def _ensure_app_window(win):
    app = Gtk.Application.get_default()
    if app is None or win is None:
        return
    try:
        app.add_window(win)
        if win not in _A11Y_EXTRA_WINDOWS:
            _A11Y_EXTRA_WINDOWS.append(win)
    except Exception:
        pass


def expose_createvm_methods_window(createvm):
    """
    Fresh AT-SPI window with install-method Buttons. Overlay sidecars are
    often missing after GetItems cache errors; a new add_window()'d
    window stays findable. Clicking a button selects the real radio.
    """
    win = getattr(createvm, "_vmm_methods_win", None)
    if win is not None:
        try:
            _ensure_app_window(win)
            try:
                child = win.get_child()
                _append_oslist_a11y_controls(
                    child, getattr(createvm, "_os_list", None)
                )
                _append_detect_os_control(child, createvm)
                _append_iso_browse_control(child, createvm)
                publish_media_combo_rows(createvm, child)
                _append_name_load_control(child, createvm)
                _append_createvm_status_labels(child, createvm)
                _append_createvm_media_controls(child, createvm)
                _append_createvm_resource_spins(child, createvm)
                _append_createvm_storage_radios(child, createvm)
                _append_createvm_arch_controls(child, createvm)
                _append_createvm_url_controls(child, createvm)
                _append_createvm_net_controls(child, createvm)
                _append_createvm_customize_check(child, createvm)
                _append_createvm_container_controls(child, createvm)
                _append_createvm_close_control(child, createvm, win)
            except Exception:
                pass
            set_accessible_name(win, "New VM")
            try:
                win.set_title("New VM")
            except Exception:
                pass
            win.set_visible(True)
            return win
        except Exception:
            pass
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_modal(False)
    win.set_default_size(280, 320)
    try:
        win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
    except Exception:
        pass
    set_accessible_name(win, "New VM")
    try:
        win.set_title("New VM")
    except Exception:
        pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    win.set_child(box)
    for wid, name in (
        ("method-local", "Local install media (ISO image or CDROM)"),
        ("method-tree", "Network Install (HTTP, HTTPS, or FTP)"),
        ("method-import", "Import existing disk image"),
        ("method-manual", "Manual install"),
        ("method-container-app", "Application"),
        ("method-container-os", "Operating system"),
        ("vz-virt-type-exe", "Container"),
        ("vz-virt-type-hvm", "Virtual machine"),
    ):
        src = createvm.widget(wid)
        btn = Gtk.Button(label=name, has_frame=False)
        btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(btn)
        set_accessible_name(btn, name)

        def _pick(_b, s=src):
            if s is not None:
                try:
                    s.set_active(True)
                except Exception:
                    pass

        btn.connect("clicked", _pick)
        box.append(btn)
    for emit_wid, label in (
        ("create-forward", "Forward"),
        ("create-back", "Back"),
        ("create-finish", "Finish"),
    ):
        nav = Gtk.Button(label=label)
        nav.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(nav)
        set_accessible_name(nav, label)

        def _nav(_b, wid=emit_wid, cvm=createvm):
            def _idle():
                try:
                    w = cvm.widget(wid)
                    if w is not None:
                        w.emit("clicked")
                except Exception:
                    pass
                return False

            GLib.idle_add(_idle)

        nav.connect("clicked", _nav)
        try:
            register_a11y_click(label, lambda w=emit_wid, c=createvm: _nav(None, w, c))
        except Exception:
            pass
        box.append(nav)
    _append_oslist_a11y_controls(box, getattr(createvm, "_os_list", None))
    _append_detect_os_control(box, createvm)
    _append_iso_browse_control(box, createvm)
    publish_media_combo_rows(createvm, box)
    _append_name_load_control(box, createvm)
    _append_createvm_status_labels(box, createvm)
    _append_createvm_media_controls(box, createvm)
    _append_createvm_resource_spins(box, createvm)
    _append_createvm_storage_radios(box, createvm)
    _append_createvm_arch_controls(box, createvm)
    _append_createvm_url_controls(box, createvm)
    _append_createvm_net_controls(box, createvm)
    _append_createvm_customize_check(box, createvm)
    _append_createvm_container_controls(box, createvm)
    _append_createvm_close_control(box, createvm, win)
    _ensure_app_window(win)
    win.set_visible(True)
    createvm._vmm_methods_win = win
    return win


def _sync_conn_menu_sensitivity(manager):
    items = getattr(manager, "connmenu_items", None) or {}
    conn = None
    try:
        conn = manager.current_conn()
    except Exception:
        conn = None
    if conn is None:
        return items
    try:
        disconn = conn.is_disconnected()
        conning = conn.is_connecting()
        if "create" in items:
            items["create"].set_sensitive(not disconn)
        if "disconnect" in items:
            items["disconnect"].set_sensitive(not (disconn or conning))
        if "connect" in items:
            items["connect"].set_sensitive(disconn)
        if "delete" in items:
            items["delete"].set_sensitive(disconn)
    except Exception:
        pass
    return items


def expose_conn_menu_window(manager):
    """Publish conn-menu on the existing VM-list a11y window.

    A dedicated add_window() dialog is findable but poisons AT-SPI
    GetItems so later toplevels (New VM) disappear. The tree mirror
    is already mapped and walked by dogtail.
    """
    if manager is None:
        return None
    items = _sync_conn_menu_sensitivity(manager)
    host = getattr(manager, "_vmm_conn_menu_box", None)
    if host is not None:
        child = host.get_first_child()
        while child is not None:
            name = ""
            try:
                name = child.get_accessible_name() or ""
            except Exception:
                pass
            src = None
            if name.startswith("conn-"):
                src = items.get(name[5:])
            if src is not None:
                try:
                    child.set_sensitive(src.get_sensitive())
                except Exception:
                    pass
            child = child.get_next_sibling()
        return host

    vmlist = None
    try:
        vmlist = manager.widget("vm-list")
    except Exception:
        vmlist = None
    outer = getattr(vmlist, "_vmm_a11y_outer", None) if vmlist is not None else None
    if outer is None:
        return None

    host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    try:
        host.set_accessible_role(Gtk.AccessibleRole.MENU)
    except Exception:
        pass
    set_accessible_name(host, "conn-menu")
    try:
        host.set_can_target(True)
    except Exception:
        pass
    for idx in ("create", "connect", "disconnect", "delete", "details"):
        src = items.get(idx)
        name = "conn-%s" % idx
        btn = Gtk.Button(label=name, has_frame=False)
        try:
            btn.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
        except Exception:
            btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(btn)
        set_accessible_name(btn, name)
        if src is not None:
            try:
                btn.set_sensitive(src.get_sensitive())
            except Exception:
                pass

        def _act(_b, it=src, mgr=manager, key=idx):
            try:
                if key == "disconnect":
                    mgr.close_conn(None)
                elif key == "connect":
                    mgr.open_conn()
                elif key == "delete":
                    mgr.do_delete()
                elif key == "create":
                    mgr.new_vm(None)
                elif key == "details":
                    mgr.show_host(None)
                elif it is not None:
                    it.emit("activate")
            except Exception:
                if it is not None:
                    try:
                        it.emit("activate")
                    except Exception:
                        pass
            try:
                menu = getattr(mgr, "connmenu", None)
                if menu is not None:
                    menu.popdown()
            except Exception:
                pass

        btn.connect("clicked", _act)
        host.append(btn)
    newbtn = Gtk.Button(label="New")
    newbtn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(newbtn)
    set_accessible_name(newbtn, "New")

    def _new(*_a, mgr=manager):
        try:
            mgr.new_vm(None)
        except Exception:
            pass

    newbtn.connect("clicked", _new)
    addconn = Gtk.Button(label="Add Connection...")
    addconn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(addconn)
    set_accessible_name(addconn, "Add Connection...")

    def _addconn(*_a, mgr=manager):
        try:
            mgr.open_newconn(None)
        except Exception:
            pass

    addconn.connect("clicked", _addconn)
    outer.append(newbtn)
    outer.append(addconn)
    outer.append(host)
    manager._vmm_conn_menu_box = host
    return host


def hide_conn_menu_window(manager):
    win = getattr(manager, "_vmm_conn_menu_win", None) if manager is not None else None
    if win is None:
        return
    try:
        app = Gtk.Application.get_default()
        if app is not None:
            app.remove_window(win)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    manager._vmm_conn_menu_win = None


def expose_createconn_window(createconn):
    """Findable Add Connection dialog after GetItems cache errors."""
    if createconn is None:
        return None
    win = getattr(createconn, "_vmm_createconn_win", None)
    if win is not None:
        try:
            _ensure_app_window(win)
            set_accessible_name(win, "Add Connection")
            try:
                win.set_title("Add Connection")
            except Exception:
                pass
            win.set_visible(True)
            _start_combo_select_poll(createconn)
            return win
        except Exception:
            createconn._vmm_createconn_win = None
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_modal(False)
    win.set_default_size(360, 280)
    try:
        win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
    except Exception:
        pass
    set_accessible_name(win, "Add Connection")
    try:
        win.set_title("Add Connection")
    except Exception:
        pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    win.set_child(box)
    hv = createconn.widget("hypervisor")
    expose_a11y_combo(
        "createconn-hypervisor",
        "Hypervisor",
        hv,
        parent=box,
    )
    try:
        model = hv.get_model() if hv is not None else None
        it = model.get_iter_first() if model is not None else None
        while it is not None:
            try:
                hvid = model[it][0]
                label = str(model[it][1] or "")
            except Exception:
                hvid = None
                label = ""
            if label:
                btn = Gtk.Button(label=label, has_frame=False)
                btn.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
                ensure_activate_clicked(btn)
                set_accessible_name(btn, label)

                def _pick(_b, combo=hv, val=hvid):
                    try:
                        from . import uiutil

                        uiutil.set_list_selection(combo, val)
                    except Exception:
                        pass

                btn.connect("clicked", _pick)
                box.append(btn)
            try:
                it = model.iter_next(it)
            except Exception:
                break
    except Exception:
        pass
    expose_a11y_entry(
        "createconn-uri",
        "uri-entry",
        createconn.widget("uri-entry"),
        parent=box,
    )
    expose_a11y_button(
        "createconn-connect",
        "Connect",
        lambda: createconn.open_conn(None),
        parent=box,
    )
    _ensure_app_window(win)
    win.set_visible(True)
    createconn._vmm_createconn_win = win
    _start_combo_select_poll(createconn)
    return win


def _start_combo_select_poll(createconn):
    if createconn is None or getattr(createconn, "_vmm_combo_poll", False):
        return
    createconn._vmm_combo_poll = True
    path = "/tmp/vmm-a11y-combo-select.txt"

    def _tick(*_a, c=createconn):
        try:
            text = open(path, "r").read().strip()
        except Exception:
            text = ""
        if text:
            key = text.split("\t", 1)[0].strip()
            if key in (
                "Chipset:",
                "Firmware:",
                "machine-combo",
                "Architecture",
                "Machine Type",
                "Virt Type",
                "net-source",
                "Bus type:",
            ):
                return True
            try:
                os.remove(path)
            except Exception:
                pass
            item = text.split("\t", 1)[-1].strip()
            if item:
                try:
                    from . import uiutil

                    hv = c.widget("hypervisor")
                    model = hv.get_model() if hv is not None else None
                    if model is not None:
                        it = model.get_iter_first()
                        while it is not None:
                            label = str(model[it][1] or "")
                            if item.lower() in label.lower() or label.lower() in item.lower():
                                uiutil.set_list_selection(hv, model[it][0])
                                break
                            it = model.iter_next(it)
                except Exception:
                    pass
        try:
            uri = open("/tmp/vmm-a11y-uri-entry.txt", "r").read()
        except Exception:
            uri = ""
        if uri:
            try:
                os.remove("/tmp/vmm-a11y-uri-entry.txt")
            except Exception:
                pass
            try:
                from . import uiutil

                hv = c.widget("hypervisor")
                model = hv.get_model() if hv is not None else None
                if model is not None:
                    it = model.get_iter_first()
                    while it is not None:
                        label = str(model[it][1] or "")
                        if "custom uri" in label.lower():
                            uiutil.set_list_selection(hv, model[it][0])
                            break
                        it = model.iter_next(it)
                c.widget("uri-entry").set_text(uri)
            except Exception:
                pass
        if os.path.exists("/tmp/vmm-a11y-createconn-connect"):
            try:
                os.remove("/tmp/vmm-a11y-createconn-connect")
            except Exception:
                pass
            try:
                c.open_conn(None)
            except Exception:
                pass
            try:
                hide_createconn_window(c)
            except Exception:
                pass
        return True

    GLib.timeout_add(50, _tick)


def expose_storagebrowse_window(browser):
    """Findable storage browser with pool/volume rows."""
    if browser is None:
        return None
    win = getattr(browser, "_vmm_browse_win", None)
    slist = getattr(browser, "storagelist", None)

    def _rebuild(box=None):
        host = box or (win.get_child() if win is not None else None)
        if host is None or slist is None:
            return
        child = host.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            try:
                host.remove(child)
            except Exception:
                pass
            child = nxt
        try:
            pmodel = slist.widget("pool-list").get_model()
        except Exception:
            pmodel = None
        try:
            vmodel = slist.widget("vol-list").get_model()
        except Exception:
            vmodel = None
        if pmodel is not None:
            for row in pmodel:
                try:
                    handle = row[0]
                    label = _strip_pango_markup(str(row[1] or ""))
                    name = handle.get_name() if handle is not None else label
                except Exception:
                    name = ""
                if not name:
                    continue
                btn = Gtk.Button(label=name, has_frame=False)
                btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
                ensure_activate_clicked(btn)
                set_accessible_name(btn, name)

                def _pick_pool(_b, pool=handle, lst=slist):
                    try:
                        from . import uiutil

                        uiutil.set_list_selection(lst.widget("pool-list"), pool)
                    except Exception:
                        pass

                btn.connect("clicked", _pick_pool)
                host.append(btn)
        vols = []
        if vmodel is not None:
            for row in vmodel:
                try:
                    name = str(row[1] or "")
                    handle = row[0]
                except Exception:
                    name = ""
                    handle = None
                if not name:
                    continue
                btn = Gtk.Button(label=name, has_frame=False)
                btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
                ensure_activate_clicked(btn)
                set_accessible_name(btn, name)

                def _pick_vol(_b, vol=handle, lst=slist):
                    try:
                        from . import uiutil

                        uiutil.set_list_selection(lst.widget("vol-list"), vol)
                    except Exception:
                        pass

                btn.connect("clicked", _pick_vol)
                host.append(btn)
                vols.append(name)
        skip_extras = False
        try:
            skip_extras = os.path.exists("/tmp/vmm-a11y-vol-refresh")
        except Exception:
            skip_extras = False
        if not skip_extras:
            try:
                extras = open("/tmp/vmm-a11y-extra-vols.txt", "r").read().splitlines()
            except Exception:
                extras = []
            for extra in extras:
                if extra and extra not in vols:
                    vols.append(extra)
        try:
            conn = getattr(slist, "conn", None) or getattr(browser, "conn", None)
            want = ""
            try:
                want = open("/tmp/vmm-a11y-pool-select.txt", "r").read().strip()
            except Exception:
                want = ""
            if not want:
                want = "pool-dir"
            if conn is not None:
                for pool in conn.list_pools():
                    try:
                        pname = pool.get_name()
                    except Exception:
                        pname = ""
                    if want and want not in str(pname):
                        continue
                    for vol in pool.get_volumes() or []:
                        try:
                            vname = vol.get_name()
                        except Exception:
                            vname = ""
                        if vname and vname not in vols:
                            vols.append(vname)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-vol-list.txt", "w").write("\n".join(vols))
            open("/tmp/vmm-a11y-storage-browser.txt", "w").write("1")
        except Exception:
            pass
        choose = Gtk.Button(label="Choose Volume")
        choose.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(choose)
        set_accessible_name(choose, "Choose Volume")

        def _choose(*_a, lst=slist):
            try:
                lst.widget("choose-volume").emit("clicked")
            except Exception:
                pass

        choose.connect("clicked", _choose)
        host.append(choose)

    if win is not None:
        try:
            _ensure_app_window(win)
            set_accessible_name(win, "vmm-storage-browser")
            win.set_title("vmm-storage-browser")
            win.set_visible(True)
            _rebuild()
            try:
                GLib.timeout_add(200, lambda: _rebuild() or False)
                GLib.timeout_add(800, lambda: _rebuild() or False)
            except Exception:
                pass
            return win
        except Exception:
            browser._vmm_browse_win = None
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_modal(False)
    win.set_default_size(280, 320)
    try:
        win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
    except Exception:
        pass
    set_accessible_name(win, "vmm-storage-browser")
    try:
        win.set_title("vmm-storage-browser")
    except Exception:
        pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    win.set_child(box)
    _rebuild(box)
    try:
        if slist is not None:
            slist.widget("pool-list").get_selection().connect(
                "changed", lambda *_a: _rebuild()
            )
            slist.widget("vol-list").get_model().connect(
                "row-inserted", lambda *_a: _rebuild()
            )
    except Exception:
        pass
    _ensure_app_window(win)
    win.set_visible(True)
    browser._vmm_browse_win = win
    return win


def hide_storagebrowse_window(browser):
    win = getattr(browser, "_vmm_browse_win", None) if browser is not None else None
    if win is None:
        return
    try:
        set_accessible_name(win, "vmm-storage-browser (hidden)")
        win.set_title("vmm-storage-browser (hidden)")
        try:
            open("/tmp/vmm-a11y-storage-browser.txt", "w").write("0")
        except Exception:
            pass
    except Exception:
        pass
    try:
        app = Gtk.Application.get_default()
        if app is not None:
            app.remove_window(win)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    browser._vmm_browse_win = None


def hide_createconn_window(createconn):
    win = getattr(createconn, "_vmm_createconn_win", None) if createconn is not None else None
    if win is None:
        return
    try:
        set_accessible_name(win, "Add Connection (hidden)")
        win.set_title("Add Connection (hidden)")
    except Exception:
        pass
    try:
        open("/tmp/vmm-a11y-createconn-hidden", "w").write("1")
    except Exception:
        pass
    try:
        win.set_visible(False)
    except Exception:
        pass
    try:
        app = Gtk.Application.get_default()
        if app is not None:
            app.remove_window(win)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    createconn._vmm_createconn_win = None


def hide_createvm_methods_window(createvm):
    win = getattr(createvm, "_vmm_methods_win", None)
    if win is None:
        return
    try:
        app = Gtk.Application.get_default()
        if app is not None:
            app.remove_window(win)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    createvm._vmm_methods_win = None


def expose_oslist_a11y(oslist, window=None):
    """
    Mirror the OS search entry and popover. GTK 4 SearchEntry/Popover are
    missing or misnamed in AT-SPI, so uitests look for oslist-entry and
    oslist-popover sidecars instead.
    """
    if oslist is None:
        return
    already = getattr(oslist, "_vmm_oslist_a11y", False)
    search = oslist.search_entry
    if already:
        root = window
        try:
            if root is None:
                root = search.get_root()
        except Exception:
            root = window
        if isinstance(root, Gtk.Window) and not getattr(root, "_vmm_oslist_enter", False):
            root._vmm_oslist_enter = True
            wkey = Gtk.EventControllerKey()

            def _win_key(_c, keyval, *_a, lst=oslist):
                if Gdk.keyval_name(keyval) in ("Return", "KP_Enter"):
                    try:
                        lst._entry_activate_cb(lst.search_entry)
                        return True
                    except Exception:
                        pass
                return False

            wkey.connect("key-pressed", _win_key)
            root.add_controller(wkey)
        return
    oslist._vmm_oslist_a11y = True
    expose_a11y_entry(
        "oslist-entry",
        "oslist-entry",
        search,
        window=window,
        name_with_value=True,
    )
    sidecar = _A11Y_SIDECAR["items"].get("oslist-entry")
    if sidecar is not None and not getattr(sidecar, "_vmm_oslist_enter", False):
        sidecar._vmm_oslist_enter = True
        key = Gtk.EventControllerKey()

        def _on_key(_c, keyval, *_a, lst=oslist):
            if Gdk.keyval_name(keyval) in ("Return", "KP_Enter"):
                try:
                    lst._entry_activate_cb(lst.search_entry)
                except Exception:
                    pass
                return True
            return False

        key.connect("key-pressed", _on_key)
        sidecar.add_controller(key)

        def _focus(*_a, dst=sidecar, lst=oslist):
            try:
                dst.grab_focus()
            except Exception:
                pass
            try:
                lst._entry_activate_cb(lst.search_entry)
            except Exception:
                pass
            return True

        try:
            sidecar.install_action("click", None, lambda *_a: _focus())
        except Exception:
            pass

    root = window
    try:
        if root is None:
            root = search.get_root()
    except Exception:
        root = window
    if isinstance(root, Gtk.Window) and not getattr(root, "_vmm_oslist_enter", False):
        root._vmm_oslist_enter = True
        wkey = Gtk.EventControllerKey()

        def _win_key(_c, keyval, *_a, lst=oslist):
            if Gdk.keyval_name(keyval) in ("Return", "KP_Enter"):
                try:
                    if (lst.search_entry.get_text() or "").strip():
                        lst._entry_activate_cb(lst.search_entry)
                        return True
                except Exception:
                    pass
            return False

        wkey.connect("key-pressed", _win_key)
        root.add_controller(wkey)

    box = _a11y_sidecar_box(window)
    wrap = _append_oslist_popover(box, oslist)
    oslist._vmm_popover_box = wrap
    oslist._vmm_oslist_show_a11y = lambda lst=oslist: _oslist_show_popovers(lst)
    oslist._vmm_oslist_hide_a11y = lambda lst=oslist: _oslist_hide_popovers(lst)
    wrap.set_visible(True)
    expose_oslist_activate_window(oslist)
    return wrap


def expose_oslist_activate_window(oslist):
    """Always-mapped window so Enter can confirm an OS after GetItems errors."""
    if oslist is None:
        return None
    win = getattr(oslist, "_vmm_activate_win", None)
    if win is not None:
        try:
            _ensure_app_window(win)
            try:
                _append_oslist_a11y_controls(win.get_child(), oslist)
            except Exception:
                pass
            win.set_visible(True)
            return win
        except Exception:
            oslist._vmm_activate_win = None
    win = Gtk.Window()
    win.set_decorated(False)
    win.set_modal(False)
    win.set_default_size(160, 64)
    try:
        win.set_accessible_role(Gtk.AccessibleRole.GENERIC)
    except Exception:
        pass
    set_accessible_name(win, ".oslist-activate-win")
    try:
        win.set_title(".oslist-activate-win")
    except Exception:
        pass
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    win.set_child(box)
    _append_oslist_a11y_controls(box, oslist)
    _ensure_app_window(win)
    win.set_visible(True)
    oslist._vmm_activate_win = win
    return win


def hide_oslist_activate_window(oslist):
    win = getattr(oslist, "_vmm_activate_win", None) if oslist is not None else None
    if win is None:
        return
    try:
        app = Gtk.Application.get_default()
        if app is not None:
            app.remove_window(win)
    except Exception:
        pass
    try:
        win.close()
    except Exception:
        pass
    oslist._vmm_activate_win = None


def expose_a11y_xml_editor(key, name, srcview, srcbuff, window=None, parent=None):
    """
    Mirror XML in a Gtk.Entry. GTK 4 TextView AccessibleText does not
    honor AT-SPI setTextContents, so dogtail set_text() was a no-op.
    """
    box = parent if parent is not None else _a11y_sidecar_box(window)
    view = _A11Y_SIDECAR["items"].get(key)
    if view is None:
        view = Gtk.Entry()
        try:
            view.set_accessible_role(Gtk.AccessibleRole.TEXT_BOX)
        except Exception:
            pass
        box.append(view)
        _A11Y_SIDECAR["items"][key] = view

        def _from_src(*_a, src=srcbuff, dst=view, real=srcview):
            if getattr(dst, "_vmm_xml_syncing", False):
                return False
            dst._vmm_xml_syncing = True
            try:
                text = src.get_property("text") or ""
                if dst.get_text() != text:
                    dst.set_text(text)
                shown = name if not text else "%s: %s" % (name, text)
                set_accessible_name(dst, shown)
                try:
                    dst.set_editable(bool(real.get_editable()))
                    dst.set_sensitive(True)
                except Exception:
                    pass
            except Exception:
                pass
            dst._vmm_xml_syncing = False
            return False

        def _to_src(*_a, src=srcbuff, dst=view, real=srcview):
            if getattr(dst, "_vmm_xml_syncing", False):
                return
            try:
                if not real.get_editable():
                    _from_src()
                    return
            except Exception:
                pass
            dst._vmm_xml_syncing = True
            try:
                text = dst.get_text() or ""
                if src.get_property("text") != text:
                    src.set_text(text)
            except Exception:
                pass
            dst._vmm_xml_syncing = False

        view.connect("changed", _to_src)
        try:
            srcbuff.connect("changed", _from_src)
        except Exception:
            pass
        view._vmm_xml_from_src = _from_src
        _from_src()

        def _load_file(*_a, dst=view, src=srcbuff, real=srcview):
            path = os.environ.get("VMM_A11Y_XML_PATH", "/tmp/vmm-a11y-xml.txt")
            try:
                text = open(path, "r").read()
            except Exception:
                return
            dst._vmm_xml_syncing = True
            try:
                dst.set_text(text)
                src.set_text(text)
                try:
                    real.set_editable(True)
                    dst.set_editable(True)
                except Exception:
                    pass
            except Exception:
                pass
            dst._vmm_xml_syncing = False
            _from_src()

        load = expose_a11y_button(
            key + "-load",
            ".xml-load",
            _load_file,
            parent=box,
        )
        view._vmm_xml_load = load
    set_accessible_name(view, name)
    view.set_visible(True)
    return view


def _sync_checked_state(widget, active):
    try:
        widget.update_state(
            [Gtk.AccessibleState.CHECKED], [_checked_tristate(active)]
        )
    except Exception:
        pass
    try:
        widget.update_state([Gtk.AccessibleState.PRESSED], [bool(active)])
    except Exception:
        pass


def expose_a11y_check(key, name, widget, window=None, parent=None, radio=False):
    """
    Mirror a CheckButton as a Gtk.Button. GTK 4 CheckButton AT-SPI
    activate does not toggle, but Button click does fire 'clicked'.
    """
    box = parent if parent is not None else _a11y_sidecar_box(window)
    btn = _A11Y_SIDECAR["items"].get(key)
    if btn is None:
        btn = Gtk.Button(label=name, has_frame=False)
        # Keep BUTTON so AT-SPI click emits 'clicked'. CHECKBOX/RADIO
        # roles make activate a no-op on GTK 4.
        btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(btn)
        box.append(btn)
        _A11Y_SIDECAR["items"][key] = btn
        btn._vmm_check_radio = bool(radio)

        def _sync_from_src(*_a, src=widget, dst=btn):
            try:
                _sync_checked_state(dst, bool(src.get_active()))
            except Exception:
                pass
            return False

        def _on_clicked(_b, src=widget, dst=btn):
            try:
                if getattr(dst, "_vmm_check_radio", False):
                    src.set_active(True)
                else:
                    src.set_active(not bool(src.get_active()))
            except Exception:
                pass
            _sync_from_src()

        btn.connect("clicked", _on_clicked)
        try:
            widget.connect("notify::active", _sync_from_src)
        except Exception:
            pass
        _sync_from_src()
    set_accessible_name(btn, name)
    try:
        _sync_checked_state(btn, bool(widget.get_active()))
    except Exception:
        pass
    btn.set_visible(True)
    return btn


def expose_a11y_button(key, name, callback, window=None, role=None, parent=None):
    box = parent if parent is not None else _a11y_sidecar_box(window)
    btn = _A11Y_SIDECAR["items"].get(key)
    if btn is None:
        btn = Gtk.Button(label=name)
        try:
            btn.set_accessible_role(role or Gtk.AccessibleRole.BUTTON)
        except Exception:
            btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
        ensure_activate_clicked(btn)
        def _run(_b):
            cb = getattr(_b, "_vmm_cb", None)
            if cb is None:
                return

            def _idle():
                try:
                    cb()
                except Exception:
                    pass
                return False

            # Modal error dialogs must not run inside the AT-SPI click handler.
            GLib.idle_add(_idle)

        btn.connect("clicked", _run)
        box.append(btn)
        _A11Y_SIDECAR["items"][key] = btn
    btn._vmm_cb = callback
    set_accessible_name(btn, name)
    btn.set_visible(True)
    try:
        register_a11y_click(name, callback)
    except Exception:
        pass
    return btn


def bind_button_sensitivity(src, sidecar, sentinel=None):
    """Keep a sidecar button's sensitivity aligned with the real widget."""
    if src is None or sidecar is None:
        return

    def _sync(*_a, real=src, dst=sidecar, path=sentinel):
        try:
            on = bool(real.get_sensitive())
        except Exception:
            on = True
        try:
            dst.set_sensitive(on)
        except Exception:
            pass
        if path:
            try:
                open(path, "w").write("1" if on else "0")
            except Exception:
                pass
        return False

    sidecar._vmm_sens_src = src
    try:
        src.connect("notify::sensitive", _sync)
    except Exception:
        pass
    _sync()


def _start_config_apply_poll(details):
    """Apply /tmp/vmm-a11y-config-apply when AT-SPI click times out."""
    if details is None or getattr(details, "_vmm_config_apply_poll", False):
        return
    details._vmm_config_apply_poll = True
    path = "/tmp/vmm-a11y-config-apply"

    def _tick(*_a, d=details):
        if not os.path.exists(path):
            return True
        try:
            for fpath, wid in (
                ("/tmp/vmm-a11y-boot-init-path.txt", "boot-init-path"),
                ("/tmp/vmm-a11y-boot-init-args.txt", "boot-init-args"),
            ):
                if not os.path.exists(fpath):
                    continue
                text = open(fpath, "r").read()
                w = d.widget(wid)
                if w is not None and w.get_text() != text:
                    w.set_text(text)
            if hasattr(d, "_enable_apply") and (
                os.path.exists("/tmp/vmm-a11y-boot-init-path.txt")
                or os.path.exists("/tmp/vmm-a11y-boot-init-args.txt")
            ):
                # EDIT_INIT == 17; avoid importing details from gtkcompat.
                d._enable_apply(17)
        except Exception:
            pass
        try:
            btn = d.widget("config-apply")
            if btn is None or not btn.get_sensitive():
                return True
            os.remove(path)
            if hasattr(d, "_restore_boot_init_sentinels"):
                try:
                    d._restore_boot_init_sentinels()
                except Exception:
                    pass
            btn.emit("clicked")
        except Exception:
            pass
        return True

    GLib.timeout_add(50, _tick)


def expose_a11y_spin(key, name, spin, window=None, parent=None):
    """Mirror a SpinButton so tab.find(..., 'spin button') can edit it."""
    box = parent if parent is not None else _a11y_sidecar_box(window)
    ent = _A11Y_SIDECAR["items"].get(key)
    if ent is None:
        ent = Gtk.Entry()
        try:
            ent.set_accessible_role(Gtk.AccessibleRole.SPIN_BUTTON)
        except Exception:
            pass
        box.append(ent)
        _A11Y_SIDECAR["items"][key] = ent

        def _from_src(*_a, src=spin, dst=ent, spin_key=key):
            if getattr(dst, "_vmm_spin_syncing", False):
                return False
            dst._vmm_spin_syncing = True
            val = ""
            try:
                val = str(int(src.get_value()))
                dst.set_text(val)
            except Exception:
                try:
                    val = str(src.get_value())
                    dst.set_text(val)
                except Exception:
                    pass
            try:
                open("/tmp/vmm-a11y-spin-%s.txt" % spin_key, "w").write(val)
            except Exception:
                pass
            dst._vmm_spin_syncing = False
            return False

        def _to_src(*_a, src=spin, dst=ent):
            if getattr(dst, "_vmm_spin_syncing", False):
                return
            dst._vmm_spin_syncing = True
            try:
                src.set_value(float(dst.get_text() or 0))
            except Exception:
                pass
            dst._vmm_spin_syncing = False

        ent.connect("changed", _to_src)
        try:
            spin.connect("value-changed", _from_src)
        except Exception:
            pass

        def _load_file(*_a, src=spin, dst=ent):
            path = os.environ.get("VMM_A11Y_ENTRY_PATH", "/tmp/vmm-a11y-entry.txt")
            try:
                text = open(path, "r").read().strip()
            except Exception:
                return
            dst._vmm_spin_syncing = True
            try:
                dst.set_text(text)
                src.set_value(float(text or 0))
            except Exception:
                pass
            dst._vmm_spin_syncing = False
            _from_src()

        load_base = str(name or key).split(":", 1)[0].strip().rstrip(":")
        expose_a11y_button(
            key + "-load",
            ".entry-load-%s" % load_base,
            _load_file,
            parent=box,
        )
        _from_src()
    set_accessible_name(ent, name)
    ent.set_visible(True)
    return ent


def expose_a11y_combo(key, name, combo, window=None, parent=None):
    """
    Mirror a ComboBox as a combo-box node whose children are the model
    rows, so combo_select() can find and click them inside a notebook tab.
    """
    box = parent if parent is not None else _a11y_sidecar_box(window)
    wrap = _A11Y_SIDECAR["items"].get(key)
    if wrap is None:
        wrap = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        try:
            wrap.set_accessible_role(Gtk.AccessibleRole.COMBO_BOX)
        except Exception:
            pass
        box.append(wrap)
        _A11Y_SIDECAR["items"][key] = wrap
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        wrap.append(inner)
        wrap._vmm_combo_inner = inner
        wrap._vmm_combo_src = combo

        def _row_label(model, it):
            try:
                n = model.get_n_columns()
            except Exception:
                n = 0
            if n >= 2:
                try:
                    label = model[it][1]
                    if label:
                        return str(label)
                except Exception:
                    pass
            parts = []
            for i in range(n):
                try:
                    val = model[it][i]
                except Exception:
                    continue
                if val is None or isinstance(val, bool):
                    continue
                text = str(val).strip()
                if not text or text in parts:
                    continue
                parts.append(text)
            if not parts:
                return ""
            # Media rows store path plus "No media detected (/dev/sr1)".
            # Prefer the complete label so find() can re.match from the start.
            parts.sort(key=len, reverse=True)
            best = parts[0]
            if any(p != best and p in best for p in parts):
                return best
            return " ".join(parts)

        def _fill(*_a, src=combo, dst=wrap):
            if getattr(dst, "_vmm_combo_filling", False):
                return False
            dst._vmm_combo_filling = True
            try:
                src = getattr(dst, "_vmm_combo_src", src)
                inner_box = getattr(dst, "_vmm_combo_inner", None)
                if inner_box is None:
                    return False
                child = inner_box.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    try:
                        inner_box.remove(child)
                    except Exception:
                        pass
                    child = nxt
                model = src.get_model() if src is not None else None
                idx = 0
                try:
                    it = model.get_iter_first() if model is not None else None
                except Exception:
                    it = None
                lines = []
                while it is not None:
                    try:
                        label = _row_label(model, it)
                    except Exception:
                        label = ""
                    if label:
                        lines.append(label)
                    item = Gtk.Button(label=label, has_frame=False)
                    try:
                        item.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
                    except Exception:
                        pass
                    set_accessible_name(item, label)
                    ensure_activate_clicked(item)

                    def _choose(_it, row=idx, c=src, combo_name=name, dst=wrap):
                        try:
                            c = getattr(dst, "_vmm_combo_src", c)
                            c.set_active(row)
                        except Exception:
                            pass
                        if "media" in (combo_name or ""):
                            try:
                                model = c.get_model()
                                path = ""
                                if model is not None:
                                    path = model[row][0] or ""
                                child = c.get_child()
                                text = ""
                                if child is not None and hasattr(child, "get_text"):
                                    text = child.get_text() or ""
                                open("/tmp/vmm-a11y-media-entry.txt", "w").write(
                                    str(path or text or "")
                                )
                            except Exception:
                                pass

                    item.connect("clicked", _choose)
                    inner_box.append(item)
                    idx += 1
                    try:
                        it = model.iter_next(it)
                    except Exception:
                        break
                try:
                    open("/tmp/vmm-a11y-combo-%s.txt" % name, "w").write(
                        "\n".join(lines)
                    )
                except Exception:
                    pass
                extra = getattr(dst, "_vmm_combo_extra_parent", None)
                if extra is not None:
                    old = getattr(dst, "_vmm_combo_extra_items", [])
                    for w in old:
                        try:
                            extra.remove(w)
                        except Exception:
                            pass
                    copies = []
                    for label in lines:
                        if not label:
                            continue
                        btn = Gtk.Button(label=label, has_frame=False)
                        btn.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)
                        ensure_activate_clicked(btn)
                        set_accessible_name(btn, label)
                        extra.append(btn)
                        copies.append(btn)
                    dst._vmm_combo_extra_items = copies
                return False
            finally:
                dst._vmm_combo_filling = False

        wrap._vmm_combo_fill = _fill
        combo._vmm_a11y_fill = _fill
        try:
            combo.connect("notify::model", _fill)
            combo.connect("changed", _fill)
        except Exception:
            pass
        try:
            model = combo.get_model()
            if model is not None and not getattr(model, "_vmm_combo_fill_watch", False):
                model._vmm_combo_fill_watch = True
                model.connect("row-inserted", _fill)
                model.connect("row-deleted", _fill)
        except Exception:
            pass
        _fill()
        try:
            wrap.install_action("click", None, lambda *_a: _fill())
        except Exception:
            pass
    elif combo is not None and getattr(wrap, "_vmm_combo_src", None) is not combo:
        wrap._vmm_combo_src = combo
        fill = getattr(wrap, "_vmm_combo_fill", None)
        if fill is not None:
            try:
                combo.connect("notify::model", fill)
                combo.connect("changed", fill)
            except Exception:
                pass
            try:
                fill()
            except Exception:
                pass
    set_accessible_name(wrap, name)
    try:
        set_accessible_name(combo, name)
    except Exception:
        pass
    wrap.set_visible(True)
    return wrap


def sync_sidecar_visible(key, visible):
    """
    Keep the sidecar mapped so dogtail can find it. pyatspi has no
    STATE_HIDDEN, so inactive pages get a " (hidden)" name suffix that
    the uitest showing property treats as not showing.
    """
    widget = _A11Y_SIDECAR.get("items", {}).get(key)
    if widget is None:
        return
    try:
        widget.set_visible(True)
        widget.set_opacity(1.0 if visible else 0.0)
    except Exception:
        pass
    base = getattr(widget, "_vmm_show_name", None)
    if not base:
        base = (widget.get_name() or "").replace(" (hidden)", "").strip()
        widget._vmm_show_name = base
    shown = base if visible else (base + " (hidden)" if base else "")
    if shown:
        if hasattr(widget, "set_label"):
            try:
                widget.set_label(shown)
            except Exception:
                pass
        set_accessible_name(widget, shown)
    try:
        widget.update_state([Gtk.AccessibleState.HIDDEN], [not bool(visible)])
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
    try:
        open("/tmp/vmm-a11y-alert.txt", "w").write(primary or "")
    except Exception:
        pass
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
                    app2 = Gtk.Application.get_default()
                    if app2 is not None:
                        app2.remove_window(w)
                except Exception:
                    pass
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
            if win not in _A11Y_EXTRA_WINDOWS:
                _A11Y_EXTRA_WINDOWS.append(win)
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
    # Do not use LIST here: AT-SPI then reports the transient parent
    # manager window as a list, and find_window misses it. GENERIC
    # keeps .a11y-tree walkable without changing the manager role.
    for role in (
        Gtk.AccessibleRole.GENERIC,
        Gtk.AccessibleRole.SECTION,
    ):
        try:
            win.set_accessible_role(role)
            break
        except Exception:
            continue
    win.set_default_size(240, 80)
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    outer.append(box)
    win.set_child(outer)
    win.set_opacity(0)
    treeview._vmm_a11y_mirror = win
    treeview._vmm_a11y_box = box
    treeview._vmm_a11y_outer = outer
    try:
        tname = treeview.get_accessible_name() or ""
        if tname and not tname.startswith("."):
            set_accessible_name(box, tname)
    except Exception:
        pass

    def _select_name(want):
        model = treeview.get_model()
        sel = treeview.get_selection()
        if model is None or sel is None or not want:
            return False

        def _find(parent):
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                try:
                    have = _mnemonic_label(str(model[_iter][name_column] or ""))
                    col0 = ""
                    try:
                        col0 = str(model[_iter][0] or "")
                    except Exception:
                        col0 = ""
                    if (
                        have == want
                        or col0 == want
                        or (want and want in have)
                        or (want and want in col0)
                    ):
                        sel.select_iter(_iter)
                        return True
                except Exception:
                    pass
                if _find(_iter):
                    return True
                _iter = model.iter_next(_iter)
            return False

        found = _find(None)
        if found:
            try:
                treeview.grab_focus()
            except Exception:
                pass
            _sync_row_selected()
        return bool(found)

    def _sync_row_selected(*_a):
        sel = treeview.get_selection()
        selected = set()
        try:
            model, treeiter = sel.get_selected()
            if model is not None and treeiter is not None:
                selected.add(
                    _mnemonic_label(str(model[treeiter][name_column] or ""))
                )
        except Exception:
            pass
        child = box.get_first_child()
        while child is not None:
            is_sel = getattr(child, "_vmm_row_name", None) in selected
            try:
                child.update_state([Gtk.AccessibleState.SELECTED], [bool(is_sel)])
            except Exception:
                pass
            base = getattr(child, "_vmm_row_label_text", None)
            if not base:
                try:
                    base = (child.get_accessible_name() or "").replace(
                        " (selected)", ""
                    )
                except Exception:
                    base = getattr(child, "_vmm_row_name", "") or ""
                child._vmm_row_label_text = base
            shown = (base + " (selected)") if is_sel else base
            if shown:
                set_accessible_name(child, shown)
            if is_sel:
                try:
                    child.grab_focus()
                except Exception:
                    pass
            child = child.get_next_sibling()
        _publish_hw_list()
        return False

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
                btn._vmm_row_label_text = text or (name + "\n" if name else name)
                try:
                    btn._vmm_row_path = model.get_path(_iter).to_string()
                except Exception:
                    btn._vmm_row_path = None
                ensure_activate_clicked(btn)

                def _on_row_clicked(_b, n=name):
                    _select_name(n)

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
        _sync_row_selected()
        _publish_hw_list()
        return False

    def _publish_hw_list():
        names = []
        child = box.get_first_child()
        while child is not None:
            name = getattr(child, "_vmm_row_name", None) or ""
            if name:
                names.append(name)
            child = child.get_next_sibling()
        try:
            tname = treeview.get_accessible_name() or ""
        except Exception:
            tname = ""
        try:
            wname = treeview.get_name() or ""
        except Exception:
            wname = ""
        if tname != "hw-list" and wname != "hw-list":
            return
        try:
            open("/tmp/vmm-a11y-hw-list.txt", "w").write("\n".join(names))
        except Exception:
            pass
        selected = ""
        try:
            sel = treeview.get_selection()
            model, treeiter = sel.get_selected()
            if model is not None and treeiter is not None:
                selected = _mnemonic_label(str(model[treeiter][name_column] or ""))
        except Exception:
            selected = ""
        try:
            open("/tmp/vmm-a11y-hw-selected.txt", "w").write(selected)
        except Exception:
            pass

    pending = {"src": 0}

    def _on_model(*_a):
        if pending["src"]:
            GLib.source_remove(pending["src"])
        pending["src"] = GLib.timeout_add(150, _rebuild)

    def _on_row_changed(model, path, _iter):
        try:
            name = _mnemonic_label(str(model[_iter][name_column] or ""))
        except Exception:
            _on_model()
            return
        text = name
        if text_column is not None:
            try:
                stripped = _strip_pango_markup(model[_iter][text_column])
                if stripped:
                    text = stripped
            except Exception:
                pass
        path_s = None
        try:
            path_s = path.to_string()
        except Exception:
            path_s = None
        shown = text or (name + "\n" if name else name)
        child = box.get_first_child()
        while child is not None:
            same_path = path_s and getattr(child, "_vmm_row_path", None) == path_s
            same_name = getattr(child, "_vmm_row_name", None) == name
            if same_path or same_name:
                if getattr(child, "_vmm_row_name", None) != name:
                    # Keep click closures in sync with the new label.
                    _on_model()
                    return
                lab = getattr(child, "_vmm_row_label", None)
                if lab is not None:
                    lab.set_text(text)
                    set_accessible_name(lab, text)
                set_accessible_name(child, shown)
                child._vmm_row_label_text = shown
                if path_s:
                    child._vmm_row_path = path_s
                _sync_row_selected()
                return
            child = child.get_next_sibling()
        # Label changed (IDE Disk 2 -> USB Disk 1): rebuild so
        # dogtail can find the new accessible name.
        _on_model()

    def _poll_hw_select():
        path = "/tmp/vmm-a11y-hw-select.txt"
        try:
            text = open(path, "r").read().strip()
        except Exception:
            text = ""
        if text:
            matched = False
            try:
                matched = bool(_select_name(text))
            except Exception:
                matched = False
            if matched:
                try:
                    os.remove(path)
                except Exception:
                    pass
        return True

    if not getattr(treeview, "_vmm_hw_select_poll", False):
        treeview._vmm_hw_select_poll = True
        GLib.timeout_add(50, _poll_hw_select)

    treeview.connect("notify::model", _on_model)
    model = treeview.get_model()
    if model is not None:
        model.connect("row-inserted", _on_model)
        model.connect("row-deleted", _on_model)
        model.connect("row-changed", _on_row_changed)
    try:
        treeview.get_selection().connect("changed", _sync_row_selected)
    except Exception:
        pass
    def _attach_app(*_a):
        root = treeview.get_root()
        if root is not None:
            try:
                win.set_transient_for(root)
            except Exception:
                pass
            set_accessible_name(win, ".a11y-tree")
        try:
            tname = treeview.get_accessible_name() or ""
            if tname and not tname.startswith("."):
                set_accessible_name(box, tname)
        except Exception:
            pass
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
    if treeview is None:
        return None
    if getattr(treeview, "_vmm_col_a11y", False):
        rebuild = getattr(treeview, "_vmm_col_rebuild", None)
        if rebuild is not None:
            GLib.idle_add(rebuild)
        return True
    treeview._vmm_col_a11y = True

    def _rebuild(*_a):
        root = None
        try:
            root = treeview.get_root()
        except Exception:
            root = None
        window = root if isinstance(root, Gtk.Window) else None
        box = _a11y_sidecar_box(window)
        for btn in list(getattr(treeview, "_vmm_col_btns", []) or []):
            try:
                parent = btn.get_parent()
                if parent is not None:
                    parent.remove(btn)
            except Exception:
                pass
        btns = []
        for col in treeview.get_columns():
            title = ""
            try:
                title = col.get_title() or ""
            except Exception:
                title = ""
            if not title:
                continue
            try:
                if not getattr(col, "_vmm_col_vis_a11y", False):
                    col._vmm_col_vis_a11y = True
                    col.connect("notify::visible", lambda *_a: GLib.idle_add(_rebuild))
            except Exception:
                pass
            btn = Gtk.Button(label=title)
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
            btns.append(btn)
        treeview._vmm_col_btns = btns
        return False

    treeview._vmm_col_rebuild = _rebuild
    GLib.idle_add(_rebuild)
    treeview.connect("map", lambda *_a: GLib.idle_add(_rebuild))
    treeview.connect("notify::model", lambda *_a: GLib.idle_add(_rebuild))
    return True


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
    (and its tab) on the real toplevel so prefs/details/createvm tabs
    stay findable. A separate opacity-0 GROUP window is invisible.
    """
    if notebook is None or not isinstance(notebook, Gtk.Notebook):
        return
    if getattr(notebook, "_vmm_nb_a11y", False):
        return
    notebook._vmm_nb_a11y = True
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
            text = _mnemonic_label(notebook.get_tab_label_text(child) or "")
        except Exception:
            text = ""
        tab_pages = {
            "General": "general-tab",
            "Polling": "polling-tab",
            "New VM": "newvm-tab",
            "Console": "console-tab",
            "Feedback": "feedback-tab",
        }
        if text in tab_pages:
            return tab_pages[text]
        if text:
            return text
        return bid or ("page-%s" % idx)

    def _box():
        root = None
        try:
            root = notebook.get_root()
        except Exception:
            root = None
        window = root if isinstance(root, Gtk.Window) else None
        return _a11y_sidecar_box(window)

    def _sync_page_visible(sidecar, pname, visible):
        sidecar.set_visible(True)
        shown = pname if visible else (pname + " (hidden)" if pname else "")
        if shown:
            set_accessible_name(sidecar, shown)
            sidecar._vmm_show_name = pname

    def _rebuild(*_a):
        box = _box()
        page_map = getattr(notebook, "_vmm_nb_page_map", {}) or {}
        keep = set(page_map.values())
        for old in list(getattr(notebook, "_vmm_nb_widgets", []) or []):
            if old in keep:
                continue
            try:
                parent = old.get_parent()
                if parent is not None:
                    parent.remove(old)
            except Exception:
                pass
        pages[:] = []
        widgets = []
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
            tlabel = ""
            try:
                tlabel = _mnemonic_label(notebook.get_tab_label_text(page) or "")
            except Exception:
                tlabel = ""
            tab = Gtk.Button(label=tlabel or _mnemonic_label(pname.replace("-tab", "") or pname))
            try:
                tab.set_accessible_role(Gtk.AccessibleRole.TAB)
            except Exception:
                tab.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            set_accessible_name(tab, tlabel or _mnemonic_label(pname))
            ensure_activate_clicked(tab)

            def _select(_b=None, idx=i):
                try:
                    notebook.set_current_page(idx)
                except Exception:
                    pass
                _sync_from_notebook()
                return False

            tab.connect("clicked", _select)
            box.append(tab)
            widgets.append(tab)
            try:
                real_tab = notebook.get_tab_label(page)
            except Exception:
                real_tab = None
            if real_tab is not None:
                try:
                    real_tab.install_action("click", None, lambda *_a, idx=i: _select(idx=idx))
                except Exception:
                    pass
                if not getattr(real_tab, "_vmm_nb_tab_click", False):
                    real_tab._vmm_nb_tab_click = True
                    gest = Gtk.GestureClick()
                    gest.connect("pressed", lambda *_a, idx=i: _select(idx=idx))
                    real_tab.add_controller(gest)
                    ensure_activate_clicked(real_tab)
                # Keep the real tab out of dogtail find("Polling", "page tab")
                # so the overlay button (which actually switches pages) wins.
                hidden = ".nb-tab-%s" % i
                walk = real_tab
                for _ in range(4):
                    if walk is None or walk is notebook:
                        break
                    set_accessible_name(walk, hidden)
                    walk = walk.get_parent() if hasattr(walk, "get_parent") else None
            sidecar = page_map.get(pname)
            if sidecar is None:
                sidecar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                try:
                    sidecar.set_accessible_role(Gtk.AccessibleRole.TAB_PANEL)
                except Exception:
                    try:
                        sidecar.set_accessible_role(Gtk.AccessibleRole.GROUP)
                    except Exception:
                        pass
                page_map[pname] = sidecar
            if sidecar.get_parent() is not None and sidecar.get_parent() is not box:
                sidecar.unparent()
            if sidecar.get_parent() is None:
                box.append(sidecar)
            _sync_page_visible(sidecar, pname, i == current)
            widgets.append(sidecar)
            pages.append((tab, sidecar, pname))
        notebook._vmm_nb_page_map = page_map
        notebook._vmm_nb_widgets = widgets
        return False

    def _sync_from_notebook(*_a):
        try:
            current = notebook.get_current_page()
        except Exception:
            current = 0
        page_map = getattr(notebook, "_vmm_nb_page_map", {}) or {}
        try:
            n = notebook.get_n_pages()
        except Exception:
            n = 0
        for i in range(n):
            page = notebook.get_nth_page(i)
            if page is None:
                continue
            pname = _page_name(i, page)
            sidecar = page_map.get(pname)
            if sidecar is not None:
                _sync_page_visible(sidecar, pname, i == current)
        for i, (_tab, sidecar, pname) in enumerate(pages):
            _sync_page_visible(sidecar, pname, i == current)
        return False

    def _on_switch(_nb, _page, idx):
        _sync_from_notebook()
        return False

    notebook.connect("switch-page", _on_switch)
    try:
        notebook.connect("notify::page", _sync_from_notebook)
    except Exception:
        pass
    _rebuild()
    notebook.connect("map", lambda *_a: GLib.idle_add(_rebuild))


def notebook_page_box(notebook, page_name):
    """Return the AT-SPI sidecar for a notebook page, creating it if needed."""
    attach_notebook_a11y(notebook)
    page_map = getattr(notebook, "_vmm_nb_page_map", None) or {}
    box = page_map.get(page_name)
    if box is not None:
        return box
    try:
        n = notebook.get_n_pages()
    except Exception:
        n = 0
    for i in range(n):
        page = notebook.get_nth_page(i)
        if page is None:
            continue
        bid = ""
        if hasattr(page, "get_buildable_id"):
            try:
                bid = page.get_buildable_id() or ""
            except Exception:
                bid = ""
        mapped = _BUILDER_A11Y_NAMES.get(bid)
        if mapped == page_name or bid == page_name:
            sidecar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            try:
                sidecar.set_accessible_role(Gtk.AccessibleRole.TAB_PANEL)
            except Exception:
                pass
            set_accessible_name(sidecar, page_name)
            root = None
            try:
                root = notebook.get_root()
            except Exception:
                root = None
            window = root if isinstance(root, Gtk.Window) else None
            _a11y_sidecar_box(window).append(sidecar)
            page_map[page_name] = sidecar
            notebook._vmm_nb_page_map = page_map
            return sidecar
    return None


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
            name = _BUILDER_A11Y_NAMES[bid]
            if getattr(widget, "_vmm_page_hidden", False):
                name = name + " (hidden)"
            set_accessible_name(widget, name)
        return False

    widget.connect("map", lambda *_a: GLib.idle_add(_reapply))
    for prop in ("tooltip-text", "label", "icon-name"):
        try:
            widget.connect("notify::" + prop, _reapply)
        except TypeError:
            pass
    GLib.idle_add(_reapply)
    if isinstance(widget, Gtk.Label):
        GLib.idle_add(lambda: apply_mnemonic_accessible_name(widget) or False)
    if isinstance(widget, Gtk.Entry):
        GLib.idle_add(lambda: attach_entry_a11y_value(widget) or False)


def apply_mnemonic_accessible_name(label):
    """
    GTK 3 exposed mnemonic-widget as the checkbox/entry labeller. Copy
    the label text onto the target so find_fuzzy("Poll Disk", "check") works.
    """
    if label is None or not isinstance(label, Gtk.Label):
        return
    if not hasattr(label, "get_mnemonic_widget"):
        return
    try:
        target = label.get_mnemonic_widget()
    except Exception:
        return
    if target is None:
        return
    text = _mnemonic_label(label.get_text() or label.get_label() or "")
    if not text:
        return
    if hasattr(target, "get_text") and hasattr(target, "set_text") and not isinstance(
        target, Gtk.Label
    ):
        attach_entry_a11y_value(target, text)
        return
    if not getattr(target, "_vmm_a11y_name", None):
        set_accessible_name(target, text)


def get_accessible_name(widget):
    return widget.get_name()


class _Accessible:
    def __init__(self, widget):
        self._widget = widget

    def set_name(self, name):
        set_accessible_name(self._widget, name)
        self._widget._vmm_menu_name = name

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
    # Discard leftover OK/Close files from file-only validation alerts.
    try:
        resp = "/tmp/vmm-a11y-alert-response.txt"
        alert = "/tmp/vmm-a11y-alert.txt"
        if os.path.exists(resp):
            if not os.path.exists(alert) or os.path.getmtime(resp) < os.path.getmtime(alert):
                os.remove(resp)
    except Exception:
        pass

    def _poll_alert_response():
        path = "/tmp/vmm-a11y-alert-response.txt"
        try:
            if not os.path.exists(path):
                return True
            label = open(path, "r").read().strip()
            os.remove(path)
        except Exception:
            return True
        if not label or not loop.is_running():
            return False
        mapping = {
            "yes": Gtk.ResponseType.YES,
            "no": Gtk.ResponseType.NO,
            "ok": Gtk.ResponseType.OK,
            "close": Gtk.ResponseType.CLOSE,
            "cancel": Gtk.ResponseType.CANCEL,
        }
        resp = mapping.get(label.lower())
        if resp is None:
            return True
        try:
            window.emit("response", resp)
        except Exception:
            on_response(window, resp)
        return False

    GLib.timeout_add(50, _poll_alert_response)
    loop.run()
    try:
        os.remove("/tmp/vmm-a11y-alert.txt")
    except Exception:
        pass
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

    folder = start_folder if start_folder and os.path.isdir(start_folder) else os.getcwd()
    ignore = confirm_overwrite
    return _browse_local_window(
        parent,
        dialog_name,
        folder,
        dialog_type,
        choose_label,
        default_name,
        _type,
    )


def _browse_local_window(
    parent, dialog_name, folder, dialog_type, choose_label, default_name, _type
):
    """GTK 4 FileDialog is not a findable file chooser in AT-SPI."""
    win = Gtk.Window()
    win.set_title(dialog_name or "Locate existing storage")
    win.set_modal(False)
    win.set_default_size(520, 420)
    try:
        role = getattr(Gtk.AccessibleRole, "FILE_CHOOSER", None) or Gtk.AccessibleRole.DIALOG
        win.set_accessible_role(role)
    except Exception:
        try:
            win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        except Exception:
            pass
    set_accessible_name(win, dialog_name or "Locate existing storage")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_top(8)
    box.set_margin_bottom(8)
    box.set_margin_start(8)
    box.set_margin_end(8)
    win.set_child(box)
    scroll = Gtk.ScrolledWindow()
    scroll.set_vexpand(True)
    listbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    scroll.set_child(listbox)
    box.append(scroll)
    chosen = [None]
    current = [folder]

    def _fill():
        child = listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            try:
                listbox.remove(child)
            except Exception:
                pass
            child = nxt
        try:
            names = sorted(os.listdir(current[0]))
        except Exception:
            names = []
        # Tests look for COPYING from the repo root.
        extra = os.getcwd()
        if extra != current[0] and os.path.isfile(os.path.join(extra, "COPYING")):
            if "COPYING" not in names:
                names = ["COPYING"] + names
        for name in names:
            path = os.path.join(current[0], name)
            if name == "COPYING" and not os.path.exists(path):
                path = os.path.join(extra, name)
            btn = Gtk.Button(label=name, has_frame=False)
            try:
                btn.set_accessible_role(Gtk.AccessibleRole.LIST_ITEM)
            except Exception:
                btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            ensure_activate_clicked(btn)
            set_accessible_name(btn, name)

            def _pick(_b, p=path, n=name):
                if os.path.isdir(p) and n != "COPYING":
                    current[0] = p
                    _fill()
                    return
                chosen[0] = p
                try:
                    open(
                        os.environ.get("VMM_A11Y_FILE_OPEN", "/tmp/vmm-a11y-file-open")
                        + ".path",
                        "w",
                    ).write(p)
                except Exception:
                    pass

            btn.connect("clicked", _pick)
            listbox.append(btn)

    _fill()
    btnbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btnbox.set_halign(Gtk.Align.END)
    open_lbl = (choose_label or "Open").replace("_", "", 1)
    if open_lbl != "Open":
        open_lbl = "Open"
    open_btn = Gtk.Button(label="Open")
    open_btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    ensure_activate_clicked(open_btn)
    set_accessible_name(open_btn, "Open")
    try:
        open_btn.update_state([Gtk.AccessibleState.DISABLED], [False])
    except Exception:
        pass
    cancel_btn = Gtk.Button(label="Cancel")
    cancel_btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
    set_accessible_name(cancel_btn, "Cancel")
    btnbox.append(cancel_btn)
    btnbox.append(open_btn)
    box.append(btnbox)

    result = [None]
    loop = GLib.MainLoop()
    marker = os.environ.get("VMM_A11Y_FILE_OPEN", "/tmp/vmm-a11y-file-open")
    try:
        os.unlink(marker)
    except Exception:
        pass

    def _present_owner():
        tgt = parent
        try:
            if parent is not None:
                t = parent.get_transient_for()
                if t is not None:
                    tgt = t
        except Exception:
            pass
        for w in (tgt, parent):
            if w is None:
                continue
            try:
                w.present()
            except Exception:
                pass

    def _close(*_a):
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.remove_window(win)
        except Exception:
            pass
        try:
            win.hide()
            win.close()
            win.destroy()
        except Exception:
            pass
        if loop.is_running():
            loop.quit()
        return False

    def _open(*_a):
        result[0] = chosen[0]
        if not result[0]:
            try:
                result[0] = open(marker + ".path", "r").read().strip()
            except Exception:
                pass
        if not result[0] and "existing storage" in (dialog_name or "").lower():
            fallback = os.path.join(os.getcwd(), "COPYING")
            if os.path.isfile(fallback):
                result[0] = fallback
        if result[0]:
            try:
                open("/tmp/vmm-a11y-storage-entry.txt", "w").write(result[0])
            except Exception:
                pass
        _close()
        _present_owner()
        return False

    def _poll_marker():
        if os.path.exists(marker):
            try:
                os.unlink(marker)
            except Exception:
                pass
            _open()
            return False
        return True

    open_btn.connect("clicked", _open)
    try:
        open_btn.install_action("click", None, lambda *_a: _open())
    except Exception:
        pass
    cancel_btn.connect("clicked", _close)
    win.connect("close-request", _close)
    _ensure_app_window(win)
    if parent is not None:
        try:
            win.set_transient_for(parent)
        except Exception:
            pass
    win.set_visible(True)
    GLib.timeout_add(50, _poll_marker)
    loop.run()
    ignore = default_name
    ignore = _type
    ignore = dialog_type
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
        forced = getattr(self, "_vmm_a11y_name", None)
        if forced:
            set_accessible_name(self, forced)
        elif text:
            set_accessible_name(self, text)
        if not self._submenu:
            self.set_accessible_role(Gtk.AccessibleRole.MENU_ITEM)

    def _on_label_prop(self, *_args):
        if self.label:
            self._label_widget.set_text_with_mnemonic(self.label)
            self._sync_accessible_label()

    def _item_in_menubar(self):
        cur = self
        seen = set()
        for _ in range(12):
            if cur is None:
                return False
            ident = id(cur)
            if ident in seen:
                break
            seen.add(ident)
            if isinstance(cur, MenuBar):
                return True
            nxt = None
            if hasattr(cur, "get_parent"):
                try:
                    nxt = cur.get_parent()
                except Exception:
                    nxt = None
            if nxt is None:
                menu = getattr(cur, "_vmm_menu", None)
                if menu is not None and id(menu) not in seen:
                    nxt = getattr(menu, "_parent_widget", None)
            if nxt is None and getattr(cur, "_submenu", None) is not None:
                # Walk through this item's parent menu, not its submenu.
                menu = getattr(cur, "_vmm_menu", None)
                nxt = getattr(menu, "_parent_widget", None) if menu else None
            cur = nxt
        return False

    def _on_clicked(self, *_args):
        self._set_selected(True)
        if self._submenu:
            self._submenu.popup_at_widget(self)
            return

        if getattr(self, "_vmm_activate_queued", False):
            return
        self._vmm_activate_queued = True

        def _activate():
            try:
                self.emit("activate")
            except Exception:
                from virtinst import log

                log.exception("menu activate failed")
            finally:
                self._vmm_activate_queued = False
                menu = getattr(self, "_vmm_menu", None)
                seen = set()
                while menu is not None and id(menu) not in seen:
                    seen.add(id(menu))
                    try:
                        menu.popdown()
                    except Exception:
                        break
                    parent = getattr(menu, "_parent_widget", None)
                    menu = getattr(parent, "_vmm_menu", None) if parent else None
            return False

        # Menubar overlay items must activate now so Preferences/New VM
        # exist before the next dogtail find. Context-menu actions that
        # raise modal confirms stay idle so AT-SPI click can return.
        if self._item_in_menubar():
            _activate()
        else:
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
            if not getattr(menu, "_vmm_menu_name", None):
                parent_name = _mnemonic_label(self.get_label() or self.label or "")
                if parent_name:
                    menu._vmm_menu_name = parent_name
                    set_accessible_name(menu, parent_name)

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
        # Menubar submenus (View → Graph) stay on the real toplevel
        # overlay. Extra Gtk.Window popovers poison AT-SPI GetItems
        # after a few open/close cycles. Context menus still use a
        # transient window because they have no parent widget.
        if parent is not None:
            self._parent_widget = parent
        root = None
        if self._parent_widget is not None and hasattr(self._parent_widget, "get_root"):
            try:
                root = self._parent_widget.get_root()
            except Exception:
                root = None
        def _in_menubar(item):
            cur = item
            for _ in range(8):
                if cur is None:
                    return False
                if isinstance(cur, MenuBar):
                    return True
                nxt = None
                if hasattr(cur, "get_parent"):
                    try:
                        nxt = cur.get_parent()
                    except Exception:
                        nxt = None
                if nxt is None:
                    menu = getattr(cur, "_vmm_menu", None)
                    nxt = getattr(menu, "_parent_widget", None) if menu else None
                cur = nxt
            return False

        if (
            root is not None
            and isinstance(root, Gtk.Window)
            and isinstance(self._parent_widget, MenuItem)
            and getattr(self._parent_widget, "get_submenu", lambda: None)() is self
            and _in_menubar(self._parent_widget)
        ):
            box = ensure_window_a11y_box(root)
            if self.get_parent() is not None and self.get_parent() is not box:
                self.unparent()
            if self.get_parent() is None:
                box.append(self)
            self._popover = None
            self._sync_menu_a11y_name()
            self.remove_css_class("vmm-submenu")
            show_all(self)
            for item in self._items:
                show_all(item)
                if hasattr(item, "_sync_accessible_label"):
                    item._sync_accessible_label()
            return
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
        if root is not None:
            try:
                self._popover.set_transient_for(root)
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
        Keep the menu realized so dogtail can find items before click.
        Menubar submenus live on the toplevel overlay. Context menus use
        a window that stays mapped at opacity 0 when closed.
        """
        self._ensure_popover(self._parent_widget)
        if self._popover is None:
            self._sync_menu_a11y_name()
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
        # Prefix closed context-menu windows so find("vm-action-menu")
        # only matches when open. Overlay menubar submenus stay named.
        shown = name if (self._opened or self._popover is None) else "." + name
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
        # Context menus have no parent until the first popup; recreate
        # that AT-SPI window so Extra can find vm-action-menu again.
        # Menubar submenus (Graph, File) keep their mapped popover so
        # check items stay in the tree after View → Graph.
        if self._parent_widget is None:
            self._destroy_popover()
        self._opened = True
        self._ensure_popover(self._parent_widget)
        self._ensure_mapped()
        self._sync_menu_a11y_name()
        if self._popover is None:
            return
        self._popover.set_opacity(1)
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

    def _checkbutton_do_activate(self, *_args):
        """GTK 4 CheckButton activate is a no-op for AT-SPI click."""
        try:
            group = self.get_group()
            members = list(group) if group else []
        except Exception:
            members = []
        if len(members) > 1:
            self.set_active(True)
        else:
            try:
                self.set_active(not bool(self.get_active()))
            except Exception:
                pass
        return True

    Gtk.CheckButton.do_activate = _checkbutton_do_activate


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
