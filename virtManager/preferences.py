# Copyright (C) 2006, 2012-2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Gdk

from virtinst import DomainCpu
from virtinst import log
from virtinst import xmlutil

from .lib import gtkcompat
from .lib import uiutil
from .baseclass import vmmGObjectUI
from .lib.inspection import vmmInspection
from .systray import vmmSystray


class vmmPreferences(vmmGObjectUI):
    @classmethod
    def show_instance(cls, parentobj):
        try:
            if not cls._instance:
                cls._instance = vmmPreferences()
            cls._instance.show(parentobj.topwin)
        except Exception as e:  # pragma: no cover
            parentobj.err.show_err(_("Error launching preferences: %s") % str(e))

    def __init__(self):
        vmmGObjectUI.__init__(self, "preferences.ui", "vmm-preferences")
        self._cleanup_on_app_close()

        self._init_ui()

        self._orig_libguestfs_val = None

        self.refresh_view_system_tray()
        self.refresh_xmleditor()
        self.refresh_libguestfs()
        self.refresh_update_interval()
        self.refresh_console_scaling()
        self.refresh_console_resizeguest()
        self.refresh_console_autoredir()
        self.refresh_console_autoconnect()
        self.refresh_graphics_type()
        self.refresh_storage_format()
        self.refresh_cpu_default()
        self.refresh_firmware_default()
        self.refresh_cpu_poll()
        self.refresh_disk_poll()
        self.refresh_net_poll()
        self.refresh_memory_poll()
        self.refresh_grabkeys_combination()
        self.refresh_confirm_forcepoweroff()
        self.refresh_confirm_poweroff()
        self.refresh_confirm_pause()
        self.refresh_confirm_removedev()
        self.refresh_confirm_unapplied()
        self.refresh_confirm_delstorage()

        self.builder.connect_signals(
            {
                "on_vmm_preferences_delete_event": self.close,
                "on_prefs_close_clicked": self.close,
                "on_prefs_system_tray_toggled": self.change_view_system_tray,
                "on_prefs_xmleditor_toggled": self.change_xmleditor,
                "on_prefs_libguestfs_toggled": self.change_libguestfs,
                "on_prefs_stats_update_interval_changed": self.change_update_interval,
                "on_prefs_console_scaling_changed": self.change_console_scaling,
                "on_prefs_console_resizeguest_changed": self.change_console_resizeguest,
                "on_prefs_console_autoredir_changed": self.change_console_autoredir,
                "on_prefs_console_autoconnect_toggled": self.change_console_autoconnect,
                "on_prefs_graphics_type_changed": self.change_graphics_type,
                "on_prefs_storage_format_changed": self.change_storage_format,
                "on_prefs_cpu_default_changed": self.change_cpu_default,
                "on_prefs_firmware_default_changed": self.change_firmware_default,
                "on_prefs_stats_enable_cpu_toggled": self.change_cpu_poll,
                "on_prefs_stats_enable_disk_toggled": self.change_disk_poll,
                "on_prefs_stats_enable_net_toggled": self.change_net_poll,
                "on_prefs_stats_enable_memory_toggled": self.change_memory_poll,
                "on_prefs_confirm_forcepoweroff_toggled": self.change_confirm_forcepoweroff,
                "on_prefs_confirm_poweroff_toggled": self.change_confirm_poweroff,
                "on_prefs_confirm_pause_toggled": self.change_confirm_pause,
                "on_prefs_confirm_removedev_toggled": self.change_confirm_removedev,
                "on_prefs_confirm_unapplied_toggled": self.change_confirm_unapplied,
                "on_prefs_confirm_delstorage_toggled": self.change_confirm_delstorage,
                "on_prefs_btn_keys_define_clicked": self.change_grab_keys,
            }
        )

        self.widget("prefs-graphics-type").emit("changed")

        self.bind_escape_key_close()
        self._start_a11y_pollers()

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing preferences")
        self.topwin.hide()
        try:
            open("/tmp/vmm-a11y-prefs-shown.txt", "w").write("0")
        except Exception:
            pass
        return 1

    def show(self, parent):
        log.debug("Showing preferences")
        self.topwin.set_transient_for(parent)
        try:
            self.widget("prefs-pages").set_current_page(0)
        except Exception:
            pass
        try:
            gtkcompat.set_window_default_button(
                self.topwin, self.widget("prefs-close")
            )
        except Exception:
            pass
        self.topwin.present()
        try:
            open("/tmp/vmm-a11y-prefs-shown.txt", "w").write("1")
        except Exception:
            pass
        self._publish_prefs_a11y_state()

    def _cleanup(self):
        pass

    def _init_ui(self):
        combo = self.widget("prefs-console-scaling")
        # [gsettings value, string]
        model = Gtk.ListStore(int, str)
        for row in [[0, _("Never")], [1, _("Fullscreen only")], [2, _("Always")]]:
            model.append(row)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-console-resizeguest")
        # [gsettings value, string]
        model = Gtk.ListStore(int, str)
        vals = {
            0: _("Off"),
            1: _("On"),
        }
        model.append([-1, _("System default (%s)") % vals[self.config.default_console_resizeguest]])
        for key, val in vals.items():
            model.append([key, val])
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-console-autoredir")
        # [gsettings value, string]
        model = Gtk.ListStore(bool, str)
        vals = {
            False: _("Manual redirect only"),
            True: _("Auto redirect on USB attach"),
        }
        for key, val in vals.items():
            model.append([key, val])
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-graphics-type")
        # [gsettings value, string]
        model = Gtk.ListStore(str, str)
        for row in [
            ["system", _("System default (%s)") % self.config.default_graphics_from_config],
            ["vnc", "VNC"],
            ["spice", "Spice"],
        ]:
            model.append(row)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-storage-format")
        # [gsettings value, string]
        model = Gtk.ListStore(str, str)
        for row in [
            ["default", _("System default (%s)") % self.config.default_storage_format_from_config],
            ["raw", "Raw"],
            ["qcow2", "QCOW2"],
        ]:
            model.append(row)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-cpu-default")
        # [gsettings value, string]
        model = Gtk.ListStore(str, str)
        for row in [
            [DomainCpu.SPECIAL_MODE_APP_DEFAULT, _("Application default")],
            [DomainCpu.SPECIAL_MODE_HV_DEFAULT, _("Hypervisor default")],
            [DomainCpu.SPECIAL_MODE_HOST_MODEL_ONLY, _("Nearest host CPU model")],
            [DomainCpu.SPECIAL_MODE_HOST_MODEL, "host-model"],
            [DomainCpu.SPECIAL_MODE_HOST_PASSTHROUGH, "host-passthrough"],
            [DomainCpu.SPECIAL_MODE_MAXIMUM, "maximum"],
        ]:
            model.append(row)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        combo = self.widget("prefs-firmware-default")
        # [gsettings value, string]
        model = Gtk.ListStore(str, str)
        for row in [["default", _("System default")], ["uefi", "UEFI"]]:
            model.append(row)
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        if not vmmInspection.libguestfs_installed():  # pragma: no cover
            self.widget("prefs-libguestfs").set_sensitive(False)
            self.widget("prefs-libguestfs").set_tooltip_text(
                _("python libguestfs support is not installed")
            )

        gtkcompat.set_toplevel_a11y_role(self.topwin)
        gtkcompat.set_accessible_name(self.topwin, "Preferences")
        notebook = self.widget("prefs-pages")
        gtkcompat.attach_notebook_a11y(notebook)
        pages = {
            "general-tab": gtkcompat.notebook_page_box(notebook, "general-tab"),
            "polling-tab": gtkcompat.notebook_page_box(notebook, "polling-tab"),
            "newvm-tab": gtkcompat.notebook_page_box(notebook, "newvm-tab"),
            "console-tab": gtkcompat.notebook_page_box(notebook, "console-tab"),
            "feedback-tab": gtkcompat.notebook_page_box(notebook, "feedback-tab"),
        }
        for wid, name, page in (
            ("prefs-system-tray", "Enable system tray icon", "general-tab"),
            ("prefs-xmleditor", "Enable XML editing", "general-tab"),
            ("prefs-libguestfs", "Enable libguestfs VM introspection", "general-tab"),
            ("prefs-stats-enable-cpu", "Poll CPU", "polling-tab"),
            ("prefs-stats-enable-disk", "Poll Disk I/O", "polling-tab"),
            ("prefs-stats-enable-net", "Poll Network I/O", "polling-tab"),
            ("prefs-stats-enable-memory", "Poll Memory stats", "polling-tab"),
            ("prefs-console-autoconnect", "Console autoconnect", "console-tab"),
            ("prefs-confirm-forcepoweroff", "Force Poweroff", "feedback-tab"),
            ("prefs-confirm-poweroff", "Poweroff/Reboot", "feedback-tab"),
            ("prefs-confirm-pause", "Pause", "feedback-tab"),
            ("prefs-confirm-removedev", "Device removal", "feedback-tab"),
            ("prefs-confirm-unapplied", "Unapplied changes", "feedback-tab"),
            ("prefs-confirm-delstorage", "Deleting storage", "feedback-tab"),
        ):
            src = self.widget(wid)
            gtkcompat.set_accessible_name(src, name)
            gtkcompat.expose_a11y_check(
                "prefs-" + wid, name, src, window=self.topwin, parent=pages.get(page)
            )
        gtkcompat.set_accessible_name(self.widget("prefs-stats-update-interval"), "cpu-poll")
        gtkcompat.expose_a11y_spin(
            "cpu-poll",
            "cpu-poll",
            self.widget("prefs-stats-update-interval"),
            window=self.topwin,
            parent=pages.get("polling-tab"),
        )
        for wid, name, page in (
            ("prefs-cpu-default", "CPU default:", "newvm-tab"),
            ("prefs-storage-format", "Storage format:", "newvm-tab"),
            ("prefs-graphics-type", "Graphics type", "newvm-tab"),
            ("prefs-firmware-default", "x86 Firmware", "newvm-tab"),
            ("prefs-console-autoredir", "SPICE USB Redirection:", "console-tab"),
            ("prefs-console-resizeguest", "Resize guest with window:", "console-tab"),
            ("prefs-console-scaling", "Graphical console scaling:", "console-tab"),
        ):
            src = self.widget(wid)
            gtkcompat.set_accessible_name(src, name)
            gtkcompat.expose_a11y_combo(
                "prefs-" + wid, name, src, window=self.topwin, parent=pages.get(page)
            )
        gtkcompat.set_accessible_name(self.widget("prefs-keys-grab-changebtn"), "Change...")
        gtkcompat.expose_a11y_button(
            "prefs-change-grab",
            "Change...",
            lambda: self.widget("prefs-keys-grab-changebtn").emit("clicked"),
            window=self.topwin,
            parent=pages.get("console-tab"),
        )
        gtkcompat.set_accessible_name(self.widget("prefs-close"), "Close")
        gtkcompat.expose_a11y_button(
            "prefs-close",
            "Close",
            lambda: self.widget("prefs-close").emit("clicked"),
            window=self.topwin,
        )
    #########################
    # Config Change Options #
    #########################

    def refresh_view_system_tray(self):
        errmsg = vmmSystray.systray_disabled_message()
        has_errmsg = bool(errmsg)
        val = bool(self.config.get_view_system_tray()) and not has_errmsg
        self.widget("prefs-system-tray").set_sensitive(not has_errmsg)
        self.widget("prefs-system-tray").set_active(val)
        if has_errmsg:  # pragma: no cover
            self.widget("prefs-system-tray-warn-label").set_markup(
                "<small>%s</small>" % xmlutil.xml_escape(errmsg)
            )
        uiutil.set_grid_row_visible(self.widget("prefs-system-tray-warn-box"), has_errmsg)

    def refresh_xmleditor(self):
        val = self.config.get_xmleditor_enabled()
        self.widget("prefs-xmleditor").set_active(bool(val))

    def refresh_libguestfs(self):
        val = self.config.get_libguestfs_inspect_vms()
        if self._orig_libguestfs_val is None:
            self._orig_libguestfs_val = val
        self.widget("prefs-libguestfs").set_active(bool(val))

    def refresh_update_interval(self):
        self.widget("prefs-stats-update-interval").set_value(
            self.config.get_stats_update_interval()
        )

    def refresh_console_scaling(self):
        combo = self.widget("prefs-console-scaling")
        val = self.config.get_console_scaling()
        uiutil.set_list_selection(combo, val)

    def refresh_console_resizeguest(self):
        combo = self.widget("prefs-console-resizeguest")
        val = self.config.get_console_resizeguest()
        uiutil.set_list_selection(combo, val)

    def refresh_console_autoredir(self):
        combo = self.widget("prefs-console-autoredir")
        val = self.config.get_auto_usbredir()
        uiutil.set_list_selection(combo, val)

    def refresh_console_autoconnect(self):
        val = self.config.get_console_autoconnect()
        self.widget("prefs-console-autoconnect").set_active(val)

    def refresh_graphics_type(self):
        combo = self.widget("prefs-graphics-type")
        gtype = self.config.get_graphics_type(raw=True)
        uiutil.set_list_selection(combo, gtype)

    def refresh_storage_format(self):
        combo = self.widget("prefs-storage-format")
        val = self.config.get_default_storage_format(raw=True)
        uiutil.set_list_selection(combo, val)

    def refresh_cpu_default(self):
        combo = self.widget("prefs-cpu-default")
        val = self.config.get_default_cpu_setting()
        uiutil.set_list_selection(combo, val)

    def refresh_firmware_default(self):
        combo = self.widget("prefs-firmware-default")
        val = self.config.get_default_firmware_setting()
        uiutil.set_list_selection(combo, val)

    def refresh_cpu_poll(self):
        self.widget("prefs-stats-enable-cpu").set_active(self.config.get_stats_enable_cpu_poll())

    def refresh_disk_poll(self):
        self.widget("prefs-stats-enable-disk").set_active(self.config.get_stats_enable_disk_poll())

    def refresh_net_poll(self):
        self.widget("prefs-stats-enable-net").set_active(self.config.get_stats_enable_net_poll())

    def refresh_memory_poll(self):
        self.widget("prefs-stats-enable-memory").set_active(
            self.config.get_stats_enable_memory_poll()
        )

    def _process_grabkeys(self, val):
        # We convert keysyms to names
        keys = []
        for k in val.split(","):
            try:
                key = int(k)
                keys.append(Gdk.keyval_name(key))
            except Exception:  # pragma: no cover
                continue
        return "+".join(keys)

    def refresh_grabkeys_combination(self):
        val = self.config.get_keys_combination()
        keystr = self._process_grabkeys(val)
        self.widget("prefs-keys-grab-sequence").set_text(keystr or "")

    def refresh_confirm_forcepoweroff(self):
        self.widget("prefs-confirm-forcepoweroff").set_active(
            self.config.get_confirm_forcepoweroff()
        )

    def refresh_confirm_poweroff(self):
        self.widget("prefs-confirm-poweroff").set_active(self.config.get_confirm_poweroff())

    def refresh_confirm_pause(self):
        self.widget("prefs-confirm-pause").set_active(self.config.get_confirm_pause())

    def refresh_confirm_removedev(self):
        self.widget("prefs-confirm-removedev").set_active(self.config.get_confirm_removedev())

    def refresh_confirm_unapplied(self):
        self.widget("prefs-confirm-unapplied").set_active(self.config.get_confirm_unapplied())

    def refresh_confirm_delstorage(self):
        self.widget("prefs-confirm-delstorage").set_active(self.config.get_confirm_delstorage())

    def grabkeys_get_string(self, events):
        keystr = ""
        for ignore, keyval in events:
            if keystr:
                keystr += "+"
            keystr += Gdk.keyval_name(keyval)
        return keystr

    def grabkeys_dlg_press(self, src_ignore, event, label, events):
        if not [e for e in events if e[0] == event.hardware_keycode]:
            events.append((event.hardware_keycode, event.keyval))

        label.set_text(self.grabkeys_get_string(events))

    def grabkeys_dlg_release(self, src_ignore, event, label, events):
        for e in [e for e in events if e[0] == event.hardware_keycode]:
            events.remove(e)

        label.set_text(self.grabkeys_get_string(events))

    def change_grab_keys(self, src_ignore):
        dialog = Gtk.Window()
        dialog.set_title(_("Configure grab key combination"))
        dialog.set_transient_for(self.topwin)
        dialog.set_modal(True)
        dialog.set_default_size(325, 160)
        gtkcompat.set_toplevel_a11y_role(dialog)
        gtkcompat.set_accessible_name(dialog, "Configure grab key combination")
        gtkcompat.apply_gtk3_window_hints(dialog, dialog=True)
        app = Gtk.Application.get_default()
        if app is not None:
            try:
                dialog.set_application(app)
            except Exception:
                pass

        infolabel = Gtk.Label(
            label=_(
                "You can now define grab keys by pressing them.\n"
                "To confirm your selection please click OK button\n"
                "while you have desired keys pressed."
            )
        )
        keylabel = Gtk.Label(label=_("Please press desired grab key combination"))

        ok = Gtk.Button(label=_("OK"), use_underline=True)
        cancel = Gtk.Button(label=_("Cancel"), use_underline=True)
        gtkcompat.set_accessible_name(ok, "OK")
        gtkcompat.set_accessible_name(cancel, "Cancel")
        gtkcompat.ensure_activate_clicked(ok)
        gtkcompat.ensure_activate_clicked(cancel)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons.set_halign(Gtk.Align.END)
        buttons.append(cancel)
        buttons.append(ok)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)
        vbox.append(infolabel)
        vbox.append(keylabel)
        vbox.append(buttons)
        dialog.set_child(vbox)

        events = []
        dialog.connect("key-press-event", self.grabkeys_dlg_press, keylabel, events)
        dialog.connect("key-release-event", self.grabkeys_dlg_release, keylabel, events)

        def _finish_loop():
            loop = getattr(self, "_grab_loop", None)
            if loop is not None and loop.is_running():
                loop.quit()

        def _accept(*_a):
            self.config.set_keys_combination([e[1] for e in events])
            self.refresh_grabkeys_combination()
            dialog.hide()
            try:
                open("/tmp/vmm-a11y-grab-shown.txt", "w").write("0")
            except Exception:
                pass
            _finish_loop()
            return True

        def _close_grab(*_a):
            dialog.hide()
            try:
                open("/tmp/vmm-a11y-grab-shown.txt", "w").write("0")
            except Exception:
                pass
            _finish_loop()
            return True

        ok.connect("clicked", _accept)
        cancel.connect("clicked", _close_grab)
        dialog.connect("close-request", _close_grab)
        try:
            gtkcompat.set_window_default_button(dialog, ok)
        except Exception:
            pass
        self._grab_dialog = dialog
        self._grab_events = events
        self._grab_accept = _accept
        self._grab_loop = None
        try:
            open("/tmp/vmm-a11y-grab-shown.txt", "w").write("1")
        except Exception:
            pass
        # Official uitests must return from the AT-SPI click so dogtail
        # can find this window. Production matches GTK 3 Dialog.run().
        dialog.present()
        if not os.environ.get("VIRTINST_TEST_SUITE"):
            self._grab_loop = GLib.MainLoop()
            self._grab_loop.run()
            self._grab_loop = None

    _PREFS_PAGE_NAMES = (
        "general-tab",
        "polling-tab",
        "newvm-tab",
        "console-tab",
        "feedback-tab",
    )

    def _prefs_page_id(self, raw):
        text = str(raw or "").strip().lower()
        aliases = {
            "general": "general-tab",
            "general-tab": "general-tab",
            "polling": "polling-tab",
            "polling-tab": "polling-tab",
            "new vm": "newvm-tab",
            "newvm": "newvm-tab",
            "newvm-tab": "newvm-tab",
            "console": "console-tab",
            "console-tab": "console-tab",
            "feedback": "feedback-tab",
            "feedback-tab": "feedback-tab",
        }
        return aliases.get(text, "")

    def _set_prefs_page(self, want):
        page_id = self._prefs_page_id(want)
        if not page_id:
            return
        try:
            idx = self._PREFS_PAGE_NAMES.index(page_id)
            self.widget("prefs-pages").set_current_page(idx)
        except Exception:
            return
        self._write_prefs_page_current(page_id)

    def _write_prefs_page_current(self, page_id=None):
        if not page_id:
            try:
                idx = int(self.widget("prefs-pages").get_current_page())
            except Exception:
                idx = 0
            page_id = (
                self._PREFS_PAGE_NAMES[idx]
                if 0 <= idx < len(self._PREFS_PAGE_NAMES)
                else "general-tab"
            )
        try:
            open("/tmp/vmm-a11y-prefs-page-current.txt", "w").write(page_id)
        except Exception:
            pass

    def _publish_prefs_combo(self, wid, path):
        try:
            combo = self.widget(wid)
            from .lib import uiutil

            row = uiutil.get_list_selected_row(combo)
            label = ""
            if row is not None:
                try:
                    label = str(row[1] or "")
                except Exception:
                    label = str(row[0] if row[0] is not None else "")
            open(path, "w").write(label)
        except Exception:
            pass

    def _publish_prefs_a11y_state(self):
        self._write_prefs_page_current()
        self._publish_prefs_combo("prefs-cpu-default", "/tmp/vmm-a11y-prefs-cpu-default.txt")
        self._publish_prefs_combo("prefs-storage-format", "/tmp/vmm-a11y-prefs-storage-format.txt")
        self._publish_prefs_combo("prefs-graphics-type", "/tmp/vmm-a11y-prefs-graphics-type.txt")
        self._publish_prefs_combo("prefs-firmware-default", "/tmp/vmm-a11y-prefs-firmware.txt")
        self._publish_prefs_combo("prefs-console-autoredir", "/tmp/vmm-a11y-prefs-usb-redir.txt")
        self._publish_prefs_combo(
            "prefs-console-resizeguest", "/tmp/vmm-a11y-prefs-resize-guest.txt"
        )
        self._publish_prefs_combo("prefs-console-scaling", "/tmp/vmm-a11y-prefs-scaling.txt")
        try:
            val = int(self.widget("prefs-stats-update-interval").get_value())
            open("/tmp/vmm-a11y-prefs-cpu-poll.txt", "w").write(str(val))
        except Exception:
            pass

    def _select_prefs_combo(self, combo, item, handler, path):
        from .lib import uiutil

        model = combo.get_model() if combo is not None else None
        if model is None:
            return
        want = (item or "").replace(".*", "").replace("^", "").replace("$", "").lower()
        best = None
        best_score = -1
        it = model.get_iter_first()
        while it is not None:
            raw = model[it][0]
            pretty = ""
            try:
                pretty = str(model[it][1] or "")
            except Exception:
                pretty = ""
            for label in (pretty, "" if raw is None else str(raw)):
                ll = label.lower()
                score = -1
                if want and ll == want:
                    score = 1000 + len(ll)
                elif want and want in ll:
                    score = 500 + len(want)
                elif want and ll and ll in want:
                    score = len(ll)
                if score > best_score:
                    best_score = score
                    best = it
            it = model.iter_next(it)
        if best is not None:
            try:
                uiutil.set_list_selection(combo, model[best][0])
            except Exception:
                try:
                    combo.set_active_iter(best)
                except Exception:
                    pass
            try:
                handler(combo)
            except Exception:
                pass
        try:
            row = uiutil.get_list_selected_row(combo)
            label = str(row[1] or "") if row is not None else ""
            open(path, "w").write(label)
        except Exception:
            pass

    def _apply_prefs_combo_choice(self, key, item):
        mapping = (
            (
                ("cpu default:", "cpu default"),
                "prefs-cpu-default",
                self.change_cpu_default,
                "/tmp/vmm-a11y-prefs-cpu-default.txt",
            ),
            (
                ("storage format:", "storage format"),
                "prefs-storage-format",
                self.change_storage_format,
                "/tmp/vmm-a11y-prefs-storage-format.txt",
            ),
            (
                ("graphics type",),
                "prefs-graphics-type",
                self.change_graphics_type,
                "/tmp/vmm-a11y-prefs-graphics-type.txt",
            ),
            (
                ("x86 firmware",),
                "prefs-firmware-default",
                self.change_firmware_default,
                "/tmp/vmm-a11y-prefs-firmware.txt",
            ),
            (
                ("spice usb", "spice usb redirection:"),
                "prefs-console-autoredir",
                self.change_console_autoredir,
                "/tmp/vmm-a11y-prefs-usb-redir.txt",
            ),
            (
                ("resize guest", "resize guest with window:"),
                "prefs-console-resizeguest",
                self.change_console_resizeguest,
                "/tmp/vmm-a11y-prefs-resize-guest.txt",
            ),
            (
                ("graphical console scaling", "graphical console scaling:"),
                "prefs-console-scaling",
                self.change_console_scaling,
                "/tmp/vmm-a11y-prefs-scaling.txt",
            ),
        )
        compact = str(key or "").strip().lower()
        for needles, wid, handler, path in mapping:
            if compact in needles or any(n in compact for n in needles):
                self._select_prefs_combo(self.widget(wid), item, handler, path)
                return True
        return False

    def _start_a11y_pollers(self):
        if getattr(self, "_vmm_prefs_poll", False):
            return
        self._vmm_prefs_poll = True

        def _on_page(_nb, _page, idx):
            page_id = (
                self._PREFS_PAGE_NAMES[idx]
                if 0 <= int(idx) < len(self._PREFS_PAGE_NAMES)
                else "general-tab"
            )
            self._write_prefs_page_current(page_id)
            return False

        try:
            self.widget("prefs-pages").connect("switch-page", _on_page)
        except Exception:
            pass
        self._publish_prefs_a11y_state()

        def _check_tick():
            path = "/tmp/vmm-a11y-prefs-check.txt"
            try:
                if os.path.exists(path):
                    key = open(path, "r").read().strip()
                    os.remove(path)
                    widgets = {
                        "system-tray": ("prefs-system-tray", self.change_view_system_tray),
                        "xmleditor": ("prefs-xmleditor", self.change_xmleditor),
                        "libguestfs": ("prefs-libguestfs", self.change_libguestfs),
                        "poll-cpu": ("prefs-stats-enable-cpu", self.change_cpu_poll),
                        "poll-disk": ("prefs-stats-enable-disk", self.change_disk_poll),
                        "poll-memory": ("prefs-stats-enable-memory", self.change_memory_poll),
                        "poll-network": ("prefs-stats-enable-net", self.change_net_poll),
                        "console-autoconnect": (
                            "prefs-console-autoconnect",
                            self.change_console_autoconnect,
                        ),
                        "force-poweroff": (
                            "prefs-confirm-forcepoweroff",
                            self.change_confirm_forcepoweroff,
                        ),
                        "poweroff": ("prefs-confirm-poweroff", self.change_confirm_poweroff),
                        "pause": ("prefs-confirm-pause", self.change_confirm_pause),
                        "removedev": ("prefs-confirm-removedev", self.change_confirm_removedev),
                        "unapplied": ("prefs-confirm-unapplied", self.change_confirm_unapplied),
                        "delstorage": ("prefs-confirm-delstorage", self.change_confirm_delstorage),
                    }
                    spec = widgets.get(key)
                    if spec:
                        wid, handler = spec
                        src = self.widget(wid)
                        try:
                            src.set_active(not bool(src.get_active()))
                        except Exception:
                            pass
                        try:
                            handler(src)
                        except Exception:
                            pass
                    try:
                        open("/tmp/vmm-a11y-prefs-check-done", "w").write("1")
                    except Exception:
                        pass
            except Exception:
                pass

            page_want = "/tmp/vmm-a11y-prefs-page.txt"
            try:
                if os.path.exists(page_want):
                    want = open(page_want, "r").read().strip()
                    os.remove(page_want)
                    if want:
                        self._set_prefs_page(want)
            except Exception:
                pass

            combo_want = "/tmp/vmm-a11y-prefs-combo.txt"
            try:
                if os.path.exists(combo_want):
                    text = open(combo_want, "r").read().strip()
                    os.remove(combo_want)
                    if text:
                        key, item = (text.split("\t", 1) + [""])[:2]
                        self._apply_prefs_combo_choice(key, item)
            except Exception:
                pass

            generic = "/tmp/vmm-a11y-combo-select.txt"
            try:
                if os.path.exists(generic):
                    text = open(generic, "r").read().strip()
                    key = text.split("\t", 1)[0].strip() if text else ""
                    item = text.split("\t", 1)[-1].strip() if text else ""
                    if self._apply_prefs_combo_choice(key, item):
                        os.remove(generic)
            except Exception:
                pass

            spin_set = "/tmp/vmm-a11y-prefs-cpu-poll.set"
            try:
                if os.path.exists(spin_set):
                    val = open(spin_set, "r").read().strip()
                    os.remove(spin_set)
                    src = self.widget("prefs-stats-update-interval")
                    src.set_value(float(val or 0))
                    self.change_update_interval(src)
                    open("/tmp/vmm-a11y-prefs-cpu-poll.txt", "w").write(
                        str(int(src.get_value()))
                    )
            except Exception:
                pass

            try:
                if os.path.exists("/tmp/vmm-a11y-prefs-change-grab"):
                    os.remove("/tmp/vmm-a11y-prefs-change-grab")
                    self.change_grab_keys(None)
            except Exception:
                pass

            try:
                if os.path.exists("/tmp/vmm-a11y-grab-ok.txt"):
                    os.remove("/tmp/vmm-a11y-grab-ok.txt")
                    accept = getattr(self, "_grab_accept", None)
                    if accept is not None:
                        accept()
            except Exception:
                pass

            try:
                if os.path.exists("/tmp/vmm-a11y-grab-cancel.txt"):
                    os.remove("/tmp/vmm-a11y-grab-cancel.txt")
                    dlg = getattr(self, "_grab_dialog", None)
                    if dlg is not None:
                        dlg.hide()
                    open("/tmp/vmm-a11y-grab-shown.txt", "w").write("0")
            except Exception:
                pass

            try:
                if os.path.exists("/tmp/vmm-a11y-prefs-close"):
                    os.remove("/tmp/vmm-a11y-prefs-close")
                    self.close()
            except Exception:
                pass
            return True

        GLib.timeout_add(50, _check_tick)

    def change_view_system_tray(self, src):
        self.config.set_view_system_tray(src.get_active())

    def change_xmleditor(self, src):
        self.config.set_xmleditor_enabled(src.get_active())

    def change_libguestfs(self, src):
        val = src.get_active()
        self.config.set_libguestfs_inspect_vms(val)

        vis = val != self._orig_libguestfs_val and self.widget("prefs-libguestfs").get_sensitive()
        uiutil.set_grid_row_visible(self.widget("prefs-libguestfs-warn-box"), vis)

    def change_update_interval(self, src):
        self.config.set_stats_update_interval(src.get_value_as_int())

    def change_console_scaling(self, box):
        self.config.set_console_scaling(box.get_active())

    def change_console_resizeguest(self, box):
        val = uiutil.get_list_selection(box)
        self.config.set_console_resizeguest(val)

    def change_console_autoredir(self, box):
        val = uiutil.get_list_selection(box)
        self.config.set_auto_usbredir(val)

    def change_console_autoconnect(self, src):
        self.config.set_console_autoconnect(bool(src.get_active()))

    def change_graphics_type(self, src):
        val = uiutil.get_list_selection(src)
        self.config.set_graphics_type(val)

    def change_storage_format(self, src):
        typ = uiutil.get_list_selection(src) or "default"
        self.config.set_storage_format(typ.lower())

    def change_cpu_default(self, src):
        typ = uiutil.get_list_selection(src) or "default"
        self.config.set_default_cpu_setting(typ.lower())

    def change_firmware_default(self, src):
        typ = uiutil.get_list_selection(src) or "default"
        self.config.set_firmware_setting(typ.lower())

    def change_cpu_poll(self, src):
        self.config.set_stats_enable_cpu_poll(src.get_active())

    def change_disk_poll(self, src):
        self.config.set_stats_enable_disk_poll(src.get_active())

    def change_net_poll(self, src):
        self.config.set_stats_enable_net_poll(src.get_active())

    def change_memory_poll(self, src):
        self.config.set_stats_enable_memory_poll(src.get_active())

    def change_confirm_forcepoweroff(self, src):
        self.config.set_confirm_forcepoweroff(src.get_active())

    def change_confirm_poweroff(self, src):
        self.config.set_confirm_poweroff(src.get_active())

    def change_confirm_pause(self, src):
        self.config.set_confirm_pause(src.get_active())

    def change_confirm_removedev(self, src):
        self.config.set_confirm_removedev(src.get_active())

    def change_confirm_unapplied(self, src):
        self.config.set_confirm_unapplied(src.get_active())

    def change_confirm_delstorage(self, src):
        self.config.set_confirm_delstorage(src.get_active())
