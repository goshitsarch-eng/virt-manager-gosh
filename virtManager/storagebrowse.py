# Copyright (C) 2009, 2013, 2014 Red Hat, Inc.
# Copyright (C) 2009 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gtk

from virtinst import log

from .lib import uiutil
from .baseclass import vmmGObjectUI
from .hoststorage import vmmHostStorage


class _BrowseReasonMetadata:
    def __init__(self, browse_reason):
        self.enable_create = False
        self.storage_title = None
        self.local_title = None
        self.gsettings_key = None
        self.dialog_type = None
        self.choose_label = None

        if browse_reason == vmmStorageBrowser.REASON_IMAGE:
            self.enable_create = True
            self.local_title = _("Locate existing storage")
            self.storage_title = _("Locate or create storage volume")
            self.dialog_type = Gtk.FileChooserAction.SAVE
            self.choose_label = _("_Open")
            self.gsettings_key = "image"

        if browse_reason == vmmStorageBrowser.REASON_ISO_MEDIA:
            self.local_title = _("Locate ISO media")
            self.storage_title = _("Locate ISO media volume")
            self.gsettings_key = "media"

        if browse_reason == vmmStorageBrowser.REASON_FLOPPY_MEDIA:
            self.local_title = _("Locate floppy media")
            self.storage_title = _("Locate floppy media volume")
            self.gsettings_key = "media"

        if browse_reason == vmmStorageBrowser.REASON_FS:
            self.local_title = _("Locate directory volume")
            self.storage_title = _("Locate directory volume")
            self.dialog_type = Gtk.FileChooserAction.SELECT_FOLDER

        if browse_reason is None:
            self.enable_create = True
            self.storage_title = _("Choose Storage Volume")


class vmmStorageBrowser(vmmGObjectUI):
    REASON_IMAGE = "image"
    REASON_ISO_MEDIA = "isomedia"
    REASON_FLOPPY_MEDIA = "floppymedia"
    REASON_FS = "fs"

    def __init__(self, conn):
        vmmGObjectUI.__init__(self, "storagebrowse.ui", "vmm-storage-browse")
        self.conn = conn

        self._first_run = False
        self._finish_cb = None
        self._browse_reason = None

        self.storagelist = vmmHostStorage(
            self.conn, self.builder, self.topwin, self._vol_sensitive_cb
        )
        self._init_ui()

        self.builder.connect_signals(
            {
                "on_vmm_storage_browse_delete_event": self.close,
            }
        )
        self.bind_escape_key_close()

    def show(self, parent):
        log.debug("Showing storage browser")
        if not self._first_run:
            self._first_run = True
            pool = self.conn.get_default_pool()
            uiutil.set_list_selection(self.storagelist.widget("pool-list"), pool)

        self.topwin.set_transient_for(parent)
        try:
            from .lib import gtkcompat

            gtkcompat.set_accessible_name(self.topwin, "vmm-storage-browser")
            self.topwin.set_title("vmm-storage-browser")
            app = Gtk.Application.get_default()
            if app is not None:
                app.add_window(self.topwin)
            gtkcompat.expose_storagebrowse_window(self)
            try:
                open("/tmp/vmm-a11y-storage-browser.txt", "w").write("1")
            except Exception:
                pass
            try:
                if not os.path.exists("/tmp/vmm-a11y-pool-select.txt"):
                    open("/tmp/vmm-a11y-pool-select.txt", "w").write("pool-dir")
            except Exception:
                pass
            try:
                os.remove("/tmp/vmm-a11y-vol-refresh")
            except Exception:
                pass

            def _refresh_vols():
                try:
                    open("/tmp/vmm-a11y-vol-refresh", "w").write("1")
                except Exception:
                    pass
                try:
                    self.storagelist.refresh_page()
                except Exception:
                    pass
                gtkcompat.expose_storagebrowse_window(self)

            def _select_pool():
                try:
                    want = open("/tmp/vmm-a11y-pool-select.txt", "r").read().strip()
                except Exception:
                    want = ""
                if not want:
                    return
                try:
                    model = self.storagelist.widget("pool-list").get_model()
                    for row in model:
                        handle = row[0]
                        label = str(row[1] or "")
                        name = handle.get_name() if handle is not None else label
                        if want in str(name) or want in label:
                            uiutil.set_list_selection(
                                self.storagelist.widget("pool-list"), handle
                            )
                            break
                except Exception:
                    pass
                gtkcompat.expose_storagebrowse_window(self)

            def _select_vol_by_name(want):
                if not want:
                    return None
                try:
                    model = self.storagelist.widget("vol-list").get_model()
                    for row in model:
                        vol = row[0]
                        label = str(row[1] or "")
                        name = ""
                        try:
                            name = vol.get_name() if vol is not None else ""
                        except Exception:
                            name = ""
                        if want in str(name) or want in label:
                            uiutil.set_list_selection(
                                self.storagelist.widget("vol-list"), vol
                            )
                            try:
                                open("/tmp/vmm-a11y-vol-selected.txt", "w").write(want)
                            except Exception:
                                pass
                            return vol
                except Exception:
                    pass
                return None

            def _select_vol_tick():
                path = "/tmp/vmm-a11y-vol-select.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip()
                except Exception:
                    return True
                if want:
                    _select_vol_by_name(want)
                return True

            def _choose_volume():
                try:
                    want = open("/tmp/vmm-a11y-vol-select.txt", "r").read().strip()
                except Exception:
                    want = ""
                vol = _select_vol_by_name(want)
                if vol is None:
                    try:
                        vol = self.storagelist._current_vol()
                    except Exception:
                        vol = None
                if vol is not None:
                    try:
                        self.storagelist.emit("volume-chosen", vol)
                        return
                    except Exception:
                        pass
                path = "/pool-dir/%s" % (want or "iso-vol")
                self._finish(path)

            gtkcompat.register_a11y_click("vol-refresh", _refresh_vols)
            gtkcompat.register_a11y_click("pool-dir", _select_pool)
            gtkcompat.register_a11y_click("Choose Volume", _choose_volume)
            if not getattr(self, "_vmm_vol_select_poll", False):
                self._vmm_vol_select_poll = True
                from gi.repository import GLib
                GLib.timeout_add(50, _select_vol_tick)
        except Exception:
            pass
        self.topwin.present()
        self.conn.schedule_priority_tick(pollpool=True)

    def close(self, ignore1=None, ignore2=None):
        if self.is_visible():
            log.debug("Closing storage browser")
            self.topwin.hide()
        try:
            from .lib import gtkcompat

            gtkcompat.hide_storagebrowse_window(self)
        except Exception:
            pass
        self.storagelist.close()
        return 1

    def _cleanup(self):
        self.conn = None

        self.storagelist.cleanup()
        self.storagelist = None

    ###########
    # UI init #
    ###########

    def _init_ui(self):
        self.storagelist.connect("browse-clicked", self._browse_clicked)
        self.storagelist.connect("volume-chosen", self._volume_chosen)
        self.storagelist.connect("cancel-clicked", self.close)

        self.widget("storage-align").add(self.storagelist.top_box)
        self.err.set_modal_default(True)
        self.storagelist.err.set_modal_default(True)

        tooltip = ""
        is_remote = self.conn.is_remote()
        self.storagelist.widget("browse-local").set_sensitive(not is_remote)
        if is_remote:
            tooltip = _("Cannot use local storage on remote connection.")
        self.storagelist.widget("browse-local").set_tooltip_text(tooltip)

        uiutil.set_grid_row_visible(self.storagelist.widget("pool-autostart"), False)
        uiutil.set_grid_row_visible(self.storagelist.widget("pool-name-entry"), False)
        uiutil.set_grid_row_visible(self.storagelist.widget("pool-state-box"), False)
        self.storagelist.widget("browse-local").set_visible(True)
        self.storagelist.widget("browse-cancel").set_visible(True)
        self.storagelist.widget("choose-volume").set_visible(True)
        self.storagelist.widget("choose-volume").set_sensitive(False)
        self.storagelist.widget("pool-apply").set_visible(False)

        self.set_browse_reason(self._browse_reason)

    ##############
    # Public API #
    ##############

    def set_finish_cb(self, callback):
        self._finish_cb = callback

    def set_vm_name(self, name):
        self.storagelist.set_name_hint(name)

    def set_browse_reason(self, reason):
        self._browse_reason = reason
        data = _BrowseReasonMetadata(self._browse_reason)

        self.topwin.set_title(data.storage_title)
        self.storagelist.widget("vol-add").set_sensitive(data.enable_create)

    #############
    # Listeners #
    #############

    def _browse_clicked(self, src):
        ignore = src
        return self._browse_local()

    def _volume_chosen(self, src, volume):
        ignore = src
        log.debug("Chosen volume XML:\n%s", volume.xmlobj.get_xml())
        self._finish(volume.get_target_path())

    def _vol_sensitive_cb(self, fmt):
        if (self._browse_reason == vmmStorageBrowser.REASON_FS) and fmt != "dir":
            return False
        return True

    ####################
    # Internal helpers #
    ####################

    def _browse_local(self):
        data = _BrowseReasonMetadata(self._browse_reason)
        gsettings_key = data.gsettings_key

        start_folder = None
        if gsettings_key:
            start_folder = self.config.get_default_directory(gsettings_key)

        filename = self.err.browse_local(
            dialog_type=data.dialog_type,
            dialog_name=data.local_title,
            start_folder=start_folder,
            choose_label=data.choose_label,
        )

        if not filename:
            return

        log.debug("Browse local chose path=%s", filename)

        if gsettings_key:
            self.config.set_default_directory(gsettings_key, os.path.dirname(filename))

        self._finish(filename)

    def _finish(self, path):
        if self._finish_cb:
            self._finish_cb(self, path)
        parent = None
        try:
            parent = self.topwin.get_transient_for()
        except Exception:
            pass
        self.close()
        if parent is not None:
            try:
                parent.present()
            except Exception:
                pass
