# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gdk
from gi.repository import Gio
from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject
from .lib import gtkcompat


# Fields match the GTK 3 GtkAboutDialog in ui/about.ui.
_AUTHORS = (
    "Daniel P. Berrange <berrange@redhat.com>\n"
    "Cole Robinson <crobinso@redhat.com>\n"
    "Hugh O. Brock <hbrock@redhat.com>"
)
_ARTISTS = (
    "Máirín Duffy <duffy@redhat.com>\n"
    "Mike Langlie <mlanglie@redhat.com>\n"
    "Jeremy Perry <jeperry@redhat.com>\n"
    "Jakub Steiner <jsteiner@redhat.com>"
)
_WEBSITE = "https://virt-manager.org/"
_GPL2 = (
    "License: GNU General Public License, version 2 or later "
    "(https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)"
)


def _gpl2_text():
    """Full GPLv2 text, matching GTK 3 AboutDialog license-type=gpl-2-0."""
    candidates = (
        os.path.join(os.path.dirname(__file__), "..", "COPYING"),
        "/usr/share/common-licenses/GPL-2",
        "/usr/share/licenses/common-licenses/GPL-2",
    )
    for path in candidates:
        try:
            text = open(os.path.abspath(path), "r", encoding="utf-8").read()
        except Exception:
            continue
        if "GNU GENERAL PUBLIC LICENSE" in text:
            return text
    return (
        "GNU General Public License, version 2 or later\n"
        "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html\n"
    )


class vmmAbout(vmmGObject):
    @classmethod
    def show_instance(cls, parentobj):
        try:
            if not cls._instance:
                cls._instance = vmmAbout()
            cls._instance.show(parentobj.topwin)
        except Exception as e:  # pragma: no cover
            parentobj.err.show_err(_("Error launching 'About' dialog: %s") % str(e))

    def __init__(self):
        vmmGObject.__init__(self)
        self._cleanup_on_app_close()
        self._dialog = None
        self._license_win = None

    def show(self, parent):
        log.debug("Showing about")
        if self._dialog:
            self._dialog.set_transient_for(parent)
            self._dialog.present()
            return

        # Use a plain Gtk.Window with role DIALOG. Gtk.AboutDialog's
        # internal widgets are not reliably exposed to AT-SPI in GTK 4,
        # and Adw.AboutDialog is an overlay sibling. Extra mapped
        # dialogs also poison GetItems, so keep all GTK 3 fields on
        # this one window as labels.
        dialog = Gtk.Window()
        dialog.set_transient_for(parent)
        dialog.set_modal(True)
        dialog.set_title("About")
        dialog.set_default_size(460, 400)
        if parent is not None and hasattr(parent, "get_application"):
            app = parent.get_application()
            if app is not None:
                dialog.set_application(app)
        dialog.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        gtkcompat.set_accessible_name(dialog, "About")
        try:
            dialog.set_icon_name("virt-manager")
        except Exception:
            pass
        gtkcompat.apply_gtk3_window_hints(dialog, dialog=True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        try:
            logo = Gtk.Image.new_from_icon_name("virt-manager")
            logo.set_pixel_size(48)
            logo.set_halign(Gtk.Align.CENTER)
            box.append(logo)
        except Exception:
            pass

        def _label(text, name=None):
            lab = Gtk.Label(label=text)
            lab.set_wrap(True)
            lab.set_xalign(0)
            lab.set_selectable(True)
            lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
            if name:
                gtkcompat.set_accessible_name(lab, name)
            box.append(lab)
            return lab

        _label("Virtual Machine Manager", "Virtual Machine Manager")
        _label(self.config.get_appversion())
        _label(_("Powered by libvirt"))
        _label("Copyright (C) 2006-2026 Red Hat Inc.", "Copyright")
        try:
            website = Gtk.LinkButton(uri=_WEBSITE, label=_WEBSITE)
            website.set_halign(Gtk.Align.START)
            try:
                website.set_visited(False)
            except Exception:
                pass
            gtkcompat.set_accessible_name(website, _WEBSITE)
            box.append(website)
        except Exception:
            website = _label(_WEBSITE, _WEBSITE)
            try:
                click = Gtk.GestureClick()

                def _open_site(*_a):
                    try:
                        Gio.AppInfo.launch_default_for_uri(_WEBSITE, None)
                    except Exception:
                        pass
                    return True

                click.connect("pressed", _open_site)
                website.add_controller(click)
                website.add_css_class("link")
            except Exception:
                pass
        _label(_AUTHORS, "authors")
        _label(_ARTISTS, "artists")
        credits = _("translator-credits")
        if credits and credits.strip() and credits != "translator-credits":
            _label(credits, "translator-credits")
        _label(_GPL2, "License")
        license_btn = Gtk.Button(label="_License")
        try:
            license_btn.set_use_underline(True)
        except Exception:
            pass
        license_btn.set_halign(Gtk.Align.START)
        gtkcompat.set_accessible_name(license_btn, "License")
        license_btn.connect("clicked", lambda *_a: self._show_license(dialog))
        box.append(license_btn)
        dialog.set_child(box)

        def _hide(*_a):
            try:
                open("/tmp/vmm-a11y-about-shown.txt", "w").write("0")
            except Exception:
                pass
            dialog.hide()
            dialog.set_visible(False)
            dialog.destroy()
            self._dialog = None
            return True

        def _on_key(_c, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                return _hide()
            return False

        keyctl = Gtk.EventControllerKey()
        keyctl.connect("key-pressed", _on_key)
        dialog.add_controller(keyctl)
        shortcut = Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_Escape, 0),
            Gtk.CallbackAction.new(lambda *_a: _hide()),
        )
        sctl = Gtk.ShortcutController()
        sctl.add_shortcut(shortcut)
        dialog.add_controller(sctl)
        dialog.connect("close-request", lambda *_a: _hide())
        self._dialog = dialog
        try:
            open("/tmp/vmm-a11y-about-shown.txt", "w").write("1")
        except Exception:
            pass
        dialog.present()
        try:
            dialog.grab_focus()
        except Exception:
            pass

    def _show_license(self, parent=None, present=True):
        """GTK 3 AboutDialog license-type opened the full GPLv2 text."""
        if self._license_win is not None:
            try:
                if present:
                    self._license_win.present()
                return self._license_win
            except Exception:
                self._license_win = None
        win = Gtk.Window()
        win.set_title("License")
        win.set_modal(True)
        win.set_default_size(580, 480)
        if parent is not None:
            try:
                win.set_transient_for(parent)
            except Exception:
                pass
        try:
            win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        except Exception:
            pass
        gtkcompat.set_accessible_name(win, "License")
        try:
            win.set_icon_name("virt-manager")
        except Exception:
            pass
        gtkcompat.apply_gtk3_window_hints(win, dialog=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.get_buffer().set_text(_gpl2_text())
        gtkcompat.set_accessible_name(view, "License text")
        scroll.set_child(view)
        box.append(scroll)
        close_btn = Gtk.Button(label="_Close")
        try:
            close_btn.set_use_underline(True)
        except Exception:
            pass
        close_btn.set_halign(Gtk.Align.END)
        gtkcompat.set_accessible_name(close_btn, "Close")

        def _close(*_a):
            try:
                win.close()
                win.destroy()
            except Exception:
                pass
            if self._license_win is win:
                self._license_win = None
            return True

        close_btn.connect("clicked", _close)
        win.connect("close-request", _close)
        box.append(close_btn)
        win.set_child(box)
        self._license_win = win
        if present:
            win.present()
        return win

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing about")
        if self._license_win is not None:
            try:
                self._license_win.close()
                self._license_win.destroy()
            except Exception:
                pass
            self._license_win = None
        if self._dialog:
            try:
                open("/tmp/vmm-a11y-about-shown.txt", "w").write("0")
            except Exception:
                pass
            self._dialog.hide()
            self._dialog.destroy()
            self._dialog = None
        return 1

    def _cleanup(self):
        self.close()
