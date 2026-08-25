# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Adw
from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject


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
        dialog = Adw.AboutDialog()
        dialog.set_application_name("Virtual Machine Manager")
        dialog.set_application_icon("virt-manager")
        dialog.set_version(self.config.get_appversion())
        dialog.set_developer_name("The virt-manager project")
        dialog.set_developers(
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
        dialog.set_copyright("Copyright (C) 2006-2020 Red Hat Inc.")
        dialog.set_comments(_("Powered by libvirt"))
        dialog.set_website("https://virt-manager.org/")
        if hasattr(dialog, "set_website_label"):
            dialog.set_website_label("https://virt-manager.org/")
        dialog.set_license_type(Gtk.License.GPL_2_0)
        dialog.set_accessible_role(Gtk.AccessibleRole.DIALOG)
        from .lib import gtkcompat

        gtkcompat.set_accessible_name(dialog, "About Virtual Machine Manager")
        self._dialog = dialog
        dialog.present(parent)

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing about")
        if self._dialog:
            self._dialog.close()
        return 1

    def _cleanup(self):
        self._dialog = None
