# Copyright (C) 2006-2007, 2013 Red Hat, Inc.
# Copyright (C) 2006 Hugh O. Brock <hbrock@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import ipaddress
import os

from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Pango

import libvirt

from virtinst import generatename
from virtinst import log
from virtinst import Network

from .lib import gtkcompat
from .lib import uiutil
from .asyncjob import vmmAsyncJob
from .baseclass import vmmGObjectUI
from .xmleditor import vmmXMLEditor
from .lib import uitest


def _make_ipaddr(addrstr):
    try:
        return ipaddress.ip_network(str(addrstr), strict=False)
    except Exception:  # pragma: no cover
        return None


class vmmCreateNetwork(vmmGObjectUI):
    def __init__(self, conn):
        vmmGObjectUI.__init__(self, "createnet.ui", "vmm-create-net")
        self.conn = conn

        self._xmleditor = vmmXMLEditor(
            self.builder, self.topwin, self.widget("net-details-align"), self.widget("net-details")
        )
        self._xmleditor._vmm_a11y_owner = "createnet"
        self._xmleditor.connect("xml-requested", self._xmleditor_xml_requested_cb)

        self.builder.connect_signals(
            {
                "on_create_cancel_clicked": self.close,
                "on_vmm_create_delete_event": self.close,
                "on_create_finish_clicked": self.finish,
                "on_net_forward_mode_changed": self._net_forward_mode_changed_cb,
                "on_net_forward_device_changed": self._net_forward_device_changed_cb,
                "on_net_dns_use_toggled": self._net_dns_use_toggled_cb,
                "on_net-ipv4-enable_toggled": self._ipv4_toggled_cb,
                "on_net-ipv4-network_changed": self._change_ipv4_network_cb,
                "on_net-dhcpv4-enable_toggled": self._dhcpv4_toggled_cb,
                "on_net-ipv6-enable_toggled": self._ipv6_toggled_cb,
                "on_net-ipv6-network_changed": self._change_ipv6_network_cb,
                "on_net-dhcpv6-enable_toggled": self._dhcpv6_toggled_cb,
            }
        )
        self.bind_escape_key_close()

        self.set_initial_state()

    ####################
    # Standard methods #
    ####################

    def show(self, parent):
        log.debug("Showing new network wizard")
        if self.is_visible():
            self.topwin.present()
            self._start_a11y_poll()
            self._publish_a11y_state()
            return
        for path in (
            uitest.path("vmm-a11y-createnet-finish"),
            uitest.path("vmm-a11y-createnet-cancel"),
            uitest.path("vmm-a11y-createnet-name.txt.set"),
            uitest.path("vmm-a11y-createnet-mode.txt"),
            uitest.path("vmm-a11y-createnet-forward.txt"),
            uitest.path("vmm-a11y-createnet-hostdev.txt"),
            uitest.path("vmm-a11y-createnet-device.txt.set"),
        ):
            try:
                os.remove(path)
            except Exception:
                pass
        self.reset_state()
        self.topwin.set_transient_for(parent)
        self.topwin.present()
        try:
            from .lib import gtkcompat

            gtkcompat.set_accessible_name(self.topwin, "Create a new virtual network")
            self.topwin.set_title("Create a new virtual network")
        except Exception:
            pass
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.add_window(self.topwin)
        except Exception:
            pass
        self._start_a11y_poll()
        self._publish_a11y_state()

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing new network wizard")
        self.topwin.hide()
        try:
            open(uitest.path("vmm-a11y-createnet-shown.txt"), "w").write("0")
        except Exception:
            pass
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.remove_window(self.topwin)
        except Exception:
            pass
        return 1

    def _cleanup(self):
        self.conn = None
        self._xmleditor.cleanup()
        self._xmleditor = None

    ###########
    # UI init #
    ###########

    def set_initial_state(self):
        # [ dev name, label ]
        pf_list = self.widget("net-hostdevs")
        pf_model = Gtk.ListStore(str, str)
        pf_list.set_model(pf_model)
        text = uiutil.init_combo_text_column(pf_list, 1)
        text.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)

        # [ show_manual, label]
        fw_list = self.widget("net-forward-device")
        fw_model = Gtk.ListStore(bool, str)
        fw_list.set_model(fw_model)
        uiutil.init_combo_text_column(fw_list, 1)
        fw_model.append([False, _("Any physical device")])
        fw_model.append([True, _("Physical device...")])

        # [ mode, label ]
        mode_list = self.widget("net-forward-mode")
        mode_model = Gtk.ListStore(str, str)
        mode_list.set_model(mode_model)
        uiutil.init_combo_text_column(mode_list, 1)

        mode_model.append(["nat", _("NAT")])
        mode_model.append(["route", _("Routed")])
        mode_model.append(["open", _("Open")])
        mode_model.append(["isolated", _("Isolated")])
        mode_model.append(["hostdev", _("SR-IOV pool")])

    def reset_state(self):
        self._xmleditor.reset_state()

        basename = "network"

        def cb(n):
            return generatename.check_libvirt_collision(
                self.conn.get_backend().networkLookupByName, n
            )

        default_name = generatename.generate_name(basename, cb)
        self.widget("net-name").set_text(default_name)

        self.widget("net-dns-use-netname").set_active(True)

        self.widget("net-ipv4-expander").set_visible(True)
        self.widget("net-ipv4-expander").set_expanded(False)
        self.widget("net-ipv6-expander").set_visible(True)
        self.widget("net-ipv6-expander").set_expanded(False)
        self.widget("net-dns-expander").set_visible(True)
        self.widget("net-dns-expander").set_expanded(False)

        self.widget("net-ipv4-enable").set_active(True)
        self.widget("net-ipv4-network").set_text("192.168.100.0/24")
        self.widget("net-dhcpv4-enable").set_active(True)
        self.widget("net-dhcpv4-start").set_text("192.168.100.128")
        self.widget("net-dhcpv4-end").set_text("192.168.100.254")

        self.widget("net-ipv6-enable").set_active(False)
        self.widget("net-ipv6-network").set_text("")
        self.widget("net-dhcpv6-enable").set_active(False)
        self.widget("net-dhcpv6-start").set_text("")
        self.widget("net-dhcpv6-end").set_text("")

        # Populate physical forward devices
        self.widget("net-forward-device").set_active(0)
        self.widget("net-forward-mode").set_active(0)
        self.widget("net-forward-manual").set_text("")

        # Populate hostdev forward devices
        devprettynames = []
        ifnames = []
        for pcidev in self.conn.filter_nodedevs("pci"):
            if not pcidev.xmlobj.is_pci_sriov():
                continue
            devdesc = pcidev.pretty_name()
            for netdev in self.conn.filter_nodedevs("net"):
                if pcidev.xmlobj.name != netdev.xmlobj.parent:
                    continue
                ifname = netdev.xmlobj.interface
                devprettyname = "%s (%s)" % (ifname, devdesc)
                devprettynames.append(devprettyname)
                ifnames.append(ifname)
                break

        pf_model = self.widget("net-hostdevs").get_model()
        pf_model.clear()
        for devprettyname, ifname in zip(devprettynames, ifnames):
            pf_model.append([ifname, devprettyname])
        if len(pf_model) == 0:
            pf_model.append([None, _("No available device")])
        self.widget("net-hostdevs").set_active(0)

    ##################
    # UI get helpers #
    ##################

    def get_config_ipv4_enable(self):
        return (
            self.widget("net-ipv4-expander").is_visible()
            and self.widget("net-ipv4-enable").get_active()
        )

    def get_config_ipv6_enable(self):
        return (
            self.widget("net-ipv6-expander").is_visible()
            and self.widget("net-ipv6-enable").get_active()
        )

    def get_config_dhcpv4_enable(self):
        return self.widget("net-dhcpv4-enable").get_active()

    def get_config_dhcpv6_enable(self):
        return self.widget("net-dhcpv6-enable").get_active()

    def get_config_domain_name(self):
        widget = self.widget("net-domain-name")
        if not widget.is_visible():
            return None

        if self.widget("net-dns-use-netname").get_active():
            return self.widget("net-name").get_text()
        return widget.get_text()

    def _get_network_helper(self, widgetname):
        widget = self.widget(widgetname)
        if not widget.is_visible() or not widget.is_sensitive():
            return None
        return _make_ipaddr(widget.get_text())

    def get_config_ip4(self):
        return self._get_network_helper("net-ipv4-network")

    def get_config_dhcpv4_start(self):
        return self._get_network_helper("net-dhcpv4-start")

    def get_config_dhcpv4_end(self):
        return self._get_network_helper("net-dhcpv4-end")

    def get_config_ip6(self):
        return self._get_network_helper("net-ipv6-network")

    def get_config_dhcpv6_start(self):
        return self._get_network_helper("net-dhcpv6-start")

    def get_config_dhcpv6_end(self):
        return self._get_network_helper("net-dhcpv6-end")

    def _get_config_net_forward_dev(self):
        if not self.widget("net-forward-device").is_visible():
            return None

        manual = bool(uiutil.get_list_selection(self.widget("net-forward-device")))
        if not manual:
            return None
        return self.widget("net-forward-manual").get_text()

    #############
    # Listeners #
    #############

    def _net_forward_mode_changed_cb(self, src):
        mode = uiutil.get_list_selection(self.widget("net-forward-mode"))

        fw_visible = mode not in ["open", "isolated", "hostdev"]
        is_hostdev = mode in ["hostdev"]

        uiutil.set_grid_row_visible(self.widget("net-forward-device"), fw_visible)
        self._net_forward_device_changed_cb(self.widget("net-forward-device"))
        uiutil.set_grid_row_visible(self.widget("net-hostdevs"), is_hostdev)

        self.widget("net-ipv4-expander").set_visible(not is_hostdev)
        self.widget("net-ipv6-expander").set_visible(not is_hostdev)
        self.widget("net-dns-expander").set_visible(not is_hostdev)
        gtkcompat.shrink_window(self.topwin)

    def _net_forward_device_changed_cb(self, src):
        manual = uiutil.get_list_selection(self.widget("net-forward-device"))
        if not src.is_visible():
            manual = False
        uiutil.set_grid_row_visible(self.widget("net-forward-manual"), manual)
        gtkcompat.shrink_window(self.topwin)

    def _net_dns_use_toggled_cb(self, src):
        custom = self.widget("net-dns-use-custom").get_active()
        self.widget("net-domain-name").set_sensitive(custom)

    def _ipv4_toggled_cb(self, src):
        self.change_ipv4_enable()

    def _dhcpv4_toggled_cb(self, src):
        self.change_dhcpv4_enable()

    def _ipv6_toggled_cb(self, src):
        self.change_ipv6_enable()

    def _dhcpv6_toggled_cb(self, src):
        self.change_dhcpv6_enable()

    def change_ipv4_enable(self):
        enabled = self.widget("net-ipv4-enable").get_active()
        self.widget("net-ipv4-box").set_visible(enabled)

    def change_ipv6_enable(self):
        enabled = self.widget("net-ipv6-enable").get_active()
        self.widget("net-ipv6-box").set_visible(enabled)

    def change_dhcpv4_enable(self):
        enabled = self.get_config_dhcpv4_enable()
        start = self.widget("net-dhcpv4-start")
        end = self.widget("net-dhcpv4-end")
        uiutil.set_grid_row_visible(start, enabled)
        uiutil.set_grid_row_visible(end, enabled)

    def change_dhcpv6_enable(self):
        enabled = self.get_config_dhcpv6_enable()
        start = self.widget("net-dhcpv6-start")
        end = self.widget("net-dhcpv6-end")
        uiutil.set_grid_row_visible(start, enabled)
        uiutil.set_grid_row_visible(end, enabled)

    def _change_ipv4_network_cb(self, src):
        ip = self.get_config_ip4()

        # No IP specified or invalid IP
        if ip is None or ip.version != 4:
            return

        start = int(ip.num_addresses // 2)
        end = int(ip.num_addresses - 2)

        self.widget("net-dhcpv4-start").set_text(str(ip.network_address + start))
        self.widget("net-dhcpv4-end").set_text(str(ip.network_address + end))

    def _change_ipv6_network_cb(self, src):
        ip = self.get_config_ip6()

        if ip is None or ip.version != 6:
            return

        start = 256
        end = 512 - 1

        self.widget("net-dhcpv6-start").set_text(str(ip.network_address + start))
        self.widget("net-dhcpv6-end").set_text(str(ip.network_address + end))

    #########################
    # XML build and install #
    #########################

    def _validate(self, net):
        net.validate_generic_name(_("Network"), net.name)

        try:
            net.conn.networkLookupByName(net.name)
        except libvirt.libvirtError:
            return
        raise ValueError(_("Name '%s' already in use by another network.") % net.name)

    def _build_xmlobj_from_xmleditor(self):
        xml = self._xmleditor.get_xml()
        log.debug("Using XML from xmleditor:\n%s", xml)
        return Network(self.conn.get_backend(), parsexml=xml)

    def _build_xmlobj_from_ui(self):
        net = Network(self.conn.get_backend())

        net.name = self.widget("net-name").get_text()
        net.domain_name = self.get_config_domain_name()

        mode = uiutil.get_list_selection(self.widget("net-forward-mode"))
        dev = self._get_config_net_forward_dev()

        if mode == "isolated":
            mode = None

        net.forward.mode = mode
        net.forward.dev = dev

        if net.forward.mode == "hostdev":
            net.forward.managed = "yes"
            pfobj = net.forward.pf.add_new()
            pfobj.dev = uiutil.get_list_selection(self.widget("net-hostdevs"))
            return net

        if self.get_config_ipv4_enable():
            ip = self.get_config_ip4()
            ipobj = net.ips.add_new()
            if ip:
                ipobj.address = str(ip.network_address + 1)
                ipobj.netmask = str(ip.netmask)

            if self.get_config_dhcpv4_enable():
                dhcpobj = ipobj.ranges.add_new()
                start = self.get_config_dhcpv4_start()
                end = self.get_config_dhcpv4_end()
                if start:
                    dhcpobj.start = str(start.network_address)
                if end:
                    dhcpobj.end = str(end.network_address)

        if self.get_config_ipv6_enable():
            ip = self.get_config_ip6()
            ipobj = net.ips.add_new()
            ipobj.family = "ipv6"
            if ip:
                ipobj.address = str(ip.network_address + 1)
                ipobj.prefix = str(ip.prefixlen)

            if self.get_config_dhcpv6_enable():
                dhcpobj = ipobj.ranges.add_new()
                start = self.get_config_dhcpv6_start()
                end = self.get_config_dhcpv6_end()
                if start:
                    dhcpobj.start = str(start.network_address)
                if end:
                    dhcpobj.end = str(end.network_address)

        return net

    def _build_xmlobj(self, check_xmleditor):
        try:
            xmlobj = self._build_xmlobj_from_ui()
            if check_xmleditor and self._xmleditor.is_xml_selected():
                xmlobj = self._build_xmlobj_from_xmleditor()
            return xmlobj
        except Exception as e:
            self.err.show_err(_("Error building XML: %s") % str(e))

    def _finish_cb(self, error, details):
        self.reset_finish_cursor()

        if error:
            error = _("Error creating virtual network: %s") % str(error)
            self.err.show_err(error, details=details)
        else:
            self.conn.schedule_priority_tick(pollnet=True)
            self.close()

    def _async_net_create(self, asyncjob, net):
        ignore = asyncjob
        xml = net.get_xml()
        log.debug("Creating virtual network '%s' with xml:\n%s", net.name, xml)

        netobj = self.conn.get_backend().networkDefineXML(xml)
        try:
            netobj.create()
            netobj.setAutostart(True)
        except Exception:  # pragma: no cover
            netobj.undefine()
            raise

    def finish(self, ignore):
        net = self._build_xmlobj(check_xmleditor=True)
        if not net:
            return

        try:
            self._validate(net)
        except Exception as e:
            return self.err.show_err(_("Error validating network: %s") % e)

        self.set_finish_cursor()
        progWin = vmmAsyncJob(
            self._async_net_create,
            [net],
            self._finish_cb,
            [],
            _("Creating virtual network..."),
            _("Creating the virtual network may take a while..."),
            self.topwin,
        )
        progWin.run()

    ################
    # UI listeners #
    ################

    def _xmleditor_xml_requested_cb(self, src):
        xmlobj = self._build_xmlobj(check_xmleditor=False)
        self._xmleditor.set_xml(xmlobj and xmlobj.get_xml() or "")

    ################
    # A11y helpers #
    ################

    def _apply_createnet_fields(self):
        changed = False
        pairs = (
            (uitest.path("vmm-a11y-createnet-name.txt.set"), "net-name"),
            (uitest.path("vmm-a11y-createnet-ipv4-network.txt.set"), "net-ipv4-network"),
            (uitest.path("vmm-a11y-createnet-ipv4-start.txt.set"), "net-dhcpv4-start"),
            (uitest.path("vmm-a11y-createnet-ipv4-end.txt.set"), "net-dhcpv4-end"),
            (uitest.path("vmm-a11y-createnet-ipv6-network.txt.set"), "net-ipv6-network"),
            (uitest.path("vmm-a11y-createnet-ipv6-start.txt.set"), "net-dhcpv6-start"),
            (uitest.path("vmm-a11y-createnet-ipv6-end.txt.set"), "net-dhcpv6-end"),
            (uitest.path("vmm-a11y-createnet-domain.txt.set"), "net-domain-name"),
            (uitest.path("vmm-a11y-createnet-device.txt.set"), "net-forward-manual"),
        )
        for path, widget_name in pairs:
            if not os.path.exists(path):
                continue
            text = open(path, "r").read()
            widget = self.widget(widget_name)
            if widget is None:
                continue
            if (widget.get_text() or "") != text:
                widget.set_text(text)
                changed = True
        return changed

    def _publish_a11y_state(self):
        try:
            open(uitest.path("vmm-a11y-createnet-shown.txt"), "w").write(
                "1" if self.topwin.get_visible() else "0"
            )
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-createnet-name.txt"), "w").write(
                self.widget("net-name").get_text() or ""
            )
        except Exception:
            pass
        try:
            row = uiutil.get_list_selected_row(self.widget("net-forward-mode"))
            open(uitest.path("vmm-a11y-createnet-mode-label.txt"), "w").write(
                str(row[1] if row else "")
            )
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-createnet-ipv4-network.txt"), "w").write(
                self.widget("net-ipv4-network").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-ipv4-start.txt"), "w").write(
                self.widget("net-dhcpv4-start").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-ipv4-end.txt"), "w").write(
                self.widget("net-dhcpv4-end").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-ipv6-network.txt"), "w").write(
                self.widget("net-ipv6-network").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-ipv6-start.txt"), "w").write(
                self.widget("net-dhcpv6-start").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-ipv6-end.txt"), "w").write(
                self.widget("net-dhcpv6-end").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-domain.txt"), "w").write(
                self.widget("net-domain-name").get_text() or ""
            )
            open(uitest.path("vmm-a11y-createnet-device.txt"), "w").write(
                self.widget("net-forward-manual").get_text() or ""
            )
        except Exception:
            pass
        try:
            open(uitest.path("vmm-a11y-createnet-devicelist-vis.txt"), "w").write(
                "1" if self.widget("net-hostdevs").is_visible() else "0"
            )
            names = []
            model = self.widget("net-hostdevs").get_model()
            if model is not None:
                for row in model:
                    names.append(str(row[1] or ""))
            open(uitest.path("vmm-a11y-createnet-hostdevs.txt"), "w").write("\n".join(names))
        except Exception:
            pass

    def _a11y_select_mode(self, item):
        want = (item or "").lower().replace(".*", "")
        aliases = {
            "isolated": "isolated",
            "sr-iov": "hostdev",
            "sriov": "hostdev",
            "routed": "route",
            "nat": "nat",
            "open": "open",
        }
        key = None
        for name, val in aliases.items():
            if name in want:
                key = val
                break
        combo = self.widget("net-forward-mode")
        if key is None:
            return self._a11y_select_combo(combo, item)
        uiutil.set_list_selection(combo, key)
        self._net_forward_mode_changed_cb(combo)
        return True

    def _a11y_select_combo(self, combo, item, column=0):
        model = combo.get_model() if combo is not None else None
        if model is None:
            return False
        want = (item or "").lower().replace(".*", "")
        best = None
        best_score = -1
        it = model.get_iter_first()
        while it is not None:
            ncols = 0
            try:
                ncols = model.get_n_columns()
            except Exception:
                ncols = 2
            for idx in range(ncols):
                try:
                    label = str(model[it][idx] or "")
                except Exception:
                    continue
                ll = label.lower()
                score = -1
                if ll == want:
                    score = 1000 + len(ll)
                elif want and want in ll:
                    score = 500 + len(want)
                elif ll and ll in want:
                    score = len(ll)
                if score > best_score:
                    best_score = score
                    best = model[it][column]
            it = model.iter_next(it)
        if best is None:
            return False
        uiutil.set_list_selection(combo, best, column=column)
        return True

    def _a11y_load_pending_xml(self):
        try:
            pending = open(uitest.path("vmm-a11y-xml.txt"), "r").read()
        except Exception:
            pending = ""
        if not pending:
            return
        try:
            os.remove(uitest.path("vmm-a11y-xml.txt"))
        except Exception:
            pass
        if (self._xmleditor.get_xml() or "") != pending:
            self._xmleditor._srcbuff.set_text(pending)

    def _a11y_finish(self):
        try:
            self._apply_createnet_fields()
            self._a11y_load_pending_xml()
            self.finish(None)
            self._publish_a11y_state()
        except Exception:
            pass
        return False

    def _start_a11y_poll(self):
        if getattr(self, "_vmm_createnet_poll", False):
            return
        self._vmm_createnet_poll = True

        def _fields_tick():
            try:
                if self._apply_createnet_fields():
                    for path in (
                        uitest.path("vmm-a11y-createnet-name.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv4-network.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv4-start.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv4-end.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv6-network.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv6-start.txt.set"),
                        uitest.path("vmm-a11y-createnet-ipv6-end.txt.set"),
                        uitest.path("vmm-a11y-createnet-domain.txt.set"),
                        uitest.path("vmm-a11y-createnet-device.txt.set"),
                    ):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                    self._publish_a11y_state()
            except Exception:
                pass
            return True

        def _toggle(path, widget_name, after=None):
            if not os.path.exists(path):
                return False
            os.remove(path)
            widget = self.widget(widget_name)
            if widget is None:
                return True
            if hasattr(widget, "set_active"):
                widget.set_active(not widget.get_active())
            if after:
                after(widget)
            self._publish_a11y_state()
            return True

        def _tick():
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-mode.txt")):
                    item = open(uitest.path("vmm-a11y-createnet-mode.txt"), "r").read().strip()
                    os.remove(uitest.path("vmm-a11y-createnet-mode.txt"))
                    self._a11y_select_mode(item)
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-forward.txt")):
                    item = open(uitest.path("vmm-a11y-createnet-forward.txt"), "r").read().strip()
                    os.remove(uitest.path("vmm-a11y-createnet-forward.txt"))
                    combo = self.widget("net-forward-device")
                    if "physical" in item.lower():
                        uiutil.set_list_selection(combo, True)
                    else:
                        self._a11y_select_combo(combo, item)
                    self._net_forward_device_changed_cb(combo)
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-hostdev.txt")):
                    item = open(uitest.path("vmm-a11y-createnet-hostdev.txt"), "r").read().strip()
                    os.remove(uitest.path("vmm-a11y-createnet-hostdev.txt"))
                    self._a11y_select_combo(self.widget("net-hostdevs"), item)
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                _toggle(
                    uitest.path("vmm-a11y-createnet-ipv4-enable.click"),
                    "net-ipv4-enable",
                    self._ipv4_toggled_cb,
                )
                _toggle(
                    uitest.path("vmm-a11y-createnet-dhcpv4.click"),
                    "net-dhcpv4-enable",
                    self._dhcpv4_toggled_cb,
                )
                _toggle(
                    uitest.path("vmm-a11y-createnet-ipv6-enable.click"),
                    "net-ipv6-enable",
                    self._ipv6_toggled_cb,
                )
                _toggle(
                    uitest.path("vmm-a11y-createnet-dhcpv6.click"),
                    "net-dhcpv6-enable",
                    self._dhcpv6_toggled_cb,
                )
                _toggle(
                    uitest.path("vmm-a11y-createnet-dns-custom.click"),
                    "net-dns-use-custom",
                    self._net_dns_use_toggled_cb,
                )
            except Exception:
                pass
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-expand.txt")):
                    which = open(uitest.path("vmm-a11y-createnet-expand.txt"), "r").read().strip()
                    os.remove(uitest.path("vmm-a11y-createnet-expand.txt"))
                    mapping = {
                        "ipv4": "net-ipv4-expander",
                        "ipv6": "net-ipv6-expander",
                        "dns": "net-dns-expander",
                    }
                    exp = self.widget(mapping.get(which, ""))
                    if exp is not None:
                        exp.set_expanded(not exp.get_expanded())
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-cancel")):
                    os.remove(uitest.path("vmm-a11y-createnet-cancel"))
                    self.close()
            except Exception:
                pass
            try:
                if os.path.exists(uitest.path("vmm-a11y-createnet-finish")):
                    os.remove(uitest.path("vmm-a11y-createnet-finish"))
                    self._apply_createnet_fields()
                    GLib.idle_add(self._a11y_finish)
            except Exception:
                pass
            return True

        uitest.poll_add(50, _fields_tick)
        uitest.poll_add(50, _tick)
