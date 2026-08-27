# Copyright (C) 2012-2013 Red Hat, Inc.
# Copyright (C) 2012 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import collections
import os
import re
import time

from gi.repository import GLib
from gi.repository import Gio
from gi.repository import Gtk

import libvirt

from virtinst import log

from ..baseclass import vmmGObjectUI
from . import uiutil


def do_we_have_session():
    pid = os.getpid()

    ret = False
    try:  # pragma: no cover
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        manager = Gio.DBusProxy.new_sync(
            bus,
            0,
            None,
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            None,
        )

        # This raises an error exception
        out = manager.GetSessionByPID("(u)", pid)
        log.debug("Found login1 session=%s", out)
        ret = True
    except Exception:  # pragma: no cover
        log.exception("Failure talking to logind")

    return ret


class _vmmConnectAuth(vmmGObjectUI):
    def __init__(self, creds):
        vmmGObjectUI.__init__(self, "connectauth.ui", "connectauth")
        self.creds = creds
        self.topwin.set_title(_("Authentication required"))

        self.builder.connect_signals(
            {
                "on_connectauth_cancel_clicked": self._cancel_cb,
                "on_connectauth_ok_clicked": self._ok_cb,
                "on_entry1_activate": self._entry_cb,
                "on_entry2_activate": self._entry_cb,
            }
        )

        self.entry1 = self.widget("entry1")
        self.entry2 = self.widget("entry2")
        self._entry2_in_use = False
        self._init_ui()

    def _cleanup(self):
        pass

    def _init_ui(self):
        try:
            area = self.topwin.get_content_area()
            if area is not None:
                area.set_visible(True)
        except Exception:
            pass
        uiutil.set_grid_row_visible(self.entry1, False)
        uiutil.set_grid_row_visible(self.entry2, False)
        self._entry2_in_use = False

        from . import gtkcompat

        for idx, cred in enumerate(self.creds):
            # Libvirt virConnectCredential
            credtype, prompt, _challenge, _defresult, _result = cred
            noecho = credtype in [libvirt.VIR_CRED_PASSPHRASE, libvirt.VIR_CRED_NOECHOPROMPT]
            if not prompt:  # pragma: no cover
                raise RuntimeError("No prompt for auth credtype=%s" % credtype)

            prompt += ": "
            label = self.widget("label%s" % (idx + 1))
            entry = self.widget("entry%s" % (idx + 1))
            uiutil.set_grid_row_visible(label, True)
            label.set_text(prompt)
            entry.set_visibility(not noecho)
            if noecho:
                gtkcompat.restore_password_input_purpose(entry)
            gtkcompat.set_accessible_name(entry, prompt + "entry")
            if idx == 1:
                self._entry2_in_use = True
        try:
            gtkcompat.set_window_default_button(
                self.topwin, self.widget("connectauth-ok")
            )
        except Exception:
            pass

    def run(self):
        self._closed = False
        self._publish_a11y()
        self._start_a11y_poll()
        self.topwin.show()
        res = self.topwin.run()
        self._closed = True
        self.topwin.hide()
        try:
            open("/tmp/vmm-a11y-connectauth-shown.txt", "w").write("0")
        except Exception:
            pass

        if res != Gtk.ResponseType.OK:
            return -1

        self._apply_pending_a11y_text()
        self.creds[0][4] = self.entry1.get_text() or self._a11y_text("user")
        if self._passphrase_row_active():
            self.creds[1][4] = self.entry2.get_text() or self._a11y_text("pass")
        return 0

    def _passphrase_row_active(self):
        """GTK 4 get_visible()/is_visible() can be false while a parent is unmapped."""
        if getattr(self, "_entry2_in_use", False) or len(self.creds) > 1:
            return True
        try:
            return bool(self.entry2.get_visible())
        except Exception:
            return False

    def _entry_a11y_name(self, entry, fallback):
        try:
            label = self.widget("label1" if entry is self.entry1 else "label2")
            text = label.get_text() if label is not None else ""
        except Exception:
            text = ""
        if text:
            return text + ("entry" if text.endswith(" ") else " entry")
        return fallback

    def _a11y_text(self, key):
        path = "/tmp/vmm-a11y-connectauth-%s.txt" % key
        try:
            return open(path, "r").read()
        except Exception:
            return ""

    def _apply_pending_a11y_text(self):
        try:
            path = "/tmp/vmm-a11y-connectauth-user.txt.set"
            if os.path.exists(path):
                text = open(path, "r").read()
                os.remove(path)
                self.entry1.set_text(text)
                open("/tmp/vmm-a11y-connectauth-user.txt", "w").write(text)
        except Exception:
            pass
        try:
            path = "/tmp/vmm-a11y-connectauth-pass.txt.set"
            if os.path.exists(path):
                text = open(path, "r").read()
                os.remove(path)
                self.entry2.set_text(text)
                open("/tmp/vmm-a11y-connectauth-pass.txt", "w").write(text)
        except Exception:
            pass
        user = self.entry1.get_text() or self._a11y_text("user")
        if user and user != self.entry1.get_text():
            self.entry1.set_text(user)
        passwd = self.entry2.get_text() or self._a11y_text("pass")
        if passwd and passwd != self.entry2.get_text():
            self.entry2.set_text(passwd)

    def _clear_a11y_inputs(self):
        for path in (
            "/tmp/vmm-a11y-connectauth-action.txt",
            "/tmp/vmm-a11y-connectauth-activate",
            "/tmp/vmm-a11y-connectauth-user.txt.set",
            "/tmp/vmm-a11y-connectauth-pass.txt.set",
        ):
            try:
                os.remove(path)
            except OSError:
                pass

    def _publish_a11y(self):
        try:
            from . import gtkcompat

            gtkcompat.set_accessible_name(self.topwin, "Authentication required")
            gtkcompat.set_accessible_name(
                self.entry1, self._entry_a11y_name(self.entry1, "Username: entry")
            )
            gtkcompat.set_accessible_name(
                self.entry2, self._entry_a11y_name(self.entry2, "Password: entry")
            )
            self._clear_a11y_inputs()
            open("/tmp/vmm-a11y-connectauth-user.txt", "w").write("")
            open("/tmp/vmm-a11y-connectauth-pass.txt", "w").write("")
            open("/tmp/vmm-a11y-connectauth-shown.txt", "w").write("1")
            open("/tmp/vmm-a11y-connectauth-focus.txt", "w").write("user")
        except Exception:
            pass

    def _start_a11y_poll(self):
        if getattr(self, "_vmm_auth_poll", False):
            return
        self._vmm_auth_poll = True

        def _tick():
            if getattr(self, "_closed", False):
                return False
            try:
                if open("/tmp/vmm-a11y-connectauth-shown.txt", "r").read().strip() != "1":
                    return not getattr(self, "_closed", False)
            except Exception:
                return not getattr(self, "_closed", False)
            self._apply_pending_a11y_text()
            try:
                if os.path.exists("/tmp/vmm-a11y-connectauth-activate"):
                    os.remove("/tmp/vmm-a11y-connectauth-activate")
                    focus = "user"
                    try:
                        focus = open("/tmp/vmm-a11y-connectauth-focus.txt", "r").read().strip()
                    except Exception:
                        focus = "user"
                    src = self.entry2 if focus == "pass" else self.entry1
                    self._entry_cb(src)
                    if src == self.entry1 and self._passphrase_row_active():
                        open("/tmp/vmm-a11y-connectauth-focus.txt", "w").write("pass")
            except Exception:
                pass
            try:
                action = open("/tmp/vmm-a11y-connectauth-action.txt", "r").read().strip()
                os.remove("/tmp/vmm-a11y-connectauth-action.txt")
            except Exception:
                action = ""
            try:
                if action == "ok":
                    self._ok_cb(None)
                elif action == "cancel":
                    self._cancel_cb(None)
            except Exception:
                pass
            return not getattr(self, "_closed", False)

        self._vmm_auth_tick = _tick
        GLib.timeout_add(50, self._vmm_auth_tick)

    def _ok_cb(self, src):
        self._apply_pending_a11y_text()
        self.topwin.response(Gtk.ResponseType.OK)

    def _cancel_cb(self, src):
        self.topwin.response(Gtk.ResponseType.CANCEL)

    def _entry_cb(self, src):
        """
        If entry 1 activated and entry2 visible, jump to entry 2.
        Otherwise, click OK
        """
        if src == self.entry1 and self._passphrase_row_active():
            self.entry2.grab_focus()
            try:
                open("/tmp/vmm-a11y-connectauth-focus.txt", "w").write("pass")
            except Exception:
                pass
            return
        self._apply_pending_a11y_text()
        self.topwin.response(Gtk.ResponseType.OK)


def creds_dialog(creds, cbdata):
    """
    Thread safe wrapper for libvirt openAuth user/pass callback
    """
    retipc = []

    def wrapper(creds, cbdata):
        try:
            _conn = cbdata
            dialogobj = _vmmConnectAuth(creds)
            ret = dialogobj.run()
            dialogobj.cleanup()
        except Exception:  # pragma: no cover
            log.exception("Error from creds dialog")
            ret = -1
        retipc.append(ret)

    GLib.idle_add(wrapper, creds, cbdata)

    while not retipc:
        time.sleep(0.1)

    return retipc[0]


def connect_error(conn, errmsg, tb, warnconsole):
    """
    Format connection error message
    """
    errmsg = errmsg.strip(" \n")
    tb = tb.strip(" \n")
    hint = ""
    show_errmsg = True

    if conn.is_remote():
        log.debug("connect_error: conn transport=%s", conn.get_uri_transport())
        if re.search(r"nc: .* -- 'U'", tb):  # pragma: no cover
            hint += _(
                "The remote host requires a version of netcat/nc which supports the -U option."
            )
            show_errmsg = False
        elif conn.get_uri_transport() == "ssh" and re.search(r"askpass", tb):  # pragma: no cover

            hint += _(
                "Configure SSH key access for the remote host, "
                "or install an SSH askpass package locally."
            )
            show_errmsg = False
        else:
            hint += _("Verify that an appropriate libvirt daemon is running on the remote host.")

    elif conn.is_xen():  # pragma: no cover
        hint += _(
            "Verify that:\n"
            " - A Xen host kernel was booted\n"
            " - The Xen service has been started"
        )

    else:
        if warnconsole:
            hint += _(
                "Could not detect a local session: if you are "
                "running virt-manager over ssh -X or VNC, you "
                "may not be able to connect to libvirt as a "
                "regular user. Try running as root."
            )
            show_errmsg = False
        elif re.search(r"virt[a-z]*-sock", tb):  # pragma: no cover
            hint += _("Verify that an appropriate libvirt daemon is running.")
            show_errmsg = False

    msg = _("Unable to connect to libvirt %s.") % conn.get_uri()
    if show_errmsg:
        msg += "\n\n%s" % errmsg
    if hint:
        msg += "\n\n%s" % hint

    msg = msg.strip("\n")
    details = msg
    details += "\n\n"
    details += "Libvirt URI is: %s\n\n" % conn.get_uri()
    details += tb

    title = _("Virtual Machine Manager Connection Failure")

    ConnectError = collections.namedtuple("ConnectError", ["msg", "details", "title"])
    return ConnectError(msg, details, title)


##################################
# App first run connection setup #
##################################


def setup_first_uri(_config, detected_uri):
    msg = ""
    if not detected_uri:
        msg += _(
            "Could not detect a default hypervisor. Make "
            "sure the appropriate QEMU/KVM virtualization and libvirt "
            "packages are installed to manage virtualization "
            "on this host."
        )

    if msg:
        msg += "\n\n"
        msg += _("A virtualization connection can be manually added via File->Add Connection")

    return msg or None
