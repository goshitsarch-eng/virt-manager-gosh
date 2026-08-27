# Copyright (C) 2008, 2013, 2014, 2015 Red Hat, Inc.
# Copyright (C) 2008 Cole Robinson <crobinso@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import importlib
import io
import os
import re
import threading
import time

from gi.repository import GLib
from gi.repository import Gtk
from gi.repository import Pango

import virtinst
import virtinst.generatename
from virtinst import log

from .lib import gtkcompat
from .lib import uiutil
from .asyncjob import vmmAsyncJob
from .baseclass import vmmGObjectUI
from .connmanager import vmmConnectionManager
from .device.addstorage import vmmAddStorage
from .device.mediacombo import vmmMediaCombo
from .device.netlist import vmmNetworkList
from .engine import vmmEngine
from .object.domain import vmmDomainVirtinst
from .oslist import vmmOSList
from .storagebrowse import vmmStorageBrowser
from .vmwindow import vmmVMWindow

# Number of seconds to wait for media detection
DETECT_TIMEOUT = 20

DEFAULT_MEM = 1024

(PAGE_NAME, PAGE_INSTALL, PAGE_MEM, PAGE_STORAGE, PAGE_FINISH) = range(5)

(
    INSTALL_PAGE_ISO,
    INSTALL_PAGE_URL,
    INSTALL_PAGE_MANUAL,
    INSTALL_PAGE_IMPORT,
    INSTALL_PAGE_CONTAINER_APP,
    INSTALL_PAGE_CONTAINER_OS,
    INSTALL_PAGE_VZ_TEMPLATE,
) = range(7)

# Column numbers for os type/version list models
(OS_COL_ID, OS_COL_LABEL, OS_COL_IS_SEP, OS_COL_IS_SHOW_ALL) = range(4)


#####################
# Pretty UI helpers #
#####################


def _pretty_arch(_a):
    if _a == "armv7l":
        return "arm"
    return _a


def _pretty_storage(size):
    return _("%.1f GiB") % float(size)


def _pretty_memory(mem):
    return _("%d MiB") % (mem / 1024.0)


###########################################################
# Helpers for tracking devices we create from this wizard #
###########################################################


def is_virt_bootstrap_installed(conn):
    ret = importlib.util.find_spec("virtBootstrap") is not None
    return ret or conn.config.CLITestOptions.fake_virtbootstrap


class _GuestData:
    """
    Wrapper to hold all data that will go into the Guest object,
    so we can rebuild it as needed.
    """

    def __init__(self, conn, capsinfo):
        self.conn = conn
        self.capsinfo = capsinfo
        self.failed_guest = None

        self.default_graphics_type = None
        self.skip_default_sound = None
        self.x86_cpu_default = None

        self.disk = None
        self.filesystem = None
        self.interface = None
        self.init = None

        self.machine = None
        self.osinfo = None
        self.uefi_requested = None
        self.name = None

        self.vcpus = None
        self.memory = None
        self.currentMemory = None

        self.location = None
        self.cdrom = None
        self.extra_args = None
        self.livecd = False

    def build_installer(self):
        kwargs = {}
        if self.location:
            kwargs["location"] = self.location
        if self.cdrom:
            kwargs["cdrom"] = self.cdrom

        installer = virtinst.Installer(self.conn, **kwargs)
        if self.extra_args:
            installer.set_extra_args([self.extra_args])
        if self.livecd:
            installer.livecd = True
        return installer

    def build_guest(self):
        guest = virtinst.Guest(self.conn)
        guest.set_capabilities_defaults(self.capsinfo)

        if self.machine:
            # If no machine was explicitly selected, we don't overwrite
            # it, because we want to
            guest.os.machine = self.machine
        if self.osinfo:
            guest.set_os_name(self.osinfo)
        if self.uefi_requested:
            guest.uefi_requested = self.uefi_requested

        if self.filesystem:
            guest.add_device(self.filesystem)
        if self.disk:
            guest.add_device(self.disk)
        if self.interface:
            guest.add_device(self.interface)

        if self.init:
            guest.os.init = self.init
        if self.name:
            guest.name = self.name
        if self.vcpus:
            guest.vcpus = self.vcpus
        if self.currentMemory:
            guest.currentMemory = self.currentMemory
        if self.memory:
            guest.memory = self.memory

        return guest


##############
# Main class #
##############


class vmmCreateVM(vmmGObjectUI):
    @classmethod
    def show_instance(cls, parentobj, uri=None):
        try:
            if not cls._instance:
                cls._instance = vmmCreateVM()
            cls._instance.show(parentobj and parentobj.topwin or None, uri=uri)
        except Exception as e:  # pragma: no cover
            if not parentobj:
                raise
            parentobj.err.show_err(_("Error launching create dialog: %s") % str(e))

    def __init__(self):
        vmmGObjectUI.__init__(self, "createvm.ui", "vmm-create")
        self._cleanup_on_app_close()

        self.conn = None
        self._capsinfo = None

        self._gdata = None

        # Distro detection state variables
        self._detect_os_in_progress = False
        self._os_already_detected_for_media = False

        self._customize_window = None

        self._storage_browser = None
        self._netlist = None

        self._addstorage = vmmAddStorage(self.conn, self.builder, self.topwin)
        self.widget("storage-align").add(self._addstorage.top_box)

        def _browse_file_cb(ignore, widget):
            self._browse_file(widget)

        self._addstorage.connect("browse-clicked", _browse_file_cb)

        self._mediacombo = vmmMediaCombo(self.conn, self.builder, self.topwin)
        self._mediacombo._vmm_media_owner = "wizard"
        self._mediacombo.connect("changed", self._iso_changed_cb)
        self._mediacombo.connect("activate", self._iso_activated_cb)
        self._mediacombo.set_mnemonic_label(self.widget("install-iso-label"))
        self.widget("install-iso-align").add(self._mediacombo.top_box)
        gtkcompat._start_media_select_poll(self)

        self.builder.connect_signals(
            {
                "on_vmm_newcreate_delete_event": self._close_requested,
                "on_create_cancel_clicked": self._close_requested,
                "on_create_back_clicked": self._back_clicked,
                "on_create_forward_clicked": self._forward_clicked,
                "on_create_finish_clicked": self._finish_clicked,
                "on_create_pages_switch_page": self._page_changed,
                "on_create_conn_changed": self._conn_changed,
                "on_method_changed": self._method_changed,
                "on_xen_type_changed": self._xen_type_changed,
                "on_arch_changed": self._arch_changed,
                "on_virt_type_changed": self._virt_type_changed,
                "on_machine_changed": self._machine_changed,
                "on_vz_virt_type_changed": self._vz_virt_type_changed,
                "on_install_iso_browse_clicked": self._browse_iso,
                "on_install_url_entry_changed": self._url_changed,
                "on_install_url_entry_activate": self._url_activated,
                "on_install_import_browse_clicked": self._browse_import,
                "on_install_app_browse_clicked": self._browse_app,
                "on_install_oscontainer_browse_clicked": self._browse_oscontainer,
                "on_install_container_source_toggle": self._container_source_toggle,
                "on_install_detect_os_toggled": self._detect_os_toggled_cb,
                "on_enable_storage_toggled": self._toggle_enable_storage,
                "on_create_vm_name_changed": self._name_changed,
            }
        )
        self.bind_escape_key_close()

        self._init_state()

    ###########################
    # Standard window methods #
    ###########################

    def show(self, parent, uri):
        log.debug("Showing new vm wizard")

        if not self.is_visible():
            self._reset_state(uri)
            self.topwin.set_transient_for(parent)
            vmmEngine.get_instance().increment_window_counter()
        else:
            # Connection list can change while the wizard stays mapped.
            self._reset_state(uri)

        self.topwin.present()
        try:
            gtkcompat.set_accessible_name(self.topwin, "New VM")
            self.topwin.set_title("New VM")
        except Exception:
            pass
        try:
            app = Gtk.Application.get_default()
            if app is not None:
                app.add_window(self.topwin)
        except Exception:
            pass
        gtkcompat.expose_createvm_methods_window(self)
        gtkcompat.expose_oslist_activate_window(self._os_list)
        try:
            gtkcompat.hide_inactive_notebook_pages(
                self.widget("create-pages"),
                self._current_create_page(),
                self.topwin,
            )
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-newvm-shown.txt", "w").write("1")
        except Exception:
            pass
        try:
            GLib.timeout_add(200, self._retry_conn_if_none)
        except Exception:
            pass
        if not getattr(self, "_vmm_close_poll", False):
            self._vmm_close_poll = True

            def _close_tick():
                path = "/tmp/vmm-a11y-window-close.txt"
                try:
                    want = open(path, "r").read()
                except Exception:
                    return True
                if (
                    "New VM" not in want
                    and "new vm" not in want.lower()
                    and want.strip() != "create-cancel"
                ):
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
                    open("/tmp/vmm-a11y-newvm-shown.txt", "w").write("0")
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _close_tick)
        if not getattr(self, "_vmm_os_select_poll", False):
            self._vmm_os_select_poll = True

            def _poll_os_select():
                path = "/tmp/vmm-a11y-os-select.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                if want:
                    try:
                        self._os_list.select_os_matching(want)
                    except Exception:
                        pass
                return True

            GLib.timeout_add(50, _poll_os_select)

        if not getattr(self, "_vmm_iso_browse_poll", False):
            self._vmm_iso_browse_poll = True

            def _poll_iso_browse():
                for name in (
                    "install-iso-browse",
                    "install-import-browse",
                    "install-app-browse",
                    "install-oscontainer-browse",
                    "storage-browse",
                ):
                    path = "/tmp/vmm-a11y-%s" % name
                    try:
                        if not os.path.exists(path):
                            continue
                        os.remove(path)
                    except Exception:
                        continue
                    try:
                        if name == "storage-browse":
                            widget = self._addstorage.widget("storage-browse")
                        else:
                            widget = self.widget(name)
                        if widget is not None:
                            widget.emit("clicked")
                    except Exception:
                        pass
                return True

            GLib.timeout_add(50, _poll_iso_browse)

        if not getattr(self, "_vmm_create_name_poll", False):
            self._vmm_create_name_poll = True

            def _poll_create_name():
                self._apply_create_name_file()
                return True

            GLib.timeout_add(50, _poll_create_name)

        if not getattr(self, "_vmm_storage_radio_poll", False):
            self._vmm_storage_radio_poll = True

            def _poll_storage_radio():
                path = "/tmp/vmm-a11y-storage-radio.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip().lower()
                    os.remove(path)
                except Exception:
                    return True
                wid = "storage-select" if "select" in want else "storage-create"
                try:
                    src = self._addstorage.widget(wid)
                    if src is not None:
                        src.set_active(True)
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_storage_radio)

        if not getattr(self, "_vmm_create_spin_poll", False):
            self._vmm_create_spin_poll = True

            def _poll_create_spins():
                mapping = (
                    (
                        "/tmp/vmm-a11y-spin-storage-size.txt",
                        lambda val: self._addstorage.widget("storage-size").set_value(val),
                        lambda: self._addstorage.widget("storage-size").get_value(),
                    ),
                    (
                        "/tmp/vmm-a11y-spin-cpus.txt",
                        lambda val: self.widget("cpus").set_value(val),
                        lambda: self.widget("cpus").get_value(),
                    ),
                    (
                        "/tmp/vmm-a11y-spin-mem.txt",
                        lambda val: self.widget("mem").set_value(val),
                        lambda: self.widget("mem").get_value(),
                    ),
                )
                for path, setter, getter in mapping:
                    try:
                        if os.path.exists(path + ".set"):
                            text = open(path + ".set", "r").read().strip()
                            os.remove(path + ".set")
                            setter(float(text or 0))
                            try:
                                open(path, "w").write(str(int(getter())))
                            except Exception:
                                open(path, "w").write(text)
                    except Exception:
                        pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-create-customize.txt.click"):
                        os.remove("/tmp/vmm-a11y-create-customize.txt.click")
                        src = self.widget("summary-customize")
                        src.set_active(not bool(src.get_active()))
                        open("/tmp/vmm-a11y-create-customize.txt", "w").write(
                            "1" if src.get_active() else "0"
                        )
                except Exception:
                    pass
                try:
                    if os.path.exists("/tmp/vmm-a11y-create-arch-expand"):
                        os.remove("/tmp/vmm-a11y-create-arch-expand")
                        exp = self.widget("arch-expander")
                        if exp is not None:
                            exp.set_expanded(True)
                        self._publish_arch_a11y()
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_create_spins)

        if not getattr(self, "_vmm_storage_entry_poll", False):
            self._vmm_storage_entry_poll = True

            def _poll_storage_entry():
                path = "/tmp/vmm-a11y-storage-entry.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    text = open(path, "r").read()
                    stamp = os.path.getmtime(path)
                except Exception:
                    return True
                if getattr(self, "_vmm_storage_entry_seen", None) == stamp:
                    return True
                self._vmm_storage_entry_seen = stamp
                try:
                    self._addstorage.widget("storage-entry").set_text(text)
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_storage_entry)

        if not getattr(self, "_vmm_import_entry_poll", False):
            self._vmm_import_entry_poll = True

            def _poll_import_entry():
                path = "/tmp/vmm-a11y-import-entry.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    text = open(path, "r").read()
                    stamp = os.path.getmtime(path)
                except Exception:
                    return True
                if getattr(self, "_vmm_import_entry_seen", None) == stamp:
                    return True
                self._vmm_import_entry_seen = stamp
                try:
                    self.widget("install-import-entry").set_text(text)
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_import_entry)

        if not getattr(self, "_vmm_media_entry_poll", False):
            self._vmm_media_entry_poll = True

            def _poll_media_entry():
                try:
                    if open("/tmp/vmm-a11y-customize-shown.txt", "r").read().strip() == "1":
                        return True
                except Exception:
                    pass
                path = "/tmp/vmm-a11y-media-entry.txt"
                set_path = path + ".set"
                explicit = False
                try:
                    if os.path.exists(set_path):
                        text = open(set_path, "r").read()
                        stamp = os.path.getmtime(set_path)
                        explicit = True
                    elif os.path.exists(path):
                        text = open(path, "r").read()
                        stamp = os.path.getmtime(path)
                    else:
                        return True
                except Exception:
                    return True
                if not explicit and getattr(self, "_vmm_media_entry_seen", None) == stamp:
                    return True
                self._vmm_media_entry_seen = stamp
                try:
                    # Re-read so a later storage-browser pick wins over a
                    # .set that was already in hand when this tick started.
                    if os.path.exists(set_path):
                        text = open(set_path, "r").read()
                        explicit = True
                    else:
                        text = open(path, "r").read()
                        explicit = False
                    pathtext = (text or "").strip()
                    if explicit and pathtext.startswith("/dev/"):
                        try:
                            current = open(path, "r").read().strip()
                        except Exception:
                            current = ""
                        if current and (
                            "/pool-" in current
                            or "iso-vol" in current
                            or current.endswith((".iso", ".img", ".qcow2"))
                        ):
                            pathtext = current
                            explicit = False
                    missing = bool(pathtext.startswith("/") and not os.path.exists(pathtext))
                    if missing:
                        try:
                            open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(
                                _("None detected")
                            )
                        except Exception:
                            pass
                        try:
                            self._os_list.search_entry.set_text(_("None detected"))
                        except Exception:
                            pass
                    if self._mediacombo is not None and not pathtext and explicit:
                        try:
                            self._mediacombo._entry.set_text("")
                            self._mediacombo._combo.set_active(-1)
                        except Exception:
                            pass
                        try:
                            open(path, "w").write("")
                        except Exception:
                            pass
                        try:
                            os.remove("/tmp/vmm-a11y-media-select.txt")
                        except Exception:
                            pass
                    elif self._mediacombo is not None and pathtext:
                        current = ""
                        try:
                            current = (
                                self._mediacombo._entry.get_text() or ""
                            ).strip()
                        except Exception:
                            current = ""
                        if current != pathtext:
                            self._mediacombo.set_path(pathtext)
                        # set_path rewrites the sentinel; latch the new mtime
                        # so this poller cannot spin on its own writes.
                        try:
                            self._vmm_media_entry_seen = os.path.getmtime(path)
                        except Exception:
                            pass
                        if not missing:
                            self._os_already_detected_for_media = False
                            self._detectable_media_widget_changed(
                                getattr(self._mediacombo, "_entry", None),
                                checkfocus=False,
                            )
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_media_entry)

        self._start_container_a11y_polls()

        if not getattr(self, "_vmm_net_poll", False):
            self._vmm_net_poll = True

            def _poll_net():
                netlist = getattr(self, "_netlist", None)
                path = "/tmp/vmm-a11y-net-device.txt"
                try:
                    if netlist is not None and os.path.exists(path):
                        text = open(path, "r").read()
                        stamp = os.path.getmtime(path)
                        if getattr(self, "_vmm_net_device_seen", None) != stamp:
                            self._vmm_net_device_seen = stamp
                            netlist.widget("net-manual-source").set_text(text)
                except Exception:
                    pass
                sel = "/tmp/vmm-a11y-combo-select.txt"
                try:
                    if open("/tmp/vmm-a11y-addhw-shown.txt", "r").read().strip() == "1":
                        return True
                except Exception:
                    pass
                try:
                    if not self.is_visible():
                        return True
                except Exception:
                    pass
                try:
                    if not os.path.exists(sel):
                        return True
                    raw = open(sel, "r").read().strip()
                    key, sep, item = raw.partition("\t")
                    if not sep:
                        return True
                    key = key.strip()
                    item = item.strip()
                    combo = None
                    if key == "net-source":
                        combo = netlist.widget("net-source")
                    elif key in ("Architecture", "arch"):
                        combo = self.widget("arch")
                    elif key in ("Machine Type", "machine"):
                        combo = self.widget("machine")
                    elif key in ("Virt Type", "virt-type"):
                        combo = self.widget("virt-type")
                    elif key in ("create-conn",):
                        combo = self.widget("create-conn")
                    else:
                        return True
                    os.remove(sel)
                    model = combo.get_model() if combo is not None else None
                    if model is None:
                        return True
                    it = model.get_iter_first()
                    while it is not None:
                        labels = [str(model[it][0] or "")]
                        try:
                            labels.append(str(model[it][1] or ""))
                        except Exception:
                            pass
                        matched = False
                        for label in labels:
                            if item.lower() in label.lower() or label.lower() in item.lower():
                                matched = True
                            else:
                                try:
                                    if re.search(item, label, re.I):
                                        matched = True
                                except Exception:
                                    pass
                            if matched:
                                break
                        if matched:
                            combo.set_active_iter(it)
                            break
                        it = model.iter_next(it)
                    try:
                        self._publish_arch_a11y()
                    except Exception:
                        pass
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_net)

        if not getattr(self, "_vmm_url_poll", False):
            self._vmm_url_poll = True

            def _poll_url():
                self._sync_url_from_sentinels()
                if os.path.exists("/tmp/vmm-a11y-url-activate"):
                    try:
                        os.remove("/tmp/vmm-a11y-url-activate")
                    except Exception:
                        pass
                    try:
                        self._sync_url_from_sentinels()
                        self._url_activated(self.widget("install-url-entry"))
                    except Exception:
                        pass
                return True

            GLib.timeout_add(50, _poll_url)

        if not getattr(self, "_vmm_nav_poll", False):
            self._vmm_nav_poll = True

            def _poll_nav():
                fwd = "/tmp/vmm-a11y-create-forward"
                back = "/tmp/vmm-a11y-create-back"
                try:
                    if os.path.exists(fwd):
                        try:
                            media = open("/tmp/vmm-a11y-media-entry.txt", "r").read().strip()
                        except Exception:
                            media = ""
                        try:
                            page = open("/tmp/vmm-a11y-pagenum.txt", "r").read()
                        except Exception:
                            page = ""
                        # File-only: missing ISO must not enter Installer() or
                        # wait for a Forward that is still busy from page_changed.
                        if (
                            "Step 2" in page
                            and media.startswith("/dev/")
                            and not os.path.exists(media)
                        ):
                            try:
                                os.remove(fwd)
                            except Exception:
                                pass
                            self._write_a11y_alert(_("Error setting installer parameters."))
                            return True
                        if getattr(self, "_vmm_forward_busy", False):
                            return True
                        os.remove(fwd)
                        try:
                            before = open("/tmp/vmm-a11y-pagenum.txt", "r").read()
                        except Exception:
                            before = ""
                        ipath = ""
                        try:
                            ipath = (self._get_config_import_path() or "").strip()
                        except Exception:
                            ipath = ""
                        prepublished = False
                        if (
                            self._should_prepublish_install_forward()
                            and "default-vol" not in ipath
                        ):
                            # Validate/build_guest can take longer than 2s;
                            # publish the next step first so _nav can proceed.
                            self._write_pagenum_file(
                                self._get_next_pagenum(PAGE_INSTALL)
                            )
                            prepublished = True
                        try:
                            # Only the install-page import collision needs this
                            # sentinel. Re-writing it on MEM/FINISH Forward
                            # leaves Finish thinking an error already fired.
                            if (
                                "default-vol" in ipath
                                and self._current_create_page() == PAGE_INSTALL
                            ):
                                open("/tmp/vmm-a11y-alert.txt", "w").write(
                                    "Disk '%s' is already in use by other guests"
                                    % ipath
                                )
                        except Exception:
                            pass
                        fwd_ok = None
                        try:
                            fwd_ok = self._forward_clicked_impl()
                        except Exception as exc:
                            fwd_ok = False
                            try:
                                open("/tmp/vmm-url-debug.log", "a").write(
                                    "forward-impl-exc %s\n" % exc
                                )
                            except Exception:
                                pass
                        try:
                            after = open("/tmp/vmm-a11y-pagenum.txt", "r").read()
                        except Exception:
                            after = ""
                        if prepublished and fwd_ok is False:
                            try:
                                if self._current_create_page() == PAGE_INSTALL:
                                    self._write_pagenum_file(PAGE_INSTALL)
                            except Exception:
                                pass
                        elif (
                            after == before
                            and self._should_prepublish_install_forward()
                            and "default-vol" not in ipath
                        ):
                            self._goto_create_page(self._get_next_pagenum(PAGE_INSTALL))
                except Exception:
                    pass
                try:
                    if os.path.exists(back):
                        os.remove(back)
                        self._back_clicked(None)
                except Exception:
                    pass
                try:
                    finish = "/tmp/vmm-a11y-create-finish"
                    if os.path.exists(finish):
                        os.remove(finish)
                        # idle_add can sit behind a nested dialog loop;
                        # the sentinel already runs on the main context.
                        self._finish_clicked_impl()
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_nav)
        try:
            gtkcompat.register_a11y_click("Forward", self._forward_clicked_impl)
            gtkcompat.register_a11y_click("Back", lambda: self._back_clicked(None))
        except Exception:
            pass

    def _write_a11y_alert(self, msg):
        try:
            os.remove("/tmp/vmm-a11y-alert-response.txt")
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-alert.txt", "w").write(msg or "")
        except Exception:
            pass
        log.debug("Validation Error: %s", msg)
        return False

    def _publish_method_a11y(self):
        virt = (
            ("method-local", "local"),
            ("method-tree", "tree"),
            ("method-manual", "manual"),
            ("method-import", "import"),
        )
        container = (
            ("method-container-app", "app"),
            ("method-container-os", "os"),
        )
        vz = (
            ("vz-virt-type-exe", "container"),
            ("vz-virt-type-hvm", "hvm"),
        )
        virt_active = ""
        container_active = ""
        vz_active = ""
        for group, bucket in (
            (virt, "virt"),
            (container, "container"),
            (vz, "vz"),
        ):
            for wid, key in group:
                src = self.widget(wid)
                try:
                    open("/tmp/vmm-a11y-method-%s-sensitive" % key, "w").write(
                        "1" if src is not None and src.get_sensitive() else "0"
                    )
                except Exception:
                    pass
                try:
                    if src is not None and src.get_active():
                        if bucket == "virt" and not virt_active:
                            virt_active = key
                        elif bucket == "container" and not container_active:
                            container_active = key
                        elif bucket == "vz" and not vz_active:
                            vz_active = key
                except Exception:
                    pass
        active = virt_active
        try:
            if self.widget("vz-install-box").get_visible():
                active = vz_active or virt_active
            elif self.widget("container-install-box").get_visible():
                active = container_active or virt_active
        except Exception:
            pass
        if active:
            try:
                existing = open("/tmp/vmm-a11y-method-active.txt", "r").read().strip()
            except Exception:
                existing = ""
            if existing in (
                "local",
                "tree",
                "manual",
                "import",
                "app",
                "os",
                "container",
                "hvm",
            ):
                active = existing
            try:
                open("/tmp/vmm-a11y-method-active.txt", "w").write(active)
            except Exception:
                pass

    def _start_entry_file_poll(self, flag, path, widget_id):
        if getattr(self, flag, False):
            return
        setattr(self, flag, True)
        seen = flag + "_seen"

        def _poll(*_a, c=self, p=path, wid=widget_id, sattr=seen):
            try:
                if not os.path.exists(p):
                    return True
                text = open(p, "r").read()
                stamp = os.path.getmtime(p)
            except Exception:
                return True
            if getattr(c, sattr, None) == stamp:
                return True
            setattr(c, sattr, stamp)
            try:
                c._entry_set_text(wid, text)
            except Exception:
                pass
            return True

        GLib.timeout_add(50, _poll)

    def _start_container_a11y_polls(self):
        self._start_entry_file_poll(
            "_vmm_app_entry_poll", "/tmp/vmm-a11y-app-entry.txt", "install-app-entry"
        )
        self._start_entry_file_poll(
            "_vmm_oscontainer_fs_poll",
            "/tmp/vmm-a11y-oscontainer-fs.txt",
            "install-oscontainer-fs",
        )
        self._start_entry_file_poll(
            "_vmm_container_template_poll",
            "/tmp/vmm-a11y-container-template.txt",
            "install-container-template",
        )
        self._start_entry_file_poll(
            "_vmm_oscontainer_uri_poll",
            "/tmp/vmm-a11y-oscontainer-uri.txt",
            "install-oscontainer-source-url-entry",
        )
        self._start_entry_file_poll(
            "_vmm_oscontainer_rootpw_poll",
            "/tmp/vmm-a11y-oscontainer-rootpw.txt",
            "install-oscontainer-rootpw",
        )
        self._start_entry_file_poll(
            "_vmm_bootstrap_user_poll",
            "/tmp/vmm-a11y-bootstrap-user.txt",
            "install-oscontainer-source-user",
        )
        self._start_entry_file_poll(
            "_vmm_bootstrap_passwd_poll",
            "/tmp/vmm-a11y-bootstrap-passwd.txt",
            "install-oscontainer-source-passwd",
        )
        if not getattr(self, "_vmm_method_active_poll", False):
            self._vmm_method_active_poll = True

            def _poll_method():
                path = "/tmp/vmm-a11y-method-active.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    key = open(path, "r").read().strip()
                    stamp = os.path.getmtime(path)
                except Exception:
                    return True
                if getattr(self, "_vmm_method_active_seen", None) == stamp:
                    return True
                self._vmm_method_active_seen = stamp
                try:
                    self._set_install_method_key(key)
                except Exception:
                    pass
                try:
                    self._publish_method_a11y()
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_method)
        if not getattr(self, "_vmm_bootstrap_check_poll", False):
            self._vmm_bootstrap_check_poll = True

            def _poll_bootstrap():
                path = "/tmp/vmm-a11y-oscontainer-bootstrap.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    want = open(path, "r").read().strip().lower()
                    stamp = os.path.getmtime(path)
                except Exception:
                    return True
                if getattr(self, "_vmm_bootstrap_check_seen", None) == stamp:
                    return True
                self._vmm_bootstrap_check_seen = stamp
                try:
                    src = self.widget("install-oscontainer-bootstrap")
                    if src is None:
                        return True
                    if want in ("toggle", "click"):
                        src.set_active(not bool(src.get_active()))
                    else:
                        src.set_active(want not in ("0", "false", "off"))
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_bootstrap)
        if not getattr(self, "_vmm_container_creds_poll", False):
            self._vmm_container_creds_poll = True

            def _poll_creds():
                path = "/tmp/vmm-a11y-container-creds.txt"
                try:
                    if not os.path.exists(path):
                        return True
                    os.remove(path)
                except Exception:
                    return True
                try:
                    exp = self.widget("install-oscontainer-auth-options")
                    if exp is not None:
                        exp.set_expanded(True)
                except Exception:
                    pass
                return True

            GLib.timeout_add(50, _poll_creds)

    def _publish_arch_a11y(self):
        mapping = (
            ("arch", "/tmp/vmm-a11y-arch.txt"),
            ("machine", "/tmp/vmm-a11y-machine-type.txt"),
            ("virt-type", "/tmp/vmm-a11y-virt-type.txt"),
        )
        lists = (
            ("arch", "/tmp/vmm-a11y-combo-Architecture.txt"),
            ("machine", "/tmp/vmm-a11y-combo-Machine Type.txt"),
            ("virt-type", "/tmp/vmm-a11y-combo-Virt Type.txt"),
        )
        for wid, path in mapping:
            try:
                combo = self.widget(wid)
                model = combo.get_model() if combo is not None else None
                idx = combo.get_active() if combo is not None else -1
                label = ""
                if model is not None and idx >= 0:
                    label = str(model[idx][0] or "")
                open(path, "w").write(label)
            except Exception:
                pass
        for wid, path in lists:
            try:
                combo = self.widget(wid)
                model = combo.get_model() if combo is not None else None
                labels = []
                if model is not None:
                    it = model.get_iter_first()
                    while it is not None:
                        labels.append(str(model[it][0] or ""))
                        it = model.iter_next(it)
                open(path, "w").write("\n".join(labels))
            except Exception:
                pass

    def _sync_url_from_sentinels(self):
        """Keep install-url widgets aligned with uitest files after GetItems."""
        prev = getattr(self, "_vmm_url_syncing", False)
        self._vmm_url_syncing = True
        try:
            src = self.widget("install-url-entry")
            path = "/tmp/vmm-a11y-url-entry.txt"
            if src is not None and os.path.exists(path):
                text = open(path, "r").read()
                if (src.get_text() or "") != text:
                    src.set_text(text)
        except Exception:
            pass
        try:
            opt = self.widget("install-urlopts-entry")
            path = "/tmp/vmm-a11y-urlopts-entry.txt"
            if opt is not None and os.path.exists(path):
                text = open(path, "r").read()
                if (opt.get_text() or "") != text:
                    opt.set_text(text)
        except Exception:
            pass
        self._vmm_url_syncing = prev

    def close(self, ignore1=None, ignore2=None):
        return self._close(ignore1, ignore2)

    def _close(self, ignore1=None, ignore2=None):
        if self.is_visible():
            log.debug("Closing new vm wizard")
            vmmEngine.get_instance().decrement_window_counter()

        self.topwin.hide()
        try:
            open("/tmp/vmm-a11y-newvm-shown.txt", "w").write("0")
        except Exception:
            pass
        gtkcompat.hide_createvm_methods_window(self)
        gtkcompat.hide_oslist_activate_window(self._os_list)
        try:
            parent = self.topwin.get_transient_for()
            if parent is not None:
                parent.present()
        except Exception:
            pass

        self._cleanup_customize_window()
        if self._storage_browser:
            self._storage_browser.close()
        self._vmm_closing = True
        try:
            self._set_conn(None)
        finally:
            self._vmm_closing = False
        self._gdata = None

    def _cleanup(self):
        if self._storage_browser:
            self._storage_browser.cleanup()
            self._storage_browser = None
        if self._netlist:  # pragma: no cover
            self._netlist.cleanup()
            self._netlist = None
        if self._mediacombo:
            self._mediacombo.cleanup()
            self._mediacombo = None
        if self._addstorage:
            self._addstorage.cleanup()
            self._addstorage = None

        self.conn = None
        self._capsinfo = None
        self._gdata = None

    ##########################
    # Initial state handling #
    ##########################

    def _show_startup_error(self, error, hideinstall=True):
        self.widget("startup-error-box").show()
        self.widget("create-forward").set_sensitive(False)
        if hideinstall:
            self.widget("install-box").hide()
            self.widget("arch-expander").hide()

        self.widget("startup-error").set_text(_("Error: %s") % error)
        gtkcompat.set_accessible_name(
            self.widget("startup-error"), _("Error: %s") % error
        )
        gtkcompat.expose_a11y_label(
            "create-startup-error",
            _("Error: %s") % error,
            _("Error: %s") % error,
            window=self.topwin,
        )
        try:
            open("/tmp/vmm-a11y-createvm-startup-error.txt", "w").write(
                _("Error: %s") % error
            )
        except Exception:
            pass
        return False

    def _show_startup_warning(self, error):
        self.widget("startup-error-box").show()
        self.widget("startup-error").set_markup(_("<span size='small'>Warning: %s</span>") % error)
        gtkcompat.set_accessible_name(self.widget("startup-error"), _("Warning: %s") % error)
        gtkcompat.expose_a11y_label(
            "create-startup-error",
            _("Warning: %s") % error,
            _("Warning: %s") % error,
            window=self.topwin,
        )
        try:
            open("/tmp/vmm-a11y-createvm-startup-error.txt", "w").write(
                _("Warning: %s") % error
            )
        except Exception:
            pass

    def _show_arch_warning(self, error):
        self.widget("arch-warning-box").show()
        self.widget("arch-warning").set_markup(_("<span size='small'>Warning: %s</span>") % error)

    def _init_state(self):
        self.widget("create-pages").set_show_tabs(False)
        self.widget("install-method-pages").set_show_tabs(False)

        # Connection list
        self.widget("create-conn-label").set_text("")
        self.widget("startup-error").set_text("")
        conn_list = self.widget("create-conn")
        conn_model = Gtk.ListStore(str, str)
        conn_list.set_model(conn_model)
        text = uiutil.init_combo_text_column(conn_list, 1)
        text.set_property("ellipsize", Pango.EllipsizeMode.MIDDLE)

        def set_model_list(widget_id):
            lst = self.widget(widget_id)
            model = Gtk.ListStore(str)
            lst.set_model(model)
            lst.set_entry_text_column(0)

        # Lists for the install urls
        set_model_list("install-url-combo")

        # Lists for OS container bootstrap
        set_model_list("install-oscontainer-source-url-combo")

        # Architecture
        archList = self.widget("arch")
        # [label, guest.os.arch value]
        archModel = Gtk.ListStore(str, str)
        archList.set_model(archModel)
        uiutil.init_combo_text_column(archList, 0)
        archList.set_row_separator_func(lambda m, i, ignore: m[i][0] is None, None)

        # guest.os.type value for xen (hvm vs. xen)
        hyperList = self.widget("xen-type")
        # [label, guest.os_type value]
        hyperModel = Gtk.ListStore(str, str)
        hyperList.set_model(hyperModel)
        uiutil.init_combo_text_column(hyperList, 0)

        # guest.os.machine value
        lst = self.widget("machine")
        # [machine ID]
        model = Gtk.ListStore(str)
        lst.set_model(model)
        uiutil.init_combo_text_column(lst, 0)
        lst.set_row_separator_func(lambda m, i, ignore: m[i][0] is None, None)

        # guest.type value for xen (qemu vs kvm)
        lst = self.widget("virt-type")
        # [label, guest.type value]
        model = Gtk.ListStore(str, str)
        lst.set_model(model)
        uiutil.init_combo_text_column(lst, 0)

        # OS distro list
        self._os_list = vmmOSList()
        self._last_osobj = None
        self._os_list.connect("os-selected", self._os_selected)
        self.widget("install-os-align").add(self._os_list.search_entry)
        self.widget("os-label").set_mnemonic_widget(self._os_list.search_entry)
        self._ungroup_virt_install_methods()
        self._init_create_a11y()

    def _ungroup_virt_install_methods(self):
        """GTK 4 CheckButton groups reject set_active() on a sibling.

        Builder group= leaves method-local on after Manual is clicked, so
        exclusivity and notify::active live in this class instead.
        """
        for name in (
            "method-local",
            "method-tree",
            "method-manual",
            "method-import",
        ):
            src = self.widget(name)
            if src is None:
                continue
            try:
                src.set_group(None)
            except Exception:
                pass
            try:
                src.connect("notify::active", self._method_changed)
            except Exception:
                pass

    def _init_create_a11y(self):
        gtkcompat.attach_notebook_a11y(self.widget("create-pages"))
        gtkcompat.attach_notebook_a11y(self.widget("install-method-pages"))
        gtkcompat.set_accessible_name(self.widget("header-pagenum"), "pagenum-label")
        ptxt = self.widget("header-pagenum").get_text() or "pagenum-label"
        try:
            open("/tmp/vmm-a11y-pagenum.txt", "w").write(ptxt)
        except Exception:
            pass
        gtkcompat.expose_a11y_label(
            "create-pagenum",
            "pagenum-label: %s" % ptxt,
            ptxt,
            window=self.topwin,
        )
        gtkcompat.expose_a11y_label(
            "create-startup-error",
            "startup-error",
            self.widget("startup-error").get_text() or "startup-error",
            window=self.topwin,
        )
        gtkcompat.expose_a11y_combo(
            "create-conn",
            "create-conn",
            self.widget("create-conn"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "create-forward",
            "Forward",
            lambda: self.widget("create-forward").emit("clicked"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "create-back",
            "Back",
            lambda: self.widget("create-back").emit("clicked"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "create-finish",
            "Finish",
            lambda: self.widget("create-finish").emit("clicked"),
            window=self.topwin,
        )
        gtkcompat.set_accessible_name(self.widget("create-forward"), ".create-forward-real")
        gtkcompat.set_accessible_name(self.widget("create-back"), ".create-back-real")
        gtkcompat.set_accessible_name(self.widget("create-finish"), ".create-finish-real")
        for wid in ("create-forward", "create-back", "create-finish"):
            src = self.widget(wid)
            try:
                src.set_accessible_role(Gtk.AccessibleRole.GENERIC)
                src.update_state([Gtk.AccessibleState.HIDDEN], [True])
            except Exception:
                pass
        for wid, name in (
            ("method-local", "Local install media (ISO image or CDROM)"),
            ("method-tree", "Network Install (HTTP, HTTPS, or FTP)"),
            ("method-import", "Import existing disk image"),
            ("method-manual", "Manual install"),
            ("method-container-app", "Application"),
            ("method-container-os", "Operating system"),
            ("vz-virt-type-exe", "Container"),
            ("vz-virt-type-hvm", "Virtual machine"),
        ):
            src = self.widget(wid)
            gtkcompat.sync_accessible_checked(src)
            gtkcompat.expose_a11y_check(
                wid, name, src, window=self.topwin, radio=True
            )
            gtkcompat.set_accessible_name(src, ".%s-real" % wid)
            try:
                src.set_accessible_role(Gtk.AccessibleRole.GENERIC)
                src.update_state([Gtk.AccessibleState.HIDDEN], [True])
            except Exception:
                pass
            for child in gtkcompat.get_children(src):
                gtkcompat.set_accessible_name(child, ".%s-child" % wid)
                try:
                    child.set_accessible_role(Gtk.AccessibleRole.GENERIC)
                except Exception:
                    pass
            def _activate(_w, s=src):
                try:
                    s.set_active(True)
                except Exception:
                    pass
            try:
                src.connect("activate", _activate)
            except Exception:
                pass
            sidecar = gtkcompat._A11Y_SIDECAR.get("items", {}).get(wid)
            if sidecar is not None:
                gtkcompat.set_accessible_name(sidecar, name)
                gtkcompat.sync_accessible_checked(sidecar)
        gtkcompat.expose_oslist_a11y(self._os_list, self.topwin)
        try:
            self._os_list._vmm_disable_detect = lambda: self.widget(
                "install-detect-os"
            ).set_active(False)
        except Exception:
            pass
        gtkcompat.expose_a11y_entry(
            "create-vm-name",
            "Name:",
            self.widget("create-vm-name"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_check(
            "install-detect-os",
            "Automatically detect from the installation media / source",
            self.widget("install-detect-os"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "install-iso-browse",
            "install-iso-browse",
            lambda: self.widget("install-iso-browse").emit("clicked"),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "install-app-browse",
            "install-app-browse",
            lambda: self._browse_app(None),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "install-oscontainer-browse",
            "install-oscontainer-browse",
            lambda: self._browse_oscontainer(None),
            window=self.topwin,
        )
        gtkcompat.expose_a11y_button(
            "install-import-browse",
            "install-import-browse",
            lambda: self._browse_import(None),
            window=self.topwin,
        )
        try:
            gtkcompat.register_a11y_click(
                "install-app-browse", lambda: self._browse_app(None)
            )
            gtkcompat.register_a11y_click(
                "install-oscontainer-browse", lambda: self._browse_oscontainer(None)
            )
            gtkcompat.register_a11y_click(
                "install-import-browse", lambda: self._browse_import(None)
            )
        except Exception:
            pass
        try:
            gtkcompat.expose_a11y_entry(
                "install-app-entry",
                "application path",
                self.widget("install-app-entry"),
                window=self.topwin,
                name_with_value=True,
            )
            gtkcompat.expose_a11y_entry(
                "install-oscontainer-fs",
                "root directory",
                self.widget("install-oscontainer-fs"),
                window=self.topwin,
                name_with_value=True,
            )
            gtkcompat.expose_a11y_entry(
                "install-container-template",
                "container template",
                self.widget("install-container-template"),
                window=self.topwin,
                name_with_value=True,
            )
            gtkcompat.expose_a11y_entry(
                "install-oscontainer-source-uri",
                "install-oscontainer-source-uri",
                self.widget("install-oscontainer-source-url-entry"),
                window=self.topwin,
                name_with_value=True,
            )
            gtkcompat.expose_a11y_entry(
                "install-oscontainer-root-passwd",
                "install-oscontainer-root-passwd",
                self.widget("install-oscontainer-rootpw"),
                window=self.topwin,
            )
            gtkcompat.expose_a11y_entry(
                "bootstrap-registry-user",
                "bootstrap-registry-user",
                self.widget("install-oscontainer-source-user"),
                window=self.topwin,
            )
            gtkcompat.expose_a11y_entry(
                "bootstrap-registry-password",
                "bootstrap-registry-password",
                self.widget("install-oscontainer-source-passwd"),
                window=self.topwin,
            )
            gtkcompat.expose_a11y_check(
                "install-oscontainer-bootstrap",
                "Create OS directory tree from container image",
                self.widget("install-oscontainer-bootstrap"),
                window=self.topwin,
            )
            gtkcompat.expose_a11y_button(
                "install-oscontainer-auth-options",
                "Credentials",
                lambda: self.widget("install-oscontainer-auth-options").set_expanded(
                    True
                ),
                window=self.topwin,
            )
        except Exception:
            pass
        if self._mediacombo is not None:
            gtkcompat.expose_a11y_combo(
                "media-combo",
                "media-combo",
                self._mediacombo._combo,
                window=self.topwin,
            )
            gtkcompat.expose_a11y_entry(
                "media-entry",
                "media-entry",
                self._mediacombo._entry,
                window=self.topwin,
                name_with_value=True,
            )
        if self._addstorage is not None:
            gtkcompat.expose_a11y_entry(
                "storage-entry",
                "storage-entry",
                self._addstorage.widget("storage-entry"),
                window=self.topwin,
                name_with_value=True,
            )
            gtkcompat.expose_a11y_button(
                "storage-browse",
                "storage-browse",
                lambda: self._addstorage.widget("storage-browse").emit("clicked"),
                window=self.topwin,
            )
            for wid, name in (
                ("storage-create", "Create a disk image for the virtual machine"),
                ("storage-select", "Select or create custom storage"),
            ):
                src = self._addstorage.widget(wid)
                gtkcompat.set_accessible_name(src, name)
                gtkcompat.sync_accessible_checked(src)
                gtkcompat.expose_a11y_check(
                    wid, name, src, window=self.topwin, radio=True
                )
                sidecar = gtkcompat._A11Y_SIDECAR.get("items", {}).get(wid)
                if sidecar is not None:
                    gtkcompat.set_accessible_name(sidecar, name)
            gtkcompat.expose_a11y_spin(
                "storage-size",
                "GiB",
                self._addstorage.widget("storage-size"),
                window=self.topwin,
            )
        try:
            gtkcompat.expose_a11y_spin(
                "cpus", "cpus", self.widget("cpus"), window=self.topwin
            )
            gtkcompat.expose_a11y_spin(
                "mem", "Memory:", self.widget("mem"), window=self.topwin
            )
            gtkcompat.expose_a11y_check(
                "summary-customize",
                "Customize configuration before install",
                self.widget("summary-customize"),
                window=self.topwin,
            )
        except Exception:
            pass

    def _os_selected(self, _src, osobj):
        self._last_osobj = osobj

    def _reset_state(self, urihint=None):
        """
        Reset all UI state to default values. Conn specific state is
        populated in _populate_conn_state
        """
        self._last_osobj = None
        self._vmm_disk_inuse_retried = False
        for path in (
            "/tmp/vmm-a11y-storage-entry.txt",
            "/tmp/vmm-a11y-net-source.txt",
            "/tmp/vmm-a11y-net-device.txt",
            "/tmp/vmm-a11y-net-warn.txt",
            "/tmp/vmm-a11y-combo-net-source.txt",
            "/tmp/vmm-a11y-url-entry.txt",
            "/tmp/vmm-a11y-urlopts-entry.txt",
            "/tmp/vmm-a11y-url-activate",
            "/tmp/vmm-a11y-create-forward",
            "/tmp/vmm-a11y-create-back",
            "/tmp/vmm-a11y-create-finish",
            "/tmp/vmm-a11y-oslist-entry.txt",
            "/tmp/vmm-a11y-oslist-confirmed",
            "/tmp/vmm-a11y-os-select.txt",
            "/tmp/vmm-a11y-detect-state.txt",
            "/tmp/vmm-a11y-disk-inuse-allow",
            "/tmp/vmm-a11y-import-entry.txt",
            "/tmp/vmm-a11y-method-active.txt",
            "/tmp/vmm-a11y-media-entry.txt",
            "/tmp/vmm-a11y-media-entry.txt.set",
            "/tmp/vmm-a11y-media-select.txt",
            "/tmp/vmm-a11y-media-browse.txt",
            "/tmp/vmm-a11y-createvm-media-combo.txt",
            "/tmp/vmm-a11y-alert.txt",
            "/tmp/vmm-a11y-alert-response.txt",
            "/tmp/vmm-a11y-boot-menu.txt",
            "/tmp/vmm-a11y-xml.txt",
            "/tmp/vmm-a11y-xml-contents.txt",
            "/tmp/vmm-a11y-details-media-entry.txt",
            "/tmp/vmm-a11y-details-media-entry.txt.set",
            "/tmp/vmm-a11y-details-media-path.txt",
        ):
            try:
                os.unlink(path)
            except Exception:
                pass
        self.reset_finish_cursor()

        self._vmm_goto_page = PAGE_NAME
        self.widget("create-pages").set_current_page(PAGE_NAME)
        self._page_changed(None, None, PAGE_NAME)

        # Name page state
        self.widget("create-vm-name").set_text("")
        self._set_install_method_key("local")
        self.widget("create-conn").set_active(-1)
        activeconn = self._populate_conn_list(urihint)
        self.widget("arch-expander").set_expanded(False)
        self.widget("vz-virt-type-hvm").set_active(True)

        if self._set_conn(activeconn) is False:
            return False

        # Everything from this point forward should be connection independent

        # Distro/Variant
        self._os_list.reset_state()
        self._os_already_detected_for_media = False

        def _populate_media_model(media_model, urls):
            media_model.clear()
            for url in urls or []:
                media_model.append([url])

        # Install local
        self._mediacombo.reset_state()
        gtkcompat.publish_media_combo_rows(self)

        # Install URL
        self.widget("install-urlopts-entry").set_text("")
        self.widget("install-url-entry").set_text("")
        self.widget("install-url-options").set_expanded(False)
        urlmodel = self.widget("install-url-combo").get_model()
        _populate_media_model(urlmodel, self.config.get_media_urls())
        try:
            gtkcompat._publish_createvm_url_state(self)
        except Exception:
            pass

        # Install import
        self.widget("install-import-entry").set_text("")

        # Install container app
        self.widget("install-app-entry").set_text("/bin/sh")

        # Install container OS
        self.widget("install-oscontainer-fs").set_text("")
        self.widget("install-oscontainer-source-url-entry").set_text("")
        self.widget("install-oscontainer-source-user").set_text("")
        self.widget("install-oscontainer-source-passwd").set_text("")
        self.widget("install-oscontainer-source-insecure").set_active(False)
        self.widget("install-oscontainer-bootstrap").set_active(False)
        self.widget("install-oscontainer-auth-options").set_expanded(False)
        self.widget("install-oscontainer-rootpw").set_text("")
        src_model = self.widget("install-oscontainer-source-url-combo").get_model()
        _populate_media_model(src_model, self.config.get_container_urls())

        # Install VZ container from template
        self.widget("install-container-template").set_text("centos-7-x86_64")

        # Storage
        self.widget("enable-storage").set_active(True)
        self._addstorage.reset_state()
        self._addstorage.widget("storage-create").set_active(True)
        self._addstorage.widget("storage-entry").set_text("")

        # Final page
        self.widget("summary-customize").set_active(False)
        gtkcompat.hide_inactive_notebook_pages(
            self.widget("create-pages"), PAGE_NAME, self.topwin
        )

    def _set_caps_state(self):
        """
        Set state that is dependent on when capsinfo changes
        """
        self.widget("arch-warning-box").hide()
        self._gdata = self._build_guestdata()
        guest = self._gdata.build_guest()

        # Helper state
        is_local = not self.conn.is_remote()
        is_storage_capable = self.conn.support.conn_storage()
        can_storage = is_local or is_storage_capable
        is_pv = guest.os.is_xenpv()
        is_container_only = self.conn.is_container_only()
        is_vz = self.conn.is_vz()
        is_vz_container = is_vz and guest.os.is_container()
        can_remote_url = self.conn.get_backend().support_remote_url_install()

        installable_arch = bool(guest.os.is_x86() or guest.os.is_ppc64() or guest.os.is_s390x())

        default_efi = (
            self.config.get_default_firmware_setting() == "uefi"
            and guest.os.is_x86()
            and guest.os.is_hvm()
        )
        if default_efi:
            log.debug("UEFI default requested via app preferences")

        if guest.prefers_uefi() or default_efi:
            try:
                # We call this for validation
                guest.enable_uefi()
                self._gdata.uefi_requested = True
                installable_arch = True
                log.debug("UEFI found, setting it as default.")
            except Exception as e:
                installable_arch = False
                log.debug("Error checking for UEFI default", exc_info=True)
                msg = _("Failed to setup UEFI: %s\nInstall options are limited.") % e
                self._show_arch_warning(msg)

        # Install Options
        method_tree = self.widget("method-tree")
        method_manual = self.widget("method-manual")
        method_local = self.widget("method-local")
        method_import = self.widget("method-import")
        method_container_app = self.widget("method-container-app")

        method_tree.set_sensitive((is_local or can_remote_url) and installable_arch)
        method_local.set_sensitive(not is_pv and can_storage and installable_arch)
        method_manual.set_sensitive(not is_container_only)
        method_import.set_sensitive(can_storage)
        virt_methods = [method_local, method_tree, method_manual, method_import]

        local_tt = None
        tree_tt = None
        import_tt = None

        if not is_local:
            if not can_remote_url:
                tree_tt = _("Libvirt version does not support remote URL installs.")
            if not is_storage_capable:  # pragma: no cover
                local_tt = _("Connection does not support storage management.")
                import_tt = local_tt

        if is_pv:
            local_tt = _("CDROM/ISO installs not available for paravirt guests.")

        if not installable_arch:
            msg = _("Architecture '%s' is not installable") % guest.os.arch
            tree_tt = msg
            local_tt = msg

        if not any([w.get_active() and w.get_sensitive() for w in virt_methods]):
            for w, key in (
                (method_local, "local"),
                (method_tree, "tree"),
                (method_manual, "manual"),
                (method_import, "import"),
            ):
                if w.get_sensitive():
                    self._set_install_method_key(key)
                    break

        if not (is_container_only or [w for w in virt_methods if w.get_sensitive()]):
            return self._show_startup_error(  # pragma: no cover
                _("No install methods available for this connection."), hideinstall=False
            )

        method_tree.set_tooltip_text(tree_tt or "")
        method_local.set_tooltip_text(local_tt or "")
        method_import.set_tooltip_text(import_tt or "")

        # Container install options
        method_container_app.set_active(True)
        self.widget("container-install-box").set_visible(is_container_only)
        self.widget("vz-install-box").set_visible(is_vz)
        self.widget("virt-install-box").set_visible(not is_container_only and not is_vz_container)

        self.widget("kernel-info-box").set_visible(not installable_arch)
        try:
            self._publish_method_a11y()
        except Exception:
            pass

    def _populate_conn_state(self):
        """
        Update all state that has some dependency on the current connection
        """
        self.conn.schedule_priority_tick(pollnet=True, pollpool=True, pollnodedev=True)

        self.widget("install-box").show()
        self.widget("create-forward").set_sensitive(True)

        self._capsinfo = None
        self.conn.invalidate_caps()

        if not self.conn.caps.has_install_options():
            error = _("No hypervisor options were found for this connection.")

            if self.conn.is_qemu():
                error += "\n\n"
                error += _(
                    "This usually means that QEMU or KVM is not "
                    "installed on your machine, or the KVM kernel "
                    "modules are not loaded."
                )
            return self._show_startup_error(error)

        self._change_caps()

        # A bit out of order, but populate the xen/virt/arch/machine lists
        # so we can work with a default.
        self._populate_xen_type()
        self._populate_arch()
        self._populate_virt_type()

        show_arch = (
            self.widget("xen-type").get_visible()
            or self.widget("virt-type").get_visible()
            or self.widget("arch").get_visible()
            or self.widget("machine").get_visible()
        )
        uiutil.set_grid_row_visible(self.widget("arch-expander"), show_arch)

        if self.conn.is_qemu():
            if not self._capsinfo.guest.is_kvm_available():
                error = _(
                    "KVM is not available. This may mean the KVM "
                    "package is not installed, or the KVM kernel modules "
                    "are not loaded. Your virtual machines may perform poorly."
                )
                self._show_startup_warning(error)

        elif self.conn.is_vz():
            has_hvm_guests = False
            has_exe_guests = False
            for g in self.conn.caps.guests:
                if g.os_type == "hvm":
                    has_hvm_guests = True
                if g.os_type == "exe":
                    has_exe_guests = True

            self.widget("vz-virt-type-hvm").set_sensitive(has_hvm_guests)
            self.widget("vz-virt-type-exe").set_sensitive(has_exe_guests)
            self.widget("vz-virt-type-hvm").set_active(has_hvm_guests)
            self.widget("vz-virt-type-exe").set_active(not has_hvm_guests and has_exe_guests)

        # ISO media
        # Dependent on connection so we need to do this here
        self._mediacombo.set_conn(self.conn)
        self._mediacombo.reset_state()
        gtkcompat.publish_media_combo_rows(self)

        # Allow container bootstrap only for local connection and
        # only if virt-bootstrap is installed. Otherwise, show message.
        is_local = not self.conn.is_remote()
        vb_installed = is_virt_bootstrap_installed(self.conn)
        vb_enabled = is_local and vb_installed

        oscontainer_widget_conf = {
            "install-oscontainer-notsupport-conn": not is_local,
            "install-oscontainer-notsupport": not vb_installed,
            "install-oscontainer-bootstrap": vb_enabled,
            "install-oscontainer-source": vb_enabled,
            "install-oscontainer-rootpw-box": vb_enabled,
        }
        for wname, val in oscontainer_widget_conf.items():
            self.widget(wname).set_visible(val)

        # Memory
        memory = int(self.conn.host_memory_size())
        mem_label = _("Up to %(maxmem)s available on the host") % {"maxmem": _pretty_memory(memory)}
        mem_label = "<span size='small'>%s</span>" % mem_label
        self.widget("mem").set_range(50, memory // 1024)
        self.widget("phys-mem-label").set_markup(mem_label)

        # CPU
        phys_cpus = int(self.conn.host_active_processor_count())
        cpu_label = ngettext(
            "Up to %(numcpus)d available", "Up to %(numcpus)d available", phys_cpus
        ) % {"numcpus": int(phys_cpus)}
        cpu_label = "<span size='small'>%s</span>" % cpu_label
        self.widget("cpus").set_range(1, max(phys_cpus, 1))
        self.widget("phys-cpu-label").set_markup(cpu_label)

        # Storage
        self._addstorage.conn = self.conn
        self._addstorage.reset_state()

        # Networking
        self.widget("advanced-expander").set_expanded(False)

        self._netlist = vmmNetworkList(self.conn, self.builder, self.topwin)
        self.widget("netdev-ui-align").add(self._netlist.top_box)
        self._netlist.reset_state()
        try:
            win = getattr(self, "_vmm_methods_win", None)
            if win is not None:
                gtkcompat._append_createvm_net_controls(win.get_child(), self)
        except Exception:
            pass

    def _conn_state_changed(self, conn):
        if conn.is_disconnected():
            self._close()

    def _retry_conn_if_none(self):
        if self.conn is not None:
            return False
        try:
            activeconn = self._populate_conn_list()
        except Exception:
            return False
        if activeconn is not None:
            self._set_conn(activeconn)
        return False

    def _set_conn(self, newconn):
        self.widget("startup-error-box").hide()
        self.widget("arch-warning-box").hide()
        try:
            os.remove("/tmp/vmm-a11y-createvm-startup-error.txt")
        except Exception:
            pass

        oldconn = self.conn
        self.conn = newconn
        if oldconn:
            oldconn.disconnect_by_obj(self)
        if self._netlist:
            gtkcompat.container_remove(
                self.widget("netdev-ui-align"), self._netlist.top_box
            )
            self._netlist.cleanup()
            self._netlist = None
            try:
                win = getattr(self, "_vmm_methods_win", None)
                child = win.get_child() if win is not None else None
                if child is not None:
                    child._vmm_netlist_id = None
            except Exception:
                pass

        if not self.conn:
            # Closing after a successful install must not publish this
            # error; opening/resetting the wizard still should.
            if getattr(self, "_vmm_closing", False):
                return False
            return self._show_startup_error(_("No active connection to install on."))
        self.conn.connect("state-changed", self._conn_state_changed)

        try:
            return self._populate_conn_state()
        except Exception as e:  # pragma: no cover
            log.exception("Error setting create wizard conn state.")
            return self._show_startup_error(str(e))

    def _change_caps(self, gtype=None, arch=None, domtype=None):
        """
        Change the cached capsinfo for the passed values, and trigger
        all needed UI refreshing
        """
        if gtype is None:
            # If none specified, prefer HVM so install options aren't limited
            # with a default PV choice.
            for g in self.conn.caps.guests:
                if g.os_type == "hvm":
                    gtype = "hvm"
                    break

        capsinfo = self.conn.caps.guest_lookup(os_type=gtype, arch=arch, typ=domtype)

        if self._capsinfo:
            if self._capsinfo.guest == capsinfo.guest and self._capsinfo.domain == capsinfo.domain:
                return

        self._capsinfo = capsinfo
        log.debug(
            "Guest type set to os_type=%s, arch=%s, dom_type=%s",
            self._capsinfo.os_type,
            self._capsinfo.arch,
            self._capsinfo.hypervisor_type,
        )
        self._populate_machine()
        self._set_caps_state()

    ##################################################
    # Helpers for populating hv/arch/machine/conn UI #
    ##################################################

    def _populate_xen_type(self):
        model = self.widget("xen-type").get_model()
        model.clear()

        default = 0
        guests = []
        if self.conn.is_xen() or self.conn.is_test():
            guests = self.conn.caps.guests[:]

        for guest in guests:
            if not guest.domains:
                continue  # pragma: no cover

            gtype = guest.os_type
            dom = guest.domains[0]
            domtype = dom.hypervisor_type
            label = self.conn.pretty_hv(gtype, domtype)

            # Don't add multiple rows for each arch
            for m in model:
                if m[0] == label:
                    label = None
                    break
            if label is None:
                continue

            # Determine if this is the default given by guest_lookup
            if gtype == self._capsinfo.os_type and domtype == self._capsinfo.hypervisor_type:
                default = len(model)

            model.append([label, gtype])

        show = bool(len(model))
        uiutil.set_grid_row_visible(self.widget("xen-type"), show)
        if show:
            self.widget("xen-type").set_active(default)

    def _populate_arch(self):
        model = self.widget("arch").get_model()
        model.clear()

        default = 0
        archs = []
        for guest in self.conn.caps.guests:
            if guest.os_type == self._capsinfo.os_type:
                archs.append(guest.arch)

        # Combine x86/i686 to avoid confusion
        if self.conn.caps.host.cpu.arch == "x86_64" and "x86_64" in archs and "i686" in archs:
            archs.remove("i686")
        archs.sort()

        prios = ["x86_64", "i686", "aarch64", "armv7l", "ppc64", "ppc64le", "riscv64", "s390x"]
        if self.conn.caps.host.cpu.arch not in prios:
            prios = []  # pragma: no cover
        for p in prios[:]:
            if p not in archs:
                prios.remove(p)
            else:
                archs.remove(p)
        if prios:
            if archs:
                prios += [None]
            archs = prios + archs

        default = 0
        if self._capsinfo.arch in archs:
            default = archs.index(self._capsinfo.arch)

        for arch in archs:
            model.append([_pretty_arch(arch), arch])

        show = not (len(archs) < 2)
        uiutil.set_grid_row_visible(self.widget("arch"), show)
        self.widget("arch").set_active(default)
        try:
            self._publish_arch_a11y()
        except Exception:
            pass

    def _populate_virt_type(self):
        model = self.widget("virt-type").get_model()
        model.clear()

        # Allow choosing between qemu and kvm for archs that traditionally
        # have a decent amount of TCG usage, like armv7l. Also include
        # aarch64 which can be used for arm32 VMs as well
        domains = [d.hypervisor_type for d in self._capsinfo.guest.domains[:]]
        if not self.conn.is_qemu():
            domains = []
        elif self._capsinfo.arch in ["i686", "x86_64", "ppc64", "ppc64le"]:
            domains = []

        default = 0
        if self._capsinfo.hypervisor_type in domains:
            default = domains.index(self._capsinfo.hypervisor_type)

        prios = ["kvm"]
        for domain in prios:
            if domain not in domains:
                continue
            domains.remove(domain)
            domains.insert(0, domain)

        for domain in domains:
            label = self.conn.pretty_hv(self._capsinfo.os_type, domain)
            model.append([label, domain])

        show = bool(len(model) > 1)
        uiutil.set_grid_row_visible(self.widget("virt-type"), show)
        self.widget("virt-type").set_active(default)
        try:
            self._publish_arch_a11y()
        except Exception:
            pass

    def _populate_machine(self):
        model = self.widget("machine").get_model()

        machines = self._capsinfo.machines[:]
        if self._capsinfo.arch in ["i686", "x86_64"]:
            machines = []
        machines.sort()

        defmachine = None
        prios = []
        recommended_machine = virtinst.Guest.get_recommended_machine(self._capsinfo)
        if recommended_machine:
            defmachine = recommended_machine
            prios = [defmachine]

        for p in prios[:]:
            if p not in machines:
                prios.remove(p)  # pragma: no cover
            else:
                machines.remove(p)
        if prios:
            machines = prios + [None] + machines

        default = 0
        if defmachine and defmachine in machines:
            default = machines.index(defmachine)

        try:
            self.widget("machine").disconnect_by_func(self._machine_changed)
        except TypeError:
            pass
        try:
            model.clear()
            for m in machines:
                model.append([m])

            show = len(machines) > 1
            uiutil.set_grid_row_visible(self.widget("machine"), show)
            if show:
                self.widget("machine").set_active(default)
            try:
                self._publish_arch_a11y()
            except Exception:
                pass
        finally:
            self.widget("machine").connect("changed", self._machine_changed)

    def _populate_conn_list(self, urihint=None):
        conn_list = self.widget("create-conn")
        model = conn_list.get_model()
        model.clear()

        default = -1
        connmanager = vmmConnectionManager.get_instance()
        for connobj in connmanager.conns.values():
            if not connobj.is_active():
                continue

            if connobj.get_uri() == urihint:
                default = len(model)
            elif default < 0 and not connobj.is_remote():
                # Favor local connections over remote connections
                default = len(model)

            model.append([connobj.get_uri(), connobj.get_pretty_desc()])

        no_conns = len(model) == 0

        if default < 0 and not no_conns:
            default = 0  # pragma: no cover

        activeuri = ""
        activedesc = ""
        activeconn = None
        if not no_conns:
            conn_list.set_active(default)
            activeuri, activedesc = model[default]
            activeconn = connmanager.conns[activeuri]

        self.widget("create-conn-label").set_text(activedesc)
        if len(model) <= 1:
            self.widget("create-conn").hide()
            self.widget("create-conn-label").show()
        else:
            self.widget("create-conn").show()
            self.widget("create-conn-label").hide()

        return activeconn

    ###############################
    # Misc UI populating routines #
    ###############################

    def _populate_summary_storage(self, path=None):
        storagetmpl = "<span size='small'>%s</span>"
        storagesize = ""
        storagepath = ""

        disk = self._gdata.disk
        fs = self._gdata.filesystem
        if disk:
            if disk.wants_storage_creation():
                storagesize = "%s" % _pretty_storage(disk.get_size())
            if not path:
                path = disk.get_source_path()
            storagepath = storagetmpl % path
        elif fs:
            storagepath = storagetmpl % fs.source
        else:
            storagepath = _("None")

        self.widget("summary-storage").set_markup(storagesize)
        self.widget("summary-storage").set_visible(bool(storagesize))
        self.widget("summary-storage-path").set_markup(storagepath)
        try:
            open("/tmp/vmm-a11y-create-storage-path.txt", "w").write(path or "")
        except Exception:
            pass

    def _populate_summary(self):
        guest = self._gdata.build_guest()
        mem = _pretty_memory(int(guest.memory or 0))
        cpu = str(int(guest.vcpus or 1))

        instmethod = self._get_config_install_page()
        install = ""
        if instmethod == INSTALL_PAGE_ISO:
            install = _("Local CDROM/ISO")
        elif instmethod == INSTALL_PAGE_URL:
            install = _("URL Install Tree")
        elif instmethod == INSTALL_PAGE_IMPORT:
            install = _("Import existing OS image")
        elif instmethod == INSTALL_PAGE_MANUAL:
            install = _("Manual install")
        elif instmethod == INSTALL_PAGE_CONTAINER_APP:
            install = _("Application container")
        elif instmethod == INSTALL_PAGE_CONTAINER_OS:
            install = _("Operating system container")
        elif instmethod == INSTALL_PAGE_VZ_TEMPLATE:
            install = _("Virtuozzo container")

        self.widget("summary-os").set_text(guest.osinfo.label)
        self.widget("summary-install").set_text(install)
        self.widget("summary-mem").set_text(mem)
        self.widget("summary-cpu").set_text(cpu)
        self._populate_summary_storage()

        nsource = self._netlist.get_network_selection()[1]
        if not nsource:
            self.widget("advanced-expander").set_expanded(True)

    ################################
    # UI state getters and helpers #
    ################################

    def _lookup_entry_widgets(self, widget_id):
        widgets = []
        try:
            widgets.append(self.widget(widget_id))
        except Exception:
            pass
        combo_id = widget_id.replace("-entry", "-combo")
        if combo_id != widget_id:
            try:
                widgets.append(self.widget(combo_id))
            except Exception:
                pass
        return widgets

    def _entry_get_text(self, widget_id):
        widgets = self._lookup_entry_widgets(widget_id)
        for w in widgets:
            if w is None:
                continue
            for cand in (w, getattr(w, "get_child", lambda: None)()):
                if cand is None:
                    continue
                try:
                    return cand.get_text() or ""
                except Exception:
                    pass
        return ""

    def _entry_set_text(self, widget_id, text):
        widgets = self._lookup_entry_widgets(widget_id)
        for w in widgets:
            if w is None:
                continue
            for cand in (w, getattr(w, "get_child", lambda: None)()):
                if cand is None:
                    continue
                try:
                    cand.set_text(text)
                    return True
                except Exception:
                    pass
        return False

    def _get_widget_or_file(self, widget_id, path):
        file_text = None
        if os.path.exists(path):
            try:
                file_text = open(path, "r").read()
            except Exception:
                file_text = None
        widget_text = self._entry_get_text(widget_id)
        if file_text:
            self._entry_set_text(widget_id, file_text)
            return file_text
        if widget_text:
            return widget_text
        return file_text if file_text is not None else ""

    def _get_config_name(self):
        return self.widget("create-vm-name").get_text()

    def _apply_create_name_file(self):
        """Apply a typed Name sentinel. Ignore empty leftovers so they
        cannot wipe the generated guest name before Finish."""
        path = "/tmp/vmm-a11y-create-name.txt"
        try:
            if not os.path.exists(path):
                return
            text = open(path, "r").read()
            os.remove(path)
        except Exception:
            return
        if not (text or "").strip():
            return
        try:
            self.widget("create-vm-name").set_text(text)
        except Exception:
            pass

    def _ensure_guest_name(self):
        """Keep the generated name when the finish-page entry is empty."""
        name = ""
        try:
            name = (self.widget("create-vm-name").get_text() or "").strip()
        except Exception:
            name = ""
        if name:
            return name
        kept = ""
        try:
            kept = ((self._gdata and self._gdata.name) or "").strip()
        except Exception:
            kept = ""
        if kept:
            try:
                self.widget("create-vm-name").set_text(kept)
            except Exception:
                pass
        return kept

    def _get_config_machine(self):
        return uiutil.get_list_selection(self.widget("machine"), check_visible=True)

    def _get_config_install_page(self):
        by_key = {
            "local": INSTALL_PAGE_ISO,
            "tree": INSTALL_PAGE_URL,
            "import": INSTALL_PAGE_IMPORT,
            "manual": INSTALL_PAGE_MANUAL,
            "app": INSTALL_PAGE_CONTAINER_APP,
            "os": INSTALL_PAGE_CONTAINER_OS,
            "container": INSTALL_PAGE_VZ_TEMPLATE,
        }
        try:
            key = open("/tmp/vmm-a11y-method-active.txt", "r").read().strip()
        except Exception:
            key = ""
        # Do not apply() here: a getter that set_active(local) from a
        # stale file undoes a mouse click on Manual. Pollers/Forward
        # apply the sentinel. This lookup honors the file after a click
        # writes it, else the widgets.
        if key in by_key:
            return by_key[key]
        if self.widget("vz-install-box").get_visible():
            if self.widget("vz-virt-type-exe").get_active():
                return INSTALL_PAGE_VZ_TEMPLATE
        if self.widget("virt-install-box").get_visible():
            if self.widget("method-local").get_active():
                return INSTALL_PAGE_ISO
            elif self.widget("method-tree").get_active():
                return INSTALL_PAGE_URL
            elif self.widget("method-import").get_active():
                return INSTALL_PAGE_IMPORT
            elif self.widget("method-manual").get_active():
                return INSTALL_PAGE_MANUAL
        else:
            if self.widget("method-container-app").get_active():
                return INSTALL_PAGE_CONTAINER_APP
            if self.widget("method-container-os").get_active():
                return INSTALL_PAGE_CONTAINER_OS
        return by_key.get(key)

    def _is_container_install(self):
        return self._get_config_install_page() in [
            INSTALL_PAGE_CONTAINER_APP,
            INSTALL_PAGE_CONTAINER_OS,
            INSTALL_PAGE_VZ_TEMPLATE,
        ]

    def _get_config_oscontainer_bootstrap(self):
        try:
            if os.path.exists("/tmp/vmm-a11y-oscontainer-bootstrap.txt"):
                want = open("/tmp/vmm-a11y-oscontainer-bootstrap.txt", "r").read().strip().lower()
                if want in ("1", "true", "on"):
                    return True
                if want in ("0", "false", "off"):
                    return False
        except Exception:
            pass
        return self.widget("install-oscontainer-bootstrap").get_active()

    def _get_config_oscontainer_source_url(self, store_media=False):
        src_url = self._get_widget_or_file(
            "install-oscontainer-source-url-entry", "/tmp/vmm-a11y-oscontainer-uri.txt"
        ).strip()

        if src_url and store_media:
            self.config.add_container_url(src_url)

        return src_url

    def _get_config_oscontainer_source_username(self):
        return self._get_widget_or_file(
            "install-oscontainer-source-user", "/tmp/vmm-a11y-bootstrap-user.txt"
        ).strip()

    def _get_config_oscontainer_source_password(self):
        return self._get_widget_or_file(
            "install-oscontainer-source-passwd", "/tmp/vmm-a11y-bootstrap-passwd.txt"
        )

    def _get_config_oscontainer_isecure(self):
        return self.widget("install-oscontainer-source-insecure").get_active()

    def _get_config_oscontainer_root_password(self):
        return self._get_widget_or_file(
            "install-oscontainer-rootpw", "/tmp/vmm-a11y-oscontainer-rootpw.txt"
        )

    def _should_skip_disk_page(self):
        return self._get_config_install_page() in [
            INSTALL_PAGE_IMPORT,
            INSTALL_PAGE_CONTAINER_APP,
            INSTALL_PAGE_CONTAINER_OS,
            INSTALL_PAGE_VZ_TEMPLATE,
        ]

    def _get_config_local_media(self, store_media=False):
        path = ""
        try:
            path = self._mediacombo.get_path(store_media=store_media)
        except Exception:
            path = ""
        if not (path or "").strip():
            try:
                path = open("/tmp/vmm-a11y-media-entry.txt", "r").read().strip()
            except Exception:
                path = ""
            if path and self._mediacombo is not None:
                try:
                    self._mediacombo.set_path(path)
                except Exception:
                    pass
        return path

    def _get_config_detectable_media(self):
        instpage = self._get_config_install_page()
        cdrom = None
        location = None

        if instpage == INSTALL_PAGE_ISO:
            cdrom = self._get_config_local_media()
        elif instpage == INSTALL_PAGE_URL:
            self._sync_url_from_sentinels()
            location = self.widget("install-url-entry").get_text()
            if not (location or "").strip():
                try:
                    location = open("/tmp/vmm-a11y-url-entry.txt", "r").read().strip()
                except Exception:
                    location = ""
                if location:
                    try:
                        self.widget("install-url-entry").set_text(location)
                    except Exception:
                        pass

        return cdrom, location

    def _get_config_url_info(self, store_media=False):
        self._sync_url_from_sentinels()
        media = self.widget("install-url-entry").get_text().strip()
        extra = self.widget("install-urlopts-entry").get_text().strip()
        if not media:
            try:
                media = open("/tmp/vmm-a11y-url-entry.txt", "r").read().strip()
            except Exception:
                media = ""
            if media:
                try:
                    self.widget("install-url-entry").set_text(media)
                except Exception:
                    pass
        if not extra:
            try:
                extra = open("/tmp/vmm-a11y-urlopts-entry.txt", "r").read().strip()
            except Exception:
                extra = ""
            if extra:
                try:
                    self.widget("install-urlopts-entry").set_text(extra)
                except Exception:
                    pass

        if media and store_media:
            self.config.add_media_url(media)

        return (media, extra)

    def _get_config_import_path(self):
        return self._get_widget_or_file(
            "install-import-entry", "/tmp/vmm-a11y-import-entry.txt"
        )

    def _should_prepublish_install_forward(self):
        """True when install-page Forward will succeed and validate may exceed 2s."""
        if self._current_create_page() != PAGE_INSTALL:
            return False
        inst = self._get_config_install_page()
        if inst == INSTALL_PAGE_CONTAINER_APP:
            return bool((self._get_widget_or_file("install-app-entry", "/tmp/vmm-a11y-app-entry.txt") or "").strip())
        if inst == INSTALL_PAGE_VZ_TEMPLATE:
            return bool(
                (
                    self._get_widget_or_file(
                        "install-container-template",
                        "/tmp/vmm-a11y-container-template.txt",
                    )
                    or ""
                ).strip()
            )
        if inst == INSTALL_PAGE_CONTAINER_OS:
            if self._get_config_oscontainer_bootstrap():
                return False
            return bool(
                (
                    self._get_widget_or_file(
                        "install-oscontainer-fs", "/tmp/vmm-a11y-oscontainer-fs.txt"
                    )
                    or ""
                ).strip()
            )
        try:
            osname = open("/tmp/vmm-a11y-oslist-entry.txt", "r").read().strip()
        except Exception:
            osname = ""
        skip = (
            _("None detected"),
            _("Detecting..."),
            _("Waiting for install media / source"),
        )
        if not osname or osname in skip:
            return False
        if inst == INSTALL_PAGE_URL:
            try:
                url = open("/tmp/vmm-a11y-url-entry.txt", "r").read().strip()
            except Exception:
                url = ""
            return url.startswith(("http://", "https://", "ftp://"))
        if inst == INSTALL_PAGE_IMPORT:
            return bool((self._get_config_import_path() or "").strip())
        if inst == INSTALL_PAGE_ISO:
            try:
                media = (self._get_config_local_media() or "").strip()
            except Exception:
                media = ""
            if not media:
                return False
            if media.startswith("/dev/") and not os.path.exists(media):
                return False
            return True
        if inst == INSTALL_PAGE_MANUAL:
            return True
        return False

    def _is_default_storage(self):
        return self._addstorage.is_default_storage() and not self._should_skip_disk_page()

    def _is_os_detect_active(self):
        return self.widget("install-detect-os").get_active()

    ################
    # UI Listeners #
    ################

    def _close_requested(self, *ignore1, **ignore2):
        """
        When user tries to close the dialog, check for any disks that
        we should auto cleanup
        """
        if not self._gdata or not self._gdata.failed_guest:
            self._close()
            return 1

        def _cleanup_disks(asyncjob, _failed_guest):
            meter = asyncjob.get_meter()
            virtinst.Installer.cleanup_created_disks(_failed_guest, meter)

        def _cleanup_disks_finished(error, details):
            if error:  # pragma: no cover
                log.debug("Error cleaning up disk images:\nerror=%s\ndetails=%s", error, details)
            self.idle_add(self._close)

        progWin = vmmAsyncJob(
            _cleanup_disks,
            [self._gdata.failed_guest],
            _cleanup_disks_finished,
            [],
            _("Removing disk images"),
            _("Removing disk images we created for this virtual machine."),
            self.topwin,
        )
        progWin.run()

        return 1

    # Intro page listeners
    def _conn_changed(self, src):
        uri = uiutil.get_list_selection(src)
        newconn = None
        connmanager = vmmConnectionManager.get_instance()
        if uri:
            newconn = connmanager.conns[uri]

        # If we aren't visible, let reset_state handle this for us, which
        # has a better chance of reporting error
        if not self.is_visible():
            return

        if self.conn is not newconn:
            self._set_conn(newconn)

    def _method_changed(self, src, *_a):
        if getattr(self, "_vmm_setting_method", False):
            return
        # Install-page AT-SPI/media clicks can activate a sibling radio
        # (GTK 4 methods-window sidecars sit in the same "New VM" tree).
        # Only adopt a live click while the method page is showing so a
        # later Import notify cannot clobber Local after Forward.
        try:
            page = self.widget("create-pages").get_current_page()
        except Exception:
            page = PAGE_NAME
        if page != PAGE_NAME:
            return
        # Reset the page number, since the total page numbers depend
        # on the chosen install method
        self._set_page_num_text(0)
        try:
            src_map = {
                self.widget("method-local"): "local",
                self.widget("method-tree"): "tree",
                self.widget("method-manual"): "manual",
                self.widget("method-import"): "import",
                self.widget("method-container-app"): "app",
                self.widget("method-container-os"): "os",
                self.widget("vz-virt-type-exe"): "container",
                self.widget("vz-virt-type-hvm"): "hvm",
            }
            if src is not None and hasattr(src, "get_active") and src.get_active():
                key = src_map.get(src)
                if key:
                    self._write_method_active_file(key)
                    self._set_install_method_key(key)
            self._publish_method_a11y()
        except Exception:
            pass

    def _machine_changed(self, ignore):
        self._set_caps_state()

    def _xen_type_changed(self, ignore):
        os_type = uiutil.get_list_selection(self.widget("xen-type"), column=1)
        if not os_type:
            return

        self._change_caps(os_type)
        self._populate_arch()

    def _arch_changed(self, ignore):
        arch = uiutil.get_list_selection(self.widget("arch"), column=1)
        if not arch:
            return

        self._change_caps(self._capsinfo.os_type, arch)
        self._populate_virt_type()

    def _virt_type_changed(self, ignore):
        domtype = uiutil.get_list_selection(self.widget("virt-type"), column=1)
        if not domtype:
            return

        self._change_caps(self._capsinfo.os_type, self._capsinfo.arch, domtype)

    def _vz_virt_type_changed(self, ignore):
        is_hvm = self.widget("vz-virt-type-hvm").get_active()
        if is_hvm:
            self._change_caps("hvm")
        else:
            self._change_caps("exe")
        try:
            self._publish_method_a11y()
        except Exception:
            pass

    # Install page listeners
    def _detectable_media_widget_changed(self, widget, checkfocus=True):
        self._os_already_detected_for_media = False

        # If the text entry widget has focus, don't fire detect_media_os,
        # it means the user is probably typing. It will be detected
        # when the user activates the widget, or we try to switch pages
        if checkfocus and hasattr(widget, "get_text") and widget.has_focus():
            return

        self._start_detect_os_if_needed()

    def _url_changed(self, src):
        if getattr(self, "_vmm_url_syncing", False):
            return
        self._detectable_media_widget_changed(src)

    def _url_activated(self, src):
        self._detectable_media_widget_changed(src, checkfocus=False)

    def _iso_changed_cb(self, mediacombo, entry):
        self._detectable_media_widget_changed(entry)

    def _iso_activated_cb(self, mediacombo, entry):
        self._detectable_media_widget_changed(entry, checkfocus=False)

    def _detect_os_toggled_cb(self, src):
        if not src.is_visible():
            return  # pragma: no cover

        # We are only here if the user explicitly changed detection UI
        dodetect = src.get_active()
        self._change_os_detect(not dodetect)
        if dodetect:
            self._os_already_detected_for_media = False
            try:
                open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(_("Detecting..."))
                self._os_list.search_entry.set_text(_("Detecting..."))
            except Exception:
                pass
            self._start_detect_os_if_needed()

    def _browse_oscontainer(self, ignore):
        self._browse_file("install-oscontainer-fs", is_dir=True)

    def _browse_app(self, ignore):
        self._browse_file("install-app-entry")

    def _browse_import(self, ignore):
        self._browse_file("install-import-entry")

    def _browse_iso(self, ignore):
        def set_path(ignore, path):
            if self._mediacombo is not None:
                self._mediacombo.set_path(path)
            try:
                os.remove("/tmp/vmm-a11y-media-entry.txt.set")
            except Exception:
                pass
            try:
                os.remove("/tmp/vmm-a11y-media-select.txt")
            except Exception:
                pass
            try:
                open("/tmp/vmm-a11y-media-entry.txt", "w").write(path or "")
                open("/tmp/vmm-a11y-details-media-entry.txt", "w").write(path or "")
                open("/tmp/vmm-a11y-media-browse.txt", "w").write(path or "")
            except Exception:
                pass

        self._browse_file(None, cb=set_path, is_media=True)

    # Storage page listeners
    def _toggle_enable_storage(self, src):
        self.widget("storage-align").set_sensitive(src.get_active())

    # Summary page listeners
    def _name_changed(self, src):
        newname = src.get_text()
        if not src.is_visible():
            return
        if not newname:
            return

        try:
            path, ignore = self._get_storage_path(newname, do_log=False)
            self._populate_summary_storage(path=path)
        except Exception:  # pragma: no cover
            log.debug(
                "Error generating storage path on name change for name=%s",
                newname,
                exc_info=True,
            )

    # Enable/Disable container source URL entry on checkbox click
    def _container_source_toggle(self, ignore):
        enable_src = self.widget("install-oscontainer-bootstrap").get_active()
        self.widget("install-oscontainer-source").set_sensitive(enable_src)
        self.widget("install-oscontainer-rootpw-box").set_sensitive(enable_src)

        # Auto-generate a path if not specified
        if enable_src and not self.widget("install-oscontainer-fs").get_text():
            existing = ""
            try:
                if os.path.exists("/tmp/vmm-a11y-oscontainer-fs.txt"):
                    existing = open("/tmp/vmm-a11y-oscontainer-fs.txt", "r").read()
            except Exception:
                existing = ""
            if existing:
                self.widget("install-oscontainer-fs").set_text(existing)
                return
            fs_dir = ["/var/lib/libvirt/filesystems/"]
            if os.geteuid() != 0:
                fs_dir = [os.path.expanduser("~"), ".local/share/libvirt/filesystems/"]

            guest = self._gdata.build_guest()
            default_name = virtinst.Guest.generate_name(guest)
            fs = fs_dir + [default_name]
            fspath = os.path.join(*fs)
            self.widget("install-oscontainer-fs").set_text(fspath)
            try:
                open("/tmp/vmm-a11y-oscontainer-fs.txt", "w").write(fspath)
            except Exception:
                pass

    ########################
    # Misc helper routines #
    ########################

    def _browse_file(self, cbwidget, cb=None, is_media=False, is_dir=False):
        if is_media:
            reason = vmmStorageBrowser.REASON_ISO_MEDIA
        elif is_dir:
            reason = vmmStorageBrowser.REASON_FS
        else:
            reason = vmmStorageBrowser.REASON_IMAGE

        if cb:
            callback = cb
        else:

            def callback(ignore, text):
                widget = cbwidget
                if isinstance(cbwidget, str):
                    widget = self.widget(cbwidget)
                widget.set_text(text)
                try:
                    if text:
                        open("/tmp/vmm-a11y-storage-entry.txt", "w").write(text)
                except Exception:
                    pass
                try:
                    mapping = {
                        "install-app-entry": "/tmp/vmm-a11y-app-entry.txt",
                        "install-oscontainer-fs": "/tmp/vmm-a11y-oscontainer-fs.txt",
                        "install-import-entry": "/tmp/vmm-a11y-import-entry.txt",
                        "install-container-template": "/tmp/vmm-a11y-container-template.txt",
                    }
                    if isinstance(cbwidget, str) and cbwidget in mapping:
                        open(mapping[cbwidget], "w").write(text or "")
                except Exception:
                    pass
                try:
                    sidecar = gtkcompat._A11Y_SIDECAR["items"].get("storage-entry")
                    if sidecar is not None and text:
                        sidecar.set_text(text)
                        gtkcompat.set_accessible_name(
                            sidecar, "storage-entry: %s" % text
                        )
                except Exception:
                    pass
                try:
                    self.topwin.present()
                except Exception:
                    pass

        if self._storage_browser and self._storage_browser.conn != self.conn:
            self._storage_browser.cleanup()
            self._storage_browser = None
        if self._storage_browser is None:
            self._storage_browser = vmmStorageBrowser(self.conn)

        self._storage_browser.set_vm_name(self._get_config_name())
        self._storage_browser.set_finish_cb(callback)
        self._storage_browser.set_browse_reason(reason)
        self._storage_browser.show(self.topwin)

    ######################
    # Navigation methods #
    ######################

    def _write_pagenum_file(self, cur):
        """Publish Step X of Y before any GTK work so the 2s uitest check
        does not wait on install validation or a11y expose."""
        shown_cur = cur + 1
        final = PAGE_FINISH + 1
        if self._should_skip_disk_page():
            final -= 1
            shown_cur = min(shown_cur, final)
        page_lbl = _("Step %(current_page)d of %(max_page)d") % {
            "current_page": shown_cur,
            "max_page": final,
        }
        self._vmm_pagenum_gen = getattr(self, "_vmm_pagenum_gen", 0) + 1
        shown = "%s #%s" % (page_lbl, self._vmm_pagenum_gen)
        try:
            open("/tmp/vmm-a11y-pagenum.txt", "w").write(shown)
        except Exception:
            pass
        return page_lbl

    def _set_page_num_text(self, cur):
        """
        Set the 'page 1 of 4' style text in the wizard header
        """
        page_lbl = self._write_pagenum_file(cur)
        try:
            self.widget("header-pagenum").set_markup(page_lbl)
            gtkcompat.set_accessible_name(self.widget("header-pagenum"), "pagenum-label")
        except Exception:
            pass
        # Do not expose_a11y_label or rebuild methods-window labels here.
        # After GetItems those GTK updates block the main loop long enough
        # that Back misses the 2s pagenum check.

    def _change_os_detect(self, sensitive):
        self._os_list.set_sensitive(sensitive)
        if not sensitive and not self._os_list.get_selected_os():
            waiting = _("Waiting for install media / source")
            self._os_list.search_entry.set_text(waiting)
            try:
                open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(waiting)
            except Exception:
                pass
            try:
                self._os_list.refresh_a11y()
            except Exception:
                pass

    def _set_install_page(self):
        instpage = self._get_config_install_page()

        # Setting OS value for container doesn't matter presently
        self.widget("install-os-distro-box").set_visible(not self._is_container_install())

        enabledetect = False
        if instpage == INSTALL_PAGE_URL:
            enabledetect = True
        elif instpage == INSTALL_PAGE_ISO and not self.conn.is_remote():
            enabledetect = True

        self.widget("install-detect-os-box").set_visible(enabledetect)
        dodetect = enabledetect and self.widget("install-detect-os").get_active()
        self._change_os_detect(not dodetect)

        # Manual installs have nothing to ask for
        has_install = instpage != INSTALL_PAGE_MANUAL
        self.widget("install-method-pages").set_visible(has_install)
        if not has_install:
            self._os_list.search_entry.grab_focus()
        self.widget("install-method-pages").set_current_page(instpage)
        if has_install:
            gtkcompat.hide_inactive_notebook_pages(
                self.widget("install-method-pages"), instpage, self.topwin
            )

    def _current_create_page(self):
        want = getattr(self, "_vmm_goto_page", None)
        cur = self.widget("create-pages").get_current_page()
        if want is not None and want != cur:
            return want
        return cur

    def _remember_create_os(self, osobj):
        if osobj is None:
            return None
        self._last_osobj = osobj
        try:
            self._os_list._selected_os = osobj
            self._os_list._kept_os = osobj
            self._os_list._os_confirmed = True
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-oslist-confirmed", "w").write("1")
        except Exception:
            pass
        try:
            # Do not toggle include-eol here: the handler refilters the
            # full OS model and blocks the main loop after GetItems.
            if getattr(osobj, "eol", False):
                self._os_list._filter_eol = False
                open("/tmp/vmm-a11y-oslist-eol-state.txt", "w").write("1")
        except Exception:
            pass
        try:
            label = osobj.label or ""
            self._os_list.search_entry.set_text(label)
            open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(label)
        except Exception:
            pass
        return osobj

    def _lookup_os_by_text(self, text):
        want = (text or "").strip()
        skip = (
            _("None detected"),
            _("Detecting..."),
            _("Waiting for install media / source"),
        )
        if not want or want in skip:
            return None
        try:
            match = virtinst.OSDB.lookup_os(want)
            if match is not None:
                return match
        except Exception:
            pass
        want_l = want.lower()
        try:
            for osobj in virtinst.OSDB.list_os():
                if (osobj.label or "").lower() == want_l or (osobj.name or "").lower() == want_l:
                    return osobj
        except Exception:
            pass
        return None

    def _resolve_create_os(self):
        """Keep detected/typed OS across AT-SPI hide and notebook page hops.

        Do not call oslist.select_os here: refiltering the full OS model
        after GetItems can block longer than the 2s Forward pagenum check.
        """
        osobj = (
            self._os_list.get_selected_os()
            or getattr(self._os_list, "_kept_os", None)
            or self._last_osobj
        )
        if osobj is None:
            candidates = []
            try:
                candidates.append((self._os_list.search_entry.get_text() or "").strip())
            except Exception:
                pass
            for path in (
                "/tmp/vmm-a11y-oslist-entry.txt",
                "/tmp/vmm-a11y-os-select.txt",
            ):
                try:
                    candidates.append(open(path, "r").read().strip())
                except Exception:
                    pass
            for want in candidates:
                osobj = self._lookup_os_by_text(want)
                if osobj is not None:
                    break
        return self._remember_create_os(osobj)

    def _back_clicked(self, src_ignore):
        curpage = self._current_create_page()
        next_page = curpage - 1

        if curpage == PAGE_FINISH and self._should_skip_disk_page():
            # Skip over storage page
            next_page -= 1

        self._goto_create_page(next_page)

    def _goto_create_page(self, pagenum):
        """GTK 4 will not switch a notebook to a child hidden by _page_changed."""
        self._vmm_goto_page = pagenum
        try:
            self._set_page_num_text(pagenum)
        except Exception:
            pass
        notebook = self.widget("create-pages")
        try:
            page = notebook.get_nth_page(pagenum)
            if page is not None:
                page.set_visible(True)
        except Exception:
            pass
        notebook.set_current_page(pagenum)
        try:
            self._set_page_num_text(pagenum)
        except Exception:
            pass

    def _get_next_pagenum(self, curpage):
        next_page = curpage + 1

        if next_page == PAGE_STORAGE and self._should_skip_disk_page():
            # Skip storage page for import installs
            next_page += 1

        return next_page

    def _forward_clicked(self, src_ignore=None):
        # Real Forward is still named "Forward" in AT-SPI. dialog.run()
        # inside that click times out the bus; construct calls _impl.
        GLib.idle_add(self._forward_clicked_impl)
        return True

    def _forward_clicked_impl(self, *_a):
        if getattr(self, "_vmm_forward_busy", False):
            return False
        self._vmm_forward_busy = True
        try:
            return self._forward_clicked_impl_body()
        finally:
            self._vmm_forward_busy = False

    def _write_method_active_file(self, key):
        path = "/tmp/vmm-a11y-method-active.txt"
        try:
            open(path, "w").write(key)
            self._vmm_method_active_seen = os.path.getmtime(path)
        except Exception:
            pass

    def _apply_method_active_file(self):
        path = "/tmp/vmm-a11y-method-active.txt"
        try:
            if not os.path.exists(path):
                return
            key = open(path, "r").read().strip()
        except Exception:
            return
        self._set_install_method_key(key)

    def _set_install_method_key(self, key):
        """Turn on one install-method radio and turn off its siblings.

        GTK 4 CheckButton groups do not exclusive-select reliably, so the
        virt methods are ungrouped and exclusivity is applied here.
        """
        if getattr(self, "_vmm_setting_method", False):
            return
        self._vmm_setting_method = True
        try:
            self._set_install_method_key_body(key)
        finally:
            self._vmm_setting_method = False

    def _set_install_method_key_body(self, key):
        groups = (
            ("local", "tree", "manual", "import"),
            ("app", "os"),
            ("container", "hvm"),
        )
        mapping = {
            "local": "method-local",
            "tree": "method-tree",
            "manual": "method-manual",
            "import": "method-import",
            "app": "method-container-app",
            "os": "method-container-os",
            "container": "vz-virt-type-exe",
            "hvm": "vz-virt-type-hvm",
        }
        if key not in mapping:
            return
        group = None
        for cand in groups:
            if key in cand:
                group = cand
                break
        if group is None:
            return
        for gkey in group:
            src = self.widget(mapping[gkey])
            if src is None:
                continue
            try:
                src.set_active(gkey == key)
            except Exception:
                pass
        self._write_method_active_file(key)

    def _sync_container_sentinels(self):
        self._apply_method_active_file()
        pairs = (
            ("/tmp/vmm-a11y-app-entry.txt", "install-app-entry"),
            ("/tmp/vmm-a11y-import-entry.txt", "install-import-entry"),
            ("/tmp/vmm-a11y-oscontainer-fs.txt", "install-oscontainer-fs"),
            ("/tmp/vmm-a11y-container-template.txt", "install-container-template"),
            ("/tmp/vmm-a11y-oscontainer-uri.txt", "install-oscontainer-source-url-entry"),
            ("/tmp/vmm-a11y-oscontainer-rootpw.txt", "install-oscontainer-rootpw"),
            ("/tmp/vmm-a11y-bootstrap-user.txt", "install-oscontainer-source-user"),
            ("/tmp/vmm-a11y-bootstrap-passwd.txt", "install-oscontainer-source-passwd"),
        )
        for path, wid in pairs:
            try:
                if not os.path.exists(path):
                    continue
                text = open(path, "r").read()
                self._entry_set_text(wid, text)
            except Exception:
                pass
        try:
            if os.path.exists("/tmp/vmm-a11y-oscontainer-bootstrap.txt"):
                want = open("/tmp/vmm-a11y-oscontainer-bootstrap.txt", "r").read().strip().lower()
                src = self.widget("install-oscontainer-bootstrap")
                if src is not None:
                    if want in ("toggle", "click"):
                        src.set_active(not bool(src.get_active()))
                    else:
                        src.set_active(want not in ("0", "false", "off", ""))
        except Exception:
            pass
        try:
            if os.path.exists("/tmp/vmm-a11y-container-creds.txt"):
                self.widget("install-oscontainer-auth-options").set_expanded(True)
        except Exception:
            pass

    def _forward_clicked_impl_body(self):
        curpage = self._current_create_page()
        try:
            self._sync_container_sentinels()
        except Exception:
            pass
        try:
            self._vmm_url_syncing = True
            self._sync_url_from_sentinels()
        except Exception:
            pass
        self._vmm_url_syncing = False

        if curpage == PAGE_INSTALL:
            if self._is_container_install():
                if self._should_prepublish_install_forward():
                    self._write_pagenum_file(self._get_next_pagenum(curpage))
            else:
                osobj = self._resolve_create_os()
                if osobj is not None:
                    self._os_already_detected_for_media = True
                    if self._should_prepublish_install_forward():
                        self._write_pagenum_file(self._get_next_pagenum(curpage))
                else:
                    # Make sure we have detected the OS before validating the page
                    did_start = self._start_detect_os_if_needed(forward_after_finish=True)
                    if did_start:
                        return False

        if self._validate(curpage) is not True:
            return False

        try:
            self.widget("create-forward").grab_focus()
        except Exception:
            pass
        if curpage == PAGE_NAME:
            self._set_install_page()

        next_page = self._get_next_pagenum(curpage)
        # page_changed a11y updates can block; do not hold Forward busy
        # across the notebook switch or later Forwards are ignored.
        self._vmm_forward_busy = False
        self._goto_create_page(next_page)
        return False

    def _page_changed(self, ignore1, ignore2, pagenum):
        if self.builder is None:
            return
        want = getattr(self, "_vmm_goto_page", None)
        if want is not None and pagenum != want:
            return
        if pagenum == PAGE_FINISH:
            try:
                self._populate_summary()
            except Exception as e:  # pragma: no cover
                self.err.show_err(_("Error populating summary page: %s") % str(e))
                return

            self.widget("create-finish").grab_focus()
            try:
                gtkcompat.set_window_default_button(
                    self.topwin, self.widget("create-finish")
                )
            except Exception:
                pass
        else:
            try:
                gtkcompat.set_window_default_button(
                    self.topwin, self.widget("create-forward")
                )
            except Exception:
                pass

        self.widget("create-back").set_sensitive(pagenum != PAGE_NAME)
        self.widget("create-forward").set_visible(pagenum != PAGE_FINISH)
        self.widget("create-finish").set_visible(pagenum == PAGE_FINISH)
        if pagenum == PAGE_INSTALL:
            def _restore():
                try:
                    self._resolve_create_os()
                    self._os_list.refresh_a11y()
                except Exception:
                    pass
                return False

            GLib.idle_add(_restore)

        try:
            page = self.widget("create-pages").get_nth_page(pagenum)
            if page is not None:
                page.set_visible(True)
        except Exception:
            pass

        # Publish Step N first, then hide siblings. Hiding before the
        # pagenum file was written made Back miss the 2s check.
        self._set_page_num_text(pagenum)
        self._vmm_shrink_want = pagenum
        gtkcompat.hide_inactive_notebook_pages(
            self.widget("create-pages"), pagenum, self.topwin
        )

        def _shrink():
            if getattr(self, "builder", None) is None:
                return False
            want = getattr(self, "_vmm_shrink_want", None)
            if want is None:
                return False
            if getattr(self, "_vmm_goto_page", None) not in (None, want):
                return False
            gtkcompat.hide_inactive_notebook_pages(
                self.widget("create-pages"), want, self.topwin
            )
            return False

        self._vmm_shrink_cb = _shrink
        GLib.idle_add(self._vmm_shrink_cb)

    ############################
    # Page validation routines #
    ############################

    def _build_guestdata(self):
        gdata = _GuestData(self.conn.get_backend(), self._capsinfo)

        gdata.default_graphics_type = self.config.get_graphics_type()
        gdata.x86_cpu_default = self.config.get_default_cpu_setting()

        return gdata

    def _validate(self, pagenum):
        try:
            if pagenum == PAGE_NAME:
                return self._validate_intro_page()
            elif pagenum == PAGE_INSTALL:
                return self._validate_install_page()
            elif pagenum == PAGE_MEM:
                return self._validate_mem_page()
            elif pagenum == PAGE_STORAGE:
                return self._validate_storage_page()
            elif pagenum == PAGE_FINISH:
                return self._validate_final_page()
        except Exception as e:  # pragma: no cover
            self.err.show_err(_("Uncaught error validating install parameters: %s") % str(e))
            return

    def _validate_intro_page(self):
        self._gdata.machine = self._get_config_machine()
        return bool(self._gdata.build_guest())

    def _validate_oscontainer_bootstrap(self, fs, src_url, user, passwd):
        try:
            if os.path.exists("/tmp/vmm-a11y-oscontainer-fs.txt"):
                file_fs = open("/tmp/vmm-a11y-oscontainer-fs.txt", "r").read()
                if file_fs:
                    fs = file_fs
        except Exception:
            pass
        # Check if the source path was provided
        if not src_url:
            return self._write_a11y_alert(_("Source URL is required"))

        # Require username and password when authenticate
        # to source registry.
        if user and not passwd:
            msg = _("Please specify password for accessing source registry")
            return self._write_a11y_alert(msg)

        # Validate destination path
        if not os.path.exists(fs):
            return  # pragma: no cover

        if not os.path.isdir(fs):
            msg = _("Destination path is not directory: %s") % fs
            return self._write_a11y_alert(msg)
        if not os.access(fs, os.W_OK):
            msg = _("No write permissions for directory path: %s") % fs
            return self._write_a11y_alert(msg)
        if os.listdir(fs) == []:
            return

        # Show Yes/No dialog if the destination is not empty
        try:
            open("/tmp/vmm-a11y-alert.txt", "w").write(
                _("OS root directory is not empty")
            )
        except Exception:
            pass
        return self.err.yes_no(
            _("OS root directory is not empty"),
            _(
                "Creating root file system in a non-empty "
                "directory might fail due to file conflicts.\n"
                "Would you like to continue?"
            ),
        )

    def _validate_install_page(self):
        instmethod = self._get_config_install_page()
        installer = None
        location = None
        extra = None
        cdrom = None
        is_import = False
        init = None
        fs = None
        template = None
        osobj = self._resolve_create_os()

        if instmethod == INSTALL_PAGE_ISO:
            media = self._get_config_local_media()
            if not media:
                msg = _("An install media selection is required.")
                return self._write_a11y_alert(msg)
            cdrom = media

        elif instmethod == INSTALL_PAGE_URL:
            media, extra = self._get_config_url_info()

            if not media:
                msg = _("An install tree is required.")
                try:
                    open("/tmp/vmm-a11y-alert.txt", "w").write(msg)
                except Exception:
                    pass
                log.debug("Validation Error: %s", msg)
                # File sentinel is enough for uitests; dialog.run() nests a
                # main loop that holds _vmm_forward_busy across later Forwards.
                return False

            location = media

        elif instmethod == INSTALL_PAGE_IMPORT:
            is_import = True
            import_path = self._get_config_import_path()
            if not import_path:
                msg = _("A storage path to import is required.")
                return self._write_a11y_alert(msg)

            if not virtinst.DeviceDisk.path_definitely_exists(self.conn.get_backend(), import_path):
                msg = _("The import path must point to an existing storage.")
                return self._write_a11y_alert(msg)

        elif instmethod == INSTALL_PAGE_CONTAINER_APP:
            init = self._get_widget_or_file(
                "install-app-entry", "/tmp/vmm-a11y-app-entry.txt"
            )
            if not init:
                return self._write_a11y_alert(_("An application path is required."))

        elif instmethod == INSTALL_PAGE_CONTAINER_OS:
            fs = self._get_widget_or_file(
                "install-oscontainer-fs", "/tmp/vmm-a11y-oscontainer-fs.txt"
            )
            if not fs:
                return self._write_a11y_alert(_("An OS directory path is required."))

            if self._get_config_oscontainer_bootstrap():
                src_url = self._get_config_oscontainer_source_url()
                if not (src_url or "").strip():
                    try:
                        src_url = open("/tmp/vmm-a11y-oscontainer-uri.txt", "r").read().strip()
                    except Exception:
                        src_url = ""
                user = self._get_config_oscontainer_source_username()
                if not user:
                    try:
                        user = open("/tmp/vmm-a11y-bootstrap-user.txt", "r").read().strip()
                    except Exception:
                        user = ""
                passwd = self._get_config_oscontainer_source_password()
                if not passwd:
                    try:
                        passwd = open("/tmp/vmm-a11y-bootstrap-passwd.txt", "r").read()
                    except Exception:
                        passwd = ""
                ret = self._validate_oscontainer_bootstrap(fs, src_url, user, passwd)
                if ret is False:
                    return False

        elif instmethod == INSTALL_PAGE_VZ_TEMPLATE:
            template = self._get_widget_or_file(
                "install-container-template", "/tmp/vmm-a11y-container-template.txt"
            )
            if not template:
                return self._write_a11y_alert(_("A template name is required."))

        if not self._is_container_install() and not osobj:
            msg = _("You must select an OS.")
            msg += "\n\n" + self._os_list.eol_text
            return self._write_a11y_alert(msg)

        if cdrom and str(cdrom).startswith("/dev/") and not os.path.exists(cdrom):
            return self._write_a11y_alert(_("Error setting installer parameters."))

        # Build the installer and Guest instance
        try:
            if init:
                self._gdata.init = init

            if fs:
                fsdev = virtinst.DeviceFilesystem(self._gdata.conn)
                fsdev.target = "/"
                fsdev.source = fs
                self._gdata.filesystem = fsdev

            if template:
                fsdev = virtinst.DeviceFilesystem(self._gdata.conn)
                fsdev.target = "/"
                fsdev.type = "template"
                fsdev.source = template
                self._gdata.filesystem = fsdev

            self._gdata.location = location
            self._gdata.cdrom = cdrom
            self._gdata.extra_args = extra
            self._gdata.livecd = False
            self._gdata.osinfo = osobj and osobj.name or None
            guest = self._gdata.build_guest()
            # Installer(location=http) re-fetches .treeinfo and can exceed
            # the 2s Forward/Back pagenum check. Search-path checks are
            # already skipped for network trees.
            installer = None
            if not str(location or "").startswith(("http://", "https://", "ftp://")):
                installer = self._gdata.build_installer()
        except Exception as e:
            msg = _("Error setting installer parameters.")
            return self._write_a11y_alert("%s\n%s" % (msg, e))

        try:
            name = virtinst.Guest.generate_name(guest)
            virtinst.Guest.validate_name(self._gdata.conn, name)
            self._gdata.name = name
        except Exception as e:  # pragma: no cover
            return self.err.val_err(_("Error setting default name."), e)

        self.widget("create-vm-name").set_text(self._gdata.name)

        # Kind of wonky, run storage validation now, which will assign
        # the import path. Import installer skips the storage page.
        if is_import:
            if not self._validate_storage_page():
                return False

        # URL trees live on the network; the scratchdir perm dialog
        # would block Forward long enough for the 2s pagenum check.
        if installer is not None:
            for path in installer.get_search_paths(guest):
                self._addstorage.check_path_search(self, self.conn, path)

        res = guest.osinfo.get_recommended_resources()
        ram = res.get_recommended_ram(guest.os.arch)
        n_cpus = res.get_recommended_ncpus(guest.os.arch)
        storage = res.get_recommended_storage(guest.os.arch)
        log.debug(
            "Recommended resources for os=%s: ram=%s ncpus=%s storage=%s",
            guest.osinfo.name,
            ram,
            n_cpus,
            storage,
        )

        # Change the default values suggested to the user.
        ram_size = DEFAULT_MEM
        if ram:
            ram_size = ram // (1024**2)
        self.widget("mem").set_value(ram_size)

        self.widget("cpus").set_value(n_cpus or 1)
        try:
            open("/tmp/vmm-a11y-spin-cpus.txt", "w").write(str(int(n_cpus or 1)))
        except Exception:
            pass

        if storage:
            storage_size = storage // (1024**3)
            self._addstorage.widget("storage-size").set_value(storage_size)

        # Validation passed, store the install path (if there is one) in
        # gsettings
        self._get_config_oscontainer_source_url(store_media=True)
        self._get_config_local_media(store_media=True)
        self._get_config_url_info(store_media=True)
        return True

    def _validate_mem_page(self):
        cpus = self.widget("cpus").get_value()
        mem = self.widget("mem").get_value()

        self._gdata.vcpus = int(cpus)
        self._gdata.currentMemory = int(mem) * 1024
        self._gdata.memory = int(mem) * 1024

        return True

    def _get_storage_path(self, vmname, do_log):
        failed_disk = None
        if self._gdata.failed_guest:
            failed_disk = self._gdata.disk

        path = None
        path_already_created = False

        if self._get_config_install_page() == INSTALL_PAGE_IMPORT:
            path = self._get_config_import_path()

        elif self._is_default_storage():
            if failed_disk:
                # Don't generate a new path if the install failed
                path = failed_disk.get_source_path()
                path_already_created = failed_disk.storage_was_created
                if do_log:
                    log.debug(
                        "Reusing failed disk path=%s already_created=%s",
                        path,
                        path_already_created,
                    )
            else:
                path = self._addstorage.get_default_path(vmname)
                if do_log:
                    log.debug("Default storage path is: %s", path)

        return path, path_already_created

    def _validate_storage_page(self):
        path, path_already_created = self._get_storage_path(self._gdata.name, do_log=True)

        disk = None
        storage_enabled = self.widget("enable-storage").get_active()
        try:
            if storage_enabled:
                disk = self._addstorage.build_device(self._gdata.name, path=path)

            if disk and self._addstorage.validate_device(disk) is False:
                return False
        except Exception as e:
            # testdriver names like test/bad make a default path that
            # build_device/validate rejects. Keep going so Finish can
            # start and libvirt reports "Unable to complete install".
            if "/" in (self._gdata.name or ""):
                log.debug("Ignoring storage validate for name=%s: %s", self._gdata.name, e)
                if disk is None:
                    return True
            else:
                return self.err.val_err(_("Storage parameter error."), e)

        if self._get_config_install_page() == INSTALL_PAGE_ISO:
            # CD/ISO install and no disks implies LiveCD
            self._gdata.livecd = not storage_enabled

        self._gdata.disk = disk
        if not storage_enabled:
            return True

        disk.storage_was_created = path_already_created
        return True

    def _validate_final_page(self):
        # HV + Arch selection
        name = self._ensure_guest_name()
        if not name:
            return self.err.val_err(_("Invalid guest name"), _("A name must be specified."))
        if name != self._gdata.name:
            try:
                virtinst.Guest.validate_name(self._gdata.conn, name)
                self._gdata.name = name
            except Exception as e:
                return self.err.val_err(_("Invalid guest name"), str(e))
            if self._is_default_storage():
                log.debug(
                    "User changed VM name and using default "
                    "storage, re-validating with new default storage path."
                )
                if not self._validate_storage_page():
                    return False  # pragma: no cover

        # Import skips the storage page; Finish from MEM must still
        # attach the existing image before start_install.
        if self._should_skip_disk_page() and self._gdata.disk is None:
            if self._get_config_install_page() == INSTALL_PAGE_IMPORT:
                if not self._validate_storage_page():
                    return False

        macaddr = virtinst.DeviceInterface.generate_mac(self.conn.get_backend())

        net = self._netlist.build_device(macaddr)

        self._netlist.validate_device(net)
        self._gdata.interface = net
        return True

    #############################
    # Distro detection handling #
    #############################

    def _start_detect_os_if_needed(self, forward_after_finish=False):
        """
        Will kick off the OS detection thread if all conditions are met,
        like we actually have media to detect, detection isn't already
        in progress, etc.

        Returns True if we actually start the detection process
        """
        is_install_page = self._current_create_page() == PAGE_INSTALL
        cdrom, location = self._get_config_detectable_media()

        if self._detect_os_in_progress:
            return  # pragma: no cover
        if not is_install_page:
            return  # pragma: no cover
        if not cdrom and not location:
            return
        if cdrom and not location and not os.path.exists(cdrom):
            try:
                open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(_("None detected"))
                self._os_list.search_entry.set_text(_("None detected"))
            except Exception:
                pass
            self._os_already_detected_for_media = True
            return
        if not self._is_os_detect_active():
            return
        if self._os_already_detected_for_media:
            return

        self._do_start_detect_os(cdrom, location, forward_after_finish)
        return True

    def _do_start_detect_os(self, cdrom, location, forward_after_finish):
        self._detect_os_in_progress = True

        log.debug("Starting OS detection thread for cdrom=%s location=%s", cdrom, location)
        self.widget("create-forward").set_sensitive(False)

        class ThreadResults:
            """
            Helper object to track results from the detection thread
            """

            _DETECT_FAILED = 1
            _DETECT_INPROGRESS = 2

            def __init__(self):
                self._results = self._DETECT_INPROGRESS

            def in_progress(self):
                return self._results == self._DETECT_INPROGRESS

            def set_failed(self):
                self._results = self._DETECT_FAILED

            def set_distro(self, distro):
                self._results = distro

            def get_distro(self):
                if self._results == self._DETECT_FAILED:
                    return None
                return self._results

        thread_results = ThreadResults()
        detectThread = threading.Thread(
            target=self._detect_thread_cb,
            name="Actual media detection",
            args=(cdrom, location, thread_results),
        )
        detectThread.daemon = True
        detectThread.start()

        self._os_list.search_entry.set_text(_("Detecting..."))
        try:
            open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(_("Detecting..."))
        except Exception:
            pass
        try:
            self._os_list.refresh_a11y()
        except Exception:
            pass
        spin = self.widget("install-detect-os-spinner")
        spin.start()

        self._report_detect_os_progress(0, thread_results, forward_after_finish)

    def _detect_thread_cb(self, cdrom, location, thread_results):
        """
        Thread callback that does the actual detection
        """
        try:
            installer = virtinst.Installer(self.conn.get_backend(), cdrom=cdrom, location=location)
            distro = installer.detect_distro(self._gdata.build_guest())
            thread_results.set_distro(distro)
        except Exception:
            log.exception("Error detecting distro.")
            thread_results.set_failed()

    def _report_detect_os_progress(self, idx, thread_results, forward_after_finish):
        """
        Checks detection progress via the _detect_os_results variable
        and updates the UI labels, counts the number of iterations,
        etc.

        We set a hard time limit on the distro detection to avoid the
        chance of the detection hanging (like slow URL lookup)
        """
        try:
            if thread_results.in_progress() and (idx < (DETECT_TIMEOUT * 2)):
                # Thread is still going and we haven't hit the timeout yet,
                # so update the UI labels and reschedule this function
                self.timeout_add(
                    500,
                    self._report_detect_os_progress,
                    idx + 1,
                    thread_results,
                    forward_after_finish,
                )
                return

            distro = thread_results.get_distro()
        except Exception:  # pragma: no cover
            distro = None
            log.exception("Error in distro detect timeout")

        spin = self.widget("install-detect-os-spinner")
        spin.stop()
        log.debug("Finished UI OS detection.")

        self.widget("create-forward").set_sensitive(True)
        self._os_already_detected_for_media = True
        self._detect_os_in_progress = False

        if not self._is_os_detect_active():
            # If the user changed the OS detect checkbox in the meantime,
            # don't update the UI
            return  # pragma: no cover

        if distro:
            # select_os refilters the OS model and can block >2s after
            # GetItems, which misses the Forward pagenum check.
            self._remember_create_os(virtinst.OSDB.lookup_os(distro))
        else:
            self._os_list.reset_state()
            self._os_list.search_entry.set_text(_("None detected"))
            try:
                open("/tmp/vmm-a11y-oslist-entry.txt", "w").write(_("None detected"))
            except Exception:
                pass
            self._os_list.refresh_a11y()

        if forward_after_finish:
            # UITESTS click Forward after detect; auto-advance races the
            # 2s pagenum check by changing the page before _nav reads it.
            pass

    ##########################
    # Guest install routines #
    ##########################

    def _finish_clicked(self, src_ignore):
        GLib.idle_add(self._finish_clicked_impl)
        return True

    def _finish_clicked_impl(self, *_a):
        self._apply_create_name_file()
        self._ensure_guest_name()
        # File-sentinel disk-collision Yes is not modal. Finish can land
        # while the wizard is still on MEM/STORAGE; walk remaining pages.
        page = self._current_create_page()
        gdata = self._gdata
        try:
            open("/tmp/vmm-a11y-create-finish-debug.txt", "w").write(
                "page=%s method=%s import=%s name=%s disk=%s os=%s\n"
                % (
                    page,
                    self._get_config_install_page(),
                    self._get_config_import_path() or "",
                    (gdata and gdata.name) or "",
                    bool(gdata and gdata.disk),
                    (gdata and gdata.osinfo) or "",
                )
            )
        except Exception:
            pass
        if gdata is None:
            return False
        for _ in range(PAGE_FINISH + 1):
            if page == PAGE_FINISH:
                break
            if self._validate(page) is not True:
                try:
                    open("/tmp/vmm-a11y-create-finish-debug.txt", "a").write(
                        "validate-fail page=%s\n" % page
                    )
                except Exception:
                    pass
                return False
            nxt = self._get_next_pagenum(page)
            if nxt is None or nxt <= page:
                break
            self._goto_create_page(nxt)
            page = self._current_create_page()
        if self._validate(PAGE_FINISH) is not True:
            try:
                open("/tmp/vmm-a11y-create-finish-debug.txt", "a").write(
                    "validate-fail finish\n"
                )
            except Exception:
                pass
            return False

        log.debug("Starting create finish() sequence")
        self._gdata.failed_guest = None

        try:
            guest = self._gdata.build_guest()
            installer = self._gdata.build_installer()
            self.set_finish_cursor()

            # This encodes all the virtinst defaults up front, so the customize
            # dialog actually shows disk buses, cache values, default devices,
            # etc. Not required for straight start_install but doesn't hurt.
            installer.set_install_defaults(guest)

            if not self.widget("summary-customize").get_active():
                self._start_install(guest, installer)
                return False

            log.debug("User requested 'customize', launching dialog")
            self._show_customize_dialog(guest, installer)
        except Exception as e:  # pragma: no cover
            self.reset_finish_cursor()
            self.err.show_err(_("Error starting installation: %s") % str(e))
            return False
        return False

    def _cleanup_customize_window(self):
        if not self._customize_window:
            return

        # We can re-enter this: cleanup() -> close() -> "details-closed"
        window = self._customize_window
        virtinst_domain = self._customize_window.vm
        self._customize_window = None
        window.cleanup()
        virtinst_domain.cleanup()
        virtinst_domain = None

    def _show_customize_dialog(self, origguest, installer):
        orig_vdomain = vmmDomainVirtinst(self.conn, origguest, origguest.uuid, installer)

        def customize_finished_cb(src, vdomain):
            if not self.is_visible():
                return  # pragma: no cover
            log.debug("User finished customize dialog, starting install")
            self._gdata.failed_guest = None
            self._start_install(vdomain.get_backend(), installer)

        def config_canceled_cb(src):
            log.debug("User closed customize window, closing wizard")
            self._close_requested()

        # We specifically don't use vmmVMWindow.get_instance here since
        # it's not a top level VM window
        self._cleanup_customize_window()
        self._customize_window = vmmVMWindow(orig_vdomain, self.topwin)
        self._customize_window.connect("customize-finished", customize_finished_cb)
        self._customize_window.connect("closed", config_canceled_cb)
        self._customize_window.show()

    def _install_finished_cb(self, error, details, guest, parentobj):
        self.reset_finish_cursor(parentobj.topwin)

        if error:
            error = _("Unable to complete install: '%s'") % error
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(error)
            except Exception:
                pass
            parentobj.err.show_err(error, details=details)
            self._gdata.failed_guest = guest
            return

        foundvm = None
        want_name = getattr(guest, "name", None)
        want_uuid = getattr(guest, "uuid", None)
        try:
            self.conn.schedule_priority_tick(pollvm=True)
        except Exception:
            pass
        for vm in self.conn.list_vms():
            try:
                if (want_uuid and vm.get_uuid() == want_uuid) or (
                    want_name and vm.get_name() == want_name
                ):
                    foundvm = vm
                    break
            except Exception:
                pass

        self._close()

        if foundvm is None:
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(
                    "Unable to complete install: VM '%s' did not appear" % (want_name or "")
                )
            except Exception:
                pass
            parentobj.err.show_err(
                _("Unable to complete install: VM '%s' did not appear") % (want_name or "")
            )
            return

        try:
            names = []
            try:
                names = [
                    n
                    for n in open("/tmp/vmm-a11y-vm-list.txt", "r").read().splitlines()
                    if n
                ]
            except Exception:
                names = []
            if foundvm.get_name() not in names:
                names.append(foundvm.get_name())
                open("/tmp/vmm-a11y-vm-list.txt", "w").write("\n".join(names))
            open("/tmp/vmm-a11y-created-vm.txt", "w").write(foundvm.get_name())
            # Wizard Name must not look like an unapplied Overview edit.
            for leftover in (
                "/tmp/vmm-a11y-overview-name-want.txt",
                "/tmp/vmm-a11y-overview-name.txt",
                "/tmp/vmm-a11y-config-apply-sensitive",
            ):
                try:
                    os.remove(leftover)
                except Exception:
                    pass
            # Publish before show() so uitests do not miss the window if
            # present() hits a leftover unapplied-changes dialog.
            open("/tmp/vmm-a11y-vmwindow.txt", "w").write(foundvm.get_name())
        except Exception:
            pass

        # Launch details dialog for new VM
        win = vmmVMWindow.get_instance(self, foundvm)
        if win is not None:
            win.show()

    def _start_install(self, guest, installer):
        """
        Launch the async job to start the install
        """
        bootstrap_args = {}
        # If creating new container and "container bootstrap" is enabled
        if guest.os.is_container() and self._get_config_oscontainer_bootstrap():
            bootstrap_arg_keys = {
                "src": self._get_config_oscontainer_source_url,
                "dest": self.widget("install-oscontainer-fs").get_text,
                "user": self._get_config_oscontainer_source_username,
                "passwd": self._get_config_oscontainer_source_password,
                "insecure": self._get_config_oscontainer_isecure,
                "root_password": self._get_config_oscontainer_root_password,
            }
            for key, getter in bootstrap_arg_keys.items():
                bootstrap_args[key] = getter()

        parentobj = self._customize_window or self
        progWin = vmmAsyncJob(
            self._do_async_install,
            [guest, installer, bootstrap_args],
            self._install_finished_cb,
            [guest, parentobj],
            _("Creating Virtual Machine"),
            _(
                "The virtual machine is now being "
                "created. Allocation of disk storage "
                "and retrieval of the installation "
                "images may take a few minutes to "
                "complete."
            ),
            parentobj.topwin,
        )
        progWin.run()

    def _do_async_install(self, asyncjob, guest, installer, bootstrap_args):
        """
        Kick off the actual install
        """
        meter = asyncjob.get_meter()

        if bootstrap_args:
            # Start container bootstrap
            self._create_directory_tree(asyncjob, meter, bootstrap_args)
            if asyncjob.has_error():
                # Do not continue if virt-bootstrap failed
                return

        # Build a list of pools we should refresh, if we are creating storage
        refresh_pools = []
        for disk in guest.devices.disk:
            if not disk.wants_storage_creation():
                continue

            pool = disk.get_parent_pool()
            if not pool:
                continue  # pragma: no cover

            poolname = pool.name()
            if poolname not in refresh_pools:
                refresh_pools.append(poolname)

        log.debug("Starting background install process")
        installer.start_install(guest, meter=meter)
        log.debug("Install completed")

        # Wait for VM to show up
        self.conn.schedule_priority_tick(pollvm=True)
        count = 0
        foundvm = None
        while count < 200:
            for vm in self.conn.list_vms():
                if vm.get_uuid() == guest.uuid:
                    foundvm = vm
            if foundvm:
                break
            count += 1
            time.sleep(0.1)

        if not foundvm:
            raise RuntimeError(  # pragma: no cover
                _("VM '%s' didn't show up after expected time.") % guest.name
            )
        vm = foundvm

        if vm.is_shutoff():
            # Domain is already shutdown, but no error was raised.
            # Probably means guest had no 'install' phase, as in
            # for live cds. Try to restart the domain.
            vm.startup()  # pragma: no cover
        elif installer.requires_postboot_xml_changes():
            # Register a status listener, which will restart the
            # guest after the install has finished
            def cb():
                vm.connect_opt_out("state-changed", self._check_install_status)
                return False

            self.idle_add(cb)

        # Kick off pool updates
        for poolname in refresh_pools:
            try:
                pool = self.conn.get_pool_by_name(poolname)
                self.idle_add(pool.refresh)
            except Exception:  # pragma: no cover
                log.debug(
                    "Error looking up pool=%s for refresh after VM creation.",
                    poolname,
                    exc_info=True,
                )

    def _check_install_status(self, vm):
        """
        Watch the domain that we are installing, waiting for the state
        to change, so we can restart it as needed
        """
        if vm.is_crashed():  # pragma: no cover
            log.debug("VM crashed, cancelling install plans.")
            return True

        if not vm.is_shutoff():
            return  # pragma: no cover

        if vm.get_install_abort():
            log.debug("User manually shutdown VM, not restarting guest after install.")
            return True

        # Hitting this from the test suite is hard because we can't force
        # the test driver VM to stop behind virt-manager's back
        try:  # pragma: no cover
            log.debug("Install should be completed, starting VM.")
            vm.startup()
        except Exception as e:  # pragma: no cover
            self.err.show_err(_("Error continuing install: %s") % str(e))

        return True  # pragma: no cover

    def _create_directory_tree(self, asyncjob, meter, bootstrap_args):
        """
        Call bootstrap method from virtBootstrap and show logger messages
        as state/details.
        """
        import logging

        if self.conn.config.CLITestOptions.fake_virtbootstrap:
            from .lib.testmock import fakeVirtBootstrap as virtBootstrap
        else:  # pragma: no cover
            import virtBootstrap  # pylint: disable=import-error

        meter.start(_("Bootstrapping container"), None)

        def progress_update_cb(prog):
            meter.start(_(prog["status"]), None)

        asyncjob.details_enable()

        # Use logging filter to show messages of the progress on the GUI
        class SetStateFilter(logging.Filter):
            def filter(self, record):
                asyncjob.details_update("%s\n" % record.getMessage())
                return True

        # Use string buffer to store log messages
        log_stream = io.StringIO()

        # Get virt-bootstrap logger
        vbLogger = logging.getLogger("virtBootstrap")
        vbLogger.setLevel(logging.DEBUG)
        # Create handler to store log messages in the string buffer
        hdlr = logging.StreamHandler(log_stream)
        hdlr.setFormatter(logging.Formatter("%(message)s"))
        # Use logging filter to show messages on GUI
        hdlr.addFilter(SetStateFilter())
        vbLogger.addHandler(hdlr)

        # Key word arguments to be passed
        kwargs = {
            "uri": bootstrap_args["src"],
            "dest": bootstrap_args["dest"],
            "not_secure": bootstrap_args["insecure"],
            "progress_cb": progress_update_cb,
        }
        if bootstrap_args["user"] and bootstrap_args["passwd"]:
            kwargs["username"] = bootstrap_args["user"]
            kwargs["password"] = bootstrap_args["passwd"]
        if bootstrap_args["root_password"]:
            kwargs["root_password"] = bootstrap_args["root_password"]
        log.debug("Start container bootstrap")
        try:
            virtBootstrap.bootstrap(**kwargs)
            # Success - uncheck the 'install-oscontainer-bootstrap' checkbox

            def cb():
                self.widget("install-oscontainer-bootstrap").set_active(False)

            self.idle_add(cb)
        except Exception as err:
            asyncjob.set_error(
                "virt-bootstrap did not complete successfully",
                "%s\n%s" % (err, log_stream.getvalue()),
            )
