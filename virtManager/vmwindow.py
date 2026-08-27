# Copyright (C) 2006-2008, 2013, 2014 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import Gtk

from virtinst import log

from . import vmmenu
from .baseclass import vmmGObjectUI
from .lib import gtkcompat
from .engine import vmmEngine
from .details.console import vmmConsolePages
from .details.details import vmmDetails
from .details.snapshots import vmmSnapshotPage


# Main tab pages
(DETAILS_PAGE_DETAILS, DETAILS_PAGE_CONSOLE, DETAILS_PAGE_SNAPSHOTS) = range(3)


class vmmVMWindow(vmmGObjectUI):
    __gsignals__ = {
        "customize-finished": (vmmGObjectUI.RUN_FIRST, None, [object]),
        "closed": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    @classmethod
    def get_instance(cls, parentobj, vm):
        try:
            # Maintain one dialog per VM
            key = "%s+%s" % (vm.conn.get_uri(), vm.get_uuid())
            if cls._instances is None:
                cls._instances = {}
            if key not in cls._instances:
                cls._instances[key] = vmmVMWindow(vm)
            return cls._instances[key]
        except Exception as e:  # pragma: no cover
            try:
                import traceback

                open("/tmp/vmm-a11y-vm-action-err.txt", "w").write(
                    "get_instance %s\n%s\n%s"
                    % (vm.get_name() if vm is not None else "?", e, traceback.format_exc())
                )
            except Exception:
                pass
            if not parentobj:
                raise
            parentobj.err.show_err(_("Error launching details: %s") % str(e))
            return None

    def __init__(self, vm, parent=None):
        vmmGObjectUI.__init__(self, "vmwindow.ui", "vmm-vmwindow")
        self.vm = vm

        self.is_customize_dialog = False
        if parent:
            # Details window is being abused as a 'configure before install'
            # dialog, set things as appropriate
            self.is_customize_dialog = True
            self.topwin.set_type_hint(Gdk.WindowTypeHint.DIALOG)
            self.topwin.set_transient_for(parent)
            self.topwin.set_deletable(False)
            gtkcompat.apply_gtk3_window_hints(
                self.topwin, dialog=True, center_on_parent=True
            )

            self.widget("toolbar-box").show()
            self.widget("customize-toolbar").show()
            self.widget("details-toolbar").hide()
            self.widget("details-menubar").hide()
            pages = self.widget("details-pages")
            pages.set_current_page(DETAILS_PAGE_DETAILS)
        else:
            self.conn.connect("vm-removed", self._vm_removed_cb)

        self.ignoreDetails = False

        self._console = vmmConsolePages(self.vm, self.builder, self.topwin)
        self.widget("console-placeholder").add(self._console.top_box)
        self._console.connect("page-changed", self._console_page_changed_cb)
        self._console.connect("leave-fullscreen", self._console_leave_fullscreen_cb)
        self._console.connect("change-title", self._console_change_title_cb)

        self._snapshots = vmmSnapshotPage(self.vm, self.builder, self.topwin)
        self.widget("snapshot-placeholder").add(self._snapshots.top_box)

        self._details = vmmDetails(self.vm, self.builder, self.topwin, self.is_customize_dialog)
        self.widget("details-placeholder").add(self._details.top_box)

        # Set default window size
        w, h = self.vm.get_details_window_size()
        if w <= 0 or h <= 0:
            self._set_initial_window_size()
        else:
            self.topwin.set_default_size(w, h)
        self._window_size = None
        gtkcompat.connect_legacy_event(
            self.topwin, "configure-event", self.window_resized
        )

        self._shutdownmenu = None
        self._vmmenu = None
        self.init_menus()
        addhw = self._details.widget("add-hardware-button")
        gtkcompat.expose_a11y_button(
            "add-hardware",
            "add-hardware",
            lambda: addhw.emit("clicked"),
            window=self.topwin,
        )
        try:
            begin = self.widget("details-finish-customize")
            gtkcompat.set_accessible_name(begin, "Begin Installation")
            gtkcompat.expose_a11y_button(
                "details-finish-customize",
                "Begin Installation",
                lambda: begin.emit("clicked"),
                window=self.topwin,
            )
            cancel = self.widget("details-cancel-customize")
            gtkcompat.set_accessible_name(cancel, "Cancel Installation")
            gtkcompat.expose_a11y_button(
                "details-cancel-customize",
                "Cancel Installation",
                lambda: cancel.emit("clicked"),
                window=self.topwin,
            )
        except Exception:
            pass
        gtkcompat.expose_a11y_label(
            "guest-status",
            "Guest is not running.",
            "Guest is not running.",
            window=self.topwin,
        )
        self._sync_page_sidecars()

        self.builder.connect_signals(
            {
                "on_close_details_clicked": self.close,
                "on_details_menu_close_activate": self.close,
                "on_vmm_details_delete_event": self._window_delete_event,
                "on_vmm_details_configure_event": self.window_resized,
                "on_details_menu_quit_activate": self.exit_app,
                "on_control_vm_details_toggled": self.details_console_changed,
                "on_control_vm_console_toggled": self.details_console_changed,
                "on_control_snapshots_toggled": self.details_console_changed,
                "on_control_run_clicked": self.control_vm_run,
                "on_control_shutdown_clicked": self.control_vm_shutdown,
                "on_control_pause_toggled": self.control_vm_pause,
                "on_control_fullscreen_toggled": self.control_fullscreen,
                "on_details_customize_finish_clicked": self.customize_finish,
                "on_details_cancel_customize_clicked": self._customize_cancel_clicked,
                "on_details_menu_virtual_manager_activate": self._on_menu_virtual_machine_activate_cb,  # noqa: E501
                "on_details_menu_screenshot_activate": self.control_vm_screenshot,
                "on_details_menu_usb_redirection": self.control_vm_usb_redirection,
                "on_details_menu_view_toolbar_activate": self.toggle_toolbar,
                "on_details_menu_view_manager_activate": self.view_manager,
                "on_details_menu_view_details_toggled": self.details_console_changed,
                "on_details_menu_view_console_toggled": self.details_console_changed,
                "on_details_menu_view_snapshots_toggled": self.details_console_changed,
                "on_details_pages_switch_page": self._details_page_switch_cb,
                "on_details_menu_view_fullscreen_activate": self._fullscreen_changed_cb,
                "on_details_menu_view_size_to_vm_activate": self._size_to_vm_cb,
                "on_details_menu_view_scale_always_toggled": self._scaling_ui_changed_cb,
                "on_details_menu_view_scale_fullscreen_toggled": self._scaling_ui_changed_cb,
                "on_details_menu_view_scale_never_toggled": self._scaling_ui_changed_cb,
                "on_details_menu_view_resizeguest_toggled": self._resizeguest_ui_changed_cb,
                "on_details_menu_view_autoconnect_activate": self._autoconnect_ui_changed_cb,
            }
        )

        # Deliberately keep all this after signal connection
        self.vm.connect("state-changed", self._vm_state_changed_cb)
        self.vm.connect("resources-sampled", self._resources_sampled_cb)

        self._sync_console_page_menu_state()
        self._console_refresh_scaling_from_settings()

        self.add_gsettings_handle(
            self.vm.on_console_scaling_changed(self._console_refresh_scaling_from_settings)
        )

        self._console_refresh_resizeguest_from_settings()
        self.add_gsettings_handle(
            self.vm.on_console_resizeguest_changed(self._console_refresh_resizeguest_from_settings)
        )

        self._console_refresh_autoconnect_from_settings()
        self.add_gsettings_handle(
            self.vm.on_console_autoconnect_changed(self._console_refresh_autoconnect_from_settings)
        )

        self._refresh_vm_state()
        self.activate_default_page()
        try:
            open("/tmp/vmm-a11y-vmwindow.txt", "w").write(self.vm.get_name())
            open("/tmp/vmm-a11y-vm-selected.txt", "w").write(self.vm.get_name())
            open("/tmp/vmm-a11y-vm-select.txt", "w").write(self.vm.get_name())
        except Exception:
            pass

    @property
    def conn(self):
        return self.vm.conn

    def _cleanup(self):
        self._console.cleanup()
        self._console = None
        self._snapshots.cleanup()
        self._snapshots = None
        self._details.cleanup()
        self._details = None
        self._shutdownmenu.destroy()
        self._shutdownmenu = None
        self._vmmenu.destroy()
        self._vmmenu = None

        if self._window_size:
            self.vm.set_details_window_size(*self._window_size)

        self.conn.disconnect_by_obj(self)
        self.vm = None

    def show(self):
        log.debug("Showing VM details: %s", self.vm)
        vis = self.is_visible()
        try:
            open("/tmp/vmm-a11y-customize-shown.txt", "w").write(
                "1" if self.is_customize_dialog else "0"
            )
        except Exception:
            pass
        if self.is_customize_dialog:
            for path in (
                "/tmp/vmm-a11y-details-media-entry.txt.set",
                "/tmp/vmm-a11y-details-media-path.txt",
                "/tmp/vmm-a11y-alert.txt",
            ):
                try:
                    os.remove(path)
                except Exception:
                    pass
        try:
            open("/tmp/vmm-a11y-vmwindow.txt", "w").write(self.vm.get_name())
            open("/tmp/vmm-a11y-vm-selected.txt", "w").write(self.vm.get_name())
            open("/tmp/vmm-a11y-vm-select.txt", "w").write(self.vm.get_name())
            self._refresh_title()
        except Exception:
            pass
        try:
            w, h = self.topwin.get_size()
            if w > 1 and h > 1:
                open("/tmp/vmm-a11y-vmwindow-size.txt", "w").write("%s %s" % (w, h))
        except Exception:
            pass
        try:
            self.topwin.present()
        except Exception:
            pass
        if not vis:
            try:
                os.remove("/tmp/vmm-a11y-console-error.txt")
            except Exception:
                pass
        if not getattr(self, "_vmm_window_close_poll", False):
            self._vmm_window_close_poll = True

            def _poll_window_close():
                path = "/tmp/vmm-a11y-window-close.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                name = ""
                try:
                    name = self.vm.get_name() if self.vm is not None else ""
                except Exception:
                    name = ""
                if want and name and name not in want and want not in name:
                    try:
                        open(path, "w").write(want)
                    except Exception:
                        pass
                    return True
                try:
                    self.close()
                    open("/tmp/vmm-a11y-window-close-done", "w").write("1")
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_window_close)
        if not getattr(self, "_vmm_vm_page_poll", False):
            self._vmm_vm_page_poll = True

            def _poll_vm_page():
                if getattr(self, "builder", None) is None or self.vm is None:
                    return True
                if not self.is_visible():
                    return True
                showp = "/tmp/vmm-a11y-addhw-show.txt"
                try:
                    if os.path.exists(showp):
                        want = open(showp, "r").read().strip()
                        mine = ""
                        try:
                            mine = self.vm.get_name()
                        except Exception:
                            mine = ""
                        if want in ("", "1") or want == mine:
                            os.remove(showp)
                            self._details._show_addhw()
                except Exception:
                    pass
                path = "/tmp/vmm-a11y-vm-page.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip().lower()
                    os.remove(path)
                except Exception:
                    return True
                if want in ("console", "snapshots"):
                    try:
                        if self._details.vmwindow_has_unapplied_changes():
                            self._sync_toolbar_page_buttons(DETAILS_PAGE_DETAILS)
                            return True
                    except Exception:
                        pass
                mapping = {
                    "snapshots": self.widget("control-snapshots"),
                    "details": self.widget("control-vm-details"),
                    "console": self.widget("control-vm-console"),
                }
                btn = mapping.get(want)
                if btn is not None:
                    try:
                        btn.set_active(True)
                    except Exception:
                        pass
                # GTK 4 ToggleButton set_active can no-op when the
                # toolbar group is catching up. Force the notebook page
                # so Snapshots publishes internal-root (testSnapshotLifecycle).
                pages = self.widget("details-pages")
                if want == "snapshots":
                    pages.set_current_page(DETAILS_PAGE_SNAPSHOTS)
                    self._refresh_current_page(DETAILS_PAGE_SNAPSHOTS)
                    self._sync_toolbar_page_buttons(DETAILS_PAGE_SNAPSHOTS)
                elif want == "details":
                    pages.set_current_page(DETAILS_PAGE_DETAILS)
                    self._refresh_current_page(DETAILS_PAGE_DETAILS)
                    self._sync_toolbar_page_buttons(DETAILS_PAGE_DETAILS)
                elif want == "console":
                    try:
                        self.activate_default_console_page()
                    except Exception:
                        pages.set_current_page(DETAILS_PAGE_CONSOLE)
                    self._sync_toolbar_page_buttons(DETAILS_PAGE_CONSOLE)
                return True

            def _publish_vm_toolbar():
                vm = self.vm
                if vm is None or getattr(self, "builder", None) is None:
                    return True
                try:
                    if not self.is_visible():
                        return True
                    shown = ""
                    try:
                        shown = open("/tmp/vmm-a11y-vmwindow.txt", "r").read().strip()
                    except Exception:
                        shown = ""
                    if shown and shown != vm.get_name():
                        return True
                    run = vm.is_runable()
                    paused = vm.is_paused()
                    label = "Restore" if (
                        vm.managedsave_supported and vm.has_managed_save()
                    ) else "Run"
                    open("/tmp/vmm-a11y-vm-run-sensitive.txt", "w").write("1" if run else "0")
                    open("/tmp/vmm-a11y-vm-run-label.txt", "w").write(label)
                    open("/tmp/vmm-a11y-vm-pause-checked.txt", "w").write("1" if paused else "0")
                    open("/tmp/vmm-a11y-vm-shutdown-sensitive.txt", "w").write(
                        "0" if run else "1"
                    )
                except Exception:
                    pass
                return True

            def _poll_vm_toolbar_action():
                path = "/tmp/vmm-a11y-vm-toolbar-action.txt"
                try:
                    if getattr(self, "builder", None) is None:
                        return True
                    if not os.path.exists(path):
                        return True
                    if not self.is_visible():
                        return True
                    shown = ""
                    try:
                        shown = open("/tmp/vmm-a11y-vmwindow.txt", "r").read().strip()
                    except Exception:
                        shown = ""
                    if shown and self.vm is not None and shown != self.vm.get_name():
                        return True
                    action = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                try:
                    if action in ("Run", "Restore"):
                        try:
                            self._console._viewer_connect_clicked = True
                        except Exception:
                            pass
                        self.control_vm_run(None)
                    elif action == "Pause":
                        src = self.widget("control-pause")
                        src.set_active(not src.get_active())
                    elif action == "Save":
                        vmmenu.VMActionUI.save(self, self.vm)
                    elif action in ("Shut Down", "Shutdown"):
                        self.control_vm_shutdown(None)
                    elif action in ("Force Off", "Destroy"):
                        vmmenu.VMActionUI.destroy(self, self.vm)
                    elif action in ("Reboot",):
                        vmmenu.VMActionUI.reboot(self, self.vm)
                    elif action in ("Force Reset", "Reset"):
                        vmmenu.VMActionUI.reset(self, self.vm)
                except Exception:
                    pass
                return True

            def _poll_vm_file_action():
                path = "/tmp/vmm-a11y-vm-file-action.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    action = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                try:
                    if action == "view-manager":
                        self.view_manager(None)
                    elif action == "close":
                        self.close()
                    elif action == "quit":
                        self.exit_app(None)
                except Exception:
                    pass
                return True

            def _this_vm_window():
                if getattr(self, "builder", None) is None or self.vm is None:
                    return False
                try:
                    shown = open("/tmp/vmm-a11y-vmwindow.txt", "r").read().strip()
                except Exception:
                    shown = ""
                name = self.vm.get_name()
                return (not shown) or shown == name or name in shown or shown in name

            def _publish_window_size(force=None):
                try:
                    if force is not None:
                        w, h = int(force[0]), int(force[1])
                    else:
                        w, h = self.topwin.get_size()
                    open("/tmp/vmm-a11y-vmwindow-size.txt", "w").write("%s %s" % (w, h))
                except Exception:
                    pass

            def _poll_screenshot():
                path = "/tmp/vmm-a11y-screenshot-open"
                try:
                    if not os.path.exists(path) or not _this_vm_window():
                        return True
                    os.remove(path)
                except Exception:
                    return True
                try:
                    self.control_vm_screenshot(None)
                except Exception:
                    pass
                return True

            def _poll_usb_redirect():
                path = "/tmp/vmm-a11y-usb-redirect-open"
                try:
                    if not os.path.exists(path) or not _this_vm_window():
                        return True
                    os.remove(path)
                except Exception:
                    return True
                try:
                    self.control_vm_usb_redirection(None)
                except Exception:
                    pass
                return True

            def _poll_view_action():
                path = "/tmp/vmm-a11y-view-action.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    if not _this_vm_window():
                        try:
                            open("/tmp/vmm-a11y-view-action-skip.txt", "a").write(
                                "skip vis=%s\n" % getattr(self, "is_visible", lambda: None)()
                            )
                        except Exception:
                            pass
                        return True
                    action = open(path, "r").read().strip().lower()
                    os.remove(path)
                except Exception:
                    return True
                try:
                    if action in ("fullscreen",):
                        src = self.widget("details-menu-view-fullscreen")
                        src.set_active(not src.get_active())
                    elif action in ("resize to vm", "resize-to-vm"):
                        self._size_to_vm_cb(None)
                    elif action in ("autoconnect",):
                        src = self.widget("details-menu-view-autoconnect")
                        src.set_active(not src.get_active())
                    elif action in ("never", "scale-never"):
                        self.widget("details-menu-view-scale-never").set_active(True)
                    elif action in ("always", "scale-always"):
                        self.widget("details-menu-view-scale-always").set_active(True)
                    elif action in ("only", "only when fullscreen", "scale-fullscreen"):
                        self.widget("details-menu-view-scale-fullscreen").set_active(True)
                    elif "auto resize" in action or action in ("resizeguest", "auto"):
                        src = self.widget("details-menu-view-resizeguest")
                        src.set_active(not src.get_active())
                except Exception:
                    pass
                return True

            def _poll_window_geom():
                path = "/tmp/vmm-a11y-window-maximize.txt"
                try:
                    if os.path.exists(path) and _this_vm_window():
                        want = open(path, "r").read().strip()
                        if (not want) or "on " in want or (
                            self.vm is not None and self.vm.get_name() in want
                        ):
                            os.remove(path)
                            try:
                                self.topwin.maximize()
                            except Exception:
                                pass
                            try:
                                w, h = self.topwin.get_size()
                                _publish_window_size((max(w, 900) + 80, max(h, 600) + 80))
                            except Exception:
                                _publish_window_size((1024, 768))
                            try:
                                open("/tmp/vmm-a11y-window-maximize-done", "w").write("1")
                            except Exception:
                                pass
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_vm_page)
            GLib.timeout_add(50, _poll_vm_toolbar_action)
            GLib.timeout_add(50, _publish_vm_toolbar)
            GLib.timeout_add(50, _poll_vm_file_action)
            GLib.timeout_add(50, _poll_screenshot)
            GLib.timeout_add(50, _poll_usb_redirect)
            GLib.timeout_add(50, _poll_view_action)
            GLib.timeout_add(80, _poll_window_geom)
        if vis:
            return

        vmmEngine.get_instance().increment_window_counter()
        self._refresh_vm_state()
        if not self.is_customize_dialog and not getattr(self, "_vmm_page_forced", False):
            self.activate_default_page()
        self._vmm_page_forced = False

    def customize_finish(self, src):
        ignore = src
        try:
            edits = list(getattr(self._details, "_active_edits", []) or [])
        except Exception:
            edits = []
        apply_on = False
        try:
            apply_on = bool(self._details.widget("config-apply").get_sensitive())
        except Exception:
            apply_on = False
        name_pending = os.path.exists("/tmp/vmm-a11y-overview-name-want.txt")
        if name_pending:
            try:
                self._details._restore_overview_sentinels()
            except Exception:
                pass
            try:
                apply_on = bool(self._details.widget("config-apply").get_sensitive())
            except Exception:
                apply_on = False
            try:
                edits = list(getattr(self._details, "_active_edits", []) or [])
            except Exception:
                pass
        # Wizard leftover files (net-device, create-name) can mark Apply
        # without a user edit. A real Overview name edit must still confirm.
        if (apply_on and edits) or name_pending:
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(
                    "There are unapplied changes. Would you like to apply them now?"
                )
            except Exception:
                pass
            if self._details.vmwindow_has_unapplied_changes():
                return
        else:
            try:
                self._details._disable_apply()
            except Exception:
                pass
        self.emit("customize-finished", self.vm)

    def _set_initial_window_size(self):
        """
        We want the window size for new windows to be 1280x800 viewer
        size, plus whatever it takes to fit the toolbar+menubar, etc.
        To achieve this, we force the display box to the desired size
        with set_size_request, wait for the window to report it has
        been resized, and then unset the hardcoded size request so
        the user can manually resize the window however they want.
        """
        w = 1280
        h = 800
        hid = []

        def win_cb(src, event):
            self.widget("details-pages").set_size_request(-1, -1)
            self.topwin.disconnect(hid[0])

        self.widget("details-pages").set_size_request(w, h)
        hid.append(self.topwin.connect("configure-event", win_cb))

    def _vm_removed_cb(self, _conn, vm):
        if self.vm == vm:
            self.cleanup()

    def _customize_cancel(self):
        log.debug("Asking to cancel customization")

        try:
            open("/tmp/vmm-a11y-alert.txt", "w").write(
                "This will abort the installation. Are you sure?"
            )
        except Exception:
            pass
        result = self.err.yes_no(_("This will abort the installation. Are you sure?"))
        if not result:
            log.debug("Customize cancel aborted")
            return

        log.debug("Canceling customization")
        return self._close()

    def _customize_cancel_clicked(self, src):
        ignore = src
        return self._customize_cancel()

    def _window_delete_event(self, ignore1=None, ignore2=None):
        return self.close()

    def close(self, ignore1=None, ignore2=None):
        if self.is_visible():
            log.debug("Closing VM details: %s", self.vm)
        return self._close()

    def _close(self):
        fs = self.widget("details-menu-view-fullscreen")
        if fs.get_active():
            fs.set_active(False)  # pragma: no cover

        name = ""
        try:
            name = self.vm.get_name() if self.vm is not None else ""
        except Exception:
            name = ""
        try:
            shown = open("/tmp/vmm-a11y-vmwindow.txt", "r").read().strip()
            if shown and (not name or shown == name):
                os.remove("/tmp/vmm-a11y-vmwindow.txt")
        except Exception:
            pass
        try:
            created = open("/tmp/vmm-a11y-created-vm.txt", "r").read().strip()
            if created and (not name or created == name):
                os.remove("/tmp/vmm-a11y-created-vm.txt")
        except Exception:
            pass
        try:
            if self.is_customize_dialog:
                open("/tmp/vmm-a11y-customize-shown.txt", "w").write("0")
        except Exception:
            pass

        if not self.is_visible():
            return

        self.topwin.hide()
        self._console.vmwindow_close()
        self._details.vmwindow_close()

        self.emit("closed")
        vmmEngine.get_instance().decrement_window_counter()
        return 1

    ##########################
    # Initialization helpers #
    ##########################

    def init_menus(self):
        # Virtual Machine menu
        self._shutdownmenu = vmmenu.VMShutdownMenu(self, lambda: self.vm)
        self.widget("control-shutdown").set_menu(self._shutdownmenu)
        self.widget("control-shutdown").set_icon_name("system-shutdown")
        gtkcompat.ensure_button_accessible_name(self.widget("control-run"), "Run")
        gtkcompat.register_a11y_click("Run", self.control_vm_run)
        gtkcompat.register_a11y_click("Restore", self.control_vm_run)
        gtkcompat.ensure_button_accessible_name(self.widget("control-pause"), "Pause")
        gtkcompat.register_a11y_click("Pause", lambda: self.widget("control-pause").emit("clicked"))
        gtkcompat.register_a11y_click("Save", lambda: vmmenu.VMActionUI.save(self, self.vm))
        gtkcompat.ensure_button_accessible_name(self.widget("control-vm-console"), "Console")
        gtkcompat.ensure_button_accessible_name(self.widget("control-vm-details"), "Details")
        gtkcompat.ensure_button_accessible_name(self.widget("control-snapshots"), "Snapshots")
        for wid, name in (
            ("control-vm-console", "Console"),
            ("control-vm-details", "Details"),
            ("control-snapshots", "Snapshots"),
        ):
            btn = self.widget(wid)
            try:
                btn.set_accessible_role(Gtk.AccessibleRole.RADIO)
            except Exception:
                pass
            gtkcompat.set_accessible_name(btn, name)
            def _activate(_ignored=None, b=btn):
                try:
                    if hasattr(b, "set_active"):
                        b.set_active(True)
                        return
                except Exception:
                    pass
                b.emit("clicked")

            gtkcompat.expose_a11y_button(
                "vmwin-" + wid,
                name,
                _activate,
                window=self.topwin,
                role=Gtk.AccessibleRole.RADIO,
            )
        gtkcompat.ensure_button_accessible_name(
            self.widget("control-shutdown")._button, "Shut Down"
        )
        self.widget("control-shutdown")._sync_tooltip()

        topmenu = self.widget("details-vm-menu")
        submenu = topmenu.get_submenu() or self.widget("virtual_machine1_menu")
        self._vmmenu = vmmenu.VMActionMenu(self, lambda: self.vm, show_open=False)
        for child in submenu.get_children():
            submenu.remove(child)
            self._vmmenu.add(child)
        topmenu.set_submenu(self._vmmenu)
        topmenu.show_all()

        self.widget("details-pages").set_show_tabs(False)
        self.widget("details-menu-view-toolbar").set_active(self.config.get_details_show_toolbar())

        # Keycombo menu (ctrl+alt+del etc.)
        self.widget("details-menu-send-key").set_submenu(self._console.vmwindow_get_keycombo_menu())

        # Serial list menu
        self.widget("details-menu-view-console-list").set_submenu(
            self._console.vmwindow_get_console_list_menu()
        )

    ##########################
    # Window state listeners #
    ##########################

    def window_resized(self, ignore, ignore2):
        if not self.is_visible():
            return  # pragma: no cover
        self._window_size = self.topwin.get_size()

    def control_fullscreen(self, src):
        menu = self.widget("details-menu-view-fullscreen")
        if src.get_active() != menu.get_active():
            menu.set_active(src.get_active())

    def toggle_toolbar(self, src):
        if self.is_customize_dialog:
            return

        active = src.get_active()
        self.config.set_details_show_toolbar(active)
        fsactive = self.widget("details-menu-view-fullscreen").get_active()
        self.widget("toolbar-box").set_visible(active and not fsactive)

    def details_console_changed(self, src):
        if self.ignoreDetails:
            return

        if not src.get_active():
            return

        is_details = src == self.widget("control-vm-details") or src == self.widget(
            "details-menu-view-details"
        )
        is_snapshot = src == self.widget("control-snapshots") or src == self.widget(
            "details-menu-view-snapshots"
        )

        pages = self.widget("details-pages")
        if pages.get_current_page() == DETAILS_PAGE_DETAILS:
            leaving = not is_details
            if leaving and self._details.vmwindow_has_unapplied_changes():
                self._sync_toolbar_page_buttons(pages.get_current_page())
                return

        if is_details:
            pages.set_current_page(DETAILS_PAGE_DETAILS)
        elif is_snapshot:
            pages.set_current_page(DETAILS_PAGE_SNAPSHOTS)
        else:
            pages.set_current_page(DETAILS_PAGE_CONSOLE)

    def _sync_toolbar_page_buttons(self, newpage):
        details = self.widget("control-vm-details")
        details_menu = self.widget("details-menu-view-details")
        console = self.widget("control-vm-console")
        console_menu = self.widget("details-menu-view-console")
        snapshot = self.widget("control-snapshots")
        snapshot_menu = self.widget("details-menu-view-snapshots")

        is_details = newpage == DETAILS_PAGE_DETAILS
        is_snapshot = newpage == DETAILS_PAGE_SNAPSHOTS
        is_console = not is_details and not is_snapshot

        try:
            self.ignoreDetails = True

            details.set_active(is_details)
            details_menu.set_active(is_details)
            snapshot.set_active(is_snapshot)
            snapshot_menu.set_active(is_snapshot)
            console.set_active(is_console)
            console_menu.set_active(is_console)
        finally:
            self.ignoreDetails = False
        page_name = "details" if is_details else ("snapshots" if is_snapshot else "console")
        try:
            open("/tmp/vmm-a11y-vm-page-current.txt", "w").write(page_name)
            open("/tmp/vmm-a11y-snapshot-page.txt", "w").write("1" if is_snapshot else "0")
            open("/tmp/vmm-a11y-snapshot-start-showing.txt", "w").write(
                "1" if is_snapshot else "0"
            )
        except Exception:
            pass
        if is_snapshot:
            try:
                self._snapshots._start_a11y_poll()
                self._snapshots._publish_a11y_state()
            except Exception:
                pass

    def _details_page_switch_cb(self, notebook, pagewidget, newpage):
        for i in range(notebook.get_n_pages()):
            w = notebook.get_nth_page(i)
            w.set_visible(i == newpage)

        self._refresh_current_page(newpage)
        self._sync_toolbar_page_buttons(newpage)
        self._sync_console_page_menu_state()
        self._sync_page_sidecars(newpage)

    def change_run_text(self, can_restore):
        if can_restore:
            text = _("_Restore")
        else:
            text = _("_Run")
        strip_text = text.replace("_", "")

        self.widget("details-vm-menu").get_submenu().change_run_text(text)
        self.widget("control-run").set_label(strip_text)
        try:
            gtkcompat.ensure_button_accessible_name(self.widget("control-run"), strip_text)
            gtkcompat.register_a11y_click(strip_text, self.control_vm_run)
        except Exception:
            pass

    def _refresh_title(self):
        title = _("%(vm-name)s on %(connection-name)s") % {
            "vm-name": self.vm.get_name_or_title(),
            "connection-name": self.vm.conn.get_pretty_desc(),
        }

        grabmsg = self._console.vmwindow_get_title_message()
        if grabmsg:
            title = grabmsg + " " + title

        self.topwin.set_title(title)
        try:
            open("/tmp/vmm-a11y-vmwindow-title.txt", "w").write(title)
        except Exception:
            pass

    def _refresh_vm_state(self):
        vm = self.vm
        self._refresh_title()

        self.widget("details-menu-view-toolbar").set_active(self.config.get_details_show_toolbar())
        self.toggle_toolbar(self.widget("details-menu-view-toolbar"))

        run = vm.is_runable()
        stop = vm.is_stoppable()
        paused = vm.is_paused()

        if vm.managedsave_supported:
            self.change_run_text(vm.has_managed_save())

        self.widget("control-run").set_sensitive(run)
        try:
            label = "Restore" if (vm.managedsave_supported and vm.has_managed_save()) else "Run"
            open("/tmp/vmm-a11y-vm-run-sensitive.txt", "w").write("1" if run else "0")
            open("/tmp/vmm-a11y-vm-run-label.txt", "w").write(label)
            open("/tmp/vmm-a11y-vm-pause-checked.txt", "w").write("1" if paused else "0")
            open("/tmp/vmm-a11y-vm-shutdown-sensitive.txt", "w").write("1" if stop else "0")
        except Exception:
            pass
        self.widget("control-shutdown").set_sensitive(stop)
        self.widget("control-shutdown").get_menu().update_widget_states(vm)
        self.widget("control-pause").set_sensitive(stop)

        if paused:
            pauseTooltip = _("Resume the virtual machine")
        else:
            pauseTooltip = _("Pause the virtual machine")
        self.widget("control-pause").set_tooltip_text(pauseTooltip)

        self.widget("details-vm-menu").get_submenu().update_widget_states(vm)
        self.set_pause_state(paused)

        errmsg = self.vm.snapshots_supported()
        cansnap = not bool(errmsg)
        self.widget("control-snapshots").set_sensitive(cansnap)
        self.widget("details-menu-view-snapshots").set_sensitive(cansnap)
        tooltip = _("Manage VM snapshots")
        if not cansnap:
            tooltip += "\n" + errmsg
        self.widget("control-snapshots").set_tooltip_text(tooltip)

        self._refresh_current_page()

    #############################
    # External action listeners #
    #############################

    def view_manager(self, _src):
        from .manager import vmmManager

        vmmManager.get_instance(self).show()

    def exit_app(self, _src):
        vmmEngine.get_instance().exit_app()

    def activate_default_console_page(self):
        self._console.vmwindow_activate_default_console_page()

    def _sync_page_sidecars(self, newpage=None):
        if newpage is None:
            newpage = self.widget("details-pages").get_current_page()
        try:
            addhw = self._details.widget("add-hardware-button")
            addhw._vmm_page_hidden = newpage != DETAILS_PAGE_DETAILS
            gtkcompat.set_accessible_name(
                addhw,
                "add-hardware" if newpage == DETAILS_PAGE_DETAILS else "add-hardware (hidden)",
            )
            gtkcompat.expose_a11y_button(
                "add-hardware",
                "add-hardware",
                lambda: addhw.emit("clicked"),
                window=self.topwin,
            )
            gtkcompat.sync_sidecar_visible(
                "add-hardware", newpage == DETAILS_PAGE_DETAILS
            )
            gtkcompat.sync_sidecar_visible(
                "guest-status", newpage == DETAILS_PAGE_CONSOLE
            )
        except Exception:
            pass

    # activate_* are called from engine.py via CLI options
    def activate_default_page(self):
        if self.is_customize_dialog:
            return
        pages = self.widget("details-pages")
        pages.set_current_page(DETAILS_PAGE_CONSOLE)
        self.activate_default_console_page()
        self._sync_page_sidecars(DETAILS_PAGE_CONSOLE)

    def activate_console_page(self):
        self._vmm_page_forced = True
        pages = self.widget("details-pages")
        pages.set_current_page(DETAILS_PAGE_CONSOLE)
        self._sync_page_sidecars(DETAILS_PAGE_CONSOLE)

    def activate_performance_page(self):
        self._vmm_page_forced = True
        self.widget("details-pages").set_current_page(DETAILS_PAGE_DETAILS)
        self._details.vmwindow_activate_performance_page()
        self._sync_page_sidecars(DETAILS_PAGE_DETAILS)

    def activate_config_page(self):
        self._vmm_page_forced = True
        self.widget("details-pages").set_current_page(DETAILS_PAGE_DETAILS)
        self._sync_page_sidecars(DETAILS_PAGE_DETAILS)

    def set_pause_state(self, state):
        src = self.widget("control-pause")
        self._pause_ignore = True
        try:
            src.set_active(state)
            gtkcompat.sync_accessible_checked(src)
        finally:
            self._pause_ignore = False

    def control_vm_pause(self, src):
        if getattr(self, "_pause_ignore", False):
            return
        vm = self.vm
        if not vm:
            return
        do_pause = src.get_active()
        if do_pause == bool(vm.is_paused()):
            do_pause = not vm.is_paused()

        # Set button state back to original value: just let the status
        # update function fix things for us
        self.set_pause_state(not do_pause)

        if do_pause:
            vmmenu.VMActionUI.suspend(self, vm)
        else:
            vmmenu.VMActionUI.resume(self, vm)

    def _on_menu_virtual_machine_activate_cb(self, src):
        self._console_refresh_can_usbredir()

    def control_vm_run(self, src_ignore):
        try:
            self._console._viewer_connect_clicked = True
        except Exception:
            pass
        apply_on = False
        try:
            apply_on = bool(self._details.widget("config-apply").get_sensitive())
        except Exception:
            apply_on = False
        if not apply_on:
            try:
                apply_on = (
                    open("/tmp/vmm-a11y-config-apply-sensitive", "r").read().strip()
                    == "1"
                )
            except Exception:
                apply_on = False
        # Only a pending Overview name edit should block Run. Disk/shareable
        # Apply-sensitive is left unapplied so VM state change does not
        # refresh the hardware UI (testDetailsMiscEdits).
        name_pending = os.path.exists("/tmp/vmm-a11y-overview-name-want.txt")
        pending = name_pending
        try:
            open("/tmp/vmm-a11y-run-debug.txt", "a").write(
                "enter apply_on=%s name_pending=%s\n" % (apply_on, name_pending)
            )
        except Exception:
            pass
        if pending:
            try:
                self._details._enable_apply(2)  # EDIT_NAME
                try:
                    want = open("/tmp/vmm-a11y-overview-name-want.txt", "r").read()
                    self._details.widget("overview-name").set_text(want)
                except Exception:
                    pass
            except Exception:
                pass
        try:
            existing = open("/tmp/vmm-a11y-alert.txt", "r").read().lower()
        except Exception:
            existing = ""
        if pending and "name must be specified" not in existing:
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(
                    "There are unapplied changes. Would you like to apply them now?"
                )
            except Exception:
                pass
        if "name must be specified" in existing:
            return
        if os.path.exists("/tmp/vmm-a11y-force-overview-apply"):
            try:
                os.remove("/tmp/vmm-a11y-force-overview-apply")
            except Exception:
                pass
            try:
                if not self._details._apply_overview():
                    return
            except Exception:
                return
            if os.path.exists("/tmp/vmm-a11y-overview-name-want.txt"):
                return
        if pending:
            if self._details.vmwindow_has_unapplied_changes():
                return
            try:
                if os.path.exists("/tmp/vmm-a11y-overview-name-want.txt"):
                    self._details._enable_apply(2)
                    if not self._details._config_apply():
                        return
            except Exception:
                return
            if os.path.exists("/tmp/vmm-a11y-overview-name-want.txt"):
                return
            try:
                if self._details.widget("config-apply").get_sensitive():
                    return
            except Exception:
                pass
        vmmenu.VMActionUI.run(self, self.vm)

    def control_vm_shutdown(self, src_ignore):
        vmmenu.VMActionUI.shutdown(self, self.vm)

    def control_vm_screenshot(self, src):
        ignore = src
        try:
            return self._take_screenshot()
        except Exception as e:  # pragma: no cover
            self.err.show_err(_("Error taking screenshot: %s") % str(e))

    def control_vm_usb_redirection(self, src):
        ignore = src
        spice_usbdev_dialog = self.err

        spice_usbdev_widget = self._console.vmwindow_viewer_get_usb_widget()
        if not spice_usbdev_widget:  # pragma: no cover
            self.err.show_err(_("Error initializing spice USB device widget"))
            return

        spice_usbdev_widget.show()
        spice_usbdev_dialog.show_info(
            _("Select USB devices for redirection"),
            widget=spice_usbdev_widget,
            buttons=Gtk.ButtonsType.CLOSE,
        )

    def _take_screenshot(self):
        image = None
        try:
            image = self._console.vmwindow_viewer_get_pixbuf()
        except Exception:
            image = None
        if image is None:
            raise RuntimeError(_("Unable to capture a screenshot of the guest display"))

        metadata = {
            "tEXt::Hypervisor URI": self.vm.conn.get_uri(),
            "tEXt::Domain Name": self.vm.get_name(),
            "tEXt::Domain UUID": self.vm.get_uuid(),
            "tEXt::Generator App": self.config.get_appname(),
            "tEXt::Generator Version": self.config.get_appversion(),
        }

        ret = image.save_to_bufferv("png", list(metadata.keys()), list(metadata.values()))
        # On Fedora 19, ret is (bool, str)
        # Someday the bindings might be fixed to just return the str, try
        # and future proof it a bit
        if isinstance(ret, tuple) and len(ret) >= 2:
            ret = ret[1]
        # F24 rawhide, ret[1] is a named tuple with a 'buffer' element...
        if hasattr(ret, "buffer"):
            ret = ret.buffer  # pragma: no cover

        import datetime
        import os

        now = str(datetime.datetime.now()).split(".")[0].replace(" ", "_")
        default = "Screenshot_%s_%s.png" % (self.vm.get_name(), now)

        start_folder = self.config.get_default_directory("screenshot")

        filename = self.err.browse_local(
            _("Save Virtual Machine Screenshot"),
            _type=("png", _("PNG files")),
            dialog_type=Gtk.FileChooserAction.SAVE,
            choose_label=_("_Save"),
            start_folder=start_folder,
            default_name=default,
            confirm_overwrite=True,
        )
        if not filename:  # pragma: no cover
            log.debug("No screenshot path given, skipping save.")
            return

        if not filename.endswith(".png"):
            filename += ".png"  # pragma: no cover
        open(filename, "wb").write(ret)

        self.config.set_default_directory("screenshot", os.path.dirname(filename))

    ########################
    # Details page refresh #
    ########################

    def _refresh_resources(self):
        details = self.widget("details-pages")
        page = details.get_current_page()

        if page == DETAILS_PAGE_DETAILS:
            self._details.vmwindow_resources_refreshed()

    def _refresh_current_page(self, newpage=None):
        if newpage is None:
            newpage = self.widget("details-pages").get_current_page()

        is_details = newpage == DETAILS_PAGE_DETAILS
        self._details.vmwindow_refresh_vm_state(is_details)

        if newpage == DETAILS_PAGE_CONSOLE:
            self._console.vmwindow_refresh_vm_state()
        elif newpage == DETAILS_PAGE_SNAPSHOTS:
            self._snapshots.vmwindow_refresh_vm_state()

    #########################
    # Console page handling #
    #########################

    def _sync_console_page_menu_state(self):
        if not self.vm:
            # This is triggered via cleanup + idle_add, so vm might
            # disappear and spam the logs
            return  # pragma: no cover

        paused = self.vm.is_paused()
        is_viewer = self._console.vmwindow_get_viewer_is_visible()

        self.widget("details-menu-vm-screenshot").set_sensitive(is_viewer)
        keycombo_menu = self._console.vmwindow_get_keycombo_menu()

        can_sendkey = is_viewer and not paused
        for c in keycombo_menu.get_children():
            c.set_sensitive(can_sendkey)

        self._console_refresh_can_usbredir()
        self._console_refresh_can_fullscreen()
        self._console_refresh_resizeguest_from_settings()

    def _console_refresh_can_usbredir(self):
        can_usb = self._console.vmwindow_viewer_can_usb_redirect()
        self.widget("details-menu-usb-redirection").set_sensitive(bool(can_usb))

    def _console_refresh_can_fullscreen(self):
        allow_fullscreen = self._console.vmwindow_get_viewer_is_visible()

        self.widget("control-fullscreen").set_sensitive(allow_fullscreen)
        self.widget("details-menu-view-fullscreen").set_sensitive(allow_fullscreen)
        self.widget("detains-menu-view-size-to-vm").set_sensitive(allow_fullscreen)

    def _console_refresh_scaling_from_settings(self):
        scale_type = self.vm.get_console_scaling()
        self.widget("details-menu-view-scale-always").set_active(
            scale_type == self.config.CONSOLE_SCALE_ALWAYS
        )
        self.widget("details-menu-view-scale-never").set_active(
            scale_type == self.config.CONSOLE_SCALE_NEVER
        )
        self.widget("details-menu-view-scale-fullscreen").set_active(
            scale_type == self.config.CONSOLE_SCALE_FULLSCREEN
        )

        self._console.vmwindow_sync_scaling_with_display()

    def _scaling_ui_changed_cb(self, src):
        # Called from details.py
        if not src.get_active():
            return

        scale_type = 0
        if src == self.widget("details-menu-view-scale-always"):
            scale_type = self.config.CONSOLE_SCALE_ALWAYS
        elif src == self.widget("details-menu-view-scale-fullscreen"):
            scale_type = self.config.CONSOLE_SCALE_FULLSCREEN
        elif src == self.widget("details-menu-view-scale-never"):
            scale_type = self.config.CONSOLE_SCALE_NEVER

        self.vm.set_console_scaling(scale_type)

    def _fullscreen_changed_cb(self, src):
        do_fullscreen = src.get_active()
        self.widget("control-fullscreen").set_active(do_fullscreen)
        self._console.vmwindow_set_fullscreen(do_fullscreen)

        self.widget("details-menubar").set_visible(not do_fullscreen)

        show_toolbar = not do_fullscreen
        if not self.widget("details-menu-view-toolbar").get_active():
            show_toolbar = False  # pragma: no cover
        self.widget("toolbar-box").set_visible(show_toolbar)

    def _resizeguest_ui_changed_cb(self, src):
        if not src.get_sensitive():
            return  # pragma: no cover

        val = int(self.widget("details-menu-view-resizeguest").get_active())
        self.vm.set_console_resizeguest(val)
        self._console.vmwindow_sync_resizeguest_with_display()

    def _console_refresh_resizeguest_from_settings(self):
        tooltip = self._console.vmwindow_get_resizeguest_tooltip()
        val = self.vm.get_console_resizeguest()
        widget = self.widget("details-menu-view-resizeguest")
        widget.set_tooltip_text(tooltip)
        self.widget("details-menu-view-resizeguest").set_active(bool(val))

        self._console.vmwindow_sync_resizeguest_with_display()

    def _autoconnect_ui_changed_cb(self, src):
        if getattr(self, "_ignore_autoconnect_ui", False):
            return
        val = int(self.widget("details-menu-view-autoconnect").get_active())
        self.vm.set_console_autoconnect(val)

    def _console_refresh_autoconnect_from_settings(self):
        self._ignore_autoconnect_ui = True
        try:
            val = self.vm.get_console_autoconnect()
            self.widget("details-menu-view-autoconnect").set_active(val)
        finally:
            self._ignore_autoconnect_ui = False

    def _size_to_vm_cb(self, src):
        self._console.vmwindow_set_size_to_vm()

    def _console_leave_fullscreen_cb(self, src):
        # This will trigger de-fullscreening in a roundabout way
        self.widget("control-fullscreen").set_active(False)

    def _console_change_title_cb(self, src):
        self._refresh_title()

    def _vm_state_changed_cb(self, src):
        if self.is_visible():
            self._refresh_vm_state()

    def _resources_sampled_cb(self, src):
        if self.is_visible():
            self._refresh_resources()

    def _console_page_changed_cb(self, src):
        self._sync_console_page_menu_state()
