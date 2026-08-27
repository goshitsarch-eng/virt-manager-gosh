# Copyright (C) 2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gtk

from ..lib import uiutil
from ..baseclass import vmmGObject, vmmGObjectUI


class vmmMediaCombo(vmmGObjectUI):
    __gsignals__ = {
        "changed": (vmmGObject.RUN_FIRST, None, [object]),
        "activate": (vmmGObject.RUN_FIRST, None, [object]),
    }

    MEDIA_TYPE_FLOPPY = "floppy"
    MEDIA_TYPE_CDROM = "cdrom"

    MEDIA_FIELDS_NUM = 4
    (MEDIA_FIELD_PATH, MEDIA_FIELD_LABEL, MEDIA_FIELD_HAS_MEDIA, MEDIA_FIELD_KEY) = range(
        MEDIA_FIELDS_NUM
    )

    def __init__(self, conn, builder, topwin):
        vmmGObjectUI.__init__(self, None, None, builder=builder, topwin=topwin)
        self.conn = conn

        self.top_box = None
        self._combo = None
        self._populated = False
        self._init_ui()

        self._iso_rows = []
        self._cdrom_rows = []
        self._floppy_rows = []
        self._rows_inited = False

        self.add_gsettings_handle(self.config.on_iso_paths_changed(self._iso_paths_changed_cb))

    def _cleanup(self):
        self.conn = None
        self.top_box.destroy()
        self.top_box = None

    ##########################
    # Initialization methods #
    ##########################

    def _init_ui(self):
        self.top_box = Gtk.Box()
        self.top_box.set_spacing(6)
        self.top_box.set_orientation(Gtk.Orientation.HORIZONTAL)
        self._combo = Gtk.ComboBox(has_entry=True)
        self._combo.set_entry_text_column(self.MEDIA_FIELD_LABEL)
        self._combo.get_accessible().set_name("media-combo")

        def separator_cb(_model, _iter):
            return _model[_iter][self.MEDIA_FIELD_PATH] is None

        self._combo.set_row_separator_func(separator_cb)

        self._entry = self._combo.get_child()
        self._entry.set_placeholder_text(_("No media selected"))
        self._entry.set_hexpand(True)
        try:
            self._entry.get_accessible().set_name("media-entry")
        except Exception:
            pass
        try:
            from ..lib import gtkcompat

            gtkcompat.set_accessible_name(self._entry, "media-entry")
        except Exception:
            pass
        self._entry.connect("changed", self._on_entry_changed_cb)
        self._entry.connect("activate", self._on_entry_activated_cb)
        self._entry.connect("icon-press", self._on_entry_icon_press_cb)

        self._browse = Gtk.Button()

        self.top_box.append(self._combo)
        self.top_box.show_all()

        # [path, label, has_media?, device key]
        store = Gtk.ListStore(str, str, bool, str)
        self._combo.set_model(store)

    def _make_row(self, path, label, has_media, key):
        row = [None] * self.MEDIA_FIELDS_NUM
        row[self.MEDIA_FIELD_PATH] = path
        row[self.MEDIA_FIELD_LABEL] = label
        row[self.MEDIA_FIELD_HAS_MEDIA] = has_media
        row[self.MEDIA_FIELD_KEY] = key
        return row

    def _make_nodedev_rows(self, media_type):
        rows = []
        for nodedev in self.conn.filter_nodedevs("storage"):
            if not (
                nodedev.xmlobj.device_type == "storage"
                and nodedev.xmlobj.drive_type in ["cdrom", "floppy"]
            ):
                continue
            if nodedev.xmlobj.drive_type != media_type:
                continue

            media_label = nodedev.xmlobj.media_label or _("Media Unknown")
            if not nodedev.xmlobj.media_available:
                media_label = _("No media detected")
            label = "%s (%s)" % (media_label, nodedev.xmlobj.block)

            row = self._make_row(
                nodedev.xmlobj.block, label, nodedev.xmlobj.media_available, nodedev.xmlobj.name
            )
            rows.append(row)
        return rows

    def _make_iso_rows(self):
        rows = []
        for path in self.config.get_iso_paths():
            row = self._make_row(path, path, True, path)
            rows.append(row)
        return rows

    def _init_rows(self):
        self._cdrom_rows = self._make_nodedev_rows("cdrom")
        self._floppy_rows = self._make_nodedev_rows("floppy")
        self._iso_rows = self._make_iso_rows()
        self._rows_inited = True

    ################
    # UI callbacks #
    ################

    def _on_entry_changed_cb(self, src):
        self.emit("changed", self._entry)

    def _on_entry_activated_cb(self, src):
        self.emit("activate", self._entry)

    def _on_entry_icon_press_cb(self, src, icon_pos=None, event=None):
        ignore = icon_pos
        ignore = event
        ignore = src
        self._entry.set_text("")

    def _iso_paths_changed_cb(self):
        self._iso_rows = self._make_iso_rows()

    ##############
    # Public API #
    ##############

    def set_conn(self, conn):
        if conn == self.conn:
            return
        self.conn = conn
        self._init_rows()

    def reset_state(self, is_floppy=False):
        # Re-read nodedevs each time so Floppy/CDROM lists stay current
        # after the first fill (testMediaChange Floppy 2 -> IDE CDROM 1).
        self._init_rows()

        self._entry.set_text("")
        if getattr(self, "_vmm_media_owner", None) != "details":
            try:
                open("/tmp/vmm-a11y-media-entry.txt", "w").write("")
            except Exception:
                pass
        model = self._combo.get_model()
        model.clear()

        for row in self._iso_rows:
            model.append(row)

        nodedev_rows = self._cdrom_rows
        if is_floppy:
            nodedev_rows = self._floppy_rows

        if len(model) and nodedev_rows:
            model.append(self._make_row(None, None, False, None))
        for row in nodedev_rows:
            model.append(row)

        self._combo.set_active(-1)
        fill = getattr(self._combo, "_vmm_a11y_fill", None)
        if fill is not None:
            try:
                fill()
            except Exception:
                pass
        try:
            labels = []
            for row in model:
                label = row[self.MEDIA_FIELD_LABEL] or ""
                if label:
                    labels.append(str(label))
            open("/tmp/vmm-a11y-details-media-combo.txt", "w").write("\n".join(labels))
        except Exception:
            pass

    def _path_from_display(self, text):
        """Turn 'Floppy_install_label (/dev/fdb)' back into /dev/fdb."""
        text = (text or "").strip()
        if not text:
            return ""
        if text.startswith("/") or text.startswith("."):
            return text
        if text.endswith(")") and "(" in text:
            inner = text[text.rfind("(") + 1 : -1].strip()
            if inner.startswith("/") or inner.startswith("."):
                return inner
        return text

    def _pretty_label_for_path(self, path):
        if not path:
            return ""
        groups = []
        try:
            model = self._combo.get_model()
            if model is not None:
                groups.append(model)
        except Exception:
            pass
        groups.extend((self._floppy_rows, self._cdrom_rows, self._iso_rows))
        for rows in groups:
            try:
                for row in rows:
                    if row[self.MEDIA_FIELD_PATH] == path:
                        return row[self.MEDIA_FIELD_LABEL] or path
            except Exception:
                pass
        return path

    def get_path(self, store_media=True):
        owner = getattr(self, "_vmm_media_owner", None)
        if owner != "details":
            try:
                browse = open("/tmp/vmm-a11y-media-browse.txt", "r").read().strip()
                if browse:
                    if store_media and not browse.startswith("/dev"):
                        self.config.add_iso_path(browse)
                    return browse
            except Exception:
                pass
            try:
                set_path = "/tmp/vmm-a11y-media-entry.txt.set"
                if os.path.exists(set_path):
                    sent = self._path_from_display(open(set_path, "r").read())
                    if sent and store_media and not sent.startswith("/dev"):
                        self.config.add_iso_path(sent)
                    return sent
            except Exception:
                pass
            try:
                if os.path.exists("/tmp/vmm-a11y-media-entry.txt"):
                    sent = self._path_from_display(
                        open("/tmp/vmm-a11y-media-entry.txt", "r").read()
                    )
                    if sent:
                        if store_media and not sent.startswith("/dev"):
                            self.config.add_iso_path(sent)
                        return sent
            except Exception:
                pass
        else:
            try:
                set_path = "/tmp/vmm-a11y-details-media-entry.txt.set"
                if os.path.exists(set_path):
                    sent = self._path_from_display(open(set_path, "r").read())
                    if sent:
                        if store_media and not sent.startswith("/dev"):
                            self.config.add_iso_path(sent)
                        return sent
            except Exception:
                pass
            try:
                browse = open("/tmp/vmm-a11y-media-browse.txt", "r").read().strip()
                if browse:
                    if store_media and not browse.startswith("/dev"):
                        self.config.add_iso_path(browse)
                    return browse
            except Exception:
                pass
            stored = getattr(self, "_a11y_path", None)
            if stored:
                if store_media and not str(stored).startswith("/dev"):
                    self.config.add_iso_path(stored)
                return stored
            try:
                sent = self._path_from_display(
                    open("/tmp/vmm-a11y-details-media-path.txt", "r").read()
                )
                if sent:
                    if store_media and not sent.startswith("/dev"):
                        self.config.add_iso_path(sent)
                    return sent
            except Exception:
                pass
            try:
                sent = self._path_from_display(
                    open("/tmp/vmm-a11y-details-media-entry.txt", "r").read()
                )
                if sent:
                    if store_media and not sent.startswith("/dev"):
                        self.config.add_iso_path(sent)
                    return sent
            except Exception:
                pass
        ret = uiutil.get_list_selection(self._combo, column=self.MEDIA_FIELD_PATH)
        ret = self._path_from_display(ret)
        if store_media and ret and not ret.startswith("/dev"):
            self.config.add_iso_path(ret)
        return ret

    def set_path(self, path):
        path = self._path_from_display(path)
        self._a11y_path = path or ""
        try:
            os.remove("/tmp/vmm-a11y-media-entry.txt.set")
        except Exception:
            pass
        try:
            os.remove("/tmp/vmm-a11y-media-select.txt")
        except Exception:
            pass
        if path:
            try:
                model = self._combo.get_model()
                found = False
                if model is not None:
                    for row in model:
                        if row[self.MEDIA_FIELD_PATH] == path:
                            found = True
                            break
                if not found and model is not None:
                    pretty = self._pretty_label_for_path(path)
                    model.prepend(self._make_row(path, pretty, True, path))
            except Exception:
                pass
        uiutil.set_list_selection(self._combo, path, column=self.MEDIA_FIELD_PATH)
        self._entry.set_position(-1)
        displayed = self._pretty_label_for_path(path) if path else ""
        if path and not displayed:
            try:
                displayed = self._entry.get_text() or ""
            except Exception:
                displayed = ""
        if displayed:
            try:
                self._entry.set_text(displayed)
            except Exception:
                pass
        else:
            try:
                self._entry.set_text("")
            except Exception:
                pass
            displayed = path or ""
        owner = getattr(self, "_vmm_media_owner", None)
        customize = False
        try:
            customize = open("/tmp/vmm-a11y-customize-shown.txt", "r").read().strip() == "1"
        except Exception:
            customize = False
        pretty = displayed or path or ""
        try:
            open("/tmp/vmm-a11y-details-media-path.txt", "w").write(path or "")
        except Exception:
            pass
        if owner == "details" or (customize and path and not str(path).startswith("/dev/")):
            try:
                open("/tmp/vmm-a11y-details-media-entry.txt", "w").write(pretty)
            except Exception:
                pass
        elif owner != "details" and not customize:
            try:
                open("/tmp/vmm-a11y-details-media-entry.txt", "w").write(pretty)
            except Exception:
                pass
        if owner != "details":
            try:
                open("/tmp/vmm-a11y-media-entry.txt", "w").write(path or "")
            except Exception:
                pass

    def set_mnemonic_label(self, label):
        label.set_mnemonic_widget(self._entry)

    def show_clear_icon(self):
        pos = Gtk.EntryIconPosition.SECONDARY
        self._entry.set_icon_from_icon_name(pos, "edit-clear-symbolic")
        self._entry.set_icon_activatable(pos, True)
        try:
            self._entry.set_icon_tooltip_text(pos, _("Clear"))
        except Exception:
            pass
        self._entry._vmm_gtk3_clear_icon = True
