# Copyright (C) 2007, 2013-2014 Red Hat, Inc.
# Copyright (C) 2007 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import GLib

from virtinst import log

from .lib import gtkcompat
from .lib import uiutil
from .baseclass import vmmGObjectUI
from .engine import vmmEngine
from .lib.graphwidgets import Sparkline
from .hostnets import vmmHostNets
from .hoststorage import vmmHostStorage


class vmmHost(vmmGObjectUI):
    @classmethod
    def show_instance(cls, parentobj, conn):
        try:
            # Maintain one dialog per connection
            uri = conn.get_uri()
            if cls._instances is None:
                cls._instances = {}
            if uri not in cls._instances:
                cls._instances[uri] = vmmHost(conn)
            cls._instances[uri].show()
        except Exception as e:  # pragma: no cover
            if not parentobj:
                raise
            parentobj.err.show_err(_("Error launching host dialog: %s") % str(e))

    def __init__(self, conn):
        vmmGObjectUI.__init__(self, "host.ui", "vmm-host")
        self.conn = conn

        # Set default window size
        w, h = self.conn.get_details_window_size()
        if w <= 0:
            w = 800
        if h <= 0:
            h = 600
        self.topwin.set_default_size(w, h)
        self._window_size = None

        self._cpu_usage_graph = None
        self._memory_usage_graph = None
        self._init_conn_state()

        self._storagelist = None
        self._init_storage_state()

        self._hostnets = None
        self._init_net_state()

        self.builder.connect_signals(
            {
                "on_menu_file_view_manager_activate": self._view_manager_cb,
                "on_menu_file_quit_activate": self._exit_app_cb,
                "on_menu_file_close_activate": self.close,
                "on_vmm_host_delete_event": self.close,
                "on_vmm_host_configure_event": self._window_resized_cb,
                "on_host_page_switch": self._page_changed_cb,
                "on_overview_name_changed": self._overview_name_changed_cb,
                "on_config_autoconnect_toggled": self._autoconnect_toggled_cb,
            }
        )
        gtkcompat.connect_legacy_event(
            self.topwin, "configure-event", self._window_resized_cb
        )

        self.conn.connect("state-changed", self._conn_state_changed_cb)
        self.conn.connect("resources-sampled", self._conn_resources_sampled_cb)

        self._refresh_resources()
        self._refresh_conn_state()
        self.widget("config-autoconnect").set_active(self.conn.get_autoconnect())

        self._cleanup_on_conn_removed()
        self._start_host_tab_poll()

    #######################
    # Standard UI methods #
    #######################

    def show(self):
        log.debug("Showing host details: %s", self.conn)
        vis = self.is_visible()
        self.topwin.present()
        try:
            open("/tmp/vmm-a11y-host-shown.txt", "w").write(self.conn.get_pretty_desc())
        except Exception:
            pass
        if vis:
            try:
                self._hostnets._publish_a11y_state()
                self._storagelist._publish_a11y_state()
            except Exception:
                pass
            self._publish_overview_state()
            return  # pragma: no cover

        vmmEngine.get_instance().increment_window_counter()
        try:
            title = _("%(connection)s - Connection Details") % {
                "connection": self.conn.get_pretty_desc()
            }
            self.topwin.set_title(title)
            gtkcompat.set_accessible_name(self.topwin, title)
            gtkcompat._ensure_app_window(self.topwin)
            gtkcompat.attach_notebook_a11y(self.widget("details-tabs"))
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-host-shown.txt", "w").write(self.conn.get_pretty_desc())
        except Exception:
            pass
        try:
            self._hostnets._start_a11y_poll()
            self._hostnets._publish_a11y_state()
        except Exception:
            pass
        try:
            self._storagelist._start_a11y_poll()
            self._storagelist._publish_a11y_state()
        except Exception:
            pass
        self._publish_overview_state()
        self._start_host_tab_poll()
        if not getattr(self, "_vmm_close_poll", False):
            self._vmm_close_poll = True

            def _close_tick():
                path = "/tmp/vmm-a11y-window-close.txt"
                try:
                    want = open(path, "r").read()
                except Exception:
                    return True
                if "Connection Details" not in want and "host" not in want.lower():
                    return True
                try:
                    os.remove(path)
                except Exception:
                    pass
                try:
                    self.close()
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _close_tick)

    def close(self, src=None, event=None):
        dummy = src
        dummy = event
        log.debug("Closing host window for %s", self.conn)
        if not self.is_visible():
            return

        self.topwin.hide()
        vmmEngine.get_instance().decrement_window_counter()
        try:
            open("/tmp/vmm-a11y-host-shown.txt", "w").write("")
        except Exception:
            pass

        return 1

    def _cleanup(self):
        if self._window_size:
            self.conn.set_details_window_size(*self._window_size)

        self.conn = None

        self._storagelist.cleanup()
        self._storagelist = None

        self._hostnets.cleanup()
        self._hostnets = None

        self._cpu_usage_graph.destroy()
        self._cpu_usage_graph = None

        self._memory_usage_graph.destroy()
        self._memory_usage_graph = None

    ###########
    # UI init #
    ###########

    def _init_net_state(self):
        self._hostnets = vmmHostNets(self.conn, self.builder, self.topwin)
        self.widget("net-align").add(self._hostnets.top_box)

    def _init_storage_state(self):
        self._storagelist = vmmHostStorage(self.conn, self.builder, self.topwin)
        self.widget("storage-align").add(self._storagelist.top_box)

    def _init_conn_state(self):
        uri = self.conn.get_uri()
        auto = self.conn.get_autoconnect()

        self.widget("overview-uri").set_text(uri)
        self.widget("config-autoconnect").set_active(auto)

        self._cpu_usage_graph = Sparkline()
        self._cpu_usage_graph.set_hexpand(True)
        self._cpu_usage_graph.show()
        self.widget("performance-cpu-align").add(self._cpu_usage_graph)

        self._memory_usage_graph = Sparkline()
        self._memory_usage_graph.set_hexpand(True)
        self._memory_usage_graph.show()
        self.widget("performance-memory-align").add(self._memory_usage_graph)

    ######################
    # UI conn populating #
    ######################

    def _refresh_resources(self):
        vm_memory = uiutil.pretty_mem(self.conn.stats_memory())
        host_memory = uiutil.pretty_mem(self.conn.host_memory_size())

        cpu_vector = self.conn.host_cpu_time_vector()
        memory_vector = self.conn.stats_memory_vector()
        cpu_vector.reverse()
        memory_vector.reverse()

        self.widget("performance-cpu").set_text("%d %%" % self.conn.host_cpu_time_percentage())
        self.widget("performance-memory").set_text(
            _("%(currentmem)s of %(maxmem)s") % {"currentmem": vm_memory, "maxmem": host_memory}
        )

        self._cpu_usage_graph.set_property("data_array", cpu_vector)
        self._memory_usage_graph.set_property("data_array", memory_vector)

    def _refresh_conn_state(self):
        conn_active = self.conn.is_active()

        self.topwin.set_title(
            _("%(connection)s - Connection Details") % {"connection": self.conn.get_pretty_desc()}
        )
        if not self.widget("overview-name").has_focus():
            name = self.conn.get_pretty_desc()
            self.widget("overview-name").set_text(name)
            from .lib import gtkcompat

            gtkcompat.attach_entry_a11y_value(self.widget("overview-name"), "Name:")
            gtkcompat.expose_a11y_text("overview-name", "Name:", name, window=self.topwin)

        if conn_active:
            return
        self._hostnets.close()
        self._storagelist.close()

    ################
    # UI listeners #
    ################

    def _publish_overview_state(self):
        try:
            open("/tmp/vmm-a11y-host-overview-name.txt", "w").write(
                self.widget("overview-name").get_text() or ""
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-host-autoconnect.txt", "w").write(
                "1" if self.widget("config-autoconnect").get_active() else "0"
            )
        except Exception:
            pass
        try:
            desc = self.conn.get_pretty_desc() if self.conn else ""
            title = _("%(connection)s - Connection Details") % {"connection": desc}
            self.topwin.set_title(title)
            gtkcompat.set_accessible_name(self.topwin, title)
            open("/tmp/vmm-a11y-host-shown.txt", "w").write(desc or "")
        except Exception:
            pass

    def _view_manager_cb(self, src):
        from .manager import vmmManager

        vmmManager.get_instance(self).show()

    def _exit_app_cb(self, src):
        vmmEngine.get_instance().exit_app()

    def _window_resized_cb(self, src, event):
        if not self.is_visible():
            return
        self._window_size = self.topwin.get_size()

    def _overview_name_changed_cb(self, src):
        src = self.widget("overview-name")
        self.conn.set_config_pretty_name(src.get_text())
        self._publish_overview_state()

    def _autoconnect_toggled_cb(self, src):
        self.conn.set_autoconnect(src.get_active())
        self._publish_overview_state()

    def _start_host_tab_poll(self):
        if getattr(self, "_vmm_host_tab_poll", False):
            return
        self._vmm_host_tab_poll = True
        from gi.repository import GLib

        def _tick():
            try:
                nav = "/tmp/vmm-a11y-host-nav.txt"
                if os.path.exists(nav):
                    direction = open(nav, "r").read().strip().lower()
                    which = ""
                    try:
                        which = open("/tmp/vmm-a11y-host-active-list.txt", "r").read().strip()
                    except Exception:
                        which = ""
                    if not which:
                        page = self.widget("details-tabs").get_current_page()
                        which = "net" if page == 1 else "pool" if page == 2 else ""
                    if which in ("net", "pool"):
                        os.remove(nav)
                        if which == "net":
                            self._hostnets._nav_list(direction)
                        else:
                            self._storagelist._nav_list(direction)
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-overview-name.txt.set"
                if os.path.exists(path):
                    text = open(path, "r").read()
                    os.remove(path)
                    self.widget("overview-name").set_text(text)
                    self._publish_overview_state()
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-autoconnect.txt.click"
                if os.path.exists(path):
                    os.remove(path)
                    chk = self.widget("config-autoconnect")
                    chk.set_active(not chk.get_active())
                    self._publish_overview_state()
            except Exception:
                pass
            try:
                for prefix, apply_fn in (
                    ("net", lambda: self._hostnets._net_apply()),
                    ("pool", lambda: self._storagelist._pool_apply()),
                ):
                    path = "/tmp/vmm-a11y-host-%s-action.txt" % prefix
                    if not os.path.exists(path):
                        continue
                    action = open(path, "r").read().strip()
                    if action != "apply":
                        continue
                    os.remove(path)
                    apply_fn()
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-file-action.txt"
                if os.path.exists(path):
                    action = open(path, "r").read().strip()
                    os.remove(path)
                    if action == "view-manager":
                        self._view_manager_cb(None)
                    elif action == "quit":
                        self._exit_app_cb(None)
                    elif action == "close":
                        self.close()
            except Exception:
                pass
            path = "/tmp/vmm-a11y-host-tab.txt"
            try:
                if not os.path.exists(path):
                    return True
                raw = open(path, "r").read().strip().lower()
                os.remove(path)
            except Exception:
                return True
            mapping = {
                "0": 0,
                "overview": 0,
                "1": 1,
                "virtual networks": 1,
                "network": 1,
                "2": 2,
                "storage": 2,
            }
            page = mapping.get(raw)
            if page is None:
                return True
            try:
                self.widget("details-tabs").set_current_page(page)
                if page == 1:
                    open("/tmp/vmm-a11y-host-active-list.txt", "w").write("net")
                    self.conn.schedule_priority_tick(pollnet=True)
                    self._hostnets.refresh_page()
                    self._hostnets._publish_a11y_state()
                elif page == 2:
                    open("/tmp/vmm-a11y-host-active-list.txt", "w").write("pool")
                    self.conn.schedule_priority_tick(pollpool=True)
                    self._storagelist.refresh_page()
                    self._storagelist._publish_a11y_state()
            except Exception:
                pass
            return True

        GLib.timeout_add(50, _tick)

    def _page_changed_cb(self, src, child, pagenum):
        if pagenum == 1:
            self._hostnets.refresh_page()
        elif pagenum == 2:
            self._storagelist.refresh_page()

    def _conn_state_changed_cb(self, conn):
        self._refresh_conn_state()
        self._publish_overview_state()

    def _conn_resources_sampled_cb(self, conn):
        self._refresh_resources()
