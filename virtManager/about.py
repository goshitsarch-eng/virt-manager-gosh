# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

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

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing about")
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
