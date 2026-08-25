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

        # Gtk.AboutDialog remains a real window/dialog in GTK 4, which
        # dogtail can find. Adw.AboutDialog presents as an overlay that
        # is not reliably in the same AT-SPI tree as the manager.
        dialog = Gtk.AboutDialog()
        dialog.set_transient_for(parent)
        dialog.set_modal(True)
        if parent is not None and hasattr(parent, "get_application"):
            app = parent.get_application()
            if app is not None:
                dialog.set_application(app)
        dialog.set_title("About")
        dialog.set_program_name("Virtual Machine Manager")
        dialog.set_logo_icon_name("virt-manager")
        dialog.set_version(self.config.get_appversion())
        dialog.set_authors(
            [
                "Daniel P. Berrange <berrange@redhat.com>",
                "Cole Robinson <crobinso@redhat.com>",
                "Hugh O. Brock <hbrock@redhat.com>",
            ]
        )
        dialog.set_artists(
            [
                "Máirín Duffy <duffy@redhat.com>",
                "Mike Langlie <mlanglie@redhat.com>",
                "Jeremy Perry <jeperry@redhat.com>",
                "Jakub Steiner <jsteiner@redhat.com>",
            ]
        )
        dialog.set_translator_credits(_("translator-credits"))
        dialog.set_copyright("Copyright (C) 2006-2026 Red Hat Inc.")
        dialog.set_comments(_("Powered by libvirt"))
        dialog.set_website("https://virt-manager.org/")
        dialog.set_license_type(Gtk.License.GPL_2_0)
        dialog.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        gtkcompat.set_accessible_name(dialog, "About")
        copyright = Gtk.Label(label="Copyright (C) 2006-2026 Red Hat Inc.")
        gtkcompat.set_accessible_name(copyright, "Copyright")
        child = dialog.get_child()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        if child is not None:
            child.unparent()
            box.append(child)
        box.append(copyright)
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
