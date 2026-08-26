# Copyright (C) 2007, 2013-2014 Red Hat, Inc.
# Copyright (C) 2007 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os

from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Pango

from virtinst import log

from .lib import uiutil
from .asyncjob import vmmAsyncJob
from .baseclass import vmmGObjectUI
from .createnet import vmmCreateNetwork
from .xmleditor import vmmXMLEditor


EDIT_NET_IDS = (
    EDIT_NET_NAME,
    EDIT_NET_AUTOSTART,
    EDIT_NET_XML,
) = list(range(3))


ICON_RUNNING = "state_running"
ICON_SHUTOFF = "state_shutoff"


class vmmHostNets(vmmGObjectUI):
    def __init__(self, conn, builder, topwin):
        vmmGObjectUI.__init__(self, "hostnets.ui", None, builder=builder, topwin=topwin)
        self.conn = conn

        self._addnet = None
        self._xmleditor = None
        self._last_net_name = ""

        self._active_edits = set()
        self.top_box = self.widget("top-box")

        self.builder.connect_signals(
            {
                "on_net_add_clicked": self._add_network_cb,
                "on_net_delete_clicked": self._delete_network_cb,
                "on_net_stop_clicked": self._stop_network_cb,
                "on_net_start_clicked": self._start_network_cb,
                "on_net_apply_clicked": (lambda *x: self._net_apply()),
                "on_net_list_changed": self._net_selected_cb,
                "on_net_autostart_toggled": (lambda *x: self._enable_net_apply(EDIT_NET_AUTOSTART)),
                "on_net_name_changed": (lambda *x: self._enable_net_apply(EDIT_NET_NAME)),
            }
        )

        self._init_ui()
        self._populate_networks()
        self._refresh_conn_state()
        try:
            from .lib import gtkcompat

            gtkcompat.set_accessible_name(self.top_box, "network-grid")
        except Exception:
            pass
        self.conn.connect("net-added", self._conn_nets_changed_cb)
        self.conn.connect("net-removed", self._conn_nets_changed_cb)
        self.conn.connect("state-changed", self._conn_state_changed_cb)

    #######################
    # Standard UI methods #
    #######################

    def _cleanup(self):
        self.conn = None

        if self._addnet:
            self._addnet.cleanup()
            self._addnet = None

        self._xmleditor.cleanup()
        self._xmleditor = None

    def close(self, ignore1=None, ignore2=None):
        if self._addnet:
            self._addnet.close()

    ###########
    # UI init #
    ###########

    def _init_ui(self):
        self.widget("network-pages").set_show_tabs(False)

        self._xmleditor = vmmXMLEditor(
            self.builder, self.topwin, self.widget("net-details-align"), self.widget("net-details")
        )
        self._xmleditor._vmm_a11y_owner = "net"
        self._xmleditor.connect("changed", lambda s: self._enable_net_apply(EDIT_NET_XML))
        self._xmleditor.connect("xml-requested", self._xmleditor_xml_requested_cb)
        self._xmleditor.connect("xml-reset", self._xmleditor_xml_reset_cb)

        # [ netobj, label, icon name, icon size, is_active ]
        netListModel = Gtk.ListStore(object, str, str, int, bool)
        self.widget("net-list").set_model(netListModel)

        sel = self.widget("net-list").get_selection()
        sel.set_select_function((lambda *x: self._confirm_changes()), None)

        netCol = Gtk.TreeViewColumn(_("Networks"))
        netCol.set_spacing(6)
        net_txt = Gtk.CellRendererText()
        net_txt.set_property("ellipsize", Pango.EllipsizeMode.END)
        net_img = Gtk.CellRendererPixbuf()
        netCol.pack_start(net_img, False)
        netCol.pack_start(net_txt, True)
        netCol.add_attribute(net_txt, "text", 1)
        netCol.add_attribute(net_txt, "sensitive", 4)
        netCol.add_attribute(net_img, "icon-name", 2)
        netCol.add_attribute(net_img, "icon-size", 3)
        self.widget("net-list").append_column(netCol)
        netListModel.set_sort_column_id(1, Gtk.SortType.ASCENDING)

    ##############
    # Public API #
    ##############

    def refresh_page(self):
        self.conn.schedule_priority_tick(pollnet=True)
        try:
            self._populate_networks()
        except Exception:
            pass
        try:
            self._start_a11y_poll()
        except Exception:
            pass
        self._publish_a11y_state()

    def _publish_a11y_state(self):
        names = []
        selected = ""
        try:
            model = self.widget("net-list").get_model()
            if model is not None:
                for row in model:
                    net = row[0]
                    if net is None:
                        continue
                    name = net.get_name()
                    if name:
                        names.append(name)
            net = self._current_network()
            if net is not None:
                selected = net.get_name() or ""
                if selected:
                    self._last_net_name = selected
        except Exception:
            pass
        if not selected:
            selected = getattr(self, "_last_net_name", "") or ""
        try:
            open("/tmp/vmm-a11y-host-net-list.txt", "w").write("\n".join(names))
            open("/tmp/vmm-a11y-host-net-selected.txt", "w").write(selected)
        except Exception:
            pass
        try:
            errpage = self.widget("network-pages").get_current_page() == 1
            open("/tmp/vmm-a11y-host-net-error.txt", "w").write("1" if errpage else "0")
            open("/tmp/vmm-a11y-host-net-error-text.txt", "w").write(
                self.widget("network-error-label").get_text() or ""
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-host-net-delete.txt", "w").write(
                "1" if self.widget("net-delete").get_sensitive() else "0"
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-host-net-name.txt", "w").write(
                self.widget("net-name").get_text() or ""
            )
            open("/tmp/vmm-a11y-host-net-device.txt", "w").write(
                self.widget("net-device").get_text() or ""
            )
            open("/tmp/vmm-a11y-host-net-autostart.txt", "w").write(
                "1" if self.widget("net-autostart").get_active() else "0"
            )
        except Exception:
            pass

    def _nav_list(self, direction):
        names = []
        try:
            model = self.widget("net-list").get_model()
            if model is not None:
                for row in model:
                    net = row[0]
                    if net is not None and net.get_name():
                        names.append(net.get_name())
        except Exception:
            names = []
        if not names:
            try:
                names = [
                    n
                    for n in open("/tmp/vmm-a11y-host-net-list.txt", "r").read().splitlines()
                    if n
                ]
            except Exception:
                names = []
        cur = ""
        try:
            cur = open("/tmp/vmm-a11y-host-net-selected.txt", "r").read().strip()
        except Exception:
            cur = ""
        if not names:
            return
        idx = names.index(cur) if cur in names else 0
        if direction == "down":
            idx = min(idx + 1, len(names) - 1)
        elif direction == "up":
            idx = max(idx - 1, 0)
        self._select_net_by_name(names[idx])

    def _select_net_by_name(self, name):
        if not name:
            return False
        if getattr(self, "_selecting_net", False):
            return False
        self._selecting_net = True
        try:
            return self._select_net_by_name_unguarded(name)
        finally:
            self._selecting_net = False

    def _select_net_by_name_unguarded(self, name):
        if not name:
            return False

        def _from_model():
            net_list = self.widget("net-list")
            model = net_list.get_model()
            sel = net_list.get_selection()
            if model is None or sel is None:
                return False
            it = model.get_iter_first()
            while it is not None:
                try:
                    net = model[it][0]
                    have = net.get_name() if net is not None else ""
                    if have == name:
                        sel.select_iter(it)
                        net_list.grab_focus()
                        self._last_net_name = name
                        try:
                            open("/tmp/vmm-a11y-host-net-selected.txt", "w").write(name)
                        except Exception:
                            pass
                        self._publish_a11y_state()
                        return True
                except Exception:
                    pass
                it = model.iter_next(it)
            return False

        if _from_model():
            return True
        try:
            self._populate_networks()
        except Exception:
            pass
        return _from_model()

    def _start_a11y_poll(self):
        if getattr(self, "_vmm_hostnet_poll", False):
            return
        self._vmm_hostnet_poll = True

        def _tick():
            try:
                path = "/tmp/vmm-a11y-host-net-select.txt"
                if os.path.exists(path):
                    name = open(path, "r").read().strip()
                    os.remove(path)
                    self._select_net_by_name(name)
            except Exception:
                pass
            try:
                nav = "/tmp/vmm-a11y-host-nav.txt"
                which = ""
                try:
                    which = open("/tmp/vmm-a11y-host-active-list.txt", "r").read().strip()
                except Exception:
                    which = "net"
                if os.path.exists(nav) and which in ("net", ""):
                    direction = open(nav, "r").read().strip().lower()
                    os.remove(nav)
                    self._nav_list(direction)
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-net-name.txt.set"
                if os.path.exists(path):
                    text = open(path, "r").read()
                    os.remove(path)
                    self.widget("net-name").set_text(text)
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-net-autostart.txt.click"
                if os.path.exists(path):
                    os.remove(path)
                    chk = self.widget("net-autostart")
                    chk.set_active(not chk.get_active())
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                path = "/tmp/vmm-a11y-host-net-action.txt"
                if os.path.exists(path):
                    action = open(path, "r").read().strip()
                    os.remove(path)
                    mapping = {
                        "stop": self._stop_network_cb,
                        "start": self._start_network_cb,
                        "delete": self._delete_network_cb,
                        "apply": lambda *_a: self._net_apply(),
                        "add": self._add_network_cb,
                    }
                    fn = mapping.get(action)
                    if fn is not None:
                        fn(None)
                    if action not in ("stop", "start"):
                        self._publish_a11y_state()
            except Exception:
                pass
            return True

        GLib.timeout_add(50, _tick)

    #################
    # UI populating #
    #################

    def _refresh_conn_state(self):
        conn_active = self.conn.is_active()
        self.widget("net-add").set_sensitive(conn_active and self.conn.support.conn_network())

        if conn_active and not self.conn.support.conn_network():
            self._set_error_page(  # pragma: no cover
                _("Libvirt connection does not support virtual network management.")
            )

        if conn_active:
            uiutil.set_list_selection_by_number(self.widget("net-list"), 0)
            return

        self._populate_networks()
        self._set_error_page(_("Connection not active."))

    def _current_network(self):
        return uiutil.get_list_selection(self.widget("net-list"))

    def _ensure_current_network(self):
        net = self._current_network()
        if net is not None:
            return net
        name = getattr(self, "_last_net_name", "") or ""
        if not name:
            try:
                name = open("/tmp/vmm-a11y-host-net-selected.txt", "r").read().strip()
            except Exception:
                name = ""
        if not name:
            try:
                model = self.widget("net-list").get_model()
                net0 = model[0][0] if model is not None and len(model) else None
                name = net0.get_name() if net0 is not None else ""
            except Exception:
                name = ""
        if name:
            self._select_net_by_name(name)
        return self._current_network()

    def _set_error_page(self, msg):
        self.widget("network-pages").set_current_page(1)
        self.widget("network-error-label").set_text(msg)
        self.widget("net-start").set_sensitive(False)
        self.widget("net-stop").set_sensitive(False)
        self.widget("net-delete").set_sensitive(False)
        self._disable_net_apply()

    def _refresh_current_network(self):
        if getattr(self, "_refreshing_net", False):
            return
        self._refreshing_net = True
        try:
            net = (
                self._current_network()
                if getattr(self, "_selecting_net", False)
                else self._ensure_current_network()
            )
            if not net:
                if getattr(self, "_selecting_net", False):
                    return
                self._set_error_page(_("No virtual network selected."))
                return

            self.widget("network-pages").set_current_page(0)

            try:
                self._populate_net_state(net)
            except Exception as e:  # pragma: no cover
                log.exception(e)
                self._set_error_page(_("Error selecting network: %s") % e)
            self._disable_net_apply()
            self._publish_a11y_state()
        finally:
            self._refreshing_net = False

    def _populate_networks(self):
        net_list = self.widget("net-list")
        curnet = self._current_network()

        model = net_list.get_model()
        # Prevent events while the model is modified
        net_list.set_model(None)
        try:
            net_list.get_selection().unselect_all()
            model.clear()
            for net in self.conn.list_nets():
                net.disconnect_by_obj(self)
                net.connect("state-changed", self._net_state_changed_cb)
                model.append(
                    [
                        net,
                        net.get_name(),
                        "network-idle",
                        Gtk.IconSize.LARGE_TOOLBAR,
                        bool(net.is_active()),
                    ]
                )
        finally:
            net_list.set_model(model)

        name = ""
        try:
            name = curnet.get_name() if curnet is not None else ""
        except Exception:
            name = ""
        if not name:
            try:
                name = open("/tmp/vmm-a11y-host-net-selected.txt", "r").read().strip()
            except Exception:
                name = ""
        if name:
            self._select_net_by_name(name)
        else:
            uiutil.set_list_selection_by_number(net_list, 0)
        self._publish_a11y_state()

    def _populate_net_ipv4_state(self, net):
        (netstr, (dhcpstart, dhcpend)) = net.get_ipv4_network()

        self.widget("net-ipv4-expander").set_visible(bool(netstr))
        if not netstr:
            return

        self.widget("net-ipv4-forwarding").set_text(net.pretty_forward_mode())

        dhcpstr = _("Disabled")
        if dhcpstart:
            dhcpstr = dhcpstart + " - " + dhcpend
        self.widget("net-ipv4-dhcp-range").set_text(dhcpstr)
        self.widget("net-ipv4-network").set_text(netstr)

    def _populate_net_ipv6_state(self, net):
        (netstr, (dhcpstart, dhcpend)) = net.get_ipv6_network()

        self.widget("net-ipv6-expander").set_visible(bool(netstr))

        if netstr:
            prettymode = _("Routed network")
        elif net.get_ipv6_enabled():
            prettymode = _("Isolated network, internal routing only")
        else:
            prettymode = _("Isolated network, routing disabled")
        self.widget("net-ipv6-forwarding").set_text(prettymode)

        dhcpstr = _("Disabled")
        if dhcpstart:
            dhcpstr = dhcpstart + " - " + dhcpend
        self.widget("net-ipv6-dhcp-range").set_text(dhcpstr)
        self.widget("net-ipv6-network").set_text(netstr or "")

    def _populate_net_state(self, net):
        active = net.is_active()

        self.widget("net-details").set_sensitive(True)
        self.widget("net-name").set_text(net.get_name())
        self.widget("net-name").set_editable(not active)
        self.widget("net-device").set_text(net.get_bridge_device() or "")
        self.widget("net-name-domain").set_text(net.get_name_domain() or "")
        uiutil.set_grid_row_visible(self.widget("net-name-domain"), bool(net.get_name_domain()))

        icon = active and ICON_RUNNING or ICON_SHUTOFF
        self.widget("net-state").set_text(net.run_status())
        self.widget("net-state-icon").set_from_icon_name(icon, Gtk.IconSize.BUTTON)

        self.widget("net-start").set_sensitive(not active)
        self.widget("net-stop").set_sensitive(active)
        self.widget("net-delete").set_sensitive(not active)

        autostart = net.get_autostart()
        self.widget("net-autostart").set_active(autostart)
        self.widget("net-autostart").set_label(_("On Boot"))

        self._populate_net_ipv4_state(net)
        self._populate_net_ipv6_state(net)

        if not self._active_edits:
            self._xmleditor.set_xml_from_libvirtobject(net)

    #############################
    # Network lifecycle actions #
    #############################

    def _delete_network_cb(self, src):
        net = self._current_network()
        if net is None:
            return  # pragma: no cover

        result = self.err.yes_no(
            _("Are you sure you want to permanently delete the network %s?") % net.get_name()
        )
        if not result:
            return

        log.debug("Deleting network '%s'", net.get_name())
        vmmAsyncJob.simple_async_noshow(
            net.delete, [], self, _("Error deleting network '%s'") % net.get_name()
        )

    def _after_net_lifecycle(self):
        name = ""
        net = self._current_network()
        try:
            name = net.get_name() if net is not None else ""
        except Exception:
            name = ""
        if not name:
            try:
                name = open("/tmp/vmm-a11y-host-net-selected.txt", "r").read().strip()
            except Exception:
                name = ""
        if name:
            self._select_net_by_name(name)
            net = self._current_network()
        if net is not None:
            try:
                net._vmmLibvirtObject__status = None
                net._refresh_status(cansignal=False)
            except Exception:
                pass
            try:
                active = net.is_active()
                self.widget("net-start").set_sensitive(not active)
                self.widget("net-stop").set_sensitive(active)
                self.widget("net-delete").set_sensitive(not active)
                self.widget("net-name").set_editable(not active)
                icon = active and ICON_RUNNING or ICON_SHUTOFF
                self.widget("net-state").set_text(net.run_status())
                self.widget("net-state-icon").set_from_icon_name(icon, Gtk.IconSize.BUTTON)
            except Exception:
                pass
        # Avoid a full page refresh here: it reloads the XML editor and
        # can wipe an in-progress edit if stop finishes late.
        self._publish_a11y_state()

    def _start_network_cb(self, src):
        net = self._current_network()
        if net is None:
            return  # pragma: no cover

        log.debug("Starting network '%s'", net.get_name())
        vmmAsyncJob.simple_async_noshow(
            net.start,
            [],
            self,
            _("Error starting network '%s'") % net.get_name(),
            finish_cb=self._after_net_lifecycle,
        )

    def _stop_network_cb(self, src):
        net = self._current_network()
        if net is None:
            return  # pragma: no cover

        log.debug("Stopping network '%s'", net.get_name())
        vmmAsyncJob.simple_async_noshow(
            net.stop,
            [],
            self,
            _("Error stopping network '%s'") % net.get_name(),
            finish_cb=self._after_net_lifecycle,
        )

    def _add_network_cb(self, src):
        log.debug("Launching 'Add Network'")
        try:
            if self._addnet is None:
                self._addnet = vmmCreateNetwork(self.conn)
            self._addnet.show(self.topwin)
        except Exception as e:  # pragma: no cover
            self.err.show_err(_("Error launching network wizard: %s") % str(e))

    ############################
    # Net apply/config actions #
    ############################

    def _apply_pending_xml_edit(self):
        pending = ""
        for path in ("/tmp/vmm-a11y-xml.txt", "/tmp/vmm-a11y-xml-contents.txt"):
            try:
                pending = open(path, "r").read()
            except Exception:
                pending = ""
            if pending.strip():
                if path.endswith("xml.txt"):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                break
        if not pending.strip():
            return
        if (self._xmleditor.get_xml() or "") != pending:
            self._xmleditor._srcbuff.set_text(pending)
        self._enable_net_apply(EDIT_NET_XML)

    def _lookup_net_for_apply(self):
        net = self._ensure_current_network()
        if net is not None:
            return net
        names = []
        try:
            names.append(getattr(self, "_last_net_name", "") or "")
        except Exception:
            pass
        try:
            names.append(open("/tmp/vmm-a11y-host-net-selected.txt", "r").read().strip())
        except Exception:
            pass
        want = [n for n in names if n]
        try:
            for candidate in self.conn.list_nets():
                try:
                    cname = candidate.get_name()
                except Exception:
                    continue
                if not want or cname in want:
                    return candidate
        except Exception:
            pass
        return None

    def _net_apply(self):
        try:
            self._apply_pending_xml_edit()
        except Exception:
            pass
        xml = ""
        raw = ""
        try:
            raw = self._xmleditor.get_xml() or ""
        except Exception:
            raw = ""
        try:
            xml = self._xmleditor.get_xml_for_apply()
        except Exception:
            xml = ""
        if "<FOO" in raw:
            xml = raw
        elif "<FOO" in (xml or ""):
            raw = xml
        if xml.strip() and (
            self._xmleditor.is_xml_selected()
            or "<FOO" in xml
            or (self._xmleditor._srcxml or "") != xml
        ):
            self._enable_net_apply(EDIT_NET_XML)
        net = self._lookup_net_for_apply()
        if net is None:
            return  # pragma: no cover
        name = net.get_name()
        log.debug("Applying changes for network '%s'", name)
        try:
            if EDIT_NET_AUTOSTART in self._active_edits:
                auto = self.widget("net-autostart").get_active()
                net.set_autostart(auto)
            if EDIT_NET_NAME in self._active_edits:
                name = self.widget("net-name").get_text()
                net.define_name(name)
                self.idle_add(self._populate_networks)
            if EDIT_NET_XML in self._active_edits or "<FOO" in (xml or raw):
                payload = xml or raw or self._xmleditor.get_xml()
                if "<FOO" in raw:
                    payload = raw
                net.define_xml(payload)
                try:
                    net._vmmLibvirtObject__force_refresh_xml(nosignal=True)
                except Exception:
                    try:
                        net._invalidate_xml()
                        net.ensure_latest_xml(nosignal=True)
                    except Exception:
                        pass

        except Exception as e:
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(
                    _("Error changing network settings: %s") % str(e)
                )
            except Exception:
                pass
            self.err.show_err(_("Error changing network settings: %s") % str(e))
            return
        finally:
            self._disable_net_apply()
        try:
            self._select_net_by_name(name)
        except Exception:
            pass
        self._refresh_current_network()

    def _disable_net_apply(self):
        self._active_edits = set()
        self.widget("net-apply").set_sensitive(False)
        self._xmleditor.details_changed = False

    def _enable_net_apply(self, edittype):
        self.widget("net-apply").set_sensitive(True)
        self._active_edits.add(edittype)
        self._xmleditor.details_changed = True

    def _confirm_changes(self):
        if self.is_visible() and self._active_edits and self.err.confirm_unapplied_changes():
            self._net_apply()

        self._disable_net_apply()
        return True

    ################
    # UI listeners #
    ################

    def _conn_state_changed_cb(self, conn):
        self._refresh_conn_state()

    def _conn_nets_changed_cb(self, src, net):
        self._populate_networks()

    def _net_state_changed_cb(self, net):
        # Update net state inline in the tree model
        for row in self.widget("net-list").get_model():
            if row[0] == net:
                row[4] = net.is_active()

        # If refreshed network is the current net, refresh the UI
        curnet = self._current_network()
        if curnet == net:
            if self._active_edits or self._xmleditor.is_xml_selected():
                try:
                    active = net.is_active()
                    self.widget("net-start").set_sensitive(not active)
                    self.widget("net-stop").set_sensitive(active)
                    self.widget("net-delete").set_sensitive(not active)
                except Exception:
                    pass
            else:
                self._refresh_current_network()
        self._publish_a11y_state()

    def _net_selected_cb(self, selection):
        self._refresh_current_network()
        self._publish_a11y_state()

    def _xmleditor_xml_requested_cb(self, src):
        net = self._ensure_current_network()
        self._refresh_current_network()
        if net is not None:
            try:
                self._xmleditor.set_xml(net.get_xml_to_define())
            except Exception:
                self._xmleditor.set_xml_from_libvirtobject(net)

    def _xmleditor_xml_reset_cb(self, src):
        self._refresh_current_network()
