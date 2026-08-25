# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject
from .lib import gtkcompat


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
        # and Adw.AboutDialog is an overlay sibling.
        dialog = Gtk.Window()
        dialog.set_transient_for(parent)
        dialog.set_modal(True)
        dialog.set_title("About")
        dialog.set_default_size(420, 320)
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

        def _label(text, name=None):
            lab = Gtk.Label(label=text)
            lab.set_wrap(True)
            lab.set_xalign(0)
            lab.set_accessible_role(Gtk.AccessibleRole.LABEL)
            if name:
                gtkcompat.set_accessible_name(lab, name)
            box.append(lab)
            return lab

        _label("Virtual Machine Manager", "Virtual Machine Manager")
        _label(self.config.get_appversion())
        _label(_("Powered by libvirt"))
        _label("Copyright (C) 2006-2026 Red Hat Inc.", "Copyright")
        _label("https://virt-manager.org/")
        _label(
            "Daniel P. Berrange, Cole Robinson, Hugh O. Brock"
        )
        dialog.set_child(box)
        self._dialog = dialog
        dialog.present()

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing about")
        if self._dialog:
            self._dialog.hide()
        return 1

    def _cleanup(self):
        if self._dialog:
            self._dialog.destroy()
        self._dialog = None
