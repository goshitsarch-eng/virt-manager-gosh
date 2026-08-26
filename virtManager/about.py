# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Gdk
from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject
from .lib import gtkcompat


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
_GPL2 = """
Virtual Machine Manager is free software; you can redistribute it
and/or modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2 of
the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
""".strip()


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

        # Plain Gtk.Window with role DIALOG so AT-SPI sees the same
        # "About" / "Copyright" names as GTK 3's GtkAboutDialog.
        dialog = Gtk.Window()
        dialog.set_transient_for(parent)
        dialog.set_modal(True)
        dialog.set_title("About Virtual Machine Manager")
        dialog.set_default_size(460, 420)
        if parent is not None and hasattr(parent, "get_application"):
            app = parent.get_application()
            if app is not None:
                dialog.set_application(app)
        dialog.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        gtkcompat.set_accessible_name(dialog, "About")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)

        try:
            logo = Gtk.Image.new_from_icon_name("virt-manager")
            logo.set_pixel_size(64)
            logo.set_halign(Gtk.Align.CENTER)
            box.append(logo)
        except Exception:
            pass

        def _label(text, name=None, center=False):
            lab = Gtk.Label(label=text)
            lab.set_wrap(True)
            lab.set_xalign(0.5 if center else 0)
            if center:
                lab.set_halign(Gtk.Align.CENTER)
            lab.set_selectable(True)
            lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
            if name:
                gtkcompat.set_accessible_name(lab, name)
            box.append(lab)
            return lab

        _label("Virtual Machine Manager", "Virtual Machine Manager", center=True)
        _label(self.config.get_appversion(), "version", center=True)
        _label(_("Powered by libvirt"), "comments", center=True)
        _label("Copyright (C) 2006-2026 Red Hat Inc.", "Copyright", center=True)

        link = Gtk.LinkButton(uri=_WEBSITE, label=_WEBSITE)
        link.set_halign(Gtk.Align.CENTER)
        gtkcompat.set_accessible_name(link, _WEBSITE)
        box.append(link)

        credits = Gtk.Expander(label=_("Credits"))
        credit_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        credit_box.set_margin_top(6)
        authors = Gtk.Label(label=_AUTHORS)
        authors.set_wrap(True)
        authors.set_xalign(0)
        authors.set_selectable(True)
        gtkcompat.set_accessible_name(authors, "authors")
        artists = Gtk.Label(label=_ARTISTS)
        artists.set_wrap(True)
        artists.set_xalign(0)
        artists.set_selectable(True)
        gtkcompat.set_accessible_name(artists, "artists")
        translators = Gtk.Label(label=_("translator-credits"))
        translators.set_wrap(True)
        translators.set_xalign(0)
        gtkcompat.set_accessible_name(translators, "translator-credits")
        credit_box.append(authors)
        credit_box.append(artists)
        credit_box.append(translators)
        credits.set_child(credit_box)
        box.append(credits)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        btn_box.set_halign(Gtk.Align.CENTER)
        license_btn = Gtk.Button(label=_("License"))
        gtkcompat.set_accessible_name(license_btn, "License")
        license_btn.connect("clicked", lambda *_a: self._show_license(dialog))
        close_btn = Gtk.Button(label=_("Close"))
        gtkcompat.set_accessible_name(close_btn, "Close")
        close_btn.connect("clicked", lambda *_a: self._hide())
        btn_box.append(license_btn)
        btn_box.append(close_btn)
        box.append(btn_box)

        dialog.set_child(box)

        def _on_key(_c, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                return self._hide()
            return False

        keyctl = Gtk.EventControllerKey()
        keyctl.connect("key-pressed", _on_key)
        dialog.add_controller(keyctl)
        shortcut = Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_Escape, 0),
            Gtk.CallbackAction.new(lambda *_a: self._hide()),
        )
        sctl = Gtk.ShortcutController()
        sctl.add_shortcut(shortcut)
        dialog.add_controller(sctl)
        dialog.connect("close-request", lambda *_a: self._hide())
        self._dialog = dialog
        dialog.present()
        try:
            dialog.grab_focus()
        except Exception:
            pass

    def _show_license(self, parent):
        if self._license_win:
            self._license_win.present()
            return
        win = Gtk.Window()
        win.set_transient_for(parent)
        win.set_modal(True)
        win.set_title(_("License"))
        win.set_default_size(520, 360)
        win.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        gtkcompat.set_accessible_name(win, "License")
        scrolled = Gtk.ScrolledWindow()
        lab = Gtk.Label(label=_GPL2)
        lab.set_wrap(True)
        lab.set_xalign(0)
        lab.set_selectable(True)
        lab.set_margin_top(12)
        lab.set_margin_bottom(12)
        lab.set_margin_start(12)
        lab.set_margin_end(12)
        gtkcompat.set_accessible_name(lab, "license-text")
        scrolled.set_child(lab)
        win.set_child(scrolled)

        def _close(*_a):
            win.destroy()
            self._license_win = None
            return True

        win.connect("close-request", _close)
        keyctl = Gtk.EventControllerKey()
        keyctl.connect(
            "key-pressed",
            lambda _c, keyval, _k, _s: _close() if keyval == Gdk.KEY_Escape else False,
        )
        win.add_controller(keyctl)
        self._license_win = win
        win.present()

    def _hide(self, *_a):
        if self._license_win:
            self._license_win.destroy()
            self._license_win = None
        if self._dialog:
            self._dialog.hide()
            self._dialog.set_visible(False)
            self._dialog.destroy()
            self._dialog = None
        return True

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing about")
        self._hide()
        return 1

    def _cleanup(self):
        self._hide()
