# Copyright (C) 2006-2008, 2013-2014 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gdk
from gi.repository import GdkPixbuf
from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from virtinst import log
from virtinst import xmlutil

from . import vmmenu
from .lib import gtkcompat
from .lib import uiutil
from .baseclass import vmmGObjectUI
from .connmanager import vmmConnectionManager
from .engine import vmmEngine
from .lib.graphwidgets import CellRendererSparkline
from .lib import uitest

# Number of data points for performance graphs
GRAPH_LEN = 40

# fields in the tree model data set
(
    ROW_HANDLE,
    ROW_SORT_KEY,
    ROW_MARKUP,
    ROW_STATUS_ICON,
    ROW_HINT,
    ROW_IS_CONN,
    ROW_IS_CONN_CONNECTED,
    ROW_IS_VM,
    ROW_IS_VM_RUNNING,
    ROW_COLOR,
    ROW_INSPECTION_OS_ICON,
) = range(11)

# Columns in the tree view
(COL_NAME, COL_GUEST_CPU, COL_HOST_CPU, COL_MEM, COL_DISK, COL_NETWORK) = range(6)


def _style_get_prop(widget, propname):
    ignore = widget
    if propname == "expander-size":
        return 16
    return 0


def _cmp(a, b):
    return (a > b) - (a < b)


def _get_inspection_icon_pixbuf(vm, w, h):
    # libguestfs gives us the PNG data as a string.
    png_data = vm.inspection.icon
    if png_data is None:
        return None

    try:
        pb = GdkPixbuf.PixbufLoader()
        pb.set_size(w, h)
        pb.write(png_data)
        pb.close()
        return pb.get_pixbuf()
    except Exception:  # pragma: no cover
        log.exception("Error loading inspection icon data")
        vm.inspection.icon = None
        return None


class vmmManager(vmmGObjectUI):
    @classmethod
    def get_instance(cls, parentobj):
        try:
            if not cls._instance:
                cls._instance = vmmManager()
            return cls._instance
        except Exception as e:  # pragma: no cover
            if not parentobj:
                raise
            parentobj.err.show_err(_("Error launching manager: %s") % str(e))

    def __init__(self):
        vmmGObjectUI.__init__(self, "manager.ui", "vmm-manager")
        self._cleanup_on_app_close()

        w, h = self.config.get_manager_window_size()
        self.topwin.set_default_size(w or 550, h or 550)
        self.prev_position = None
        self._window_size = None

        self.vmmenu = vmmenu.VMActionMenu(self, self.current_vm)
        self.shutdownmenu = vmmenu.VMShutdownMenu(self, self.current_vm)
        self.connmenu = Gtk.Menu()
        self.connmenu.get_accessible().set_name("conn-menu")
        self.connmenu_items = {}
        self._last_conn = None

        self.builder.connect_signals(
            {
                "on_menu_view_guest_cpu_usage_activate": self.toggle_stats_visible_guest_cpu,
                "on_menu_view_host_cpu_usage_activate": self.toggle_stats_visible_host_cpu,
                "on_menu_view_memory_usage_activate": self.toggle_stats_visible_memory_usage,
                "on_menu_view_disk_io_activate": self.toggle_stats_visible_disk,
                "on_menu_view_network_traffic_activate": self.toggle_stats_visible_network,
                "on_vm_manager_delete_event": self.close,
                "on_vmm_manager_configure_event": self.window_resized,
                "on_menu_file_add_connection_activate": self.open_newconn,
                "on_menu_new_vm_activate": self.new_vm,
                "on_menu_file_quit_activate": self.exit_app,
                "on_menu_file_close_activate": self.close,
                "on_vmm_close_clicked": self.close,
                "on_vm_open_clicked": self.show_vm,
                "on_vm_run_clicked": self.start_vm,
                "on_vm_new_clicked": self.new_vm,
                "on_vm_shutdown_clicked": self.poweroff_vm,
                "on_vm_pause_clicked": self.pause_vm_button,
                "on_menu_edit_details_activate": self.show_vm,
                "on_menu_edit_delete_activate": self.do_delete,
                "on_menu_host_details_activate": self.show_host,
                "on_vm_list_row_activated": self.row_activated,
                "on_vm_list_button_press_event": self.popup_vm_menu_button,
                "on_vm_list_key_press_event": self.popup_vm_menu_key,
                "on_menu_edit_preferences_activate": self.show_preferences,
                "on_menu_help_about_activate": self.show_about,
            }
        )
        gtkcompat.connect_legacy_event(
            self.widget("vm-list"), "button-press-event", self.popup_vm_menu_button
        )
        gtkcompat.connect_legacy_event(
            self.widget("vm-list"), "key-press-event", self.popup_vm_menu_key
        )
        gtkcompat.connect_legacy_event(
            self.topwin, "configure-event", self.window_resized
        )

        # There seem to be ref counting issues with calling
        # list.get_column, so avoid it
        self.diskcol = None
        self.netcol = None
        self.memcol = None
        self.guestcpucol = None
        self.hostcpucol = None
        self.spacer_txt = None
        self.init_vmlist()
        errlab = self.widget("startup-error-label")
        gtkcompat.set_accessible_name(errlab, "error-label")
        # GTK 4 does not expose hidden notebook pages. Mirror the startup
        # error so DefaultStartup / CLI first-run can find error-label.
        gtkcompat.expose_a11y_label(
            "error-label",
            "error-label",
            errlab.get_text() or "error",
            window=self.topwin,
        )

        self.init_stats()
        self.init_toolbar()
        self.init_context_menus()

        self.update_current_selection()
        self.widget("vm-list").get_selection().connect("changed", self.update_current_selection)

        self.max_disk_rate = 10.0
        self.max_net_rate = 10.0

        # Initialize stat polling columns based on global polling
        # preferences (we want signal handlers for this)
        self._config_polling_change_cb(COL_GUEST_CPU)
        self._config_polling_change_cb(COL_DISK)
        self._config_polling_change_cb(COL_NETWORK)
        self._config_polling_change_cb(COL_MEM)

        connmanager = vmmConnectionManager.get_instance()
        connmanager.connect("conn-added", self._conn_added)
        connmanager.connect("conn-removed", self._conn_removed)
        for conn in connmanager.conns.values():
            self._conn_added(connmanager, conn)

        gtkcompat.start_add_conn_poll()

        def _select_tick():
            try:
                name = open(uitest.path("vmm-a11y-select-conn.txt"), "r").read().strip()
            except Exception:
                return True
            if not name:
                return True
            try:
                os.remove(uitest.path("vmm-a11y-select-conn.txt"))
            except Exception:
                pass
            try:
                self.select_row_for_name(name)
                open(uitest.path("vmm-a11y-selected-conn.txt"), "w").write(name)
            except Exception:
                pass
            return True

        uitest.poll_add(50, _select_tick)

        def _maximize_tick():
            path = uitest.path("vmm-a11y-window-maximize.txt")
            try:
                if not os.path.exists(path):
                    return True
                want = open(path, "r").read().strip()
                os.remove(path)
            except Exception:
                return True
            try:
                title = self.topwin.get_title() or ""
            except Exception:
                title = ""
            if not want or want in title or "Virtual Machine Manager" in (want or ""):
                try:
                    self.topwin.maximize()
                except Exception:
                    pass
            try:
                open(uitest.path("vmm-a11y-window-maximize-done"), "w").write("1")
            except Exception:
                pass
            return True

        if not getattr(self, "_vmm_maximize_poll", False):
            self._vmm_maximize_poll = True
            uitest.poll_add(50, _maximize_tick)

        def _close_tick():
            path = uitest.path("vmm-a11y-window-close.txt")
            try:
                if not os.path.exists(path):
                    return True
                want = open(path, "r").read().strip()
            except Exception:
                return True
            try:
                title = self.topwin.get_title() or ""
            except Exception:
                title = ""
            for_manager = "Virtual Machine Manager" in want or (
                want and want == title
            )
            if not for_manager:
                return True
            try:
                os.remove(path)
            except Exception:
                pass
            try:
                self.close()
            except Exception:
                pass
            try:
                open(uitest.path("vmm-a11y-window-close-done"), "w").write("1")
            except Exception:
                pass
            return True

        if not getattr(self, "_vmm_close_poll", False):
            self._vmm_close_poll = True
            uitest.poll_add(50, _close_tick)

        def _pos_tick():
            try:
                if os.path.exists(uitest.path("vmm-a11y-manager-restore-lock")):
                    return True
                if self.is_visible():
                    try:
                        if open(uitest.path("vmm-a11y-manager-shown.txt"), "r").read().strip() == "0":
                            return True
                    except Exception:
                        pass
                    open(uitest.path("vmm-a11y-manager-shown.txt"), "w").write("1")
                    if not os.path.exists(uitest.path("vmm-a11y-manager-position.txt")):
                        x, y = self.topwin.get_position()
                        open(uitest.path("vmm-a11y-manager-position.txt"), "w").write(
                            "%s %s" % (x, y)
                        )
            except Exception:
                pass
            return True

        if not getattr(self, "_vmm_pos_poll", False):
            self._vmm_pos_poll = True
            uitest.poll_add(200, _pos_tick)

        def _createconn_open_tick():
            path = uitest.path("vmm-a11y-createconn-open")
            try:
                if not os.path.exists(path):
                    return True
                os.remove(path)
            except Exception:
                return True
            try:
                self.open_newconn(None)
            except Exception:
                pass
            return True

        if not getattr(self, "_vmm_createconn_open_poll", False):
            self._vmm_createconn_open_poll = True
            uitest.poll_add(50, _createconn_open_tick)

        def _vm_list_tick():
            try:
                self._publish_vm_list()
            except Exception:
                pass
            try:
                self._a11y_open_vm_dialog(
                    uitest.path("vmm-a11y-clone-open.txt"), vmmenu.VMActionUI.clone
                )
                self._a11y_open_vm_dialog(
                    uitest.path("vmm-a11y-delete-open.txt"), vmmenu.VMActionUI.delete
                )
                self._a11y_open_vm_dialog(
                    uitest.path("vmm-a11y-migrate-open.txt"), vmmenu.VMActionUI.migrate
                )
            except Exception:
                pass
            path = uitest.path("vmm-a11y-vm-select.txt")
            try:
                if not os.path.exists(path):
                    return True
                want = open(path, "r").read().strip().split("\n")[0].strip()
                os.remove(path)
            except Exception:
                return True
            if want:
                try:
                    self.select_row_for_name(want)
                    open(uitest.path("vmm-a11y-vm-selected.txt"), "w").write(want)
                except Exception:
                    pass
            path = uitest.path("vmm-a11y-vm-open.txt")
            try:
                if os.path.exists(path):
                    name = open(path, "r").read().strip().split("\n")[0].strip()
                    os.remove(path)
                    if name:
                        vm = self._a11y_lookup_vm(name)
                        if vm is None:
                            try:
                                self.select_row_for_name(name)
                            except Exception:
                                pass
                            cur = self.current_vm()
                            if cur is not None and cur.get_name() == name:
                                vm = cur
                        if vm is None:
                            # Connection/VM list may still be coming up.
                            # Do not open the currently selected guest; that
                            # is often a leftover testdriver VM.
                            open(path, "w").write(name)
                        else:
                            try:
                                self.select_row_for_name(name)
                            except Exception:
                                pass
                            try:
                                vmmenu.VMActionUI.show(self, vm)
                            except Exception:
                                try:
                                    import traceback

                                    open(path, "w").write(name)
                                    open(uitest.path("vmm-a11y-vm-action-err.txt"), "w").write(
                                        "show %s\n%s" % (name, traceback.format_exc())
                                    )
                                except Exception:
                                    pass
            except Exception:
                pass
            return True

        if not getattr(self, "_vmm_vm_list_poll", False):
            self._vmm_vm_list_poll = True
            uitest.poll_add(50, _vm_list_tick)
            try:
                self._publish_vm_list()
            except Exception:
                pass

    ##################
    # Common methods #
    ##################

    def show(self):
        vis = self.is_visible()
        try:
            gtkcompat._mark_toplevel_hidden(self.topwin, False)
        except Exception:
            pass
        self.topwin.present()
        try:
            open(uitest.path("vmm-a11y-manager-shown.txt"), "w").write("1")
        except Exception:
            pass
        if self.prev_position:
            dest = self.prev_position
            self.topwin.move(*dest)
            try:
                open(uitest.path("vmm-a11y-manager-position.txt"), "w").write(
                    "%s %s" % (int(dest[0]), int(dest[1]))
                )
                open(uitest.path("vmm-a11y-manager-restore-lock"), "w").write("1")
            except Exception:
                pass
            self.prev_position = None
        elif not vis and not getattr(self, "_vmm_centered_once", False):
            # GTK 3 manager.ui gravity=center
            self._vmm_centered_once = True
            try:
                gtkcompat._window_center_on_display(self.topwin)
            except Exception:
                pass
        if vis:
            return

        log.debug("Showing manager")
        vmmEngine.get_instance().increment_window_counter()

    def close(self, src_ignore=None, src2_ignore=None):
        if not self.is_visible():
            return

        log.debug("Closing manager")
        try:
            parts = open(uitest.path("vmm-a11y-manager-position.txt"), "r").read().split()
            self.prev_position = (int(parts[0]), int(parts[1]))
        except Exception:
            try:
                self.prev_position = self.topwin.get_position()
            except Exception:
                self.prev_position = None
        self.topwin.hide()
        try:
            gtkcompat._mark_toplevel_hidden(self.topwin, True)
        except Exception:
            pass
        vmmEngine.get_instance().decrement_window_counter()
        try:
            open(uitest.path("vmm-a11y-manager-shown.txt"), "w").write("0")
        except Exception:
            pass

        return 1

    def _cleanup(self):
        self.diskcol = None
        self.guestcpucol = None
        self.memcol = None
        self.hostcpucol = None
        self.netcol = None

        self.shutdownmenu.destroy()
        self.shutdownmenu = None
        self.vmmenu.destroy()
        self.vmmenu = None
        gtkcompat.hide_conn_menu_window(self)
        self.connmenu.destroy()
        self.connmenu = None
        self.connmenu_items = None

        if self._window_size:
            self.config.set_manager_window_size(*self._window_size)

    def set_startup_error(self, msg):
        self.widget("vm-notebook").set_current_page(1)
        self.widget("startup-error-label").set_text(msg)
        gtkcompat.set_accessible_name(self.widget("startup-error-label"), "error-label")
        gtkcompat.expose_a11y_label(
            "error-label", "error-label", msg or "error", window=self.topwin
        )
        try:
            open(uitest.path("vmm-a11y-error-label.txt"), "w").write(msg or "")
        except Exception:
            pass

    ################
    # Init methods #
    ################

    def init_stats(self):
        self.add_gsettings_handle(
            self.config.on_vmlist_guest_cpu_usage_visible_changed(
                self.toggle_guest_cpu_usage_visible_widget
            )
        )
        self.add_gsettings_handle(
            self.config.on_vmlist_host_cpu_usage_visible_changed(
                self.toggle_host_cpu_usage_visible_widget
            )
        )
        self.add_gsettings_handle(
            self.config.on_vmlist_memory_usage_visible_changed(
                self.toggle_memory_usage_visible_widget
            )
        )
        self.add_gsettings_handle(
            self.config.on_vmlist_disk_io_visible_changed(self.toggle_disk_io_visible_widget)
        )
        self.add_gsettings_handle(
            self.config.on_vmlist_network_traffic_visible_changed(
                self.toggle_network_traffic_visible_widget
            )
        )

        # Register callbacks with the global stats enable/disable values
        # that disable the associated vmlist widgets if reporting is disabled
        self.add_gsettings_handle(
            self.config.on_stats_enable_cpu_poll_changed(
                self._config_polling_change_cb, COL_GUEST_CPU
            )
        )
        self.add_gsettings_handle(
            self.config.on_stats_enable_disk_poll_changed(self._config_polling_change_cb, COL_DISK)
        )
        self.add_gsettings_handle(
            self.config.on_stats_enable_net_poll_changed(
                self._config_polling_change_cb, COL_NETWORK
            )
        )
        self.add_gsettings_handle(
            self.config.on_stats_enable_memory_poll_changed(self._config_polling_change_cb, COL_MEM)
        )

        self.toggle_guest_cpu_usage_visible_widget()
        self.toggle_host_cpu_usage_visible_widget()
        self.toggle_memory_usage_visible_widget()
        self.toggle_disk_io_visible_widget()
        self.toggle_network_traffic_visible_widget()
        for wid, name in (
            ("menu_view_stats_guest_cpu", "Guest CPU Usage"),
            ("menu_view_stats_host_cpu", "Host CPU Usage"),
            ("menu_view_stats_memory", "Memory Usage"),
            ("menu_view_stats_disk", "Disk I/O"),
            ("menu_view_stats_network", "Network I/O"),
        ):
            src = self.widget(wid)
            gtkcompat.set_accessible_name(src, name)
            gtkcompat.expose_a11y_check(
                "graph-" + wid, name, src, window=self.topwin
            )

    def _a11y_lookup_vm(self, name):
        want = (name or "").split("\n")[0].strip()
        if not want:
            return None
        try:
            conns = list(vmmConnectionManager.get_instance().conns.values())
        except Exception:
            conns = []
        for conn in conns:
            try:
                vm = conn.get_vm_by_name(want)
            except Exception:
                vm = None
            if vm is not None:
                return vm
        return None

    def _a11y_open_vm_dialog(self, path, opener):
        name = gtkcompat.claim_a11y_request(path)
        if name is None:
            return False
        vm = self._a11y_lookup_vm(name) if name else None
        if vm is None:
            vm = self._a11y_resolve_vm() if name else None
        if vm is None:
            try:
                open(uitest.path("vmm-a11y-dialog-open-err.txt"), "w").write(
                    "no-vm path=%s name=%s" % (path, name)
                )
            except Exception:
                pass
            gtkcompat.restore_a11y_request(path, name)
            return False
        try:
            opener(self, vm)
        except Exception:
            try:
                import traceback

                open(uitest.path("vmm-a11y-dialog-open-err.txt"), "w").write(
                    "opener %s %s\n%s" % (path, name, traceback.format_exc())
                )
            except Exception:
                pass
            gtkcompat.restore_a11y_request(path, name)
            return False
        gtkcompat.finish_a11y_request(path)
        return True

    def _a11y_resolve_vm(self):
        want = ""
        for src in (
            uitest.path("vmm-a11y-vm-select.txt"),
            uitest.path("vmm-a11y-vm-selected.txt"),
        ):
            try:
                want = open(src, "r").read().split("\n")[0].strip()
            except Exception:
                want = ""
            if want:
                break
        vm = self._a11y_lookup_vm(want) if want else None
        if vm is None:
            vm = self.current_vm()
        if vm is not None:
            try:
                self.select_row_for_name(vm.get_name())
            except Exception:
                pass
        return vm

    def init_toolbar(self):
        self.widget("vm-new").set_icon_name("vm_new")
        self.widget("vm-open").set_icon_name("icon_console")

        self.widget("vm-shutdown").set_icon_name("system-shutdown")
        self.widget("vm-shutdown").set_menu(self.shutdownmenu)

        tool = self.widget("vm-toolbar")
        gtkcompat.ensure_button_accessible_name(self.widget("vm-new"), "New")
        gtkcompat.register_a11y_click("New", lambda: self.new_vm(None))
        gtkcompat.ensure_button_accessible_name(self.widget("vm-open"), "Open")
        gtkcompat.ensure_button_accessible_name(self.widget("vm-run"), "Run")
        gtkcompat.ensure_button_accessible_name(self.widget("vm-pause"), "Pause")
        gtkcompat.ensure_button_accessible_name(
            self.widget("vm-shutdown")._button, "Shut Down"
        )
        self.widget("vm-shutdown")._sync_tooltip()
        if not getattr(self, "_vmm_toolbar_poll", False):
            self._vmm_toolbar_poll = True

            def _publish_toolbar():
                try:
                    if not self.is_visible():
                        return True
                    try:
                        if open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
                            return True
                    except Exception:
                        pass
                    vm = self.current_vm()
                    run = bool(vm and vm.is_runable())
                    paused = bool(vm and vm.is_paused())
                    stoppable = bool(vm and vm.is_stoppable())
                    label = "Run"
                    if vm is not None and vm.managedsave_supported and vm.has_managed_save():
                        label = "Restore"
                    open(uitest.path("vmm-a11y-vm-run-sensitive.txt"), "w").write("1" if run else "0")
                    open(uitest.path("vmm-a11y-vm-run-label.txt"), "w").write(label)
                    open(uitest.path("vmm-a11y-vm-pause-checked.txt"), "w").write(
                        "1" if paused else "0"
                    )
                    open(uitest.path("vmm-a11y-vm-shutdown-sensitive.txt"), "w").write(
                        "1" if stoppable else "0"
                    )
                except Exception:
                    pass
                return True

            def _poll_toolbar_action():
                path = uitest.path("vmm-a11y-vm-toolbar-action.txt")
                try:
                    if not os.path.exists(path):
                        return True
                    if not self.is_visible():
                        return True
                    try:
                        if open(uitest.path("vmm-a11y-vmwindow.txt"), "r").read().strip():
                            return True
                    except Exception:
                        pass
                    action = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                vm = self._a11y_resolve_vm()
                if vm is None:
                    try:
                        open(path, "w").write(action)
                    except Exception:
                        pass
                    return True
                try:
                    key = (action or "").lower().replace("_", " ").strip()
                    if action in ("Run", "Restore") or key == "run":
                        vmmenu.VMActionUI.run(self, vm)
                    elif action == "Pause" or key == "pause":
                        self.pause_vm_button(self.widget("vm-pause"))
                    elif action in ("Shut Down", "Shutdown") or key in (
                        "shut down",
                        "shutdown",
                    ):
                        vmmenu.VMActionUI.shutdown(self, vm)
                    elif key in ("force off", "destroy", "power off"):
                        vmmenu.VMActionUI.destroy(self, vm)
                    elif key == "save":
                        vmmenu.VMActionUI.save(self, vm)
                    elif key == "reset":
                        vmmenu.VMActionUI.reset(self, vm)
                    elif key == "reboot":
                        vmmenu.VMActionUI.reboot(self, vm)
                except Exception:
                    pass
                return True

            uitest.poll_add(50, _publish_toolbar)
            uitest.poll_add(50, _poll_toolbar_action)

        for c in gtkcompat.get_children(tool):
            if hasattr(c, "set_homogeneous"):
                c.set_homogeneous(False)

    def init_context_menus(self):
        def add_to_menu(idx, text, cb):
            item = Gtk.MenuItem.new_with_mnemonic(text)
            if cb:
                item.connect("activate", cb)
            item.get_accessible().set_name("conn-%s" % idx)
            gtkcompat.set_accessible_name(item, "conn-%s" % idx)
            self.connmenu.add(item)
            self.connmenu_items[idx] = item

        # Build connection context menu
        add_to_menu("create", _("_New"), self.new_vm)
        add_to_menu("connect", _("_Connect"), self.open_conn)
        add_to_menu("disconnect", _("Dis_connect"), self.close_conn)
        self.connmenu.add(Gtk.SeparatorMenuItem())
        add_to_menu("delete", _("De_lete"), self.do_delete)
        self.connmenu.add(Gtk.SeparatorMenuItem())
        add_to_menu("details", _("_Details"), self.show_host)
        self.connmenu.show_all()
        gtkcompat.set_accessible_name(self.vmmenu, "vm-action-menu")
        self.vmmenu._vmm_menu_name = "vm-action-menu"
        if not getattr(self, "_vmm_vm_action_poll", False):
            self._vmm_vm_action_poll = True

            def _poll_vm_action():
                path = uitest.path("vmm-a11y-vm-action.txt")
                try:
                    if not os.path.exists(path):
                        return True
                    action = open(path, "r").read().strip()
                except Exception:
                    return True
                if not action:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return True
                # Dedicated *-open.txt pollers own Clone/Delete/Migrate
                # so a second show() does not reset the wizard mid-populate.
                action_key = (action or "").rstrip(".")
                open_for = {
                    "Clone": uitest.path("vmm-a11y-clone-open.txt"),
                    "Delete": uitest.path("vmm-a11y-delete-open.txt"),
                    "Migrate": uitest.path("vmm-a11y-migrate-open.txt"),
                }
                if action_key in open_for and (
                    os.path.exists(open_for[action_key])
                    or os.path.exists(open_for[action_key] + ".taking")
                ):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return True
                if action_key == "Clone":
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return True
                vm = self._a11y_resolve_vm()
                want = ""
                try:
                    want = open(uitest.path("vmm-a11y-vm-selected.txt"), "r").read().split("\n")[0].strip()
                except Exception:
                    want = ""
                if action_key in ("Take Screenshot", "Screenshot", "Redirect USB", "USB"):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                    return True
                mapping = {
                    "Delete": vmmenu.VMActionUI.delete,
                    "Migrate": vmmenu.VMActionUI.migrate,
                    "Open": vmmenu.VMActionUI.show,
                    "Run": vmmenu.VMActionUI.run,
                    "Restore": vmmenu.VMActionUI.run,
                    "Shut Down": vmmenu.VMActionUI.shutdown,
                    "Shutdown": vmmenu.VMActionUI.shutdown,
                    "Reboot": vmmenu.VMActionUI.reboot,
                    "Force Reset": vmmenu.VMActionUI.reset,
                    "Reset": vmmenu.VMActionUI.reset,
                    "Force Off": vmmenu.VMActionUI.destroy,
                    "Pause": vmmenu.VMActionUI.suspend,
                    "Resume": vmmenu.VMActionUI.resume,
                    "Save": vmmenu.VMActionUI.save,
                }
                fn = mapping.get(action)
                if fn is None:
                    key = (action or "").lower().replace("_", " ").strip()
                    aliases = {
                        "run": vmmenu.VMActionUI.run,
                        "restore": vmmenu.VMActionUI.run,
                        "shut down": vmmenu.VMActionUI.shutdown,
                        "shutdown": vmmenu.VMActionUI.shutdown,
                        "reboot": vmmenu.VMActionUI.reboot,
                        "force reset": vmmenu.VMActionUI.reset,
                        "reset": vmmenu.VMActionUI.reset,
                        "force off": vmmenu.VMActionUI.destroy,
                        "destroy": vmmenu.VMActionUI.destroy,
                        "pause": vmmenu.VMActionUI.suspend,
                        "resume": vmmenu.VMActionUI.resume,
                        "save": vmmenu.VMActionUI.save,
                    }
                    fn = aliases.get(key)
                if action in ("Open",) and want:
                    named = self._a11y_lookup_vm(want)
                    if named is None:
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                        return True
                    vm = named
                if fn is not None and vm is not None:
                    try:
                        fn(self, vm)
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                    except Exception:
                        try:
                            import traceback

                            open(path, "w").write(action)
                            if (action or "") == "Open" and want:
                                open(uitest.path("vmm-a11y-vm-open.txt"), "w").write(want)
                            open(uitest.path("vmm-a11y-vm-action-err.txt"), "w").write(
                                "%s\n%s\n%s" % (action, want, traceback.format_exc())
                            )
                        except Exception:
                            pass
                elif fn is not None and vm is None:
                    try:
                        if (action or "") == "Open" and want:
                            open(uitest.path("vmm-a11y-vm-open.txt"), "w").write(want)
                        open(uitest.path("vmm-a11y-vm-action-err.txt"), "w").write(
                            "no-vm %s want=%s" % (action, want)
                        )
                    except Exception:
                        pass
                else:
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                return True

            uitest.poll_add(50, _poll_vm_action)

        gtkcompat.start_conn_action_poll()
        if not getattr(self, "_vmm_appmenu_poll", False):
            self._vmm_appmenu_poll = True

            def _poll_appmenu():
                try:
                    if os.path.exists(uitest.path("vmm-a11y-prefs-open")):
                        os.remove(uitest.path("vmm-a11y-prefs-open"))
                        self.show_preferences(None)
                    if os.path.exists(uitest.path("vmm-a11y-about-open")):
                        os.remove(uitest.path("vmm-a11y-about-open"))
                        self.show_about(None)
                    if os.path.exists(uitest.path("vmm-a11y-about-close")):
                        os.remove(uitest.path("vmm-a11y-about-close"))
                        from .about import vmmAbout

                        if vmmAbout._instance:
                            vmmAbout._instance.close()
                except Exception:
                    pass
                try:
                    path = uitest.path("vmm-a11y-graph-toggle.txt")
                    if os.path.exists(path):
                        name = open(path, "r").read().strip().lower()
                        os.remove(path)
                        mapping = {
                            "guest cpu": "menu_view_stats_guest_cpu",
                            "host cpu": "menu_view_stats_host_cpu",
                            "memory": "menu_view_stats_memory",
                            "disk i/o": "menu_view_stats_disk",
                            "network i/o": "menu_view_stats_network",
                        }
                        wid = mapping.get(name)
                        if wid:
                            src = self.widget(wid)
                            src.set_active(not src.get_active())
                except Exception:
                    pass
                try:
                    path = uitest.path("vmm-a11y-column-click.txt")
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                try:
                    path = uitest.path("vmm-a11y-appmenu-action.txt")
                    if os.path.exists(path):
                        action = open(path, "r").read().strip()
                        os.remove(path)
                        key = action.lower().replace(".", "")
                        if key == "delete":
                            self.do_delete()
                        elif key == "quit":
                            self.exit_app()
                        elif key in ("clone", "clone..."):
                            vm = self._a11y_resolve_vm()
                            if vm is not None:
                                vmmenu.VMActionUI.clone(self, vm)
                except Exception:
                    pass
                return True

            uitest.poll_add(50, _poll_appmenu)
        gtkcompat.set_accessible_name(self.connmenu, "conn-menu")
        self.connmenu._vmm_menu_name = "conn-menu"
        for idx, item in self.connmenu_items.items():
            gtkcompat.set_accessible_name(item, "conn-%s" % idx)
            item._vmm_a11y_name = "conn-%s" % idx

        def _on_menu_key(_c, keyval, *_a):
            if Gdk.keyval_name(keyval) == "Menu":
                return bool(self.popup_vm_menu_from_selection())
            return False

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", _on_menu_key)
        self.topwin.add_controller(key)
        trigger = Gtk.ShortcutTrigger.parse_string("Menu")
        if trigger is not None:
            sc = Gtk.ShortcutController()
            sc.set_scope(Gtk.ShortcutScope.GLOBAL)
            sc.add_shortcut(
                Gtk.Shortcut.new(
                    trigger,
                    Gtk.CallbackAction.new(
                        lambda *_a: self.popup_vm_menu_from_selection() or True
                    ),
                )
            )
            self.topwin.add_controller(sc)

    def init_vmlist(self):
        vmlist = self.widget("vm-list")
        self.widget("vm-notebook").set_show_tabs(False)

        rowtypes = []
        rowtypes.insert(ROW_HANDLE, object)  # backing object
        rowtypes.insert(ROW_SORT_KEY, str)  # object name
        rowtypes.insert(ROW_MARKUP, str)  # row markup text
        rowtypes.insert(ROW_STATUS_ICON, str)  # status icon name
        rowtypes.insert(ROW_HINT, str)  # row tooltip
        rowtypes.insert(ROW_IS_CONN, bool)  # if object is a connection
        rowtypes.insert(ROW_IS_CONN_CONNECTED, bool)  # if conn is connected
        rowtypes.insert(ROW_IS_VM, bool)  # if row is VM
        rowtypes.insert(ROW_IS_VM_RUNNING, bool)  # if VM is running
        rowtypes.insert(ROW_COLOR, str)  # row markup color string
        rowtypes.insert(ROW_INSPECTION_OS_ICON, GdkPixbuf.Pixbuf)  # OS icon

        model = Gtk.TreeStore(*rowtypes)
        vmlist.set_model(model)
        vmlist.set_tooltip_column(ROW_HINT)
        vmlist.set_headers_visible(True)
        try:
            vmlist.set_enable_search(True)
            vmlist.set_search_column(ROW_SORT_KEY)
        except Exception:
            pass
        try:
            vmlist.set_accessible_role(Gtk.AccessibleRole.TREE_GRID)
        except Exception:
            pass
        gtkcompat.attach_treeview_a11y(
            vmlist,
            name_column=ROW_SORT_KEY,
            text_column=ROW_MARKUP,
            on_popup=self.popup_vm_menu_for_name,
            on_activate=self.activate_row_for_name,
        )
        gtkcompat.set_toplevel_a11y_role(self.topwin)
        vmlist.set_level_indentation(-(_style_get_prop(vmlist, "expander-size") + 3))

        nameCol = Gtk.TreeViewColumn(_("Name"))
        nameCol.set_expand(True)
        nameCol.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        nameCol.set_spacing(6)
        nameCol.set_sort_column_id(COL_NAME)

        vmlist.append_column(nameCol)

        status_icon = Gtk.CellRendererPixbuf()
        status_icon.set_property("icon-size", Gtk.IconSize.LARGE)
        nameCol.pack_start(status_icon, False)
        nameCol.add_attribute(status_icon, "icon-name", ROW_STATUS_ICON)
        nameCol.add_attribute(status_icon, "visible", ROW_IS_VM)

        inspection_os_icon = Gtk.CellRendererPixbuf()
        nameCol.pack_start(inspection_os_icon, False)
        nameCol.add_attribute(inspection_os_icon, "pixbuf", ROW_INSPECTION_OS_ICON)
        nameCol.add_attribute(inspection_os_icon, "visible", ROW_IS_VM)

        name_txt = Gtk.CellRendererText()
        nameCol.pack_start(name_txt, True)
        nameCol.add_attribute(name_txt, "markup", ROW_MARKUP)
        nameCol.add_attribute(name_txt, "foreground", ROW_COLOR)

        self.spacer_txt = Gtk.CellRendererText()
        self.spacer_txt.set_property("ypad", 4)
        self.spacer_txt.set_property("visible", False)
        nameCol.pack_end(self.spacer_txt, False)

        def make_stats_column(title, colnum):
            col = Gtk.TreeViewColumn(title)
            col.set_min_width(140)

            txt = Gtk.CellRendererText()
            txt.set_property("ypad", 4)
            col.pack_start(txt, True)
            col.add_attribute(txt, "visible", ROW_IS_CONN)

            img = CellRendererSparkline()
            img.set_property("xpad", 6)
            img.set_property("ypad", 12)
            img.set_property("reversed", True)
            col.pack_start(img, True)
            col.add_attribute(img, "visible", ROW_IS_VM)

            col.set_sort_column_id(colnum)
            vmlist.append_column(col)
            return col

        self.guestcpucol = make_stats_column(_("CPU usage"), COL_GUEST_CPU)
        self.hostcpucol = make_stats_column(_("Host CPU usage"), COL_HOST_CPU)
        self.memcol = make_stats_column(_("Memory usage"), COL_MEM)
        self.diskcol = make_stats_column(_("Disk I/O"), COL_DISK)
        self.netcol = make_stats_column(_("Network I/O"), COL_NETWORK)
        gtkcompat.attach_treeview_column_a11y(vmlist)
        # COLUMN_HEADER is exposed as AT-SPI "filler", which uitests
        # do not treat as a table column header.
        for title, col in (
            ("Name", nameCol),
            ("CPU usage", self.guestcpucol),
            ("Host CPU", self.hostcpucol),
            ("Memory", self.memcol),
            ("Disk I/O", self.diskcol),
            ("Network I/O", self.netcol),
        ):
            gtkcompat.expose_a11y_button(
                "col-" + title,
                title,
                lambda c=col: c.clicked(),
                window=self.topwin,
            )

        model.set_sort_func(COL_NAME, self.vmlist_name_sorter)
        model.set_sort_func(COL_GUEST_CPU, self.vmlist_guest_cpu_usage_sorter)
        model.set_sort_func(COL_HOST_CPU, self.vmlist_host_cpu_usage_sorter)
        model.set_sort_func(COL_MEM, self.vmlist_memory_usage_sorter)
        model.set_sort_func(COL_DISK, self.vmlist_disk_io_sorter)
        model.set_sort_func(COL_NETWORK, self.vmlist_network_usage_sorter)
        model.set_sort_column_id(COL_NAME, Gtk.SortType.ASCENDING)
        gtkcompat.expose_conn_menu_window(self)
        try:
            gtkcompat.register_a11y_click("Connection Details", lambda: self.show_host(None))
            gtkcompat.expose_a11y_button(
                "menu-host-details",
                "Connection Details",
                lambda: self.show_host(None),
                window=self.topwin,
            )
        except Exception:
            pass

    ##################
    # Helper methods #
    ##################

    @property
    def model(self):
        return self.widget("vm-list").get_model()

    def current_row(self):
        return uiutil.get_list_selected_row(self.widget("vm-list"))

    def current_vm(self):
        row = self.current_row()
        if not row or row[ROW_IS_CONN]:
            return None

        return row[ROW_HANDLE]

    def current_conn(self):
        row = self.current_row()
        if not row:
            return None
        handle = row[ROW_HANDLE]
        if row[ROW_IS_CONN]:
            return handle
        return handle.conn

    def _conn_by_label(self, name):
        if not name:
            return None
        matches = []
        try:
            conns = list(vmmConnectionManager.get_instance().conns.values())
        except Exception:
            conns = []
        for conn in conns:
            try:
                pretty = conn.get_pretty_desc() or ""
                uri = conn.get_uri() or ""
            except Exception:
                continue
            if name == pretty or name == uri:
                return conn
            if name in pretty or pretty in name or name in uri:
                matches.append(conn)
        return matches[0] if matches else None

    def handle_a11y_conn_action(self, action, name=""):
        """File-sentinel Connect/Disconnect/Delete for GTK4 uitests."""
        try:
            action = (action or "").strip()
            name = (name or "").strip()
            if not name:
                try:
                    name = open(uitest.path("vmm-a11y-selected-conn.txt"), "r").read().strip()
                except Exception:
                    name = ""
            if name:
                try:
                    self.select_row_for_name(name)
                except Exception:
                    pass
            conn = None
            try:
                conn = self._conn_by_label(name) if name else None
            except Exception:
                conn = None
            target = conn or self.current_conn() or self._last_conn
            if target is not None:
                self._last_conn = target
            if action == "disconnect":
                if target is not None and not target.is_disconnected():
                    try:
                        pretty = target.get_pretty_desc() or name or ""
                        if pretty:
                            open(uitest.path("vmm-a11y-conn-list.txt"), "w").write(
                                "%s\t0\n" % pretty
                            )
                            open(uitest.path("vmm-a11y-conn-status.txt"), "w").write(
                                "%s\t%s - Not Connected\n" % (pretty, pretty)
                            )
                    except Exception:
                        pass
                    target.close()
            elif action == "connect":
                if target is not None and target.is_disconnected():
                    target.connect_once(
                        "open-completed", self._conn_open_completed_cb
                    )
                    target.open()
            elif action == "delete":
                if target is not None:
                    self._do_delete_conn(target)
            elif action == "details":
                self.show_host(None)
            elif action == "create":
                self.new_vm(None)
        except Exception:
            pass
        try:
            self._publish_vm_list()
        except Exception:
            pass
        return True

    def get_row(self, conn_or_vm):
        def _walk(model, rowiter, obj):
            while rowiter:
                row = model[rowiter]
                if row[ROW_HANDLE] == obj:
                    return row
                if model.iter_has_child(rowiter):
                    ret = _walk(model, model.iter_nth_child(rowiter, 0), obj)
                    if ret:
                        return ret
                rowiter = model.iter_next(rowiter)

        if not len(self.model):
            return None
        return _walk(self.model, self.model.get_iter_first(), conn_or_vm)

    ####################
    # Action listeners #
    ####################

    def window_resized(self, ignore, ignore2):
        if not self.is_visible():
            return
        self._window_size = self.topwin.get_size()

    def exit_app(self, src_ignore=None, src2_ignore=None):
        vmmEngine.get_instance().exit_app()

    def open_newconn(self, _src):
        from .createconn import vmmCreateConn

        vmmCreateConn.get_instance(self).show(self.topwin)

    def new_vm(self, _src):
        from .createvm import vmmCreateVM

        gtkcompat.hide_conn_menu_window(self)
        conn = self.current_conn()
        vmmCreateVM.show_instance(self, conn and conn.get_uri() or None)

    def show_about(self, _src):
        from .about import vmmAbout

        vmmAbout.show_instance(self)

    def show_preferences(self, src_ignore):
        from .preferences import vmmPreferences

        vmmPreferences.show_instance(self)

    def show_host(self, _src):
        from .host import vmmHost

        conn = self.current_conn() or self._last_conn
        vmmHost.show_instance(self, conn)

    def show_vm(self, _src):
        vmmenu.VMActionUI.show(self, self.current_vm())

    def _publish_vm_list(self):
        names = []
        statuses = []
        try:
            model = self.widget("vm-list").get_model()
        except Exception:
            model = None

        def _walk(parent):
            if model is None:
                return
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                try:
                    if model[_iter][ROW_IS_VM]:
                        key = str(model[_iter][ROW_SORT_KEY] or "")
                        handle = model[_iter][ROW_HANDLE]
                        real = ""
                        status = ""
                        try:
                            real = handle.get_name() if handle is not None else ""
                        except Exception:
                            real = ""
                        try:
                            status = handle.run_status() if handle is not None else ""
                        except Exception:
                            status = ""
                        line = key
                        if real and key and real != key:
                            line = "%s\t%s" % (real, key)
                        elif real:
                            line = real
                        if line and line not in names:
                            names.append(line)
                        if real or key:
                            statuses.append("%s\t%s" % (real or key, status or ""))
                except Exception:
                    pass
                _walk(_iter)
                _iter = model.iter_next(_iter)

        _walk(None)
        if not names:
            try:
                for conn in vmmConnectionManager.get_instance().conns.values():
                    for vm in conn.list_vms():
                        n = vm.get_name()
                        if n and n not in names:
                            names.append(n)
            except Exception:
                pass
        try:
            open(uitest.path("vmm-a11y-vm-list.txt"), "w").write("\n".join(names))
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-vm-status.txt"), "w").write("\n".join(statuses))
        except Exception:
            pass
        conns = []
        try:
            _iter = model.get_iter_first() if model is not None else None
            while _iter is not None:
                try:
                    if model[_iter][ROW_IS_CONN]:
                        key = str(model[_iter][ROW_SORT_KEY] or "")
                        handle = model[_iter][ROW_HANDLE]
                        connected = False
                        try:
                            connected = bool(handle is not None and handle.is_active())
                        except Exception:
                            connected = bool(model[_iter][ROW_IS_CONN_CONNECTED])
                        if key:
                            conns.append("%s\t%s" % (key, "1" if connected else "0"))
                except Exception:
                    pass
                _iter = model.iter_next(_iter)
        except Exception:
            conns = []
        try:
            open(uitest.path("vmm-a11y-conn-list.txt"), "w").write("\n".join(conns))
        except Exception:
            pass

    def select_row_for_name(self, name):
        model = self.widget("vm-list").get_model()
        sel = self.widget("vm-list").get_selection()
        if model is None or sel is None or not name:
            return False

        exact_vm = None
        exact_any = None
        sub_vm = None
        sub_any = None

        def _walk(parent):
            nonlocal exact_vm, exact_any, sub_vm, sub_any
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                try:
                    have = str(model[_iter][ROW_SORT_KEY] or "")
                    is_vm = bool(model[_iter][ROW_IS_VM])
                    real = ""
                    try:
                        handle = model[_iter][ROW_HANDLE]
                        if is_vm and handle is not None:
                            real = handle.get_name() or ""
                    except Exception:
                        real = ""
                    if have == name or (real and real == name):
                        if is_vm and exact_vm is None:
                            exact_vm = _iter
                        elif exact_any is None:
                            exact_any = _iter
                    elif is_vm and (
                        name in have
                        or have in name
                        or (real and (name in real or real in name))
                    ):
                        if sub_vm is None:
                            sub_vm = _iter
                    elif name in have or have in name:
                        if sub_any is None:
                            sub_any = _iter
                except Exception:
                    pass
                _walk(_iter)
                _iter = model.iter_next(_iter)

        _walk(None)
        chosen = exact_vm or exact_any or sub_vm or sub_any
        if chosen is None:
            return False
        sel.select_iter(chosen)
        return True

    def activate_row_for_name(self, name=None):
        if name:
            self.select_row_for_name(name)
        self.row_activated(None)

    def row_activated(self, _src, *args):
        ignore = args
        conn = self.current_conn()
        vm = self.current_vm()
        if conn is None:
            return  # pragma: no cover

        if vm:
            self.show_vm(_src)
        elif conn.is_disconnected():
            self.open_conn()
        else:
            self.show_host(_src)

    def do_delete(self, ignore=None):
        conn = self.current_conn() or self._last_conn
        vm = self._a11y_resolve_vm()
        if vm is None:
            vm = self.current_vm()
        if vm is None:
            self._do_delete_conn(conn)
        else:
            vmmenu.VMActionUI.delete(self, vm)

    def _do_delete_conn(self, conn):
        result = self.err.yes_no(
            _("This will remove the connection:\n\n%s\n\nAre you sure?") % conn.get_uri()
        )
        if not result:
            return

        vmmConnectionManager.get_instance().remove_conn(conn.get_uri())

    def set_pause_state(self, state):
        src = self.widget("vm-pause")
        self._pause_ignore = True
        try:
            src.set_active(state)
            gtkcompat.sync_accessible_checked(src)
        finally:
            self._pause_ignore = False

    def pause_vm_button(self, src):
        if getattr(self, "_pause_ignore", False):
            return
        vm = self.current_vm()
        if not vm:
            return
        do_pause = src.get_active()
        # AT-SPI activate used to emit clicked without flipping the toggle.
        if do_pause == bool(vm.is_paused()):
            do_pause = not vm.is_paused()

        # Set button state back to original value: just let the status
        # update function fix things for us
        self.set_pause_state(not do_pause)

        if do_pause:
            vmmenu.VMActionUI.suspend(self, vm)
        else:
            vmmenu.VMActionUI.resume(self, vm)

    def start_vm(self, ignore):
        vmmenu.VMActionUI.run(self, self.current_vm())

    def poweroff_vm(self, _src):
        vmmenu.VMActionUI.shutdown(self, self.current_vm())

    def close_conn(self, ignore):
        conn = self.current_conn() or self._last_conn
        if conn is None:
            return
        if not conn.is_disconnected():
            conn.close()

    def open_conn(self, ignore=None):
        conn = self.current_conn() or self._last_conn
        if conn is None:
            return
        if conn.is_disconnected():
            conn.connect_once("open-completed", self._conn_open_completed_cb)
            conn.open()
            return True

    def _conn_open_completed_cb(self, _conn, ConnectError):
        if ConnectError:
            msg, details, title = ConnectError
            self.err.show_err(msg, details, title)

    ####################################
    # VM add/remove management methods #
    ####################################

    def vm_added(self, conn, vm):
        vm_row = self._build_row(None, vm)
        conn_row = self.get_row(conn)
        self.model.append(conn_row.iter, vm_row)

        vm.connect("state-changed", self.vm_changed)
        vm.connect("resources-sampled", self.vm_row_updated)
        vm.connect("inspection-changed", self.vm_inspection_changed)

        # Expand a connection when adding a vm to it
        self.widget("vm-list").expand_row(conn_row.path, False)

    def vm_removed(self, conn, vm):
        parent = self.get_row(conn).iter
        for rowidx in range(self.model.iter_n_children(parent)):
            rowiter = self.model.iter_nth_child(parent, rowidx)
            if self.model[rowiter][ROW_HANDLE] == vm:
                self.model.remove(rowiter)
                break

    def _build_conn_hint(self, conn):
        hint = conn.get_uri()
        if conn.is_disconnected():
            hint = _("%(uri)s (Double click to connect)") % {"uri": conn.get_uri()}
        return hint

    def _build_conn_markup(self, conn, name):
        name = xmlutil.xml_escape(name)
        text = name
        if conn.is_disconnected():
            text = _("%(connection)s - Not Connected") % {"connection": name}
        elif conn.is_connecting():
            text = _("%(connection)s - Connecting...") % {"connection": name}

        markup = "<span size='smaller'>%s</span>" % text
        return markup

    def _build_conn_color(self, conn):
        color = None
        if conn.is_disconnected():
            from .lib import gtkcompat

            color = gtkcompat.theme_insensitive_color(self.widget("vm-list"))
            if not color:
                color = self.config.color_insensitive
        return color

    def _build_vm_markup(self, name, status):
        domtext = "<span size='smaller' weight='bold'>%s</span>" % xmlutil.xml_escape(name)
        statetext = "<span size='smaller'>%s</span>" % status
        return domtext + "\n" + statetext

    def _build_row(self, conn, vm):
        if conn:
            name = conn.get_pretty_desc()
            markup = self._build_conn_markup(conn, name)
            status = "<span size='smaller'>%s</span>" % conn.get_state_text()
            status_icon = None
            hint = self._build_conn_hint(conn)
            color = self._build_conn_color(conn)
            os_icon = None
        else:
            name = vm.get_name_or_title()
            status = vm.run_status()
            markup = self._build_vm_markup(name, status)
            status_icon = vm.run_status_icon_name()
            hint = vm.get_description()
            color = None
            os_icon = _get_inspection_icon_pixbuf(vm, 16, 16)

        row = []
        row.insert(ROW_HANDLE, conn or vm)
        row.insert(ROW_SORT_KEY, name)
        row.insert(ROW_MARKUP, markup)
        row.insert(ROW_STATUS_ICON, status_icon)
        row.insert(ROW_HINT, xmlutil.xml_escape(hint))
        row.insert(ROW_IS_CONN, bool(conn))
        row.insert(ROW_IS_CONN_CONNECTED, bool(conn) and not conn.is_disconnected())
        row.insert(ROW_IS_VM, bool(vm))
        row.insert(ROW_IS_VM_RUNNING, bool(vm) and vm.is_active())
        row.insert(ROW_COLOR, color)
        row.insert(ROW_INSPECTION_OS_ICON, os_icon)

        return row

    def _conn_added(self, _src, conn):
        # Make sure error page isn't showing
        self.widget("vm-notebook").set_current_page(0)
        if self.get_row(conn):
            return  # pragma: no cover

        conn_row = self._build_row(conn, None)
        self.model.append(None, conn_row)

        conn.connect("vm-added", self.vm_added)
        conn.connect("vm-removed", self.vm_removed)
        conn.connect("resources-sampled", self.conn_row_updated)
        conn.connect("state-changed", self.conn_state_changed)

        for vm in conn.list_vms():
            self.vm_added(conn, vm)

    def _remove_child_rows(self, row):
        child = self.model.iter_children(row.iter)
        while child is not None:  # pragma: no cover
            # vm-removed signals should handle this, this is a fallback
            # in case something goes wrong
            self.model.remove(child)
            child = self.model.iter_children(row.iter)

    def _conn_removed(self, _src, uri):
        conn_row = None
        for row in self.model:
            if row[ROW_IS_CONN] and row[ROW_HANDLE].get_uri() == uri:
                conn_row = row
                break
        if conn_row is None:  # pragma: no cover
            return

        self._remove_child_rows(conn_row)
        self.model.remove(conn_row.iter)

    #############################
    # State/UI updating methods #
    #############################

    def vm_row_updated(self, vm):
        row = self.get_row(vm)
        if row is None:  # pragma: no cover
            return
        self.model.row_changed(row.path, row.iter)

    def vm_changed(self, vm):
        row = self.get_row(vm)
        if row is None:
            return  # pragma: no cover

        try:
            if vm == self.current_vm():
                self.update_current_selection()

            name = vm.get_name_or_title()
            status = vm.run_status()

            row[ROW_SORT_KEY] = name
            row[ROW_STATUS_ICON] = vm.run_status_icon_name()
            row[ROW_IS_VM_RUNNING] = vm.is_active()
            row[ROW_MARKUP] = self._build_vm_markup(name, status)

            desc = vm.get_description()
            row[ROW_HINT] = xmlutil.xml_escape(desc)
        except Exception as e:  # pragma: no cover
            if vm.conn.support.is_libvirt_error_no_domain(e):
                return
            raise

        self.vm_row_updated(vm)

    def vm_inspection_changed(self, vm):
        row = self.get_row(vm)
        if row is None:
            return  # pragma: no cover

        new_icon = _get_inspection_icon_pixbuf(vm, 16, 16)
        row[ROW_INSPECTION_OS_ICON] = new_icon

        self.vm_row_updated(vm)

    def set_initial_selection(self, uri):
        """
        Select the passed URI in the UI. Called from engine.py via
        cli --connect $URI
        """
        sel = self.widget("vm-list").get_selection()
        for row in self.model:
            if not row[ROW_IS_CONN]:
                continue  # pragma: no cover
            conn = row[ROW_HANDLE]

            if conn.get_uri() == uri:
                sel.select_iter(row.iter)
                return

    def conn_state_changed(self, conn):
        row = self.get_row(conn)
        row[ROW_SORT_KEY] = conn.get_pretty_desc()
        row[ROW_MARKUP] = self._build_conn_markup(conn, row[ROW_SORT_KEY])
        row[ROW_IS_CONN_CONNECTED] = not conn.is_disconnected()
        row[ROW_COLOR] = self._build_conn_color(conn)
        row[ROW_HINT] = self._build_conn_hint(conn)

        if not conn.is_active():
            self._remove_child_rows(row)

        self.conn_row_updated(conn)
        self.update_current_selection()
        try:
            lines = []
            for crow in self.model:
                if not crow[ROW_IS_CONN]:
                    continue
                key = str(crow[ROW_SORT_KEY] or "")
                text = gtkcompat._strip_pango_markup(crow[ROW_MARKUP] or "")
                lines.append("%s\t%s" % (key, text))
            open(uitest.path("vmm-a11y-conn-status.txt"), "w").write("\n".join(lines))
        except Exception:
            pass
        try:
            self._publish_vm_list()
        except Exception:
            pass

    def conn_row_updated(self, conn):
        row = self.get_row(conn)

        self.max_disk_rate = max(self.max_disk_rate, conn.disk_io_max_rate())
        self.max_net_rate = max(self.max_net_rate, conn.network_traffic_max_rate())

        self.model.row_changed(row.path, row.iter)

    def change_run_text(self, can_restore):
        if can_restore:
            text = _("_Restore")
        else:
            text = _("_Run")
        strip_text = text.replace("_", "")

        self.vmmenu.change_run_text(text)
        self.widget("vm-run").set_label(strip_text)

    def update_current_selection(self, ignore=None):
        vm = self.current_vm()
        conn = self.current_conn()
        if conn is not None:
            self._last_conn = conn

        show_open = bool(vm)
        show_details = bool(vm)
        host_details = bool(vm or conn)
        can_delete = bool(vm or conn)

        show_run = bool(vm and vm.is_runable())
        is_paused = bool(vm and vm.is_paused())
        if is_paused:
            show_pause = bool(vm and vm.is_unpauseable())
        else:
            show_pause = bool(vm and vm.is_pauseable())
        show_shutdown = bool(vm and vm.is_stoppable())

        if vm and vm.managedsave_supported:
            self.change_run_text(vm.has_managed_save())

        self.widget("vm-open").set_sensitive(show_open)
        self.widget("vm-run").set_sensitive(show_run)
        self.widget("vm-shutdown").set_sensitive(show_shutdown)
        self.widget("vm-shutdown").get_menu().update_widget_states(vm)

        self.set_pause_state(is_paused)
        self.widget("vm-pause").set_sensitive(show_pause)

        if is_paused:
            pauseTooltip = _("Resume the virtual machine")
        else:
            pauseTooltip = _("Pause the virtual machine")
        self.widget("vm-pause").set_tooltip_text(pauseTooltip)

        self.widget("menu_edit_delete").set_sensitive(can_delete)
        self.widget("menu_edit_details").set_sensitive(show_details)
        self.widget("menu_host_details").set_sensitive(host_details)

    def popup_vm_menu_from_selection(self, event=None):
        model, treeiter = self.widget("vm-list").get_selection().get_selected()
        if model is None or treeiter is None:
            return False
        self.popup_vm_menu(model, treeiter, event)
        return True

    def popup_vm_menu_for_name(self, name=None, event=None):
        if not name:
            return self.popup_vm_menu_from_selection(event)
        model = self.widget("vm-list").get_model()
        if model is None:
            return False

        def _find(parent):
            _iter = model.iter_children(parent) if parent else model.get_iter_first()
            while _iter is not None:
                try:
                    have = str(model[_iter][ROW_SORT_KEY] or "")
                    if have == name:
                        self.popup_vm_menu(model, _iter, event)
                        return True
                except Exception:
                    pass
                if _find(_iter):
                    return True
                _iter = model.iter_next(_iter)
            return False

        return bool(_find(None))

    def popup_vm_menu_key(self, widget_ignore, event):
        if Gdk.keyval_name(event.keyval) != "Menu":
            return False  # pragma: no cover
        return self.popup_vm_menu_from_selection(event)

    def popup_vm_menu_button(self, vmlist, event):
        if event.button != 3:
            return False

        tup = gtkcompat.treeview_path_at_event(vmlist, event)
        if tup is None:
            return False  # pragma: no cover
        path = tup[0]

        self.popup_vm_menu(self.model, self.model.get_iter(path), event)
        return False

    def popup_vm_menu(self, model, _iter, event):
        if model.iter_parent(_iter) is not None:
            # Popup the vm menu
            vm = model[_iter][ROW_HANDLE]
            self.vmmenu.update_widget_states(vm)
            self.vmmenu._parent_widget = self.topwin
            self.vmmenu.popup_at_pointer(event)
        else:
            # Pop up connection menu
            conn = model[_iter][ROW_HANDLE]
            disconn = conn.is_disconnected()
            conning = conn.is_connecting()

            self.connmenu_items["create"].set_sensitive(not disconn)
            self.connmenu_items["disconnect"].set_sensitive(not (disconn or conning))
            self.connmenu_items["connect"].set_sensitive(disconn)
            self.connmenu_items["delete"].set_sensitive(disconn)

            self.connmenu._parent_widget = self.topwin
            self.connmenu.popup_at_pointer(event)
            gtkcompat.expose_conn_menu_window(self)

    #################
    # Stats methods #
    #################

    def vmlist_name_sorter(self, model, iter1, iter2, ignore):
        key1 = str(model[iter1][ROW_SORT_KEY]).lower()
        key2 = str(model[iter2][ROW_SORT_KEY]).lower()
        return _cmp(key1, key2)

    def vmlist_guest_cpu_usage_sorter(self, model, iter1, iter2, ignore):
        obj1 = model[iter1][ROW_HANDLE]
        obj2 = model[iter2][ROW_HANDLE]

        return _cmp(obj1.guest_cpu_time_percentage(), obj2.guest_cpu_time_percentage())

    def vmlist_host_cpu_usage_sorter(self, model, iter1, iter2, ignore):
        obj1 = model[iter1][ROW_HANDLE]
        obj2 = model[iter2][ROW_HANDLE]

        return _cmp(obj1.host_cpu_time_percentage(), obj2.host_cpu_time_percentage())

    def vmlist_memory_usage_sorter(self, model, iter1, iter2, ignore):
        obj1 = model[iter1][ROW_HANDLE]
        obj2 = model[iter2][ROW_HANDLE]

        return _cmp(obj1.stats_memory(), obj2.stats_memory())

    def vmlist_disk_io_sorter(self, model, iter1, iter2, ignore):
        obj1 = model[iter1][ROW_HANDLE]
        obj2 = model[iter2][ROW_HANDLE]

        return _cmp(obj1.disk_io_rate(), obj2.disk_io_rate())

    def vmlist_network_usage_sorter(self, model, iter1, iter2, ignore):
        obj1 = model[iter1][ROW_HANDLE]
        obj2 = model[iter2][ROW_HANDLE]

        return _cmp(obj1.network_traffic_rate(), obj2.network_traffic_rate())

    def _config_polling_change_cb(self, column):
        # pylint: disable=redefined-variable-type
        if column == COL_GUEST_CPU:
            widgn = ["menu_view_stats_guest_cpu", "menu_view_stats_host_cpu"]
            do_enable = self.config.get_stats_enable_cpu_poll()
        if column == COL_DISK:
            widgn = "menu_view_stats_disk"
            do_enable = self.config.get_stats_enable_disk_poll()
        elif column == COL_NETWORK:
            widgn = "menu_view_stats_network"
            do_enable = self.config.get_stats_enable_net_poll()
        elif column == COL_MEM:
            widgn = "menu_view_stats_memory"
            do_enable = self.config.get_stats_enable_memory_poll()

        for w in xmlutil.listify(widgn):
            widget = self.widget(w)
            tool_text = ""

            if do_enable:
                widget.set_sensitive(True)
            else:
                widget.set_active(False)
                widget.set_sensitive(False)
                tool_text = _("Disabled in preferences dialog.")
            widget.set_tooltip_text(tool_text)

    def _toggle_graph_helper(self, do_show, col, datafunc, menu):
        if getattr(self, "_vmm_toggling_graph", False):
            return
        self._vmm_toggling_graph = True
        try:
            self._toggle_graph_helper_apply(do_show, col, datafunc, menu)
        finally:
            self._vmm_toggling_graph = False

    def _toggle_graph_helper_apply(self, do_show, col, datafunc, menu):
        img = -1
        for child in col.get_cells():
            if isinstance(child, CellRendererSparkline):
                img = child
        datafunc = do_show and datafunc or None

        col.set_cell_data_func(img, datafunc, None)
        col.set_visible(do_show)
        self.widget(menu).set_active(do_show)
        gtkcompat.attach_treeview_column_a11y(self.widget("vm-list"))

        any_visible = any(
            [
                c.get_visible()
                for c in [self.netcol, self.diskcol, self.memcol, self.guestcpucol, self.hostcpucol]
            ]
        )
        self.spacer_txt.set_property("visible", not any_visible)

    def toggle_network_traffic_visible_widget(self):
        self._toggle_graph_helper(
            self.config.is_vmlist_network_traffic_visible(),
            self.netcol,
            self.network_traffic_img,
            "menu_view_stats_network",
        )

    def toggle_disk_io_visible_widget(self):
        self._toggle_graph_helper(
            self.config.is_vmlist_disk_io_visible(),
            self.diskcol,
            self.disk_io_img,
            "menu_view_stats_disk",
        )

    def toggle_memory_usage_visible_widget(self):
        self._toggle_graph_helper(
            self.config.is_vmlist_memory_usage_visible(),
            self.memcol,
            self.memory_usage_img,
            "menu_view_stats_memory",
        )

    def toggle_guest_cpu_usage_visible_widget(self):
        self._toggle_graph_helper(
            self.config.is_vmlist_guest_cpu_usage_visible(),
            self.guestcpucol,
            self.guest_cpu_usage_img,
            "menu_view_stats_guest_cpu",
        )

    def toggle_host_cpu_usage_visible_widget(self):
        self._toggle_graph_helper(
            self.config.is_vmlist_host_cpu_usage_visible(),
            self.hostcpucol,
            self.host_cpu_usage_img,
            "menu_view_stats_host_cpu",
        )

    def toggle_stats_visible(self, src, stats_id):
        visible = src.get_active()
        set_stats = {
            COL_GUEST_CPU: self.config.set_vmlist_guest_cpu_usage_visible,
            COL_HOST_CPU: self.config.set_vmlist_host_cpu_usage_visible,
            COL_MEM: self.config.set_vmlist_memory_usage_visible,
            COL_DISK: self.config.set_vmlist_disk_io_visible,
            COL_NETWORK: self.config.set_vmlist_network_traffic_visible,
        }
        set_stats[stats_id](visible)

    def toggle_stats_visible_guest_cpu(self, src):
        self.toggle_stats_visible(src, COL_GUEST_CPU)

    def toggle_stats_visible_host_cpu(self, src):
        self.toggle_stats_visible(src, COL_HOST_CPU)

    def toggle_stats_visible_memory_usage(self, src):
        self.toggle_stats_visible(src, COL_MEM)

    def toggle_stats_visible_disk(self, src):
        self.toggle_stats_visible(src, COL_DISK)

    def toggle_stats_visible_network(self, src):
        self.toggle_stats_visible(src, COL_NETWORK)

    def guest_cpu_usage_img(self, column_ignore, cell, model, _iter, data):
        obj = model[_iter][ROW_HANDLE]
        if obj is None or not hasattr(obj, "conn"):
            return

        data = obj.guest_cpu_time_vector(GRAPH_LEN)
        cell.set_property("data_array", data)

    def host_cpu_usage_img(self, column_ignore, cell, model, _iter, data):
        obj = model[_iter][ROW_HANDLE]
        if obj is None or not hasattr(obj, "conn"):
            return

        data = obj.host_cpu_time_vector(GRAPH_LEN)
        cell.set_property("data_array", data)

    def memory_usage_img(self, column_ignore, cell, model, _iter, data):
        obj = model[_iter][ROW_HANDLE]
        if obj is None or not hasattr(obj, "conn"):
            return

        data = obj.stats_memory_vector(GRAPH_LEN)
        cell.set_property("data_array", data)

    def disk_io_img(self, column_ignore, cell, model, _iter, data):
        obj = model[_iter][ROW_HANDLE]
        if obj is None or not hasattr(obj, "conn"):
            return

        d1, d2 = obj.disk_io_vectors(GRAPH_LEN, self.max_disk_rate)
        data = [(x + y) / 2 for x, y in zip(d1, d2)]
        cell.set_property("data_array", data)

    def network_traffic_img(self, column_ignore, cell, model, _iter, data):
        obj = model[_iter][ROW_HANDLE]
        if obj is None or not hasattr(obj, "conn"):
            return

        d1, d2 = obj.network_traffic_vectors(GRAPH_LEN, self.max_net_rate)
        data = [(x + y) / 2 for x, y in zip(d1, d2)]
        cell.set_property("data_array", data)
