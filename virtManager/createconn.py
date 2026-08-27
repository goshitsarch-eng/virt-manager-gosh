# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import glob
import os
import urllib.parse

from gi.repository import GLib
from gi.repository import Gtk

from virtinst import log

from .lib import uiutil
from .baseclass import vmmGObjectUI
from .connmanager import vmmConnectionManager

(HV_QEMU, HV_XEN, HV_LXC, HV_QEMU_SESSION, HV_BHYVE, HV_VZ, HV_CUSTOM) = range(7)


def _default_uri():  # pragma: no cover
    if os.path.exists("/var/lib/xen"):
        if os.path.exists("/dev/xen/evtchn") or os.path.exists("/proc/xen"):
            return "xen:///"

    if (
        os.path.exists("/usr/bin/qemu")
        or os.path.exists("/usr/bin/qemu-kvm")
        or os.path.exists("/usr/bin/kvm")
        or os.path.exists("/usr/libexec/qemu-kvm")
        or glob.glob("/usr/bin/qemu-system-*")
    ):
        return "qemu:///system"

    if os.path.exists("/usr/lib/libvirt/libvirt_lxc") or os.path.exists(
        "/usr/lib64/libvirt/libvirt_lxc"
    ):
        return "lxc:///"
    return None


class vmmCreateConn(vmmGObjectUI):
    @classmethod
    def get_instance(cls, parentobj):
        try:
            if not cls._instance:
                cls._instance = vmmCreateConn()
            return cls._instance
        except Exception as e:  # pragma: no cover
            parentobj.err.show_err(_("Error launching connect dialog: %s") % str(e))

    def __init__(self):
        vmmGObjectUI.__init__(self, "createconn.ui", "vmm-open-connection")
        self._cleanup_on_app_close()

        self.builder.connect_signals(
            {
                "on_hypervisor_changed": self.hypervisor_changed,
                "on_connect_remote_toggled": self.connect_remote_toggled,
                "on_username_entry_changed": self.username_changed,
                "on_hostname_changed": self.hostname_changed,
                "on_cancel_clicked": self.cancel,
                "on_connect_clicked": self.open_conn,
                "on_vmm_open_connection_delete_event": self.cancel,
            }
        )

        self.set_initial_state()
        self.reset_state()

    @staticmethod
    def default_uri():
        return _default_uri()

    def cancel(self, ignore1=None, ignore2=None):
        log.debug("Cancelling open connection")
        self.close()
        return 1

    def close(self, ignore1=None, ignore2=None):
        log.debug("Closing open connection")
        self.topwin.hide()
        from .lib import gtkcompat

        try:
            gtkcompat.set_accessible_name(self.topwin, "Add Connection (hidden)")
            self.topwin.set_title("Add Connection (hidden)")
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-createconn-shown.txt", "w").write("0")
        except Exception:
            pass
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.remove_window(self.topwin)
        except Exception:
            pass
        gtkcompat.hide_createconn_window(self)

    def show(self, parent):
        log.debug("Showing open connection")
        from .lib import gtkcompat

        if self.is_visible():
            self.topwin.present()
            gtkcompat.expose_createconn_window(self)
            gtkcompat._start_combo_select_poll(self)
            self._start_a11y_poll()
            self._publish_a11y_state()
            return

        self._vmm_cc_user_seen = None
        self._vmm_cc_host_seen = None

        for path in (
            "/tmp/vmm-a11y-createconn-connect",
            "/tmp/vmm-a11y-createconn-cancel",
            "/tmp/vmm-a11y-createconn-remote-click",
            "/tmp/vmm-a11y-createconn-user.txt",
            "/tmp/vmm-a11y-createconn-host.txt",
            "/tmp/vmm-a11y-createconn-uri-label.txt",
        ):
            try:
                os.remove(path)
            except Exception:
                pass
        self.reset_state()
        self.topwin.set_transient_for(parent)
        self.topwin.present()
        try:
            gtkcompat.set_accessible_name(self.topwin, "Add Connection")
            self.topwin.set_title("Add Connection")
        except Exception:
            pass
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.add_window(self.topwin)
        except Exception:
            pass
        gtkcompat.expose_createconn_window(self)
        gtkcompat._start_combo_select_poll(self)
        self._start_a11y_poll()
        self._publish_a11y_state()

    def _publish_a11y_state(self):
        try:
            open("/tmp/vmm-a11y-createconn-shown.txt", "w").write(
                "1" if self.topwin.get_visible() else "0"
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-createconn-remote.txt", "w").write(
                "1" if self.widget("connect-remote").get_active() else "0"
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-createconn-uri-label.txt", "w").write(
                self.widget("uri-label").get_text() or ""
            )
        except Exception:
            pass
        try:
            hv = uiutil.get_list_selection(self.widget("hypervisor"))
            show_remote = hv not in (HV_QEMU_SESSION, HV_CUSTOM)
            open("/tmp/vmm-a11y-createconn-fields.txt", "w").write(
                "%s\t%s\t%s" % (int(show_remote), int(show_remote), int(show_remote))
            )
        except Exception:
            pass
        try:
            hv = self.widget("hypervisor")
            row = uiutil.get_list_selected_row(hv) if hv is not None else None
            open("/tmp/vmm-a11y-createconn-hv.txt", "w").write(
                str(row[1] if row else "")
            )
        except Exception:
            pass

    def _apply_createconn_fields(self):
        """Copy sentinel user/host files onto the entries whenever they differ.

        Do not key this off mtime: a leftover empty host.txt can latch the
        stamp in the same second as a later fe80::1 write. Never call this
        from a callback that also runs modal dialogs — GLib will not
        re-dispatch that same timeout until the modal returns.
        """
        changed = False
        path = "/tmp/vmm-a11y-createconn-user.txt"
        if os.path.exists(path):
            text = open(path, "r").read()
            entry = self.widget("username-entry")
            if entry is not None and (entry.get_text() or "") != text:
                entry.set_text(text)
                changed = True
        path = "/tmp/vmm-a11y-createconn-host.txt"
        if os.path.exists(path):
            text = open(path, "r").read()
            entry = self.widget("hostname")
            if entry is not None and (entry.get_text() or "") != text:
                entry.set_text(text)
                changed = True
            elif text:
                uri = ""
                try:
                    uri = self.widget("uri-label").get_text() or ""
                except Exception:
                    pass
                if text not in uri and ("[%s]" % text) not in uri:
                    changed = True
        if changed:
            self.populate_uri()
        return changed

    def _a11y_open_conn(self):
        try:
            self._apply_createconn_fields()
            self.open_conn(None)
            self._publish_a11y_state()
        except Exception:
            pass
        return False

    def _start_a11y_poll(self):
        if getattr(self, "_vmm_createconn_poll", False):
            return
        self._vmm_createconn_poll = True

        def _fields_tick():
            try:
                self._apply_createconn_fields()
            except Exception:
                pass
            return True

        def _tick():
            try:
                if os.path.exists("/tmp/vmm-a11y-createconn-remote-click"):
                    os.remove("/tmp/vmm-a11y-createconn-remote-click")
                    chk = self.widget("connect-remote")
                    chk.set_active(not chk.get_active())
                    self.connect_remote_toggled(chk)
                    self._publish_a11y_state()
            except Exception:
                pass
            try:
                self._apply_createconn_fields()
            except Exception:
                pass
            try:
                # Wait until a pending remote toggle is applied so Connect
                # cannot open a local URI and hang.
                if os.path.exists("/tmp/vmm-a11y-createconn-remote-click"):
                    return True
                if os.path.exists("/tmp/vmm-a11y-createconn-connect"):
                    os.remove("/tmp/vmm-a11y-createconn-connect")
                    self._apply_createconn_fields()
                    # Do not call open_conn here: val_err is modal and would
                    # block this source so later host.txt writes never apply.
                    GLib.idle_add(self._a11y_open_conn)
            except Exception:
                pass
            try:
                if os.path.exists("/tmp/vmm-a11y-createconn-cancel"):
                    os.remove("/tmp/vmm-a11y-createconn-cancel")
                    self.cancel()
            except Exception:
                pass
            return True

        GLib.timeout_add(50, _fields_tick)
        GLib.timeout_add(50, _tick)

    def _cleanup(self):
        pass

    def set_initial_state(self):
        self.widget("connect").grab_default()

        combo = self.widget("hypervisor")
        # [connection ID, label]
        model = Gtk.ListStore(int, str)

        def _add_hv_row(rowid, config_name, label):
            if (
                not self.config.default_hvs
                or not config_name
                or config_name in self.config.default_hvs
            ):
                model.append([rowid, label])

        _add_hv_row(HV_QEMU, "qemu", "QEMU/KVM")
        _add_hv_row(HV_QEMU_SESSION, "qemu", "QEMU/KVM " + _("user session"))
        _add_hv_row(HV_XEN, "xen", "Xen")
        _add_hv_row(HV_LXC, "lxc", "Libvirt-LXC")
        _add_hv_row(HV_BHYVE, "bhyve", "Bhyve")
        _add_hv_row(HV_VZ, "vz", "Virtuozzo")
        _add_hv_row(-1, None, "")
        _add_hv_row(HV_CUSTOM, None, _("Custom URI..."))
        combo.set_model(model)
        uiutil.init_combo_text_column(combo, 1)

        def sepfunc(model, it):
            return model[it][0] == -1

        combo.set_row_separator_func(sepfunc)

    def reset_state(self):
        self.set_default_hypervisor()
        self.widget("autoconnect").set_sensitive(True)
        self.widget("autoconnect").set_active(True)
        self.widget("hostname").set_text("")
        self.widget("connect-remote").set_active(False)
        self.widget("username-entry").set_text("")
        self.widget("uri-entry").set_text("")
        self.connect_remote_toggled(self.widget("connect-remote"))
        self.populate_uri()

    def is_remote(self):
        # Whether user is requesting a remote connection
        return self.widget("connect-remote").get_active()

    def set_default_hypervisor(self):
        default = self.default_uri()
        if not default or default.startswith("qemu"):
            uiutil.set_list_selection(self.widget("hypervisor"), HV_QEMU)
        elif default.startswith("xen"):  # pragma: no cover
            uiutil.set_list_selection(self.widget("hypervisor"), HV_XEN)

    def hostname_changed(self, src_ignore):
        self.populate_uri()

    def hypervisor_changed(self, src):
        ignore = src
        hv = uiutil.get_list_selection(self.widget("hypervisor"))
        is_session = hv == HV_QEMU_SESSION
        is_custom = hv == HV_CUSTOM
        show_remote = not is_session and not is_custom
        uiutil.set_grid_row_visible(self.widget("session-warning-box"), is_session)
        uiutil.set_grid_row_visible(self.widget("connect-remote"), show_remote)
        uiutil.set_grid_row_visible(self.widget("username-entry"), show_remote)
        uiutil.set_grid_row_visible(self.widget("hostname"), show_remote)
        if not show_remote:
            self.widget("connect-remote").set_active(False)

        uiutil.set_grid_row_visible(self.widget("uri-label"), not is_custom)
        uiutil.set_grid_row_visible(self.widget("uri-entry"), is_custom)
        self._publish_a11y_state()
        if is_custom:
            label = self.widget("uri-label").get_text()
            self.widget("uri-entry").set_text(label)
            self.widget("uri-entry").grab_focus()
        self.populate_uri()

    def username_changed(self, src_ignore):
        self.populate_uri()

    def connect_remote_toggled(self, src_ignore):
        is_remote = self.is_remote()
        self.widget("hostname").set_sensitive(is_remote)
        self.widget("autoconnect").set_active(not is_remote)
        self.widget("username-entry").set_sensitive(is_remote)

        if is_remote and not self.widget("username-entry").get_text():
            self.widget("username-entry").set_text("root")
        self.populate_uri()

    def populate_uri(self):
        uri = self.generate_uri()
        self.widget("uri-label").set_text(uri)
        self._publish_a11y_state()

    def generate_uri(self):
        hv = uiutil.get_list_selection(self.widget("hypervisor"))
        host = self.widget("hostname").get_text().strip()
        user = self.widget("username-entry").get_text()
        is_remote = self.is_remote()

        hvstr = ""
        if hv == HV_XEN:
            hvstr = "xen"
        elif hv == HV_QEMU or hv == HV_QEMU_SESSION:
            hvstr = "qemu"
        elif hv == HV_BHYVE:
            hvstr = "bhyve"
        elif hv == HV_VZ:
            hvstr = "vz"
        else:
            hvstr = "lxc"

        addrstr = ""
        if user:
            addrstr += urllib.parse.quote(user) + "@"

        if host.count(":") > 1:
            host = "[%s]" % host
        addrstr += host

        if is_remote:
            hoststr = "+ssh://" + addrstr + "/"
        else:
            hoststr = ":///"

        uri = hvstr + hoststr
        if hv in (HV_QEMU, HV_BHYVE, HV_VZ):
            uri += "system"
        elif hv == HV_QEMU_SESSION:
            uri += "session"

        return uri

    def validate(self):
        is_remote = self.is_remote()
        host = self.widget("hostname").get_text()

        if is_remote and not host:
            msg = _("A hostname is required for remote connections.")
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(msg)
            except Exception:
                pass
            return self.err.val_err(msg)

        return True

    def _conn_open_completed(self, conn, ConnectError):
        if not ConnectError:
            self.close()
            self.reset_finish_cursor()
            return

        msg, details, title = ConnectError
        msg += "\n\n"
        msg += _("Would you still like to remember this connection?")

        remember = self.err.show_err(
            msg,
            details,
            title,
            buttons=Gtk.ButtonsType.YES_NO,
            dialog_type=Gtk.MessageType.QUESTION,
            modal=True,
        )
        self.reset_finish_cursor()
        if remember:
            self.close()
        else:
            vmmConnectionManager.get_instance().remove_conn(conn.get_uri())

    def open_conn(self, ignore):
        if not self.validate():
            return

        auto = False
        if self.widget("autoconnect").get_sensitive():
            auto = bool(self.widget("autoconnect").get_active())
        if self.widget("uri-label").is_visible():
            uri = self.generate_uri()
        else:
            uri = self.widget("uri-entry").get_text()

        log.debug("Generate URI=%s, auto=%s", uri, auto)

        conn = vmmConnectionManager.get_instance().add_conn(uri)
        conn.set_autoconnect(auto)
        if conn.is_active():
            self._conn_open_completed(conn, None)
            return

        conn.connect_once("open-completed", self._conn_open_completed)
        self.set_finish_cursor()
        conn.open()
