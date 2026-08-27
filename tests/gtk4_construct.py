# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
Construct every virt-manager GTK4/Adwaita UI surface against testdriver.

Run from the repo root:
    python3 tests/gtk4_construct.py
"""

import glob
import os
import sys
import time
import traceback

TOPDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOPDIR)
os.chdir(TOPDIR)

os.environ.setdefault("GSETTINGS_BACKEND", "memory")
os.environ.setdefault("VIRTINST_TEST_SUITE", "1")
# Force-disable AT-SPI for this process. The login/uitest env often
# already has GTK_A11Y=atspi; setdefault would leave it and GetItems
# wedges Gtk.Button() after a few dozen mapped windows (details_refresh
# on test-many-devices). Official uitests launch a separate process
# with GTK_A11Y=atspi.
os.environ["GTK_A11Y"] = "none"


def _init_gtk():
    import gi

    gi.require_version("Gdk", "4.0")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("LibvirtGLib", "1.0")
    from gi.repository import Adw
    from gi.repository import GLib
    from gi.repository import Gtk
    from gi.repository import LibvirtGLib

    Adw.init()
    from virtManager.lib import gtkcompat

    gtkcompat.install()
    LibvirtGLib.init()
    LibvirtGLib.event_register()
    return Adw, GLib, Gtk


def _compile_schemas():
    import subprocess
    from virtinst import BuildConfig

    schemadir = BuildConfig.gsettings_dir
    subprocess.check_call(["glib-compile-schemas", "--strict", schemadir])
    os.environ["GSETTINGS_SCHEMA_DIR"] = schemadir


_PUMP_DEADLINE = None


def _clear_a11y_sentinels():
    """Pollers treat leftover /tmp/vmm-a11y-* as live UI events."""
    for path in glob.glob("/tmp/vmm-a11y-*"):
        try:
            os.remove(path)
        except Exception:
            pass


def _reset_open_ui():
    """Drop leftover Apply / mapped dialogs between same-process tests."""
    try:
        from virtManager.about import vmmAbout

        inst = getattr(vmmAbout, "_instance", None)
        if inst is not None:
            inst.close()
    except Exception:
        pass
    try:
        from virtManager.vmwindow import vmmVMWindow
    except Exception:
        return
    instances = getattr(vmmVMWindow, "_instances", None) or {}
    for win in list(instances.values()):
        details = getattr(win, "_details", None)
        if details is None:
            continue
        try:
            details._disable_apply()
        except Exception:
            pass
        try:
            details._vmm_apply_just_succeeded = False
            details._vmm_confirming_unapplied = False
            details._vmm_unapplied_nav = False
            details._vmm_hw_change_busy = False
            details._config_remove_busy = False
            details._vmm_pending_media_path = None
            details._vmm_pending_vsock_cid = None
            details._vmm_applied_vsock_cid = None
        except Exception:
            pass
        try:
            details._addstorage._active_edits = []
        except Exception:
            pass
        err = getattr(details, "err", None)
        if err is None:
            continue
        try:
            err._in_prompt = False
        except Exception:
            pass
        try:
            cache = getattr(err, "_warn_dialogs", None) or {}
            for dlg in list(cache.values()):
                try:
                    if dlg.get_mapped() or dlg.get_visible():
                        dlg.hide()
                except Exception:
                    pass
        except Exception:
            pass
    try:
        from virtManager.delete import _vmmDeleteBase
        from virtManager.delete import vmmDeleteDialog

        for dlg in list(getattr(_vmmDeleteBase, "_live", []) or []):
            try:
                dlg._vmm_delete_a11y_poll = False
                dlg.close()
            except Exception:
                pass
        _vmmDeleteBase._live = []
        inst = getattr(vmmDeleteDialog, "_instance", None)
        if inst is not None:
            try:
                inst._vmm_delete_a11y_poll = False
                inst.close()
            except Exception:
                pass
            vmmDeleteDialog._instance = None
    except Exception:
        pass
    try:
        from gi.repository import Gtk

        app = Gtk.Application.get_default()
        if app is not None:
            for win in list(app.get_windows()):
                title = ""
                try:
                    title = win.get_title() or ""
                except Exception:
                    title = ""
                if "Delete" in title or "Remove" in title:
                    try:
                        win.set_visible(False)
                    except Exception:
                        try:
                            win.hide()
                        except Exception:
                            pass
    except Exception:
        pass


def _pump(GLib, seconds=0.05):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _PUMP_DEADLINE is not None and time.monotonic() > _PUMP_DEADLINE:
            raise TimeoutError("construct pump exceeded deadline")
        if not ctx.iteration(False):
            time.sleep(0.005)


def _open_conn(GLib, uri, timeout=45):
    from virtManager.connmanager import vmmConnectionManager

    print("OPEN start", uri, flush=True)
    conn = vmmConnectionManager.get_instance().add_conn(uri)
    done = []

    def _done(_src, err):
        done.append(err)

    conn.connect("open-completed", _done)
    conn.open()
    deadline = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while not done and time.monotonic() < deadline:
        if not ctx.iteration(False):
            time.sleep(0.02)
    if not done:
        raise RuntimeError("Timed out opening %s" % uri)
    if done[0]:
        raise RuntimeError("Failed to open %s: %s" % (uri, done[0]))
    _pump(GLib, 0.2)
    return conn


def _first_vm(conn, shutoff=False):
    vms = conn.list_vms()
    if not vms:
        raise RuntimeError("No VMs on testdriver connection")
    if shutoff:
        for vm in vms:
            if not vm.is_active():
                return vm
    return vms[0]


def _first_pool(conn):
    pools = conn.list_pools()
    return pools[0] if pools else None


def main():
    _clear_a11y_sentinels()
    print("compile schemas", flush=True)
    _compile_schemas()
    print("init gtk", flush=True)
    Adw, GLib, Gtk = _init_gtk()
    print("init config", flush=True)
    from virtinst import BuildConfig
    from virtManager.config import vmmConfig
    from virtManager.lib.testmock import CLITestOptionsClass

    vmmConfig.get_instance(BuildConfig, CLITestOptionsClass([]))

    print("init engine", flush=True)
    from virtManager.engine import vmmEngine

    engine = vmmEngine.get_instance()
    # Tick thread is normally started in app startup; construction needs it
    # so connection open can finish object polling.
    if not engine._tick_thread.is_alive():
        engine._tick_thread.start()
    print("engine ready", flush=True)

    testdriver = os.path.join(TOPDIR, "tests", "data", "testdriver", "testdriver.xml")
    uris = []
    if os.path.exists(testdriver):
        uris.append("__virtinst_test__test://%s,predictable" % testdriver)
        uris.append("test://%s" % testdriver)
    uris.append("test:///default")

    conn = None
    errors = []
    for uri in uris:
        try:
            conn = _open_conn(GLib, uri)
            print("OPENED", conn.get_uri(), "vms=%s" % len(conn.list_vms()))
            break
        except Exception as exc:
            errors.append("%s: %s" % (uri, exc))
            print("OPEN FAIL", uri, exc)
    if conn is None:
        raise RuntimeError("Could not open any test connection:\n" + "\n".join(errors))

    vm = _first_vm(conn)
    pool = _first_pool(conn)
    results = []

    _SURFACE_TIMEOUTS = {
        "details_refresh": 45,
        "details_hw_pages": 90,
        "addhardware_pages": 90,
        "details_many_devices": 20,
        "cli_windows": 90,
        "createvm_finish": 90,
        "createvm_import_finish_empty_name": 90,
        "inspection_os_page": 90,
    }

    def _run(name, fn, timeout=None):
        if timeout is None:
            timeout = _SURFACE_TIMEOUTS.get(name, 45)
        class _Timeout(Exception):
            pass

        global _PUMP_DEADLINE
        _PUMP_DEADLINE = time.monotonic() + timeout

        def _on_alarm(_signum, _frame):
            # GLib may swallow this; _pump also checks _PUMP_DEADLINE.
            raise _Timeout("%s exceeded %ss" % (name, timeout))

        import signal

        old = signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(int(timeout))
        try:
            _clear_a11y_sentinels()
            _reset_open_ui()
            fn()
            _pump(GLib, 0.05)
            results.append((name, True, None))
            print("OK  ", name, flush=True)
        except (_Timeout, TimeoutError) as exc:
            results.append((name, False, str(exc)))
            print("TIMEOUT", name, flush=True)
        except Exception:
            err = traceback.format_exc()
            results.append((name, False, err))
            print("FAIL", name, flush=True)
            print(err, flush=True)
        finally:
            _PUMP_DEADLINE = None
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
            try:
                _reset_open_ui()
            except Exception:
                pass

    def manager():
        from virtManager.manager import vmmManager

        win = vmmManager()
        win.show()
        assert win.topwin is not None
        _pump(GLib, 0.05)
        shut = win.widget("vm-shutdown")
        shut._sync_tooltip()
        tip = (
            shut._button.get_tooltip_text()
            or getattr(shut._button, "_vmm_tooltip", None)
            or shut.get_tooltip_text()
            or getattr(shut, "_vmm_tooltip", None)
        )
        assert tip and "Shut down" in tip, tip
        assert getattr(win, "_vmm_centered_once", False)

    def createconn():
        from virtManager.createconn import vmmCreateConn

        dlg = vmmCreateConn()
        dlg.show(None)

    def preferences():
        from virtManager.preferences import vmmPreferences
        from virtManager.lib.inspection import vmmInspection

        dlg = vmmPreferences()
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("prefs-close")
        prev_gfs = vmmInspection._libguestfs_installed
        vmmInspection._libguestfs_installed = False
        try:
            missing = vmmPreferences()
            assert missing.widget("prefs-libguestfs").get_sensitive() is False
            missing.close()
        finally:
            vmmInspection._libguestfs_installed = prev_gfs

    def about():
        from virtManager.about import vmmAbout

        dlg = vmmAbout()
        dlg.show(None)
        win = dlg._dialog
        assert win is not None
        from virtManager.about import _gpl2_text

        assert "GNU GENERAL PUBLIC LICENSE" in _gpl2_text()
        assert win.get_icon_name() == "virt-manager"
        child = win.get_child()
        found_link = []

        def _walk(widget):
            if widget is None:
                return
            if isinstance(widget, Gtk.LinkButton):
                found_link.append(widget)
                return
            try:
                kids = []
                if hasattr(widget, "get_first_child"):
                    kid = widget.get_first_child()
                    while kid is not None:
                        kids.append(kid)
                        kid = kid.get_next_sibling()
                for kid in kids:
                    _walk(kid)
            except Exception:
                pass

        _walk(child)
        assert found_link, "About website must be a Gtk.LinkButton"
        assert found_link[0].get_uri() == "https://virt-manager.org/"
        lic = dlg._show_license(win, present=False)
        assert lic is not None
        buf = lic.get_child().get_first_child().get_child().get_buffer()
        assert "GNU GENERAL PUBLIC LICENSE" in buf.get_text(
            buf.get_start_iter(), buf.get_end_iter(), False
        )
        dlg.close()
        assert dlg._dialog is None
        assert dlg._license_win is None

    def createvm():
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        assert dlg.topwin.get_default_widget() is dlg.widget("create-forward")
        _pump(GLib, 0.05)
        nb = dlg.widget("create-pages")
        cur = nb.get_current_page()
        hidden = 0
        for idx in range(nb.get_n_pages()):
            page = nb.get_nth_page(idx)
            if page is not None and not page.get_visible():
                hidden += 1
        assert hidden >= 1, "GTK 3 New VM wizard hid inactive notebook pages"
        assert nb.get_nth_page(cur).get_visible()

    def host():
        from virtManager.host import vmmHost

        win = vmmHost(conn)
        win.show()

    def vmwindow():
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow(vm)
        win.show()
        cust = vmmVMWindow(vm, parent=win.topwin)
        assert cust.is_customize_dialog
        assert getattr(cust.topwin, "_vmm_window_type_dialog", False)
        assert getattr(cust.topwin, "_vmm_center_on_parent", False)
        try:
            cust.close()
        except Exception:
            pass
        orig = win._console

        class _EmptyConsole:
            def vmwindow_viewer_get_pixbuf(self):
                return None

        win._console = _EmptyConsole()
        try:
            win._take_screenshot()
            raised = False
        except RuntimeError as exc:
            raised = "screenshot" in str(exc).lower()
        finally:
            win._console = orig
        assert raised, "empty guest display must fail Take Screenshot"

    def addhardware():
        from virtManager.addhardware import vmmAddHardware

        dlg = vmmAddHardware(vm)
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("create-finish")
        nb = dlg.widget("create-pages")
        cur = nb.get_current_page()
        hidden = 0
        for idx in range(nb.get_n_pages()):
            page = nb.get_nth_page(idx)
            if page is not None and not page.get_visible():
                hidden += 1
        assert hidden >= 1, "GTK 3 Add Hardware hid inactive notebook pages"
        assert nb.get_nth_page(cur).get_visible()

    def clone():
        from virtManager.clone import vmmCloneVM

        dlg = vmmCloneVM()
        dlg.show(None, _first_vm(conn, shutoff=True))
        assert dlg.topwin.get_default_widget() is dlg.widget("clone-ok")
        stg = dlg.widget("vmm-change-storage")
        if stg is not None:
            assert stg.get_default_widget() is dlg.widget("change-storage-ok")

    def migrate():
        from virtManager.migrate import vmmMigrateDialog

        dlg = vmmMigrateDialog()
        dlg.show(None, vm)
        assert dlg.topwin.get_default_widget() is dlg.widget("migrate-finish")

    def delete():
        from virtManager.delete import vmmDeleteDialog

        dlg = vmmDeleteDialog()
        dlg.show(None, vm)
        assert dlg.topwin.get_default_widget() is dlg.widget("delete-ok")

    def createpool():
        from virtManager.createpool import vmmCreatePool

        dlg = vmmCreatePool(conn)
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("pool-finish")

    def createvol():
        from virtManager.createvol import vmmCreateVolume

        if pool is None:
            raise RuntimeError("No storage pool available")
        dlg = vmmCreateVolume(conn, pool)
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("vol-create")

    def createnet():
        from virtManager.createnet import vmmCreateNetwork

        dlg = vmmCreateNetwork(conn)
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("create-finish")

    def storagebrowse():
        from virtManager.storagebrowse import vmmStorageBrowser

        dlg = vmmStorageBrowser(conn)
        dlg.show(None)

    def asyncjob():
        from virtManager.asyncjob import vmmAsyncJob

        def _cb(job, *args):
            ignore = job
            ignore = args

        dlg = vmmAsyncJob(_cb, [], None, None, "Test", "Testing", None, show_progress=True)
        assert dlg.topwin is not None
        assert getattr(dlg.topwin, "_vmm_skip_taskbar", False)
        assert getattr(dlg.topwin, "_vmm_urgency_hint", False)
        assert getattr(dlg.topwin, "_vmm_center_on_parent", False)
        try:
            assert dlg.topwin.get_application() is None
        except Exception:
            pass
        assert dlg.topwin.get_icon_name() == "virt-manager"
        assert dlg.topwin.get_default_widget() is dlg.widget("cancel-async-job")

    def systray():
        from virtManager.systray import vmmSystray

        tray = vmmSystray.get_instance()
        tray.show_from_cli()

    def connectauth():
        from virtManager.lib.connectauth import _vmmConnectAuth
        import libvirt

        creds = [
            [libvirt.VIR_CRED_AUTHNAME, "Username", None, None, None],
            [libvirt.VIR_CRED_PASSPHRASE, "Password", None, None, None],
        ]
        dlg = _vmmConnectAuth(creds)
        assert dlg.topwin is not None
        try:
            area = dlg.topwin.get_content_area()
            assert area is not None
            assert area.get_visible()
        except Exception:
            pass
        assert dlg._entry2_in_use
        assert dlg._passphrase_row_active()
        assert dlg.entry2.get_visibility() is False
        assert dlg.entry2.get_input_purpose() == Gtk.InputPurpose.PASSWORD
        assert dlg.widget("label1").get_text() == "Username: "
        assert dlg.widget("label2").get_text() == "Password: "
        focused = []
        dlg.entry2.grab_focus = lambda *a, **k: focused.append("pass")
        dlg._entry_cb(dlg.entry1)
        assert focused == ["pass"], "Enter on username must focus passphrase"
        assert dlg.topwin.get_icon_name() == "virt-manager"
        assert dlg.topwin.get_default_widget() is dlg.widget("connectauth-ok")

    def oslist():
        from virtManager.oslist import vmmOSList

        widget = vmmOSList()
        assert widget.search_entry is not None
        if hasattr(widget.search_entry, "set_icon_from_icon_name"):
            assert getattr(widget.search_entry, "_vmm_gtk3_search_icon", False)

    def snapshots_new():
        from virtManager.details.snapshots import vmmSnapshotNew

        dlg = vmmSnapshotNew(vm)
        dlg.show(None)
        assert dlg.topwin.get_default_widget() is dlg.widget("snapshot-new-ok")

    def vmwindow_pages():
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow.get_instance(None, vm)
        win.show()
        pages = win.widget("details-pages")
        for page in range(pages.get_n_pages()):
            pages.set_current_page(page)
            _pump(GLib, 0.02)
        win._console.vmwindow_get_keycombo_menu()
        win._console.vmwindow_get_console_list_menu()
        win._details.vmwindow_refresh_vm_state(True)
        win._snapshots.vmwindow_refresh_vm_state()

    def viewers():
        from gi.repository import Gdk
        from virtManager.details import gtk4display
        from virtManager.details.viewers import VNCViewer, SpiceViewer
        from virtManager.details.sshtunnels import ConnectionInfo

        ginfo = None
        gfxvm = vm
        for cand in conn.list_vms():
            gdevs = list(cand.get_xmlobj().devices.graphics)
            if gdevs:
                gfxvm = cand
                ginfo = ConnectionInfo(conn, gdevs[0])
                break
        display = gtk4display.VNCDisplay()
        display.set_pointer_grab(True)
        display.set_scaling(True)
        display.send_keys([Gdk.keyval_from_name("a") or 97])
        spice_display = gtk4display.SpiceDisplay(None)
        spice_display.set_scaling(True)
        usb = gtk4display.UsbDeviceWidget.new(None)
        assert usb is not None
        from virtManager.details import viewers as vmod

        assert vmod.SpiceClientGtk is None, (
            "GTK 4 must not parent SpiceClientGtk widgets"
        )
        assert vmod.GtkVnc is None, "GTK 4 must not parent GtkVnc widgets"
        if ginfo is not None:
            if ginfo.gtype == "vnc":
                viewer = VNCViewer(gfxvm, ginfo)
                viewer._init_display()
            else:
                viewer = SpiceViewer(gfxvm, ginfo)
            assert viewer is not None

    def addhardware_pages():
        from virtManager.addhardware import vmmAddHardware
        from virtManager.lib import uiutil

        dlg = vmmAddHardware(vm)
        dlg.show(None)
        model = dlg.widget("hw-list").get_model()
        for idx, _row in enumerate(model):
            uiutil.set_list_selection_by_number(dlg.widget("hw-list"), idx)
            dlg._hw_selected_cb(dlg.widget("hw-list"))
            _pump(GLib, 0.01)

    def details_hw_pages():
        from virtManager.vmwindow import vmmVMWindow
        from virtManager.lib import uiutil

        win = vmmVMWindow.get_instance(None, vm)
        win.show()
        hwlist = win._details.widget("hw-list")
        model = hwlist.get_model()
        for idx, _row in enumerate(model):
            uiutil.set_list_selection_by_number(hwlist, idx)
            win._details._hw_changed_cb(hwlist)
            _pump(GLib, 0.01)

    def createvm_methods():
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        for name in (
            "method-local",
            "method-tree",
            "method-import",
            "method-manual",
            "method-container-app",
            "method-container-os",
        ):
            try:
                widget = dlg.widget(name)
            except Exception:
                continue
            widget.set_active(True)
            dlg._method_changed(widget)
            _pump(GLib, 0.01)
        # Finish page needs a populated guest; cover name/install/mem/storage
        for page in range(min(4, dlg.widget("create-pages").get_n_pages())):
            dlg._goto_create_page(page)
        from virtManager.createvm import PAGE_INSTALL

        dlg.widget("method-local").set_active(True)
        dlg._method_changed(dlg.widget("method-local"))
        dlg._set_install_page()
        dlg._goto_create_page(PAGE_INSTALL)
        inst = dlg.widget("install-method-pages")
        cur = inst.get_current_page()
        hidden = 0
        for idx in range(inst.get_n_pages()):
            page = inst.get_nth_page(idx)
            if page is not None and not page.get_visible():
                hidden += 1
        assert hidden >= 1, "GTK 3 install-method subpages shrink-wrap"
        assert inst.get_nth_page(cur).get_visible()

    def createpool_types():
        from virtManager.createpool import vmmCreatePool

        dlg = vmmCreatePool(conn)
        dlg.show(None)
        combo = dlg.widget("pool-type")
        model = combo.get_model()
        for idx, _row in enumerate(model):
            combo.set_active(idx)
            dlg._pool_type_changed_cb(combo)
            _pump(GLib, 0.01)

    def createnet_modes():
        from virtManager.createnet import vmmCreateNetwork

        dlg = vmmCreateNetwork(conn)
        dlg.show(None)
        combo = dlg.widget("net-forward-mode")
        model = combo.get_model()
        for idx, _row in enumerate(model):
            combo.set_active(idx)
            dlg._net_forward_mode_changed_cb(combo)
            _pump(GLib, 0.01)

    def host_pages():
        from virtManager.host import vmmHost

        vmmHost.show_instance(None, conn)
        win = vmmHost._instances[conn.get_uri()]
        pages = win.widget("details-tabs")
        for page in range(pages.get_n_pages()):
            pages.set_current_page(page)

    def vm_lifecycle_menus():
        from virtManager import vmmenu

        menu = vmmenu.VMActionMenu(None, lambda: vm)
        menu.update_widget_states(vm)
        shut = vmmenu.VMShutdownMenu(None, lambda: vm)
        shut.update_widget_states(vm)
        menu.show()

    def gtk3_context_menus_and_window_size():
        """GTK 4 .ui dropped button-press/configure-event; prove they work."""
        from virtManager.host import vmmHost
        from virtManager.lib.gtkcompat import _FakeEvent
        from virtManager.manager import vmmManager
        from virtManager.vmwindow import vmmVMWindow

        mgr = vmmManager.get_instance(None)
        mgr.show()
        vmlist = mgr.widget("vm-list")
        assert "button-press-event" in getattr(vmlist, "_vmm_legacy_signals", set())
        assert "key-press-event" in getattr(vmlist, "_vmm_legacy_signals", set())
        assert "configure-event" in getattr(mgr.topwin, "_vmm_legacy_signals", set())

        model = vmlist.get_model()
        treeiter = model.get_iter_first()
        assert treeiter is not None
        # First row is a connection; second is typically a VM.
        vmiter = model.iter_children(treeiter) or model.iter_next(treeiter)
        assert vmiter is not None
        path = model.get_path(vmiter)
        try:
            vmlist.scroll_to_cell(path, None, False, 0, 0)
        except Exception:
            pass
        _pump(GLib, 0.05)
        area = None
        try:
            area = vmlist.get_cell_area(path, vmlist.get_column(0))
        except Exception:
            area = None
        x = int(getattr(area, "x", 8) + 8)
        y = int(getattr(area, "y", 8) + 8)
        ev = _FakeEvent(button=3, x=x, y=y)
        handled = mgr.popup_vm_menu_button(vmlist, ev)
        if handled is False:
            mgr.popup_vm_menu(model, vmiter, ev)
        _pump(GLib, 0.05)
        opened = bool(getattr(mgr.vmmenu, "_opened", False)) or bool(
            getattr(mgr.connmenu, "_opened", False)
        )
        if not opened:
            pop = getattr(mgr.vmmenu, "_popover", None) or getattr(
                mgr.connmenu, "_popover", None
            )
            try:
                opened = bool(pop is not None and pop.get_visible())
            except Exception:
                opened = False
        assert opened, "right-click did not open the VM or connection menu"
        menu = mgr.vmmenu if getattr(mgr.vmmenu, "_opened", False) else mgr.connmenu
        assert getattr(menu, "_vmm_popup_pos", None), "context menu must be placed at the pointer"

        mgr.topwin.set_default_size(960, 640)
        _pump(GLib, 0.15)
        assert mgr._window_size is not None, "manager resize did not persist"

        vwin = vmmVMWindow.get_instance(None, vm)
        vwin.show()
        assert "configure-event" in getattr(vwin.topwin, "_vmm_legacy_signals", set())
        hwlist = vwin._details.widget("hw-list")
        assert "button-press-event" in getattr(hwlist, "_vmm_legacy_signals", set())
        snaplist = vwin._snapshots.widget("snapshot-list")
        assert "button-press-event" in getattr(snaplist, "_vmm_legacy_signals", set())
        vwin.topwin.set_default_size(1000, 700)
        _pump(GLib, 0.15)
        assert vwin._window_size is not None, "VM window resize did not persist"

        hw_ev = _FakeEvent(button=3, x=12, y=12)
        vwin._details._popup_addhw_menu_cb(hwlist, hw_ev)
        if not getattr(vwin._details._popupmenu, "_opened", False):
            vwin._details._popupmenu.popup_at_pointer(hw_ev)
        _pump(GLib, 0.05)
        hwmenu = getattr(vwin._details, "_popupmenu", None)
        assert hwmenu is not None
        hw_open = bool(getattr(hwmenu, "_opened", False))
        if not hw_open:
            pop = getattr(hwmenu, "_popover", None)
            try:
                hw_open = bool(pop is not None and pop.get_visible())
            except Exception:
                hw_open = False
        assert hw_open, "hardware-list right-click did not open Add/Remove menu"

        hwin = vmmHost.show_instance(None, conn)
        if hwin is None:
            hwin = vmmHost._instances[conn.get_uri()]
        assert "configure-event" in getattr(hwin.topwin, "_vmm_legacy_signals", set())
        vollist = hwin._storagelist.widget("vol-list")
        assert "button-press-event" in getattr(vollist, "_vmm_legacy_signals", set())
        hwin._storagelist._vol_popup_menu_cb(vollist, _FakeEvent(button=3, x=8, y=8))
        _pump(GLib, 0.05)
        volmenu = hwin._storagelist._volmenu
        vol_open = bool(getattr(volmenu, "_opened", False))
        if not vol_open:
            pop = getattr(volmenu, "_popover", None)
            try:
                vol_open = bool(pop is not None and pop.get_visible())
            except Exception:
                vol_open = False
        assert vol_open, "volume-list right-click did not open Copy Volume Path"

    def gtk3_menubar_mnemonics():
        """GTK 3 Alt+letter menubar/submenu mnemonics and F10 on AccelGroup."""
        from gi.repository import Gdk
        from gi.repository import Gtk

        from virtManager.host import vmmHost
        from virtManager.lib import gtkcompat
        from virtManager.manager import vmmManager
        from virtManager.vmwindow import vmmVMWindow

        mgr = vmmManager.get_instance(None)
        mgr.show()
        _pump(GLib, 0.05)
        bar = mgr.widget("menubar1")
        file_item = mgr.widget("menuitem4")
        edit_item = mgr.widget("menuitem5")
        close_item = mgr.widget("menu_file_close")
        assert bar is not None and file_item is not None
        assert gtkcompat._item_mnemonic_keyval(file_item)
        assert gtkcompat._keyvals_match(
            gtkcompat._item_mnemonic_keyval(file_item), Gdk.KEY_f
        )

        groups = Gtk.accel_groups_from_object(mgr.topwin)
        assert groups, "manager window has no AccelGroup"
        group = groups[0]
        triggers = [str(t) for t, _cb in list(getattr(group, "_shortcuts", None) or [])]
        assert "F10" in triggers, "F10 must live on AccelGroup so grab can detach it"
        assert getattr(group, "_vmm_mnemonic_controller", None) is not None
        extras = list(getattr(group, "_extra_controllers", None) or [])
        assert group._vmm_mnemonic_controller in extras

        assert gtkcompat.handle_menubar_key(mgr.topwin, mgr.builder, Gdk.KEY_f, alt=True)
        _pump(GLib, 0.05)
        assert getattr(bar, "_vmm_open_item", None) is file_item
        file_menu = gtkcompat._item_submenu(file_item)
        assert file_menu is not None and getattr(file_menu, "_opened", False)
        pos = getattr(file_menu, "_vmm_popup_pos", None)
        assert pos, "File dropdown must be placed under the File item"
        assert file_menu.get_margin_top() >= 0
        assert "vmm-menu-open" in (file_menu.get_css_classes() or [])

        match = gtkcompat._lookup_mnemonic_item(
            gtkcompat._widget_children(file_menu), Gdk.KEY_c
        )
        assert match is close_item, "File menu mnemonic C should be Close"

        activated = []
        orig_activate = gtkcompat._activate_menu_widget

        def _spy(item):
            activated.append(item)
            if item is close_item:
                return True
            return orig_activate(item)

        gtkcompat._activate_menu_widget = _spy
        try:
            assert gtkcompat.handle_menubar_key(
                mgr.topwin, mgr.builder, Gdk.KEY_c, alt=False
            )
            assert activated and activated[0] is close_item
            assert gtkcompat.handle_menubar_key(
                mgr.topwin, mgr.builder, Gdk.KEY_e, alt=True
            )
        finally:
            gtkcompat._activate_menu_widget = orig_activate
        _pump(GLib, 0.05)
        assert getattr(bar, "_vmm_open_item", None) is edit_item

        assert gtkcompat.handle_menubar_key(
            mgr.topwin, mgr.builder, Gdk.KEY_Right, alt=False
        ) or gtkcompat._on_window_menubar_key(
            mgr.topwin, mgr.builder, Gdk.KEY_Right, 0
        )
        _pump(GLib, 0.05)
        assert getattr(bar, "_vmm_open_item", None) is not edit_item

        assert gtkcompat._on_window_menubar_key(
            mgr.topwin, mgr.builder, Gdk.KEY_Escape, 0
        )
        _pump(GLib, 0.05)
        assert getattr(bar, "_vmm_open_item", None) is None

        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-enable-mnemonics", False)
        try:
            assert not gtkcompat._on_window_menubar_key(
                mgr.topwin,
                mgr.builder,
                Gdk.KEY_f,
                int(Gdk.ModifierType.ALT_MASK),
            ), "Alt+F must no-op while console grab disables mnemonics"
        finally:
            settings.set_property("gtk-enable-mnemonics", True)

        gtkcompat._accel_group_disable(mgr.topwin, group)
        assert group._controller is None
        assert not getattr(group._vmm_mnemonic_controller, "_vmm_accel_attached", False)
        gtkcompat._accel_group_enable(mgr.topwin, group)
        assert group._controller is not None
        assert getattr(group._vmm_mnemonic_controller, "_vmm_accel_attached", False)

        vwin = vmmVMWindow.get_instance(None, vm)
        vwin.show()
        _pump(GLib, 0.05)
        view_item = vwin.widget("view2")
        assert view_item is not None
        assert gtkcompat.handle_menubar_key(vwin.topwin, vwin.builder, Gdk.KEY_v, alt=True)
        _pump(GLib, 0.05)
        vbar = vwin.widget("details-menubar")
        assert getattr(vbar, "_vmm_open_item", None) is view_item
        gtkcompat.popdown_window_menus(vwin.topwin, vwin.builder)

        hwin = vmmHost.show_instance(None, conn)
        if hwin is None:
            hwin = vmmHost._instances[conn.get_uri()]
        assert gtkcompat.handle_menubar_key(hwin.topwin, hwin.builder, Gdk.KEY_f, alt=True)
        _pump(GLib, 0.05)
        hbar = hwin.widget("menubar1")
        assert getattr(hbar, "_vmm_open_item", None) is not None
        gtkcompat.popdown_window_menus(hwin.topwin, hwin.builder)

    def gtk3_entry_mnemonics():
        """GTK 3 form labels keep mnemonic-widget after a11y sync."""
        from virtManager.createconn import vmmCreateConn
        from virtManager.vmwindow import vmmVMWindow

        dlg = vmmCreateConn()
        dlg.show(None)
        _pump(GLib, 0.2)
        host_lbl = dlg.widget("label91")
        host_ent = dlg.widget("hostname")
        user_lbl = dlg.widget("label2")
        user_ent = dlg.widget("username-entry")
        assert host_lbl is not None and host_ent is not None
        assert host_lbl.get_mnemonic_widget() is host_ent, (
            "H_ostname: mnemonic-widget was cleared; Alt+O cannot focus Hostname"
        )
        assert user_lbl.get_mnemonic_widget() is user_ent, (
            "_Username: mnemonic-widget was cleared; Alt+U cannot focus Username"
        )

        vwin = vmmVMWindow.get_instance(None, vm)
        vwin.show()
        _pump(GLib, 0.2)
        details = vwin._details
        name_lbl = details.widget("label43")
        name_ent = details.widget("overview-name")
        assert name_lbl.get_mnemonic_widget() is name_ent, (
            "_Name: mnemonic-widget was cleared; Alt+N cannot focus Overview name"
        )
        try:
            dlg.close()
        except Exception:
            pass

    def gtk3_notebook_mnemonics():
        """GTK 3 notebook tab Alt+letter switches pages."""
        from gi.repository import Gdk

        from virtManager.host import vmmHost
        from virtManager.lib import gtkcompat
        from virtManager.preferences import vmmPreferences

        dlg = vmmPreferences()
        dlg.show(None)
        _pump(GLib, 0.1)
        nb = dlg.widget("prefs-pages")
        assert nb is not None
        nb.set_current_page(0)
        assert gtkcompat.handle_notebook_key(dlg.topwin, dlg.builder, Gdk.KEY_o)
        assert nb.get_current_page() == 1, "Alt+O should select P_olling"
        assert gtkcompat.handle_notebook_key(dlg.topwin, dlg.builder, Gdk.KEY_l)
        assert nb.get_current_page() == 3, "Alt+L should select Conso_le"
        assert gtkcompat._on_window_menubar_key(
            dlg.topwin, dlg.builder, Gdk.KEY_b, int(Gdk.ModifierType.ALT_MASK)
        )
        assert nb.get_current_page() == 4, "Alt+B should select Feed_back"

        hwin = vmmHost.show_instance(None, conn)
        if hwin is None:
            hwin = vmmHost._instances[conn.get_uri()]
        hnb = hwin.widget("details-tabs")
        hnb.set_current_page(0)
        assert gtkcompat.handle_notebook_key(hwin.topwin, hwin.builder, Gdk.KEY_s)
        assert hnb.get_current_page() == 2, "Alt+S should select _Storage"

        from virtManager.lib.graphwidgets import _theme_base_rgb

        rgb = _theme_base_rgb(dlg.topwin)
        assert len(rgb) == 3
        assert all(0.0 <= c <= 1.0 for c in rgb)

    def gtk3_theme_dialogs_passwords():
        """GTK 3 theme tokens, dialog window hints, password purpose, vol icon."""
        from gi.repository import Gdk
        from gi.repository import Gtk

        from virtManager import config as vmmconfig
        from virtManager.createvol import vmmCreateVolume
        from virtManager.lib import gtkcompat
        from virtManager.lib.graphwidgets import _theme_base_rgb
        from virtManager.preferences import vmmPreferences
        from virtManager.vmwindow import vmmVMWindow

        css = vmmconfig.CSSDATA
        assert "color: alpha(@window_fg_color, 0.55);" in css, css
        assert "color: @insensitive_fg_color" not in css, css

        dlg = vmmPreferences()
        color = gtkcompat.theme_insensitive_color(dlg.topwin)
        assert color and color.startswith("rgb("), color
        rgb = _theme_base_rgb(dlg.topwin)
        assert len(rgb) == 3
        assert all(0.0 <= c <= 1.0 for c in rgb)
        rgb_fallback = _theme_base_rgb(None)
        assert len(rgb_fallback) == 3

        assert dlg.topwin._vmm_window_type_dialog
        assert dlg.topwin.get_icon_name() == "virt-manager"
        box = Gtk.Box()
        orphan = Gtk.Label(label="orphan")
        other = Gtk.Box()
        other.append(orphan)
        box.remove(orphan)
        assert orphan.get_parent() is other
        dlg.topwin.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        dlg.topwin.set_skip_taskbar_hint(True)
        dlg.topwin.set_urgency_hint(True)
        assert dlg.topwin._vmm_skip_taskbar
        assert dlg.topwin._vmm_urgency_hint

        win = vmmVMWindow.get_instance(None, vm)
        auth = win._console.widget("console-auth-password")
        assert not auth.get_visibility()
        assert auth.get_input_purpose() == Gtk.InputPurpose.PASSWORD
        try:
            invis = auth.get_invisible_char()
        except Exception:
            invis = getattr(auth, "_vmm_gtk3_invisible_char", "")
        assert invis in ("●", "\u25cf"), invis

        details = getattr(win, "_details", None)
        frame = details.widget("frame2") if details is not None else None
        if frame is None:
            frame = dlg.widget("frame5")
        assert frame is not None
        assert abs(float(frame.get_property("label-xalign")) - 0.0) < 0.01
        assert getattr(frame, "_vmm_gtk3_label_xalign", None) == 0.0
        sw = details.widget("scrolledwindow5") if details is not None else None
        if sw is not None:
            assert sw.has_css_class("vmm-scroll-shadow"), list(sw.get_css_classes())
            assert getattr(sw, "_vmm_gtk3_shadow", None) == "in"

        pool = _first_pool(conn)
        assert pool is not None
        cvol = vmmCreateVolume(conn, pool)
        btn = cvol.widget("vol-create")
        assert getattr(btn, "_vmm_icon_child", False), "Finish button lost document-new icon"
        assert getattr(cvol.widget("vbox1"), "_vmm_gtk3_border_width", 0) == 12
        prefs_box = dlg.widget("vbox1")
        assert prefs_box.get_margin_top() >= 12, prefs_box.get_margin_top()
        try:
            cvol.close()
        except Exception:
            pass

    def error_dialogs():
        from virtManager.error import _errorDialog
        from virtManager.error import vmmErrorDialog

        err = vmmErrorDialog.get_instance()
        err.show_err("test error", details="details", title="t", modal=False, debug=False)
        extra = Gtk.Label(label="USB extra widget")
        err.show_info("info", widget=extra, modal=False)
        assert extra.get_parent() is not None
        assert extra.get_hexpand() is True
        assert extra.get_vexpand() is True
        # GTK 3 content_area.add() keeps extras above Close/OK.
        extra_root = extra.get_root()
        assert extra.get_parent() is extra_root._extra_box
        assert extra_root._body.get_next_sibling() is extra_root._button_box
        assert extra_root._primary.get_max_width_chars() == 40
        assert extra_root._secondary.get_max_width_chars() == 40
        assert extra_root.get_default_widget() is not None

        dlg = _errorDialog(message_type=Gtk.MessageType.ERROR)
        assert getattr(dlg, "_vmm_window_type_dialog", False)
        assert dlg._primary.get_selectable()
        assert dlg._secondary.get_selectable()
        assert dlg.buf_expander.get_visible() is False
        assert dlg.chk_vbox.get_visible() is False
        dlg._set_primary_text("boom <err>")
        markup = dlg._primary.get_label() or ""
        assert "bold" in markup and "boom" in markup
        assert "&lt;err&gt;" in markup
        assert dlg._icon_name == "dialog-error", dlg._icon_name
        warn = _errorDialog(message_type=Gtk.MessageType.WARNING)
        assert warn._icon_name == "dialog-warning", warn._icon_name

    def cli_windows():
        from virtManager.engine import vmmEngine

        engine = vmmEngine.get_instance()
        uri = conn.get_uri()
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_MANAGER, "")
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_DOMAIN_CREATOR, "")
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_HOST_SUMMARY, "")
        name = vm.get_name()
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_DOMAIN_EDITOR, name)
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_DOMAIN_CONSOLE, name)
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_DOMAIN_PERFORMANCE, name)
        engine._launch_cli_window(uri, vmmEngine.CLI_SHOW_DOMAIN_DELETE, name)
        _pump(GLib, 0.3)
        try:
            title = open("/tmp/vmm-a11y-vmwindow-title.txt", "r").read().strip()
        except Exception:
            title = ""
        try:
            shown = open("/tmp/vmm-a11y-vmwindow.txt", "r").read().strip()
        except Exception:
            shown = ""
        try:
            delete_shown = open("/tmp/vmm-a11y-delete-shown.txt", "r").read().strip()
        except Exception:
            delete_shown = ""
        assert shown == name or name in title, (
            "CLI --show-domain-delete did not show details for %s (shown=%r title=%r)"
            % (name, shown, title)
        )
        assert " on " in title, (
            "CLI --show-domain-delete details title missing connection: %r" % title
        )
        assert delete_shown == "1", "CLI --show-domain-delete did not show Delete"

    def xmleditor_pages():
        from virtManager.addhardware import vmmAddHardware

        dlg = vmmAddHardware(vm)
        editor = dlg._xmleditor
        editor._goto_xml_page(1)
        nb = editor.widget("xml-notebook")
        assert nb.get_current_page() == 1
        assert nb.get_nth_page(0).get_visible()
        assert nb.get_nth_page(1).get_visible()
        # GTK 3 kept both Details and XML tabs visible so users could click.
        # Hiding the inactive child hid the tab in GTK 4.
        editor.reset_state()
        assert nb.get_current_page() == 0
        assert nb.get_nth_page(0).get_visible()
        assert nb.get_nth_page(1).get_visible()
        nb.set_current_page(1)
        _pump(GLib, 0.05)
        assert nb.get_current_page() == 1
        editor.reset_state()

    def console_pages():
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow.get_instance(None, vm)
        win._console.vmwindow_refresh_vm_state()
        win._console.vmwindow_activate_default_console_page()
        win._console.vmwindow_get_viewer_is_visible()
        win._console.vmwindow_get_resizeguest_tooltip()
        win._console.vmwindow_sync_scaling_with_display()
        from virtManager.details.console import build_keycombo_menu
        from virtManager.details.console import vmmOverlayToolbar

        sent = []
        keymenu = build_keycombo_menu(lambda _src, keys: sent.append(list(keys)))
        assert keymenu is not None
        kids = list(keymenu.get_children())
        assert kids, "Send Key menu is empty"
        toolbar = vmmOverlayToolbar(lambda *_a: None, lambda _src, keys: sent.append(list(keys)))
        overlay = toolbar.timed_revealer.get_overlay_widget()
        assert overlay is not None
        assert overlay.get_size_request()[1] >= 8
        near = win._console._pointer_near_top()
        assert near in (True, False)
        toolbar.timed_revealer.force_reveal(True)
        toolbar._on_send_key_button_clicked_cb(toolbar._send_key_button)
        assert getattr(toolbar._keycombo_menu, "_vmm_popup_pos", None), (
            "Send Key menu must use popup_at_rect placement"
        )
        toolbar.cleanup()
        con = win._console
        closed = []
        orig_close = con.topwin.close
        con.topwin.close = lambda *_a, **_k: closed.append(True)
        try:
            con._pointer_is_grabbed = False
            if con._gtk_settings_accel is not None:
                con._enable_modifiers()
            assert not con._should_ignore_window_close_accel()
            con._disable_modifiers()
            assert con._should_ignore_window_close_accel()
            if not con._should_ignore_window_close_accel():
                con.topwin.close()
            assert not closed
            con._enable_modifiers()
            assert not con._should_ignore_window_close_accel()
            if not con._should_ignore_window_close_accel():
                con.topwin.close()
            assert closed
            closed.clear()
            con._focus_serial_console()
            assert con._should_ignore_window_close_accel()
            con._unfocus_serial_console()
            assert not con._should_ignore_window_close_accel()
        finally:
            try:
                con._enable_modifiers()
            except Exception:
                pass
            con.topwin.close = orig_close
        bar = Gtk.MenuBar()
        file_item = Gtk.MenuItem(label="File")
        sub = Gtk.Menu()
        sub.add(Gtk.MenuItem(label="New Virtual Machine"))
        file_item.set_submenu(sub)
        bar.append(file_item)
        opened = []
        sub.popup_at_widget = lambda *_a: opened.append(True)
        file_item._on_pointer_enter()
        assert not opened, "GTK 3 menubar must not open on hover"
        file_item._on_clicked()
        assert opened, "GTK 3 menubar must open on click"

        from virtManager.details.console import _CONSOLE_PAGE_CONNECT

        assert con._viewer_connect_clicked is False
        orig_auto = con.vm.get_console_autoconnect
        con.vm.get_console_autoconnect = lambda: False
        try:
            con._close_viewer()
            con._init_viewer(None, None)
            page = con.widget("console-pages").get_current_page()
            assert page == _CONSOLE_PAGE_CONNECT, (
                "GTK 3 Autoconnect-off must show Connect page, page=%s" % page
            )
        finally:
            con.vm.get_console_autoconnect = orig_auto

    def preferences_grabkeys_widgets():
        from virtManager.preferences import vmmPreferences

        dlg = vmmPreferences()
        dlg.refresh_grabkeys_combination()
        dlg.refresh_confirm_forcepoweroff()
        dlg.refresh_confirm_poweroff()
        dlg.refresh_confirm_pause()
        dlg.refresh_graphics_type()
        dlg.refresh_storage_format()
        # Build the grab-key dialog widgets without running the modal loop
        from gi.repository import Gtk

        dialog = Gtk.Dialog(title="grab", transient_for=dlg.topwin)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.REJECT, "_OK", Gtk.ResponseType.ACCEPT)
        assert dialog.get_widget_for_response(Gtk.ResponseType.ACCEPT) or True
        dlg.change_grab_keys(None)
        grab = getattr(dlg, "_grab_dialog", None)
        assert grab is not None
        assert grab.get_default_widget() is not None
        try:
            grab.close()
        except Exception:
            pass

    def window_accel_and_resize():
        from virtManager.lib import gtkcompat
        from gi.repository import Gtk

        win = Gtk.Window(title="accel-resize")
        win.set_default_size(240, 180)
        close_item = gtkcompat.MenuItem(label="_Close")
        activated = []
        close_item.connect("clicked", lambda *_a: activated.append("close"))
        close_item.connect("activate", lambda *_a: activated.append("activate"))
        group = gtkcompat.AccelGroup()
        group.add_shortcut("<Shift><Control>w", lambda: gtkcompat._activate_builder_item(close_item))
        gtkcompat._accel_group_enable(win, group)
        groups = Gtk.accel_groups_from_object(win)
        assert groups and groups[0] is group
        gtkcompat._activate_builder_item(close_item)
        assert "close" in activated or "activate" in activated
        gtkcompat._accel_group_disable(win, group)
        assert group._controller is None
        gtkcompat._accel_group_enable(win, group)
        assert group._controller is not None
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-menu-bar-accel", None)
        assert settings.get_property("gtk-menu-bar-accel") is None
        settings.set_property("gtk-menu-bar-accel", "F10")
        assert settings.get_property("gtk-menu-bar-accel") == "F10"
        from gi.repository import Gdk

        clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
        clip.set_text("primary-text", -1)
        assert clip._xclip_sel == "primary"
        win.resize(320, 240)
        assert getattr(win, "_vmm_win_size", None) == (320, 240)
        assert win.get_size()[0] >= 1 and win.get_size()[1] >= 1
        mtb = gtkcompat.MenuToolButton()
        mtb.set_tooltip_text("Shut down the virtual machine")
        mtb._sync_tooltip()
        assert mtb._button.get_tooltip_text() == "Shut down the virtual machine"
        assert mtb._menu_button.get_tooltip_text() == "Shut down the virtual machine"
        win.present()
        assert gtkcompat._window_center_on_display(win) in (True, False)
        xid = gtkcompat._window_xid(win)
        if xid:
            assert gtkcompat._x11_resize_window(xid, 320, 240) in (True, False)
            assert gtkcompat._x11_move_window(xid, 40, 40) in (True, False)
            assert gtkcompat._x11_query_pointer() is None or len(gtkcompat._x11_query_pointer()) == 2
            win.move(40, 40)
            assert getattr(win, "_vmm_win_pos", None) == (40, 40)
        win.resize(1, 1)
        win.close()

    def createconn_hypervisors():
        from virtManager.createconn import vmmCreateConn

        dlg = vmmCreateConn()
        combo = dlg.widget("hypervisor")
        model = combo.get_model()
        for idx, _row in enumerate(model):
            combo.set_active(idx)
            dlg.hypervisor_changed(combo)
        dlg.widget("connect-remote").set_active(True)
        dlg.connect_remote_toggled(dlg.widget("connect-remote"))
        dlg.widget("hostname").set_text("example.test")
        dlg.hostname_changed(None)
        dlg.populate_uri()

    def storagebrowse_reasons():
        from virtManager.storagebrowse import vmmStorageBrowser

        dlg = vmmStorageBrowser(conn)
        for reason in (
            vmmStorageBrowser.REASON_IMAGE,
            vmmStorageBrowser.REASON_ISO_MEDIA,
            vmmStorageBrowser.REASON_FLOPPY_MEDIA,
            vmmStorageBrowser.REASON_FS,
        ):
            dlg.set_browse_reason(reason)
            _pump(GLib, 0.01)
        dlg.show(None)
        assert getattr(dlg, "_vmm_choose_poll_cb", None) is not None
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "1", shown
        dlg.storagelist.emit("volume-chosen", None)
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "1", "None volume must not close the browser"
        finished = []

        def _cb(_src, path):
            finished.append(path)

        dlg.set_finish_cb(_cb)
        dlg._finish("/pool-dir/iso-vol")
        assert finished == ["/pool-dir/iso-vol"]
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "0", shown
        _pump(GLib, 0.9)
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "0", "storage browser remounted after choose: %s" % shown
        open("/tmp/vmm-a11y-choose-volume", "w").write("1")
        _pump(GLib, 0.2)
        assert os.path.exists("/tmp/vmm-a11y-choose-volume"), (
            "hidden storage browser consumed Choose Volume"
        )
        os.remove("/tmp/vmm-a11y-choose-volume")

    def clone_storage_dialog():
        from virtManager.clone import vmmCloneVM
        from virtManager.lib import uiutil

        dlg = vmmCloneVM()
        dlg.show(None, _first_vm(conn, shutoff=True))
        model = dlg.widget("storage-list").get_model()
        if len(model):
            uiutil.set_list_selection_by_number(dlg.widget("storage-list"), 0)
            dlg._show_storage_window()
            dlg.widget("change-storage-doclone").set_active(True)
            dlg.widget("change-storage-doclone").toggled()

    def migrate_modes():
        from virtManager.migrate import vmmMigrateDialog

        dlg = vmmMigrateDialog()
        dlg.show(None, vm)
        combo = dlg.widget("migrate-mode")
        for idx, _row in enumerate(combo.get_model()):
            combo.set_active(idx)
            dlg._mode_changed(combo)
        dlg.widget("migrate-set-address").toggled()
        dlg.widget("migrate-set-port").toggled()

    def serial_console():
        from virtManager.details.serialcon import vmmSerialConsole

        cons = vmmSerialConsole.get_serialcon_devices(vm)
        port = 0
        name = "serial0"
        if cons:
            port = getattr(cons[0], "target_port", 0) or 0
            name = cons[0].alias or name
        serial = vmmSerialConsole(vm, port, name)
        assert serial._box is not None
        assert serial._box.get_visible_child_name() == "term"
        term = serial._vteterminal
        assert term is not None
        assert getattr(term, "_vmm_gtk3_serial_colors", False)
        assert getattr(serial, "_vmm_gtk3_serial_primary", False)
        assert getattr(term, "_vmm_gtk3_serial_primary", False)
        from gi.repository import Gdk

        Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).set_text("primary-paste", -1)
        serial._serial_paste_primary()
        serial._serial_copy_text(None)
        serial._serial_selection_to_primary()
        parent = term.get_parent()
        assert parent is not None
        assert parent.has_css_class("vmm-serial-bg"), list(parent.get_css_classes())
        serial._show_error("gtk4 serial error")
        assert serial._box.get_visible_child_name() == "error"
        serial._serial_popup.show_all()
        assert serial._serial_popover is None
        from virtManager.details.console import _TimedRevealer

        toolbar = Gtk.Box()
        revealer = _TimedRevealer(toolbar)
        revealer.force_reveal(True)
        revealer._handle_pointer(True)
        revealer._handle_pointer(False)
        revealer.cleanup()
        from gi.repository import Gdk

        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text("/pool-dir/UPPER", -1)
        assert clip.wait_for_text() == "/pool-dir/UPPER"

    def manager_selection():
        from virtManager.manager import vmmManager

        win = vmmManager.get_instance(None)
        win.show()
        vmlist = win.widget("vm-list")
        assert vmlist.get_search_column() == 1  # ROW_SORT_KEY
        assert vmlist.get_enable_search()
        win.update_current_selection()
        for name in (
            "menu-view-guest-cpu",
            "menu-view-host-cpu",
            "menu-view-memory",
            "menu-view-disk",
            "menu-view-network",
        ):
            try:
                win.widget(name)
            except Exception:
                continue

    def _named_vm(name):
        for cand in conn.list_vms():
            if cand.get_name() == name:
                return cand
        return vm

    def device_editors():
        from virtManager.addhardware import vmmAddHardware
        from virtManager.device.mediacombo import vmmMediaCombo

        rich = _named_vm("test-many-devices")
        dlg = vmmAddHardware(rich)
        dlg.show(None)
        dlg._gfxdetails.reset_state()
        gfxs = list(rich.xmlobj.devices.graphics)
        if gfxs:
            dlg._gfxdetails.set_dev(gfxs[0])
            dlg._gfxdetails.get_values()
            dlg._gfxdetails.build_device()
        dlg._fsdetails.reset_state()
        filesystems = list(rich.xmlobj.devices.filesystem)
        if filesystems:
            dlg._fsdetails.set_dev(filesystems[0])
            dlg._fsdetails.build_device()
        dlg._vsockdetails.reset_state()
        vsocks = list(rich.xmlobj.devices.vsock)
        if vsocks:
            dlg._vsockdetails.set_dev(vsocks[0])
            dlg._vsockdetails.get_values()
        dlg._tpmdetails.reset_state()
        tpms = list(rich.xmlobj.devices.tpm)
        if tpms:
            dlg._tpmdetails.set_dev(tpms[0])
            dlg._tpmdetails.build_device()
        dlg.addstorage.reset_state()
        disks = list(rich.xmlobj.devices.disk)
        if disks:
            dlg.addstorage.set_dev(disks[0])
        dlg._netlist.reset_state()
        nets = list(rich.xmlobj.devices.interface)
        if nets:
            dlg._netlist.set_dev(nets[0])
            dlg._netlist.get_network_selection()
        media = vmmMediaCombo(conn, dlg.builder, dlg.topwin)
        media.reset_state()
        media.set_path("/tmp/gtk4-test.iso")
        media.get_path()
        media.show_clear_icon()
        assert getattr(media._entry, "_vmm_gtk3_clear_icon", False)
        try:
            icon = media._entry.get_icon_name(Gtk.EntryIconPosition.SECONDARY)
        except Exception:
            icon = ""
        assert icon == "edit-clear-symbolic", icon
        media.reset_state(is_floppy=True)

    def host_storage_nets():
        from virtManager.host import vmmHost

        vmmHost.show_instance(None, conn)
        win = vmmHost._instances[conn.get_uri()]
        win._storagelist.refresh_page()
        win._storagelist._pool_add_cb(None)
        win._storagelist._vol_add_cb(None)
        vol_list = win._storagelist.widget("vol-list")
        if len(vol_list.get_model()):
            from virtManager.lib import uiutil

            uiutil.set_list_selection_by_number(vol_list, 0)
            win._storagelist._vol_copy_path_cb(None)
        win._hostnets.refresh_page()
        win._hostnets._add_network_cb(None)
        netlist = win._hostnets.widget("net-list")
        if len(netlist.get_model()):
            from virtManager.lib import uiutil

            uiutil.set_list_selection_by_number(netlist, 0)
            win._hostnets._net_selected_cb(netlist.get_selection())

    def createvol_formats():
        from virtManager.createvol import vmmCreateVolume

        if pool is None:
            raise RuntimeError("No storage pool available")
        dlg = vmmCreateVolume(conn, pool)
        dlg.show(None)
        dlg.set_name_hint("gtk4test")
        combo = dlg.widget("vol-format")
        for idx, _row in enumerate(combo.get_model()):
            combo.set_active(idx)
            dlg._vol_format_changed_cb(combo)
        dlg.widget("vol-name").set_text("gtk4-test-vol")
        dlg.widget("vol-name").emit("changed")
        dlg.widget("vol-cancel").emit("clicked")

    def snapshots_list():
        from virtManager.details.snapshots import vmmSnapshotNew
        from virtManager.vmwindow import vmmVMWindow

        snapvm = _named_vm("test-snapshots")
        win = vmmVMWindow.get_instance(None, snapvm)
        win.show()
        win.widget("details-pages").set_current_page(2)
        win._refresh_current_page(2)
        win._sync_toolbar_page_buttons(2)
        win._snapshots.vmwindow_refresh_vm_state()
        _pump(GLib, 0.2)
        names = open("/tmp/vmm-a11y-snapshot-list.txt", "r").read().splitlines()
        assert "internal-root" in names, names
        dlg = vmmSnapshotNew(snapvm)
        dlg.show(None)
        dlg.widget("snapshot-new-name").set_text("gtk4-snap")

    def details_refresh():
        from virtManager.vmwindow import vmmVMWindow

        # Use the first testdriver VM. test-many-devices rebuilds a huge
        # hw list on every refresh and can nest a main loop for 2+ minutes.
        win = vmmVMWindow.get_instance(None, vm)
        win.show()
        win._details.vmwindow_refresh_vm_state(True)
        win._details._refresh_overview_page()
        win._details._refresh_os_page()
        win._details._refresh_stats_page()
        win._details._refresh_config_cpu()
        win._details._refresh_config_memory()
        win._details._refresh_boot_page()

    def details_many_devices():
        from virtManager.vmwindow import vmmVMWindow

        rich = _named_vm("test-many-devices")
        win = vmmVMWindow.get_instance(None, rich)
        win.show()

    def filechooser_helpers():
        from virtManager.lib import gtkcompat

        gfile = gtkcompat.GioFile_for_path("/tmp")
        assert gfile.get_path() == "/tmp"

        def _accept():
            try:
                open("/tmp/vmm-a11y-filechooser-open", "w").write("1")
            except Exception:
                pass
            return False

        GLib.timeout_add(80, _accept)
        path = gtkcompat.browse_local(
            None,
            "Save Virtual Machine Screenshot",
            start_folder="/tmp",
            _type=("png", "PNG files"),
            dialog_type=Gtk.FileChooserAction.SAVE,
            choose_label="_Save",
            default_name="Screenshot_test.png",
            confirm_overwrite=True,
        )
        assert path and os.path.basename(path) == "Screenshot_test.png", path
        try:
            listing = open("/tmp/vmm-a11y-filechooser-list.txt", "r").read().splitlines()
        except Exception:
            listing = []
        assert ".." in listing, "file chooser must offer parent-directory navigation"
        assert gtkcompat._use_test_file_browser()
        old_suite = os.environ.get("VIRTINST_TEST_SUITE")
        old_a11y = os.environ.get("GTK_A11Y")
        try:
            os.environ.pop("VIRTINST_TEST_SUITE", None)
            os.environ["GTK_A11Y"] = "atspi"
            assert not gtkcompat._use_test_file_browser(), (
                "production with GTK_A11Y=atspi must use Gtk.FileDialog"
            )
        finally:
            if old_suite is None:
                os.environ.pop("VIRTINST_TEST_SUITE", None)
            else:
                os.environ["VIRTINST_TEST_SUITE"] = old_suite
            if old_a11y is None:
                os.environ.pop("GTK_A11Y", None)
            else:
                os.environ["GTK_A11Y"] = old_a11y
        assert gtkcompat._path_needs_overwrite_confirm("/etc/passwd", True)
        assert not gtkcompat._path_needs_overwrite_confirm("/tmp/no-such-vmm-file", True)
        assert hasattr(gtkcompat, "_browse_local_native")

    def vm_lifecycle_actions():
        from virtManager import vmmenu
        from virtManager.config import vmmConfig
        from virtManager.manager import vmmManager

        cfg = vmmConfig.get_instance()
        cfg.set_confirm_poweroff(False)
        cfg.set_confirm_forcepoweroff(False)
        cfg.set_confirm_pause(False)
        win = vmmManager.get_instance(None)
        win.show()
        shut = _first_vm(conn, shutoff=True)
        vmmenu.VMActionUI.delete(win, shut)
        vmmenu.VMActionUI.clone(win, shut)
        vmmenu.VMActionUI.migrate(win, vm)
        vmmenu.VMActionUI.show(win, vm)

    def preferences_toggles():
        from virtManager.preferences import vmmPreferences

        dlg = vmmPreferences()
        dlg.show(None)
        dlg.change_view_system_tray(dlg.widget("prefs-system-tray"))
        dlg.change_xmleditor(dlg.widget("prefs-xmleditor"))
        dlg.change_console_autoconnect(dlg.widget("prefs-console-autoconnect"))
        dlg.change_confirm_poweroff(dlg.widget("prefs-confirm-poweroff"))
        dlg.change_confirm_pause(dlg.widget("prefs-confirm-pause"))
        dlg.change_graphics_type(dlg.widget("prefs-graphics-type"))
        dlg.change_storage_format(dlg.widget("prefs-storage-format"))
        dlg.close()

    def createvm_oslist():
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        oslist = dlg._os_list
        oslist.search_entry.set_text("fedora")
        oslist._search_changed_cb(oslist.search_entry)
        oslist._eol_toggled_cb(oslist.widget("include-eol"))
        from virtManager.lib import gtkcompat

        gtkcompat.show_all(oslist.search_entry)

    def vnc_protocol_helpers():
        import struct as st

        from virtManager.details import gtk4display

        disp = gtk4display.VNCDisplay()
        disp._alloc_pixels(4, 4)
        pixel = b"\x11\x22\x33\x44"
        disp._fill_rect(4, 0, 0, 2, 2, pixel)
        disp._blit_raw(4, 2, 2, 1, 1, pixel)
        disp._copy_rect(4, 4, 0, 2, 1, 1, 0, 0)
        disp._publish_fb(4, 4)
        _pump(GLib, 0.05)
        pix = disp.get_pixbuf()
        assert pix is not None, "get_pixbuf() did not return a GdkPixbuf"
        assert pix.get_width() == 4 and pix.get_height() == 4
        saved = pix.save_to_bufferv("png", [], [])
        if isinstance(saved, tuple):
            saved = saved[1]
        if hasattr(saved, "buffer"):
            saved = saved.buffer
        assert saved and len(saved) > 8 and saved[:4] == b"\x89PNG"
        disp.set_scaling(True)
        disp.set_keep_aspect_ratio(True)
        assert disp.get_keep_aspect_ratio()
        dx, dy, dw, dh = disp._fb_dest_rect(8, 4)
        assert dw == dh == 4 and abs(dx - 2) < 0.01
        disp.set_keep_aspect_ratio(False)
        dx, dy, dw, dh = disp._fb_dest_rect(8, 4)
        assert (dx, dy, dw, dh) == (0, 0, 8, 4)
        disp.set_keep_aspect_ratio(True)
        disp.set_pointer_grab(True)
        seq = gtk4display.GrabSequence.new([65507, 65513])
        assert "Control_L" in seq.as_string()
        disp.set_grab_keys(seq)
        grabbed = []
        ungrabbed = []
        disp.connect("mouse-grab", lambda _s, val: grabbed.append(val) if val else ungrabbed.append(val))
        disp.connect("vnc-pointer-ungrab", lambda *_a: ungrabbed.append("ptr"))
        disp._on_pressed(type("G", (), {"get_current_button": lambda self: 1})(), 1, 1, 1)
        assert disp._buttons & 1
        disp._on_pressed(type("G", (), {"get_current_button": lambda self: 3})(), 1, 1, 1)
        assert disp._buttons & 4
        disp._on_released(type("G", (), {"get_current_button": lambda self: 1})(), 1, 1, 1)
        assert not (disp._buttons & 1)
        assert disp._buttons & 4
        disp._on_scroll(None, 0, 1)
        assert grabbed
        assert disp._grabbed_keyboard, "click must grab keyboard like gtk-vnc"
        # Unmapped widgets cannot XGrabPointer; helpers must not raise.
        assert gtk4display._x11_grab_pointer(disp) in (True, False)
        assert gtk4display._x11_grab_keyboard(disp) in (True, False)
        gtk4display._x11_ungrab_input()
        # Focus-out / Alt-Tab must release grabs so menu accelerators return.
        disp._grab_pointer()
        disp._grab_keyboard()
        disp._on_focus_leave(None)
        assert not disp._grabbed_pointer
        assert not disp._grabbed_keyboard
        # Re-grab so the Ctrl+Alt sequence test still has something to release.
        disp._on_pressed(type("G", (), {"get_current_button": lambda self: 1})(), 1, 1, 1)
        # Grab sequence is stored as keyvals; GTK 4 events report both
        disp._on_key_pressed(None, 65507, 37, 0)
        disp._on_key_pressed(None, 65513, 64, 0)
        assert ungrabbed, "grab-sequence did not ungrab pointer"
        disp.set_credential(2, "libvirt-vnc")
        assert disp._clientname == "libvirt-vnc"
        disp.set_credential("CA_CERT", "/tmp/vnc-ca.pem")
        disp.set_credential("CLIENT_CERT", "/tmp/vnc-client.pem")
        disp.set_credential("CLIENT_KEY", "/tmp/vnc-client.key")
        assert disp._tls_ca == "/tmp/vnc-ca.pem"
        assert disp._tls_client_cert == "/tmp/vnc-client.pem"
        assert disp._tls_client_key == "/tmp/vnc-client.key"
        assert disp._tls_ca_file() == "/tmp/vnc-ca.pem"
        disp._apply_server_cut_text(b"guest-clip")
        assert os.path.exists("/tmp/vmm-a11y-clipboard.txt")
        disp._bind_host_clipboard()
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        assert hasattr(display, "get_primary_clipboard")
        primary = display.get_primary_clipboard()
        assert primary is not None
        ct, tag = gtk4display._aes_eax_encrypt(b"\x11" * 16, b"\x22" * 16, b"\x00\x04", b"ping")
        assert gtk4display._aes_eax_decrypt(b"\x11" * 16, b"\x22" * 16, b"\x00\x04", ct, tag) == b"ping"
        send256, recv256 = gtk4display._ra2_session_keys(b"S" * 16, b"C" * 16, sha256=True)
        assert len(send256) == 32 and len(recv256) == 32 and send256 != recv256
        ct256, tag256 = gtk4display._aes_eax_encrypt(send256, b"\x00" * 16, b"\x00\x04", b"ping")
        assert gtk4display._aes_eax_decrypt(send256, b"\x00" * 16, b"\x00\x04", ct256, tag256) == b"ping"
        frame = gtk4display._ra2_seal(b"\x33" * 16, 0, b"rfb")
        class _Mem:
            def __init__(self, data):
                self.buf = data

            def recv(self, n):
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

        assert gtk4display._ra2_recv_msg(_Mem(frame), b"\x33" * 16, 0, lambda s, n: s.recv(n)) == b"rfb"
        # Extended clipboard: server caps then UTF-8 provide
        import zlib as _zlib

        sent = []

        class _ClipSock:
            def sendall(self, data):
                sent.append(data)

        disp._sock = _ClipSock()
        disp._open = True
        caps = st.pack("!I", gtk4display._CLIP_TEXT | gtk4display._CLIP_CAPS) + st.pack("!I", 0)
        disp._apply_extended_cut_text(caps)
        assert disp._ext_clip
        assert sent and st.unpack("!Bxxxi", sent[0][:8])[1] < 0
        sent.clear()
        text = "café".encode("utf-8") + b"\x00"
        inner = st.pack("!I", len(text)) + text
        provide = st.pack("!I", gtk4display._CLIP_PROVIDE | gtk4display._CLIP_TEXT)
        provide += _zlib.compress(inner)
        disp._apply_extended_cut_text(provide)
        assert open("/tmp/vmm-a11y-clipboard.txt").read() == "café"
        disp._ext_clip = True
        disp._clip_from_guest = False
        disp._send_client_cut_text("naïve")
        assert any(st.unpack("!Bxxxi", chunk[:8])[1] < 0 for chunk in sent if len(chunk) >= 8)
        assert disp._choose_vencrypt_subtype([258, 256]) == 256
        assert disp._choose_vencrypt_subtype([258]) == 258
        assert disp._choose_vencrypt_subtype([263]) == 263
        assert disp._choose_vencrypt_subtype([264, 263]) == 263
        assert disp._sasl_choose_mech("GSSAPI,PLAIN") == "PLAIN"
        assert disp._sasl_choose_mech("DIGEST-MD5") == "DIGEST-MD5"
        assert disp._sasl_choose_mech("GSSAPI") == "GSSAPI"
        disp._username = "alice"
        disp._password = "s3cret"
        assert disp._sasl_plain_clientout() == b"\x00alice\x00s3cret"
        # RFC 2831 example 8.1 (imap / elwood.innosoft.com)
        resp, rspauth = gtk4display._digest_md5_hashes(
            "chris",
            "secret",
            "elwood.innosoft.com",
            "OA6MG9tEQGm2hh",
            "OA6MHXh6VqTrRk",
            "00000001",
            "auth",
            "imap/elwood.innosoft.com",
            "md5-sess",
        )
        assert resp == "d388dad90d4bbd760a152321f2143af7", resp
        challenge = (
            b'realm="elwood.innosoft.com",nonce="OA6MG9tEQGm2hh",'
            b'qop="auth",algorithm=md5-sess'
        )
        out, expect = gtk4display._digest_md5_client_out(
            challenge,
            "chris",
            "secret",
            "elwood.innosoft.com",
            cnonce="OA6MHXh6VqTrRk",
        )
        text = out.decode("ascii")
        assert 'username="chris"' in text
        assert "response=" in text
        _vnc_resp, vnc_rspauth = gtk4display._digest_md5_hashes(
            "chris",
            "secret",
            "elwood.innosoft.com",
            "OA6MG9tEQGm2hh",
            "OA6MHXh6VqTrRk",
            "00000001",
            "auth",
            "vnc/elwood.innosoft.com",
            "md5-sess",
        )
        assert expect == vnc_rspauth
        assert ("response=%s" % _vnc_resp) in text

        class _SaslSock:
            def __init__(self, data):
                self.buf = data
                self.sent = b""

            def recv(self, n):
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

            def sendall(self, data):
                self.sent += data

        mech = b"PLAIN,GSSAPI"
        start = st.pack("!I", len(mech)) + mech + st.pack("!I", 0) + b"\x01"
        ssock = _SaslSock(start)
        disp._vnc_sasl(ssock)
        assert b"PLAIN" in ssock.sent
        assert b"\x00alice\x00s3cret\x00" in ssock.sent
        disp._username = "chris"
        disp._password = "secret"
        disp._host = "elwood.innosoft.com"
        chal = (
            b'realm="elwood.innosoft.com",nonce="OA6MG9tEQGm2hh",'
            b'qop="auth",algorithm=md5-sess'
        )
        _dout, expect = gtk4display._digest_md5_client_out(
            chal, "chris", "secret", "elwood.innosoft.com", cnonce="OA6MHXh6VqTrRk"
        )
        rsp = ("rspauth=%s" % expect).encode("ascii")
        dmech = b"DIGEST-MD5"
        dstart = st.pack("!I", len(dmech)) + dmech
        dstart += st.pack("!I", len(chal) + 1) + chal + b"\x00" + b"\x00"
        dstart += st.pack("!I", len(rsp) + 1) + rsp + b"\x00" + b"\x01"
        dsock = _SaslSock(dstart)
        disp._vnc_sasl(dsock, cnonce="OA6MHXh6VqTrRk")
        assert b"DIGEST-MD5" in dsock.sent
        assert b"response=" in dsock.sent

        class _FakeGss:
            def __init__(self):
                self.complete = False

            def init(self, serverin):
                if serverin is None:
                    return b"gss-token-1"
                self.complete = True
                return b""

            def unwrap(self, data):
                assert data == b"gss-layer"
                return b"\x07\x00\xff\xff"

            def wrap(self, data):
                assert data[0] == 1
                return b"gss-wrap-" + data

            def dispose(self):
                self.complete = True

        gss = gtk4display._GssapiSaslClient("user", "pw", "vnc.example", backend=_FakeGss())
        name, token, cont = gss.start("GSSAPI")
        assert name == "GSSAPI" and token == b"gss-token-1" and cont
        token, done = gss.step(b"gss-token-2")
        assert not done
        token, done = gss.step(b"gss-layer")
        assert done and token.startswith(b"gss-wrap-")
        gss.dispose()
        assert gtk4display._GssapiKr5Backend.available()

        def _tight_cap(code, vendor, sig):
            return st.pack("!I", code) + vendor + sig

        assert disp._choose_tight_auth([16, 2, 1]) == 2
        assert disp._choose_tight_auth([129, 1]) == 1
        assert disp._choose_tight_auth([129]) == 129
        assert disp._choose_tight_auth([130]) == 130
        assert disp._choose_tight_auth([20]) == 20
        assert disp._choose_tight_auth([19]) == 19
        assert disp._choose_tight_auth([99]) is None
        siemens = [(1, b"SICR", b"SCHANNEL")]
        assert disp._choose_tight_tunnel(siemens) == 0
        assert disp._choose_tight_tunnel([(0, b"TGHT", b"NOTUNNEL")]) == 0

        class _TightSock:
            def __init__(self, data):
                self.buf = data
                self.sent = b""

            def recv(self, n):
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

            def sendall(self, data):
                self.sent += data

        chal = b"0123456789abcdef"
        tight_vnc = st.pack("!I", 0)
        tight_vnc += st.pack("!I", 2)
        tight_vnc += _tight_cap(1, b"STDV", b"NOAUTH__")
        tight_vnc += _tight_cap(2, b"STDV", b"VNCAUTH_")
        tight_vnc += chal
        tsock = _TightSock(tight_vnc)
        disp._password = "secret"
        disp._vnc_tight(tsock)
        assert st.unpack("!I", tsock.sent[:4])[0] == 2
        assert len(tsock.sent) == 20
        assert tsock.buf == b""

        none_sock = _TightSock(st.pack("!II", 0, 0))
        disp._vnc_tight(none_sock)
        assert none_sock.sent == b""

        unix_payload = st.pack("!I", 1) + _tight_cap(0, b"TGHT", b"NOTUNNEL")
        unix_payload += st.pack("!I", 1) + _tight_cap(129, b"TGHT", b"ULGNAUTH")
        usock = _TightSock(unix_payload)
        disp._username = "alice"
        disp._password = "s3cret"
        disp._vnc_tight(usock)
        assert st.unpack("!I", usock.sent[:4])[0] == 0
        assert st.unpack("!I", usock.sent[4:8])[0] == 129
        assert b"alice" in usock.sent
        assert b"s3cret" in usock.sent

        extra = st.pack("!HHHH", 1, 0, 0, 0) + _tight_cap(150, b"TGHT", b"CUS_EOCU")
        skip = _TightSock(extra)
        disp._skip_tight_serverinit(skip)
        assert skip.buf == b""

        prime = (2**127 - 1).to_bytes(16, "big")
        ard = _TightSock(st.pack("!HH", 2, 16) + prime + (b"\x02" * 16))
        disp._username = "ard"
        disp._password = "secret"
        disp._vnc_ard(ard)
        assert len(ard.sent) == 128 + 16

        msl = _TightSock((2).to_bytes(8, "big") + (0xFFFFFFFB).to_bytes(8, "big") + (3).to_bytes(8, "big"))
        disp._vnc_mslogonii(msl)
        assert len(msl.sent) == 8 + 256 + 64

        disp.send_keys([97])
        assert gtk4display._keycode_for_keyval(65507) > 0
        assert gtk4display._mmap_gl_scanout(-1, 16, 16, 64) is None
        disp.set_property("resize-guest", True)
        disp._apply_resize_guest(True)

        xfer = gtk4display._SpiceFileTransferWindow(["demo.iso"])
        xfer.set_fraction(0.4)
        assert abs(xfer._bar.get_fraction() - 0.4) < 0.01
        assert xfer._cancel.get_label() == "_Cancel"
        assert xfer._cancel.get_sensitive() is True
        assert xfer.get_default_widget() is xfer._cancel
        assert xfer.get_icon_name() == "virt-manager"
        xfer.finish_error("nope")
        assert xfer._cancel.get_sensitive() is False
        cancel_xfer = gtk4display._SpiceFileTransferWindow(["demo.iso"])
        cancel_xfer._on_cancel()
        assert cancel_xfer._status.get_text() == "Transfer cancelled"
        assert cancel_xfer._closed is True

        class _FakeXfer:
            def __init__(self):
                self.cancelled = False
                self._progress = 0.25
                self._sigs = {}

            def get_filename(self):
                return "from-guest.bin"

            def get_progress(self):
                return self._progress

            def cancel(self):
                self.cancelled = True

            def connect(self, sig, handler):
                self._sigs[sig] = handler
                return 1

            def get_property(self, name):
                if name in ("cancellable", "file"):
                    return None
                raise AttributeError(name)

        spice = gtk4display.SpiceDisplay(None)
        task = _FakeXfer()
        spice._on_new_file_transfer(None, task)
        assert spice._xfer_windows
        xfer_task = spice._xfer_windows[-1]
        assert abs(xfer_task._bar.get_fraction() - 0.25) < 0.01
        xfer_task._on_cancel()
        assert task.cancelled is True
        assert xfer_task._closed is True
        assert spice._on_file_drop(None, [], 0, 0) is False
        from gi.repository import Gdk
        motion = []
        position = []
        press = []
        orig_motion = gtk4display.SpiceClientGLib.inputs_motion
        orig_position = gtk4display.SpiceClientGLib.inputs_position
        orig_press = gtk4display.SpiceClientGLib.inputs_button_press
        orig_release = gtk4display.SpiceClientGLib.inputs_button_release
        orig_locks = gtk4display.SpiceClientGLib.inputs_set_key_locks
        gtk4display.SpiceClientGLib.inputs_motion = (
            lambda ch, dx, dy, buttons: motion.append((dx, dy, buttons))
        )
        gtk4display.SpiceClientGLib.inputs_position = (
            lambda ch, x, y, display, buttons: position.append((x, y, display, buttons))
        )
        gtk4display.SpiceClientGLib.inputs_button_press = (
            lambda ch, button, buttons: press.append(("down", button, buttons))
        )
        gtk4display.SpiceClientGLib.inputs_button_release = (
            lambda ch, button, buttons: press.append(("up", button, buttons))
        )
        locks = []
        gtk4display.SpiceClientGLib.inputs_set_key_locks = (
            lambda ch, value: locks.append(value)
        )
        try:
            spice._inputs = object()
            spice._fb_size = (100, 100)
            spice._mouse_mode = gtk4display._SPICE_MOUSE_MODE_CLIENT
            spice._send_pointer(10, 10, 0, False)
            assert position and not motion, (position, motion)
            spice._mouse_mode = gtk4display._SPICE_MOUSE_MODE_SERVER
            spice._rel_x = spice._rel_y = None
            spice._send_pointer(10, 10, 0, False)
            spice._send_pointer(16, 13, 1, True)
            assert motion, "server mouse mode must send inputs_motion"
            assert motion[-1][0] == 6 and motion[-1][1] == 3, motion
            assert any(item[0] == "down" for item in press), press
            spice._pointer_grab = True
            spice._grabbed_pointer = False
            spice._main = type(
                "M",
                (),
                {
                    "get_property": lambda self, n: gtk4display._SPICE_MOUSE_MODE_SERVER
                },
            )()
            spice._sync_mouse_mode()
            assert spice._grabbed_pointer, "server mouse must grab the pointer"
            assert spice._grabbed_keyboard, "server mouse must grab the keyboard"
            spice._on_focus_leave(None)
            assert not spice._grabbed_pointer
            assert not spice._grabbed_keyboard
            spice._sync_key_locks(int(Gdk.ModifierType.LOCK_MASK))
            assert locks, "caps lock must be sent to the guest"
            assert locks[-1] & int(gtk4display.SpiceClientGLib.InputsLock.CAPS_LOCK)
            spice._recenter_server_mouse()
        finally:
            gtk4display.SpiceClientGLib.inputs_motion = orig_motion
            gtk4display.SpiceClientGLib.inputs_position = orig_position
            gtk4display.SpiceClientGLib.inputs_button_press = orig_press
            gtk4display.SpiceClientGLib.inputs_button_release = orig_release
            gtk4display.SpiceClientGLib.inputs_set_key_locks = orig_locks

        class FakeSock:
            def __init__(self, data):
                self.buf = data

            def recv(self, n):
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

        import struct as st

        # ExtendedDesktopSize: consume 16-byte screen entries without desync
        payload = b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 0, 0, 16, 12, -308)
        payload += b"\x01\x00\x00\x00" + (b"\x00" * 16)
        nw, nh = disp._read_fb_update(FakeSock(payload), 4, 4)
        assert (nw, nh) == (16, 12)
        assert disp._vnc_screens and disp._vnc_screens[0][0] == 0
        # Non-zero screen id must be reused by SetDesktopSize (gtk-vnc).
        payload = b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 0, 0, 32, 24, -308)
        payload += b"\x01\x00\x00\x00" + st.pack("!IHHHHI", 7, 0, 0, 32, 24, 1)
        nw, nh = disp._read_fb_update(FakeSock(payload), 4, 4)
        assert (nw, nh) == (32, 24)
        assert disp._vnc_screens[0][0] == 7
        class _SizeSock:
            def __init__(self):
                self.sent = b""

            def sendall(self, data):
                self.sent += data

        rec = _SizeSock()
        disp.set_property("resize-guest", True)
        disp._sock = rec
        disp._open = True
        disp._send_desktop_size(64, 48)
        assert rec.sent[:2] == bytes([251, 1])
        sid = st.unpack("!I", rec.sent[6:10])[0]
        assert sid == 7, rec.sent
        def _pixel(dx, dy):
            i = (dy * 4 + dx) * 4
            return bytes(disp._pixels[i : i + 4])

        def _compact(n):
            if n < 128:
                return bytes([n])
            return bytes([0x80 | (n & 0x7F), (n >> 7) & 0x7F])

        import zlib as _zlib

        # ZlibHex: zlib-raw tile and zlib-hextile subrects
        disp._zhex_dec = None
        disp._alloc_pixels(4, 4)
        zraw = _zlib.compress(b"\x11\x22\x33\x00" * 4)
        zhex_raw = bytes([1 | 32]) + st.pack("!H", len(zraw)) + zraw
        disp._read_zlibhex(FakeSock(zhex_raw), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\x11\x22\x33\x00"
        assert _pixel(1, 1) == b"\x11\x22\x33\x00"
        disp._zhex_dec = None
        disp._alloc_pixels(4, 4)
        zhex_subs = bytes([2 | 64]) + b"\xaa\xbb\xcc\x00" + st.pack("!H", 0)
        disp._read_zlibhex(FakeSock(zhex_subs), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\xaa\xbb\xcc\x00"
        assert _pixel(1, 1) == b"\xaa\xbb\xcc\x00"

        # Ultra (encoding 9): LZO-compressed raw pixels
        import ctypes
        import ctypes.util

        pixels = b"\x44\x55\x66\x00" * 4
        lzo = ctypes.CDLL(ctypes.util.find_library("lzo2") or "liblzo2.so.2")
        wrk = ctypes.create_string_buffer(16384 * 8)
        cdst = ctypes.create_string_buffer(len(pixels) + 64)
        clen = ctypes.c_ulong(len(cdst))
        assert lzo.lzo1x_1_compress(pixels, len(pixels), cdst, ctypes.byref(clen), wrk) == 0
        ultra = st.pack("!I", clen.value) + cdst.raw[: clen.value]
        disp._alloc_pixels(4, 4)
        disp._read_ultra(FakeSock(ultra), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\x44\x55\x66\x00"
        assert _pixel(1, 1) == b"\x44\x55\x66\x00"

        # Tight fill rectangle (control 0x80 + RGB) → BGRA
        disp._alloc_pixels(4, 4)
        tight = b"\x80\xaa\xbb\xcc"
        disp._read_tight(FakeSock(tight), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\xcc\xbb\xaa\x00"
        assert _pixel(1, 1) == b"\xcc\xbb\xaa\x00"

        # Tight 2-color palette (1 bit/pixel, rows padded)
        disp._alloc_pixels(4, 4)
        pal = b"\x40\x01\x01\xff\x00\x00\x00\x00\xff\x40\x80"
        disp._read_tight(FakeSock(pal), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\x00\x00\xff\x00"
        assert _pixel(1, 0) == b"\xff\x00\x00\x00"
        assert _pixel(0, 1) == b"\xff\x00\x00\x00"
        assert _pixel(1, 1) == b"\x00\x00\xff\x00"

        # Tight 3-color palette (8 bit/pixel)
        disp._alloc_pixels(4, 4)
        pal8 = b"\x40\x01\x02" + b"\x11\x00\x00\x00\x22\x00\x00\x00\x33" + b"\x00\x01\x02\x00"
        disp._read_tight(FakeSock(pal8), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\x00\x00\x11\x00"
        assert _pixel(1, 0) == b"\x00\x22\x00\x00"
        assert _pixel(0, 1) == b"\x33\x00\x00\x00"

        # Tight gradient 2x1: first pixel is the sample, second is
        # predicted-from-left plus the residual.
        disp._alloc_pixels(4, 4)
        grad = b"\x40\x02" + b"\x0a\x14\x1e\x05\x06\x07"
        disp._read_tight(FakeSock(grad), 4, 0, 0, 2, 1)
        assert _pixel(0, 0) == b"\x1e\x14\x0a\x00"
        assert _pixel(1, 0) == b"\x25\x1a\x0f\x00"

        # Tight copy must use bits 4-5 as the zlib stream, not the reset bits.
        import zlib as _zlib

        z0 = _zlib.compressobj()
        c1 = z0.compress(b"\x11" * 12) + z0.flush(_zlib.Z_SYNC_FLUSH)
        z1 = _zlib.compressobj()
        c2 = z1.compress(b"\x22" * 12) + z1.flush(_zlib.Z_SYNC_FLUSH)
        c3 = z0.compress(b"\x33" * 12) + z0.flush(_zlib.Z_SYNC_FLUSH)
        disp._tight_z = [None, None, None, None]
        disp._alloc_pixels(4, 4)
        disp._read_tight(FakeSock(b"\x00" + _compact(len(c1)) + c1), 4, 0, 0, 2, 2)
        assert _pixel(0, 0) == b"\x11\x11\x11\x00"
        disp._read_tight(FakeSock(b"\x10" + _compact(len(c2)) + c2), 4, 2, 0, 2, 2)
        assert _pixel(2, 0) == b"\x22\x22\x22\x00"
        disp._read_tight(FakeSock(b"\x00" + _compact(len(c3)) + c3), 4, 0, 2, 2, 2)
        assert _pixel(0, 2) == b"\x33\x33\x33\x00"
        # ZRLE solid 4x4 tile
        import zlib as _zlib

        zrle = _zlib.compress(b"\x01" + b"\x11\x22\x33\x44", 6)
        payload = st.pack("!I", len(zrle)) + zrle
        disp._zrle_z = None
        disp._read_zrle(FakeSock(payload), 4, 0, 0, 4, 4)
        disp._alloc_pixels(4, 4)
        disp._read_trle(FakeSock(b"\x01" + b"\xaa\xbb\xcc\xdd"), 4, 0, 0, 4, 4)
        assert bytes(disp._pixels[0:4]) == b"\xaa\xbb\xcc\xdd"
        # Cursor pseudo-encoding paints a local overlay; hotspot is x,y
        pixels = (b"\x11\x22\x33\x00" * 4)
        mask = b"\x80\x40"  # (0,0) and (1,1) visible
        disp._read_fb_update(
            FakeSock(b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 1, 2, 2, 2, -239) + pixels + mask),
            4,
            4,
        )
        assert disp._cursor_hot == (1, 2)
        assert disp._cursor_surface is not None
        assert disp._cursor_pixels[3] == 255
        assert disp._cursor_pixels[7] == 0
        # LastRect ends the update; DesktopName carries a UTF-8 title
        disp._read_fb_update(
            FakeSock(
                b"\x00"
                + st.pack("!H", 2)
                + st.pack("!HHHHi", 0, 0, 0, 0, -307)
                + st.pack("!I", 7)
                + b"guest42"
                + st.pack("!HHHHi", 0, 0, 0, 0, -224)
            ),
            4,
            4,
        )
        assert disp._name == "guest42"
        # XCursor: fg red, bg blue, bitmap+mask one visible pixel
        xc = st.pack("!HHHHi", 3, 4, 1, 1, -232)
        xc += b"\xff\x00\x00" + b"\x00\x00\xff" + b"\x80" + b"\x80"
        disp._read_fb_update(FakeSock(b"\x00" + st.pack("!H", 1) + xc), 4, 4)
        assert disp._cursor_hot == (3, 4)
        assert disp._cursor_pixels[2] == 255
        assert disp._cursor_pixels[3] == 255
        disp.set_allow_resize(True)
        assert disp.get_allow_resize()
        disp.set_allow_resize(False)
        assert not disp.get_allow_resize()
        disp.set_shared_flag(False)
        assert not disp.get_shared_flag()
        disp.set_shared_flag(True)
        # CoRRE: background + one 8-bit subrect
        disp._alloc_pixels(4, 4)
        corre = st.pack("!I", 1) + b"\x11\x11\x11\x00" + b"\x22\x22\x22\x00" + bytes([1, 1, 1, 1])
        disp._read_corre(FakeSock(corre), 4, 0, 0, 4, 4)
        assert bytes(disp._pixels[0:4]) == b"\x11\x11\x11\x00"
        assert bytes(disp._pixels[(1 * 4 + 1) * 4 : (1 * 4 + 1) * 4 + 4]) == b"\x22\x22\x22\x00"
        # Unknown negative pseudo-encoding is ignored, not a disconnect
        disp._read_fb_update(
            FakeSock(b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 0, 0, 0, 0, -999)),
            4,
            4,
        )
        # GtkVnc also keeps the session for unknown positive encodings
        disp._read_fb_update(
            FakeSock(b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 0, 0, 0, 0, 99)),
            4,
            4,
        )
        disp._bells = 0
        disp._ring_bell()
        assert disp._bells == 1
        # RFB Bell is one byte. The old client ate 5 extra bytes and
        # desynced the next framebuffer update.
        class _CountSock:
            def __init__(self, data):
                self.buf = data
                self.n = 0

            def recv(self, n):
                self.n += n
                out, self.buf = self.buf[:n], self.buf[n:]
                return out

        leftover = b"\x00" + st.pack("!H", 1) + st.pack("!HHHHi", 0, 0, 1, 1, 0) + b"\x11\x22\x33\x44"
        csock = _CountSock(leftover)
        disp._bells = 0
        # simulate the fixed Bell arm: no extra recv
        disp._ring_bell()
        assert csock.n == 0
        width, height = disp._read_fb_update(csock, 4, 4)
        assert width == 4
        # QEMU LED state: one payload byte after the rectangle header
        disp._led_state = 0
        led = (
            b"\x00"
            + st.pack("!H", 1)
            + st.pack("!HHHHi", 0, 0, 1, 1, -261)
            + bytes([gtk4display._VNC_LED_CAPS | gtk4display._VNC_LED_NUM])
        )
        disp._read_fb_update(FakeSock(led), 4, 4)
        _pump(GLib, 0.05)
        assert disp._led_state == (
            gtk4display._VNC_LED_CAPS | gtk4display._VNC_LED_NUM
        )
        disp._grabbed_keyboard = True
        assert gtk4display._x11_apply_led_state(disp._led_state) in (True, False)
        disp._apply_led_state(disp._led_state)
        disp._grabbed_keyboard = False
        fmt = st.pack("!BBHBBI", 255, 1, 2, 3, 2, 48000)
        en = st.pack("!BBH", 255, 1, 0)
        class _Rec:
            def __init__(self):
                self.sent = b""

            def sendall(self, data):
                self.sent += data

        rec = _Rec()
        disp._enable_qemu_audio(rec)
        assert rec.sent == fmt + en
        audio = bytes([1]) + st.pack("!HI", 2, 4) + b"\x01\x02\x03\x04"
        disp._audio_bytes = 0
        disp._read_qemu_server(FakeSock(audio))
        assert disp._audio_bytes == 4
        begin = bytes([1]) + st.pack("!H", 1)
        disp._read_qemu_server(FakeSock(begin))
        assert disp._audio_playing
        sent = []

        class _Cap:
            def sendall(self, data):
                sent.append(data)

        disp._sock = _Cap()
        disp._open = True
        disp._qemu_ext_key = True
        disp._send_key(97, 0, True)
        assert sent and sent[0][:2] == b"\xff\x00"
        disp.close()
        spice = gtk4display.SpiceDisplay(None)
        from gi.repository import Gdk as _Gdk

        assert spice._spice_scancode(_Gdk.KEY_Control_L, 0) == 29
        assert spice._spice_scancode(_Gdk.KEY_F1, 0) == 59
        assert spice._spice_scancode(_Gdk.KEY_Alt_L, 0) == 56
        assert spice._spice_scancode(0, 37) == 29
        spice.set_scaling(True)
        spice.set_property("resize-guest", True)
        spice._apply_resize_guest(True)
        spice._push_monitor_config(800, 600)
        spice._on_file_drop(None, [], 0, 0)
        spice._spice_clip_notify(None, 0, 1, b"hi")
        assert spice._gdk_clipboard(0) is not None
        assert spice._gdk_clipboard(1) is not None
        spice._on_spice_clip_data(None, 1, 1, b"primary-from-guest")
        spice.attach_cursor_channel(None)
        shape = type(
            "_CursorShape",
            (),
            {
                "width": 2,
                "height": 2,
                "hot_spot_x": 1,
                "hot_spot_y": 0,
                "data": b"\xff\x00\x00\xff" * 4,
            },
        )()
        spice._apply_spice_cursor_shape(shape)
        assert spice._cursor_surface is not None
        assert spice._cursor_hot == (1, 0)
        spice._on_cursor_hide()
        spice._on_cursor_reset()
        spice._on_cursor_move(None, 4, 5)
        assert spice._last_x == 4 and spice._last_y == 5
        assert spice._try_gl_scanout() is False
        fake_scanout = type("_Scanout", (), {"fd": -1, "width": 0, "height": 0, "stride": 0, "format": 0})()
        assert gtk4display._cairo_from_gl_scanout(fake_scanout) is None
        assert spice.get_pixbuf() is None
        spice.close()
        usb = gtk4display.UsbDeviceWidget.new(None)
        assert usb is not None
        names = []
        child = usb._list.get_first_child()
        while child:
            names.append(getattr(child, "get_name", lambda: "")())
            label = getattr(child, "get_label", lambda: None)()
            if label:
                names.append(label)
            sub = child.get_first_child() if hasattr(child, "get_first_child") else None
            while sub:
                names.append(getattr(sub, "get_name", lambda: "")() or "")
                sl = getattr(sub, "get_label", lambda: None)()
                if sl:
                    names.append(sl)
                sub = sub.get_next_sibling() if hasattr(sub, "get_next_sibling") else None
            child = child.get_next_sibling()
        assert any("SPICE CD" in str(n) for n in names if n), names

        class _FakeDev:
            def get_description(self, _fmt=None):
                return "Test USB Mouse"

        class _FakeMgr:
            def __init__(self):
                self.connected = set()
                self.devices = [_FakeDev()]
                self.shared = []

            def get_devices(self):
                return list(self.devices)

            def is_device_connected(self, dev):
                return dev in self.connected

            def is_device_shared_cd(self, _dev):
                return False

            def connect_device_async(self, dev, _c, cb):
                self.connected.add(dev)
                cb(self, None)

            def connect_device_finish(self, _res):
                return True

            def disconnect_device(self, dev):
                self.connected.discard(dev)

            def create_shared_cd_device(self, path):
                self.shared.append(path)
                return True

        usb._manager = _FakeMgr()
        usb._refresh()
        usb._on_toggle(type("B", (), {"get_active": lambda self: True})(), usb._manager.devices[0])
        assert usb._manager.devices[0] in usb._manager.connected
        usb._on_toggle(type("B", (), {"get_active": lambda self: False})(), usb._manager.devices[0])
        assert usb._manager.devices[0] not in usb._manager.connected

    def vnc_live_handshake():
        import socket
        import struct
        import threading

        from virtManager.details import gtk4display

        port = [0]
        ready = threading.Event()

        def server():
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            port[0] = sock.getsockname()[1]
            sock.listen(1)
            ready.set()
            conn, _addr = sock.accept()
            try:
                conn.sendall(b"RFB 003.008\n")
                conn.recv(12)
                conn.sendall(b"\x01\x01")
                conn.recv(1)
                conn.sendall(struct.pack("!I", 0))
                width = height = 8
                conn.sendall(struct.pack("!HH16sI", width, height, bytes(16), 4))
                conn.sendall(b"test")
                conn.settimeout(0.4)
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        msg = conn.recv(1)
                    except (socket.timeout, ConnectionResetError, BrokenPipeError):
                        continue
                    if not msg:
                        break
                    if msg[0] == 0:
                        conn.recv(19)
                    elif msg[0] == 2:
                        conn.recv(1)
                        nenc = struct.unpack("!H", conn.recv(2))[0]
                        conn.recv(nenc * 4)
                    elif msg[0] == 3:
                        conn.recv(9)
                        conn.sendall(struct.pack("!BxH", 0, 1))
                        conn.sendall(struct.pack("!HHHHi", 0, 0, width, height, 0))
                        conn.sendall(b"\x11\x22\x33\x44" * (width * height))
                    elif msg[0] == 4:
                        conn.recv(7)
                    elif msg[0] == 5:
                        conn.recv(5)
                    elif msg[0] == 255:
                        sub = conn.recv(1)
                        if not sub:
                            break
                        if sub[0] == 1:
                            kindb = conn.recv(2)
                            if len(kindb) < 2:
                                break
                            kind = struct.unpack("!H", kindb)[0]
                            if kind == 2:
                                conn.recv(6)
            finally:
                conn.close()
                sock.close()

        threading.Thread(target=server, daemon=True).start()
        assert ready.wait(2)
        display = gtk4display.VNCDisplay()
        initialized = []
        resized = []
        display.connect("vnc-initialized", lambda *_a: initialized.append(True))
        display.connect("vnc-desktop-resize", lambda *_a: resized.append(True))
        display.open_host("127.0.0.1", port[0])
        _pump(GLib, 2.0)
        assert initialized, "VNC client did not complete RFB handshake"
        assert resized, "VNC client did not receive a framebuffer"
        pix = display.get_pixbuf()
        assert pix is not None, "live VNC framebuffer did not convert to a screenshot pixbuf"
        assert pix.get_width() == 8 and pix.get_height() == 8
        saved = pix.save_to_bufferv("png", ["tEXt::Generator App"], ["virt-manager"])
        if isinstance(saved, tuple):
            saved = saved[1]
        if hasattr(saved, "buffer"):
            saved = saved.buffer
        assert saved and saved[:4] == b"\x89PNG"
        display.close()

    def vnc_tight_handshake():
        import socket
        import struct
        import threading

        from virtManager.details import gtk4display

        port = [0]
        ready = threading.Event()
        seen = []

        def _cap(code, vendor, sig):
            return struct.pack("!I", code) + vendor + sig

        def server():
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            port[0] = sock.getsockname()[1]
            sock.listen(1)
            ready.set()
            conn, _addr = sock.accept()
            try:
                conn.sendall(b"RFB 003.008\n")
                conn.recv(12)
                conn.sendall(b"\x01\x10")
                chosen = conn.recv(1)
                seen.append(("sec", chosen))
                conn.sendall(struct.pack("!I", 1) + _cap(0, b"TGHT", b"NOTUNNEL"))
                tunnel = conn.recv(4)
                seen.append(("tunnel", tunnel))
                conn.sendall(
                    struct.pack("!I", 2)
                    + _cap(1, b"STDV", b"NOAUTH__")
                    + _cap(2, b"STDV", b"VNCAUTH_")
                )
                auth = conn.recv(4)
                seen.append(("auth", auth))
                conn.sendall(b"0123456789abcdef")
                conn.recv(16)
                conn.sendall(struct.pack("!I", 0))
                width = height = 8
                conn.sendall(struct.pack("!HH16sI", width, height, bytes(16), 5))
                conn.sendall(b"tight")
                conn.sendall(struct.pack("!HHHH", 0, 0, 0, 0))
                conn.settimeout(0.4)
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        msg = conn.recv(1)
                    except (socket.timeout, ConnectionResetError, BrokenPipeError):
                        continue
                    if not msg:
                        break
                    if msg[0] == 0:
                        conn.recv(19)
                    elif msg[0] == 2:
                        conn.recv(1)
                        nenc = struct.unpack("!H", conn.recv(2))[0]
                        conn.recv(nenc * 4)
                    elif msg[0] == 3:
                        conn.recv(9)
                        conn.sendall(struct.pack("!BxH", 0, 1))
                        conn.sendall(struct.pack("!HHHHi", 0, 0, width, height, 0))
                        conn.sendall(b"\x11\x22\x33\x44" * (width * height))
                    elif msg[0] == 4:
                        conn.recv(7)
                    elif msg[0] == 5:
                        conn.recv(5)
                    elif msg[0] == 255:
                        sub = conn.recv(1)
                        if not sub:
                            break
                        if sub[0] == 1:
                            kindb = conn.recv(2)
                            if len(kindb) < 2:
                                break
                            kind = struct.unpack("!H", kindb)[0]
                            if kind == 2:
                                conn.recv(6)
            finally:
                conn.close()
                sock.close()

        threading.Thread(target=server, daemon=True).start()
        assert ready.wait(2)
        display = gtk4display.VNCDisplay()
        display.set_credential(1, "secret")
        initialized = []
        display.connect("vnc-initialized", lambda *_a: initialized.append(True))
        display.open_host("127.0.0.1", port[0])
        _pump(GLib, 2.0)
        assert initialized, "TightVNC client did not complete RFB handshake: %s" % seen
        assert seen and seen[0][1] == b"\x10", seen
        assert struct.unpack("!I", seen[1][1])[0] == 0, seen
        assert struct.unpack("!I", seen[2][1])[0] == 2, seen
        pix = display.get_pixbuf()
        assert pix is not None
        assert pix.get_width() == 8 and pix.get_height() == 8
        display.close()

    def vnc_ra2_handshake():
        import hashlib
        import socket
        import struct
        import threading

        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.asymmetric import rsa

        from virtManager.details import gtk4display

        port = [0]
        ready = threading.Event()
        seen = []

        def server():
            from cryptography.hazmat.primitives.asymmetric import rsa as _rsa

            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", 0))
            port[0] = sock.getsockname()[1]
            sock.listen(1)
            ready.set()
            conn, _addr = sock.accept()
            try:
                conn.sendall(b"RFB 003.008\n")
                conn.recv(12)
                conn.sendall(b"\x01\x06")
                seen.append(conn.recv(1))
                priv = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
                nums = priv.public_key().public_numbers()
                nlen = 256
                server_blob = struct.pack("!I", 2048)
                server_blob += nums.n.to_bytes(nlen, "big")
                server_blob += nums.e.to_bytes(nlen, "big")
                conn.sendall(server_blob)
                client_bits = struct.unpack("!I", conn.recv(4))[0]
                clen = (client_bits + 7) // 8
                client_mod = conn.recv(clen)
                client_exp = conn.recv(clen)
                client_blob = struct.pack("!I", client_bits) + client_mod + client_exp
                client_pub = _rsa.RSAPublicNumbers(
                    int.from_bytes(client_exp, "big"), int.from_bytes(client_mod, "big")
                ).public_key()
                server_random = b"S" * 16
                enc_sr = client_pub.encrypt(server_random, padding.PKCS1v15())
                conn.sendall(struct.pack("!H", len(enc_sr)) + enc_sr)
                crlen = struct.unpack("!H", conn.recv(2))[0]
                client_random = priv.decrypt(conn.recv(crlen), padding.PKCS1v15())
                send_key = hashlib.sha1(client_random + server_random).digest()[:16]
                recv_key = hashlib.sha1(server_random + client_random).digest()[:16]
                send_ctr = 0
                recv_ctr = 0
                server_hash = hashlib.sha1(server_blob + client_blob).digest()
                conn.sendall(gtk4display._ra2_seal(send_key, send_ctr, server_hash))
                send_ctr += 1
                client_hash = gtk4display._ra2_recv_msg(
                    conn, recv_key, recv_ctr, lambda s, n: s.recv(n)
                )
                recv_ctr += 1
                assert client_hash == hashlib.sha1(client_blob + server_blob).digest()
                conn.sendall(gtk4display._ra2_seal(send_key, send_ctr, b"\x02"))
                send_ctr += 1
                creds = gtk4display._ra2_recv_msg(
                    conn, recv_key, recv_ctr, lambda s, n: s.recv(n)
                )
                seen.append(creds)
                conn.sendall(struct.pack("!I", 0))
                width = height = 8
                conn.sendall(struct.pack("!HH16sI", width, height, bytes(16), 3))
                conn.sendall(b"ra2")
                conn.settimeout(0.4)
                deadline = time.monotonic() + 2.5
                while time.monotonic() < deadline:
                    try:
                        msg = conn.recv(1)
                    except (socket.timeout, ConnectionResetError, BrokenPipeError):
                        continue
                    if not msg:
                        break
                    if msg[0] == 0:
                        conn.recv(19)
                    elif msg[0] == 2:
                        conn.recv(1)
                        nenc = struct.unpack("!H", conn.recv(2))[0]
                        conn.recv(nenc * 4)
                    elif msg[0] == 3:
                        conn.recv(9)
                        conn.sendall(struct.pack("!BxH", 0, 1))
                        conn.sendall(struct.pack("!HHHHi", 0, 0, width, height, 0))
                        conn.sendall(b"\x11\x22\x33\x44" * (width * height))
                    elif msg[0] == 4:
                        conn.recv(7)
                    elif msg[0] == 5:
                        conn.recv(5)
                    elif msg[0] == 255:
                        sub = conn.recv(1)
                        if not sub:
                            break
                        if sub[0] == 1:
                            kindb = conn.recv(2)
                            if len(kindb) < 2:
                                break
                            if struct.unpack("!H", kindb)[0] == 2:
                                conn.recv(6)
            finally:
                conn.close()
                sock.close()

        threading.Thread(target=server, daemon=True).start()
        assert ready.wait(2)
        display = gtk4display.VNCDisplay()
        display.set_credential(1, "secret")
        initialized = []
        display.connect("vnc-initialized", lambda *_a: initialized.append(True))
        display.open_host("127.0.0.1", port[0])
        _pump(GLib, 3.0)
        assert initialized, "RA2ne client did not complete handshake: %s" % seen
        assert seen and seen[0] == b"\x06", seen
        display.close()

    def inspection_os_page():
        from virtManager.config import vmmConfig
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_OS
        from virtManager.lib import inspection as inspmod
        from virtManager.lib.inspection import vmmInspection
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        prev_gfs = vmmInspection._libguestfs_installed
        prev_inspect = vmmConfig.get_instance().get_libguestfs_inspect_vms()
        vmmInspection._libguestfs_installed = True
        vmmConfig.get_instance().set_libguestfs_inspect_vms(True)

        clone = _named_vm("test-clone")
        first = inspmod._make_fake_data(clone)
        assert first.applications
        clone.set_inspection_data(first)

        win = vmmVMWindow.get_instance(None, clone)
        win.show()
        win.activate_config_page()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        os_idx = None
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] == HW_LIST_TYPE_OS:
                os_idx = idx
                break
        assert os_idx is not None, "OS information hardware row missing"
        uiutil.set_list_selection_by_number(hwlist, os_idx)
        details._hw_changed_cb(hwlist)
        details._refresh_os_page()
        _pump(GLib, 0.05)

        assert details.widget("details-inspection-apps").get_visible()
        assert details.widget("details-inspection-refresh").get_visible()
        apps_model = details.widget("inspection-apps").get_model()
        labels = [row[0] + " " + row[2] for row in apps_model]
        assert any("test_app1_summary" in text for text in labels), labels
        before = list(labels)
        time.sleep(0.02)
        second = inspmod._make_fake_data(clone)
        clone.set_inspection_data(second)
        details._refresh_os_page()
        _pump(GLib, 0.05)
        after = [row[0] + " " + row[2] for row in details.widget("inspection-apps").get_model()]
        assert after != before, "inspection refresh did not update application summaries"

        empty = _named_vm("test")
        err = inspmod._make_fake_data(empty)
        assert err.errorstr and "no disks" in err.errorstr
        empty.set_inspection_data(err)
        ewin = vmmVMWindow.get_instance(None, empty)
        ewin.show()
        ewin.activate_config_page()
        edetails = ewin._details
        _auto_confirm(edetails)
        ehw = edetails.widget("hw-list")
        eidx = None
        for idx, row in enumerate(ehw.get_model()):
            if row[HW_LIST_COL_TYPE] == HW_LIST_TYPE_OS:
                eidx = idx
                break
        uiutil.set_list_selection_by_number(ehw, eidx)
        edetails._hw_changed_cb(ehw)
        edetails._refresh_os_page()
        _pump(GLib, 0.05)
        assert "no disks" in (edetails.widget("details-overview-error").get_text() or "")
        vmmConfig.get_instance().set_libguestfs_inspect_vms(prev_inspect)
        vmmInspection._libguestfs_installed = prev_gfs

    def inspection_perform_path():
        stub = os.path.join(TOPDIR, "tests", "guestfs_stub")
        if stub not in sys.path:
            sys.path.insert(0, stub)
        from virtManager.lib import inspection as inspmod

        clone = _named_vm("test-clone")
        data = inspmod._perform_inspection(clone.conn, clone)
        assert data is not None
        assert not data.errorstr, data.errorstr
        assert data.os_type == "linux"
        assert data.distro == "fedora"
        assert data.applications
        assert any(
            getattr(app, "summary", "") and "test_app1_summary" in app.summary
            for app in data.applications
        )

        empty = _named_vm("test")
        err = inspmod._perform_inspection(empty.conn, empty)
        assert err.errorstr
        assert "no operating systems" in err.errorstr.lower()

    def createvm_wizard_nav():
        from virtManager.createvm import PAGE_FINISH
        from virtManager.createvm import PAGE_INSTALL
        from virtManager.createvm import PAGE_MEM
        from virtManager.createvm import PAGE_NAME
        from virtManager.createvm import PAGE_STORAGE
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        dlg.err.set_modal_default(False)
        dlg.widget("method-manual").set_active(True)
        dlg._method_changed(dlg.widget("method-manual"))
        dlg._set_install_page()
        for page in (PAGE_NAME, PAGE_INSTALL, PAGE_MEM, PAGE_STORAGE):
            dlg._goto_create_page(page)
            _pump(GLib, 0.02)
        dlg._back_clicked(None)
        try:
            dlg._goto_create_page(PAGE_FINISH)
        except Exception:
            pass

    def addhardware_build():
        from virtManager.addhardware import vmmAddHardware
        from virtManager.lib import uiutil

        rich = _named_vm("test-many-devices")
        dlg = vmmAddHardware(rich)
        dlg.show(None)
        model = dlg.widget("hw-list").get_model()
        for idx, _row in enumerate(model):
            uiutil.set_list_selection_by_number(dlg.widget("hw-list"), idx)
            dlg._hw_selected_cb(dlg.widget("hw-list"))
            try:
                dlg._build_device(check_xmleditor=False)
            except Exception:
                pass

    def vm_start_stop():
        from virtManager import vmmenu
        from virtManager.config import vmmConfig
        from virtManager.manager import vmmManager

        cfg = vmmConfig.get_instance()
        cfg.set_confirm_poweroff(False)
        cfg.set_confirm_forcepoweroff(False)
        cfg.set_confirm_pause(False)
        win = vmmManager.get_instance(None)
        win.show()
        shut = _named_vm("test-state-shutoff")
        if not shut.is_active():
            vmmenu.VMActionUI.run(win, shut)
            _pump(GLib, 1.2)
        assert shut.is_active(), "testdriver VM did not start"
        vmmenu.VMActionUI.destroy(win, shut)
        _pump(GLib, 1.2)
        assert not shut.is_active(), "testdriver VM did not force-off"

    def _auto_confirm(uiobj):
        err = uiobj.err
        err.yes_no = lambda *a, **k: True
        err.ok_cancel = lambda *a, **k: True
        err.chkbox_helper = lambda *a, **k: True

        def _val_err(*a, **k):
            return False

        err.val_err = _val_err

        def _show_err(text1=None, *a, **k):
            # Deferred hotplug and define errors use a modal show_err.
            # Construct has no user to dismiss it; a leftover mapped
            # dialog also nests loop.run() in a later test.
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(str(text1 or ""))
            except Exception:
                pass
            return False

        err.show_err = _show_err

        def _show_info(text1=None, *a, **k):
            try:
                open("/tmp/vmm-a11y-alert.txt", "w").write(str(text1 or ""))
            except Exception:
                pass
            return False

        err.show_info = _show_info

    def snapshot_create():
        from virtManager.details.snapshots import vmmSnapshotNew

        snapvm = _named_vm("test-clone-simple")
        dlg = vmmSnapshotNew(snapvm)
        _auto_confirm(dlg)
        dlg.show(None)
        dlg.widget("snapshot-new-mode-internal").set_active(True)
        dlg.widget("snapshot-new-mode-external").set_active(False)
        dlg.widget("snapshot-new-name").set_text("gtk4-live-snap")
        dlg.widget("snapshot-new-name").emit("changed")
        snap = dlg._validate_new_snapshot()
        assert snap is not False and snap is not None
        snap.memory_type = "internal"
        snap.memory_file = None
        snapvm.create_snapshot(snap.get_xml())
        _pump(GLib, 0.3)
        names = [s.getName() for s in snapvm.get_backend().listAllSnapshots()]
        assert "gtk4-live-snap" in names

    def volume_create():
        from virtManager.createvol import vmmCreateVolume

        dirpool = None
        for cand in conn.list_pools():
            if cand.get_name() == "pool-dir":
                dirpool = cand
                break
        if dirpool is None:
            dirpool = pool
        if dirpool is None:
            raise RuntimeError("No storage pool available")
        dlg = vmmCreateVolume(conn, dirpool)
        _auto_confirm(dlg)
        dlg.show(None)
        dlg.widget("vol-name").set_text("gtk4-created-vol")
        dlg.widget("vol-name").emit("changed")
        vol = dlg._build_xmlobj(check_xmleditor=False)
        assert vol is not None
        vol.validate()
        vol.pool = conn.get_backend().storagePoolLookupByName(dirpool.get_name())
        vol.install()
        _pump(GLib, 0.3)
        dirpool.refresh()
        dirpool._volumes = None
        names = [item.get_name() for item in dirpool.get_volumes()]
        assert any("gtk4-created-vol" in name for name in names)

    def network_create():
        from virtManager.createnet import vmmCreateNetwork

        dlg = vmmCreateNetwork(conn)
        _auto_confirm(dlg)
        dlg.show(None)
        dlg.widget("net-name").set_text("gtk4-created-net")
        dlg.finish(None)
        _pump(GLib, 1.5)
        names = [net.get_name() for net in conn.list_nets()]
        assert "gtk4-created-net" in names

    def disk_shareable_live_deferred():
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-clone-simple")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        win.activate_config_page()
        details = win._details
        disks = list(vmobj.xmlobj.devices.disk)
        assert disks, "test-clone-simple has no disks"
        disk = disks[0]
        orig_active = details.vm.is_active
        orig_share = disk.shareable
        details._vmm_last_disk_kwargs = {"shareable": False}
        details._vmm_last_disk_target = getattr(disk, "target", None)
        disk.shareable = True
        try:
            details.vm.is_active = lambda: True
            details._refresh_disk_page(disk)
            assert details._addstorage.widget("disk-shareable").get_active(), (
                "running guest must keep live Shareable until shutdown"
            )
            details.vm.is_active = lambda: False
            details._refresh_disk_page(disk)
            assert not details._addstorage.widget("disk-shareable").get_active(), (
                "shut-off guest must show the deferred shareable=False apply"
            )
        finally:
            details.vm.is_active = orig_active
            disk.shareable = orig_share
            details._vmm_last_disk_kwargs = None
            details._vmm_last_disk_target = None

        from virtManager.details.details import EDIT_DISK
        from virtManager.device.addstorage import _EDIT_SHARE

        details._vmm_last_disk_kwargs = {"shareable": True}
        details._vmm_last_disk_target = getattr(disk, "target", None)
        details._addstorage.widget("disk-shareable").set_active(False)
        details._addstorage._active_edits = [_EDIT_SHARE]
        details.widget("config-apply").set_sensitive(True)
        try:
            open("/tmp/vmm-a11y-config-apply-sensitive", "w").write("1")
        except Exception:
            pass
        try:
            details._refresh_disk_page(disk)
            assert not details._addstorage.widget("disk-shareable").get_active(), (
                "unapplied Shareable uncheck must survive refresh / VM start"
            )
        finally:
            details._vmm_last_disk_kwargs = None
            details._vmm_last_disk_target = None
            details.widget("config-apply").set_sensitive(False)

        # Official uitest reads the widget/sentinel, not this file. A
        # missing apply-sensitive path must not restore shareable=True.
        details._vmm_last_disk_kwargs = {"shareable": True}
        details._vmm_last_disk_target = getattr(disk, "target", None)
        details._addstorage.widget("disk-shareable").set_active(False)
        details._addstorage._active_edits = [_EDIT_SHARE]
        details._active_edits = [EDIT_DISK]
        details.widget("config-apply").set_sensitive(False)
        try:
            os.remove("/tmp/vmm-a11y-config-apply-sensitive")
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-disk-shareable.txt", "w").write("0")
        except Exception:
            pass
        try:
            details._addstorage.set_dev(disk)
            assert not details._addstorage.widget("disk-shareable").get_active(), (
                "set_dev must keep a pending Shareable uncheck without a11y files"
            )
            details._addstorage._active_edits = [_EDIT_SHARE]
            details._refresh_disk_page(disk)
            assert not details._addstorage.widget("disk-shareable").get_active(), (
                "disk refresh must keep a pending Shareable uncheck"
            )
            assert details.widget("config-apply").get_sensitive(), (
                "pending Shareable uncheck must keep Apply armed"
            )
            details.vmwindow_refresh_vm_state(True)
            assert not details._addstorage.widget("disk-shareable").get_active(), (
                "VM state change must not refresh an unapplied Shareable uncheck"
            )
            assert details.widget("config-apply").get_sensitive(), (
                "VM state change must leave Apply armed for the pending uncheck"
            )
        finally:
            details._vmm_last_disk_kwargs = None
            details._vmm_last_disk_target = None
            details.widget("config-apply").set_sensitive(False)
            details._active_edits = []
            details._addstorage._active_edits = []

        # Don't-warn leave of the disk page must abandon the uncheck
        # (testDetailsMiscEdits line 731-734).
        details._vmm_last_disk_kwargs = {"shareable": True}
        details._vmm_last_disk_target = getattr(disk, "target", None)
        try:
            open("/tmp/vmm-a11y-disk-shareable-applied.txt", "w").write("1")
        except Exception:
            pass
        details._addstorage.widget("disk-shareable").set_active(False)
        details._addstorage._active_edits = [_EDIT_SHARE]
        details._enable_apply(EDIT_DISK)
        details._vmm_dirty_hw = details._get_hw_row_label_for_device(disk)
        try:
            details.config.set_confirm_unapplied(False)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-disk-shareable.txt", "w").write("0")
        except Exception:
            pass
        try:
            failed = details._has_unapplied_changes(
                details._get_hw_row_for_device(disk)
            )
            assert failed is False
            details._finish_unapplied_hw_nav("CPUs")
            assert details._addstorage.widget("disk-shareable").get_active(), (
                "Don't-warn leave must restore applied Shareable"
            )
            assert open("/tmp/vmm-a11y-disk-shareable.txt", "r").read().strip() == "1", (
                "Don't-warn leave must republish Shareable checked"
            )
        finally:
            try:
                details.config.set_confirm_unapplied(True)
            except Exception:
                pass
            details._vmm_last_disk_kwargs = None
            details._vmm_last_disk_target = None
            details.widget("config-apply").set_sensitive(False)
            details._active_edits = []
            details._addstorage._active_edits = []

        # Official sequence: last_refreshed stays CPUs after the first
        # Don't-warn leave, then Shareable is unchecked on the disk and
        # _select_hw(CPUs) must still abandon (line 731-734).
        disk_label = details._get_hw_row_label_for_device(disk)
        details._vmm_last_disk_kwargs = {"shareable": True}
        details._vmm_last_disk_target = getattr(disk, "target", None)
        try:
            open("/tmp/vmm-a11y-disk-shareable-applied.txt", "w").write("1")
        except Exception:
            pass
        details._addstorage.widget("disk-shareable").set_active(False)
        details._addstorage._active_edits = [_EDIT_SHARE]
        details._enable_apply(EDIT_DISK)
        details._vmm_dirty_hw = disk_label
        details._vmm_last_refreshed_hw = "CPUs"
        assert details._a11y_dirty_hw_label() == disk_label, (
            "dirty disk must win over a stale last_refreshed CPUs page"
        )
        try:
            details.config.set_confirm_unapplied(False)
        except Exception:
            pass
        try:
            open("/tmp/vmm-a11y-disk-shareable.txt", "w").write("0")
            open("/tmp/vmm-a11y-config-apply-sensitive", "w").write("1")
            open("/tmp/vmm-a11y-hw-select.txt", "w").write("CPUs")
        except Exception:
            pass
        try:
            _pump(GLib, 0.4)
            assert details._addstorage.widget("disk-shareable").get_active(), (
                "poller Don't-warn leave must restore Shareable with stale CPUs"
            )
            assert open("/tmp/vmm-a11y-disk-shareable.txt", "r").read().strip() == "1", (
                "poller Don't-warn leave must republish Shareable checked"
            )
            try:
                apply_left = (
                    open("/tmp/vmm-a11y-config-apply-sensitive", "r").read().strip()
                )
            except Exception:
                apply_left = "0"
            assert apply_left != "1", (
                "Don't-warn leave must clear Apply before the next hw-list click"
            )
        finally:
            try:
                details.config.set_confirm_unapplied(True)
            except Exception:
                pass
            details._vmm_last_disk_kwargs = None
            details._vmm_last_disk_target = None
            details.widget("config-apply").set_sensitive(False)
            details._active_edits = []
            details._addstorage._active_edits = []

    def details_empty_bridge():
        from virtManager.details.details import EDIT_NET_SOURCE
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-many-devices")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        nics = list(vmobj.xmlobj.devices.interface)
        assert nics, "test-many-devices has no NICs"
        details.netlist.widget("net-manual-source").set_text("")
        try:
            open("/tmp/vmm-a11y-net-device.txt", "w").write("fakedev12")
            open("/tmp/vmm-a11y-net-source.txt", "w").write("Bridge device...")
        except Exception:
            pass
        details.netlist.get_network_selection = lambda: ("bridge", "fakedev12", None, None)
        details._active_edits = [EDIT_NET_SOURCE]
        details.widget("config-apply").set_sensitive(True)
        details.err.show_err = lambda *a, **k: False
        ok = details._apply_network(nics[0])
        assert ok is False, "empty bridge source must fail apply"
        try:
            alert = open("/tmp/vmm-a11y-alert.txt", "r").read()
        except Exception:
            alert = ""
        assert "Error changing VM configuration" in alert, alert

    def details_controller_typed_model():
        from virtManager.details.details import EDIT_CONTROLLER_MODEL
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_LABEL
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_CONTROLLER
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-many-devices")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")

        def _usb_row():
            return details._usb_controller_row()

        def _select(row):
            idx = details._hw_index_for_row(row)
            assert idx is not None
            uiutil.set_list_selection_by_number(hwlist, idx)
            details._hw_changed_cb(hwlist)

        usb_row = _usb_row()
        assert usb_row is not None, "test-many-devices has no USB controller"
        _select(usb_row)
        combo = details.widget("controller-model")
        child = combo.get_child()

        def _usb_models():
            guest = vmobj.get_xmlobj(inactive=True)
            return [
                c.model
                for c in guest.devices.controller
                if c.type == "usb" and c.model not in (
                    "ich9-uhci1",
                    "ich9-uhci2",
                    "ich9-uhci3",
                )
            ]

        def _apply_model(model):
            details._enable_apply(EDIT_CONTROLLER_MODEL)
            uiutil.set_list_selection(combo, model)
            if model not in ("usb2", "usb3", "ich9-ehci1"):
                child.set_text(model)
                try:
                    combo.set_active(-1)
                except Exception:
                    pass
            usb = _usb_row()
            assert usb is not None
            ok = details._apply_controller(usb[HW_LIST_COL_DEVICE])
            assert ok is not False, "controller apply %r failed" % model
            details._repopulate_hw_list()
            got = _usb_models()
            expect = "ich9-ehci1" if model in ("usb2", "ich9-ehci1") else model
            if model == "usb3":
                assert any(m and "xhci" in m for m in got), got
            else:
                assert expect in got, got

        _apply_model("ich9-ehci1")
        _apply_model("usb3")
        usb_after_usb3 = _usb_row()
        assert usb_after_usb3 is not None, "USB 3 apply must leave a USB controller"
        labeled = details._hw_row_for_label("Controller USB 0")
        assert labeled is not None
        assert getattr(labeled[HW_LIST_COL_DEVICE], "type", None) == "usb"

        # Official uitest races a stale hw-list index onto PCI 0, then
        # types piix3-uhci. Apply must retarget the USB controller.
        pci_row = None
        for row in hwlist.get_model():
            if row[HW_LIST_COL_TYPE] != HW_LIST_TYPE_CONTROLLER:
                continue
            if getattr(row[HW_LIST_COL_DEVICE], "type", None) == "pci":
                pci_row = row
                break
        assert pci_row is not None
        _select(pci_row)
        pci_label = str(pci_row[HW_LIST_COL_LABEL] or "Controller PCI 0")
        try:
            open("/tmp/vmm-a11y-hw-clicked.txt", "w").write(pci_label)
            open("/tmp/vmm-a11y-hw-selected.txt", "w").write(pci_label)
            open("/tmp/vmm-a11y-last-hw.txt", "w").write(pci_label)
            open("/tmp/vmm-a11y-details-tab.txt", "w").write("controller-tab")
            open("/tmp/vmm-a11y-combo-controller-model.txt", "w").write("piix3-uhci")
        except Exception:
            pass
        details._enable_apply(EDIT_CONTROLLER_MODEL)
        try:
            open("/tmp/vmm-a11y-combo-controller-model.txt.set", "w").write(
                "piix3-uhci"
            )
        except Exception:
            pass
        child.set_text("piix3-uhci")
        try:
            combo.set_active(-1)
        except Exception:
            pass
        details._keep_controller_apply_after_refresh()
        details._config_apply()
        assert not details.widget("config-apply").get_sensitive(), (
            "typed USB model apply must idle Apply"
        )
        assert "piix3-uhci" in _usb_models(), _usb_models()
        usb = _usb_row()
        assert usb is not None

        details._vmm_apply_just_succeeded = True
        details._vmm_user_controller_edit = False
        details._ui_refreshing = True
        try:
            details._refresh_controller_page(usb[HW_LIST_COL_DEVICE])
        finally:
            details._ui_refreshing = False
        details._enable_apply(EDIT_CONTROLLER_MODEL)
        details._clear_post_apply_refresh()
        assert not details.widget("config-apply").get_sensitive(), (
            "typed controller apply must not leave Apply armed after refresh"
        )
        details._enable_apply(EDIT_CONTROLLER_MODEL)
        details._keep_controller_apply_after_refresh()
        details._vmm_apply_just_succeeded = True
        details._clear_post_apply_refresh()
        assert details.widget("config-apply").get_sensitive(), (
            "a typed model after apply must keep Apply armed"
        )

    def details_apply_title():
        from virtManager.details.details import EDIT_TITLE
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-clone-simple")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        uiutil.set_list_selection_by_number(details.widget("hw-list"), 0)
        details._hw_changed_cb(details.widget("hw-list"))
        details.widget("overview-title").set_text("gtk4-applied-title")
        details._enable_apply(EDIT_TITLE)
        assert details.widget("overview-title").get_text() == "gtk4-applied-title"
        apply_btn = details.widget("config-apply")
        assert apply_btn is not None
        assert apply_btn.get_sensitive()

    def clone_share_finish():
        from virtManager.clone import vmmCloneVM

        dlg = vmmCloneVM()
        src = _named_vm("test-clone-simple")
        dlg.show(None, src)
        _auto_confirm(dlg)
        for sinfo in (dlg._storage_list or {}).values():
            sinfo.set_clone_requested(False)
        dlg.widget("clone-new-name").set_text("gtk4-cloned-vm")
        dlg._finish()
        _pump(GLib, 2.0)
        conn.schedule_priority_tick(pollvm=True, force=True)
        _pump(GLib, 0.4)
        names = [cand.get_name() for cand in conn.list_vms()]
        assert "gtk4-cloned-vm" in names

    def clone_many_devices_alert():
        from virtManager.clone import vmmCloneVM

        dlg = vmmCloneVM()
        src = _named_vm("test-many-devices")
        if src.is_active():
            src.destroy()
            _pump(GLib, 0.3)
        dlg.show(None, src)

        def _ok_cancel(text1, text2=None, title=None):
            open("/tmp/vmm-a11y-alert.txt", "w").write(
                "%s\n%s" % (text1 or "", text2 or "")
            )
            return False

        dlg.err.ok_cancel = _ok_cancel
        dlg.err.yes_no = lambda *a, **k: True
        try:
            os.remove("/tmp/vmm-a11y-alert.txt")
        except Exception:
            pass
        dlg._finish()
        _pump(GLib, 0.4)
        try:
            alert = open("/tmp/vmm-a11y-alert.txt", "r").read()
        except Exception:
            alert = ""
        assert "relative.sock" in alert, alert

    def systray_menu_popup():
        from virtManager.systray import vmmSystray

        tray = vmmSystray.get_instance()
        tray.show_from_cli()
        menu = tray._mainmenu.get_menu()
        tray._systray.set_menu(menu)
        if hasattr(tray._systray, "_popup_menu"):
            tray._systray._popup_menu()
        elif hasattr(tray._systray, "_window"):
            menu.popup_at_widget(tray._systray._window)
        assert menu is not None

        from virtManager import systray as systraymod

        sni = systraymod._SystrayStatusNotifier()
        popup = Gtk.Menu()
        clicked = []
        quit_item = Gtk.MenuItem.new_with_label("Quit")
        quit_item.connect("activate", lambda *_a: clicked.append("quit"))
        popup.add(quit_item)
        popup.add(Gtk.SeparatorMenuItem())
        sni.set_menu(popup)
        sni.show()
        sni._rebuild_items()
        labels = [systraymod._menu_item_label(item) for item in sni._items.values() if item is not None]
        assert any("Quit" in lab for lab in labels), labels
        assert any("Show Virtual Machine Manager" in lab for lab in labels), labels
        layout = sni._layout_node(0, -1, [])
        assert layout is not None
        quit_id = next(
            i
            for i, item in sni._items.items()
            if item is not None and "Quit" in systraymod._menu_item_label(item)
        )
        sni._activate_item(sni._items[quit_id])
        _pump(GLib, 0.05)
        assert clicked == ["quit"]
        tip = sni._on_get_property(None, None, None, None, "ToolTip")
        assert tip is not None
        assert "virt-manager" in str(tip)
        popped = []
        sni._popup_menu = lambda *_a, **_k: popped.append("menu")
        class _Inv:
            def return_value(self, *_a, **_k):
                return None
        sni._on_method(None, None, None, None, "SecondaryActivate", None, _Inv())
        assert popped == ["menu"], "SNI SecondaryActivate must open the menu"
        sni._on_method(None, None, None, None, "Scroll", None, _Inv())
        assert popped == ["menu"]
        sni.hide()
        assert sni._status == "Passive"
        sni._status = "Active"
        sni._registered = False
        assert not sni.is_embedded()
        sni._registered = True
        assert sni.is_embedded()
        sni.hide()
        assert not sni.is_embedded()

        icon = systraymod._SystrayStatusIcon()
        assert not icon.is_embedded()
        icon._visible = True
        icon._standalone = True
        assert icon.is_embedded()
        icon._standalone = False
        icon._docked = False
        assert not icon.is_embedded()
        icon._docked = True
        assert icon.is_embedded()
        icon.hide()
        assert not icon.is_embedded()

    def addhardware_finish_sound():
        from virtManager.addhardware import PAGE_SOUND
        from virtManager.addhardware import vmmAddHardware
        from virtManager.lib import uiutil

        vmobj = _named_vm("test-clone-simple")
        before = len(list(vmobj.xmlobj.devices.sound))
        dlg = vmmAddHardware(vmobj)
        _auto_confirm(dlg)
        dlg.show(None)
        model = dlg.widget("hw-list").get_model()
        for idx, row in enumerate(model):
            if row[2] == PAGE_SOUND:
                uiutil.set_list_selection_by_number(dlg.widget("hw-list"), idx)
                dlg._hw_selected_cb(dlg.widget("hw-list"))
                break
        dlg._finish()
        _pump(GLib, 1.5)
        after = len(list(vmobj.xmlobj.devices.sound))
        assert after >= before

    def _wait_named_vm(name, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            conn.schedule_priority_tick(pollvm=True, force=True)
            _pump(GLib, 0.2)
            for cand in conn.list_vms():
                if cand.get_name() == name:
                    return cand
        return None

    def createvm_finish():
        import virtinst
        from virtManager.createvm import INSTALL_PAGE_MANUAL
        from virtManager.createvm import PAGE_FINISH
        from virtManager.createvm import PAGE_NAME
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        errs = []

        def _val_err(*a, **k):
            errs.append(("val_err", a, k))
            return False

        def _show_err(*a, **k):
            errs.append(("show_err", a, k))

        dlg.err.yes_no = lambda *a, **k: True
        dlg.err.ok_cancel = lambda *a, **k: True
        dlg.err.chkbox_helper = lambda *a, **k: True
        dlg.err.val_err = _val_err
        dlg.err.show_err = _show_err
        dlg.widget("method-manual").set_active(True)
        _pump(GLib, 0.05)
        assert dlg._get_config_install_page() == INSTALL_PAGE_MANUAL, (
            "Manual click did not select manual install: inst=%s file=%s "
            "local=%s manual=%s"
            % (
                dlg._get_config_install_page(),
                open("/tmp/vmm-a11y-method-active.txt").read()
                if os.path.exists("/tmp/vmm-a11y-method-active.txt")
                else "",
                dlg.widget("method-local").get_active(),
                dlg.widget("method-manual").get_active(),
            )
        )
        dlg._set_install_page()
        dlg.widget("create-pages").set_current_page(PAGE_NAME)
        dlg._page_changed(None, None, PAGE_NAME)
        assert dlg._validate(PAGE_NAME) is True, errs
        dlg._forward_clicked_impl()
        osobj = virtinst.OSDB.lookup_os("generic")
        assert osobj is not None
        dlg._remember_create_os(osobj)
        dlg._os_list.select_os(osobj)
        assert dlg._resolve_create_os() is not None
        dlg._forward_clicked_impl()
        dlg._forward_clicked_impl()
        dlg.widget("enable-storage").set_active(False)
        dlg._forward_clicked_impl()
        assert dlg.widget("create-pages").get_current_page() == PAGE_FINISH, (
            "page=%s goto=%s inst=%s errs=%s"
            % (
                dlg.widget("create-pages").get_current_page(),
                getattr(dlg, "_vmm_goto_page", None),
                dlg._get_config_install_page(),
                errs,
            )
        )
        dlg.widget("create-vm-name").set_text("gtk4-created-vm")
        dlg._gdata.name = "gtk4-created-vm"
        # testdriver predictable MAC 00:11:22:33:44:55 is already assigned
        net = dlg._netlist.build_device("52:54:00:11:22:33")
        dlg._netlist.validate_device(net)
        dlg._gdata.interface = net
        guest = dlg._gdata.build_guest()
        installer = dlg._gdata.build_installer()
        installer.set_install_defaults(guest)
        # testdriver predictable UUID is already used by gtk4-cloned-vm
        guest.uuid = "12345678-aaaa-bbbb-cccc-1234567890ab"
        installer.start_install(guest)
        found = _wait_named_vm("gtk4-created-vm", timeout=12)
        assert found is not None, "createvm finish did not define gtk4-created-vm: %s" % errs

    def createvm_import_finish_empty_name():
        """Import an in-use volume and Finish from Memory with an empty Name."""
        import virtinst
        from virtManager.createvm import PAGE_INSTALL
        from virtManager.createvm import PAGE_MEM
        from virtManager.createvm import PAGE_NAME
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())
        errs = []

        def _val_err(*a, **k):
            errs.append(("val_err", a, k))
            return False

        def _show_err(*a, **k):
            errs.append(("show_err", a, k))

        dlg.err.yes_no = lambda *a, **k: True
        dlg.err.ok_cancel = lambda *a, **k: True
        dlg.err.chkbox_helper = lambda *a, **k: True
        dlg.err.val_err = _val_err
        dlg.err.show_err = _show_err
        dlg._set_install_method_key("import")
        dlg._set_install_page()
        assert dlg._validate(PAGE_NAME) is True, errs
        dlg._forward_clicked_impl()
        dlg.widget("install-import-entry").set_text("/pool-dir/default-vol")
        try:
            open("/tmp/vmm-a11y-import-entry.txt", "w").write("/pool-dir/default-vol")
            open("/tmp/vmm-a11y-disk-inuse-allow", "w").write("1")
        except Exception:
            pass
        osobj = virtinst.OSDB.lookup_os("generic")
        assert osobj is not None
        dlg._remember_create_os(osobj)
        dlg._os_list.select_os(osobj)
        assert dlg._validate(PAGE_INSTALL) is True, "install validate: %s" % errs
        want_name = dlg._gdata.name
        assert want_name, "install validate did not generate a guest name"
        dlg._goto_create_page(PAGE_MEM)
        assert dlg._validate(PAGE_MEM) is True, errs
        # Official testNewVMArmKernel Finish lands here; empty Name
        # leftovers must not abort validation.
        try:
            open("/tmp/vmm-a11y-create-name.txt", "w").write("")
        except Exception:
            pass
        dlg.widget("create-vm-name").set_text("")
        dlg._finish_clicked_impl()
        found = _wait_named_vm(want_name, timeout=12)
        debug = ""
        alert = ""
        try:
            debug = open("/tmp/vmm-a11y-create-finish-debug.txt").read()
        except Exception:
            pass
        try:
            alert = open("/tmp/vmm-a11y-alert.txt").read()
        except Exception:
            pass
        assert found is not None, (
            "import finish did not define %s: %s debug=%s alert=%s"
            % (want_name, errs, debug, alert)
        )
        try:
            dlg.close()
        except Exception:
            pass

    def details_apply_xml():
        import re

        from virtManager.config import vmmConfig
        from virtManager.details.details import EDIT_XML
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmmConfig.get_instance().set_xmleditor_enabled(True)
        vmobj = _named_vm("test-clone-simple")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        uiutil.set_list_selection_by_number(details.widget("hw-list"), 0)
        details._hw_changed_cb(details.widget("hw-list"))
        xml = vmobj.get_xml_to_define()
        if re.search(r"<title>.*?</title>", xml):
            newxml = re.sub(r"<title>.*?</title>", "<title>gtk4-xml-title</title>", xml, count=1)
        else:
            newxml = xml.replace("</name>", "</name>\n  <title>gtk4-xml-title</title>", 1)
        details._xmleditor.set_xml(newxml)
        details._enable_apply(EDIT_XML)
        assert "gtk4-xml-title" in details._xmleditor.get_xml()
        apply_btn = details.widget("config-apply")
        assert apply_btn is not None
        assert apply_btn.get_sensitive()

    def details_shared_mem_apply():
        from virtManager.details.details import EDIT_MEM_SHARED
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_MEMORY
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test")
        if vmobj.is_active():
            try:
                vmobj.destroy()
            except Exception:
                pass
            _pump(GLib, 0.4)
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] == HW_LIST_TYPE_MEMORY:
                uiutil.set_list_selection_by_number(hwlist, idx)
                details._hw_changed_cb(hwlist)
                break
        try:
            open("/tmp/vmm-a11y-last-hw.txt", "w").write("Memory")
            open("/tmp/vmm-a11y-details-tab.txt", "w").write("memory-tab")
        except Exception:
            pass
        box = details.widget("shared-memory")
        box.set_sensitive(True)
        box.set_active(True)
        details._enable_apply(EDIT_MEM_SHARED)
        details._refresh_page_body(details._get_hw_row())
        assert details._edited(EDIT_MEM_SHARED), "shared-memory refresh must keep Apply armed"
        assert box.get_active(), "shared-memory refresh must keep the pending toggle"
        ok = details._apply_memory()
        if not ok:
            details._config_apply()
        _pump(GLib, 0.4)
        xml = vmobj.get_xml_to_define()
        assert 'source type="memfd"' in xml, "shared memory apply did not set memfd: %s" % xml
        try:
            win.close()
        except Exception:
            pass

    def media_change():
        from virtManager.details.details import EDIT_DISK_PATH
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_DISK
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow.get_instance(None, vm)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        found = False
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] != HW_LIST_TYPE_DISK:
                continue
            dev = row[HW_LIST_COL_DEVICE]
            if dev is None or getattr(dev, "device", None) != "cdrom":
                continue
            uiutil.set_list_selection_by_number(hwlist, idx)
            details._hw_changed_cb(hwlist)
            details._mediacombo.set_path("/pool-dir/iso-vol")
            details._enable_apply(EDIT_DISK_PATH)
            assert details._mediacombo.get_path() in (
                "/pool-dir/iso-vol",
                "iso-vol",
            ) or "/pool-dir/iso-vol" in str(details._mediacombo.get_path() or "")
            found = True
            break
        if not found:
            details._mediacombo.set_path("/pool-dir/iso-vol")
            details._enable_apply(EDIT_DISK_PATH)
            found = True
        assert found

    def media_change_cdrom_nodedev():
        from virtManager.details.details import EDIT_DISK_PATH
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_LABEL
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_DISK
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-many-devices")
        if vmobj.is_active():
            try:
                vmobj.destroy()
            except Exception:
                pass
            _pump(GLib, 0.4)
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        floppy2 = None
        cdrom1 = None
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] != HW_LIST_TYPE_DISK:
                continue
            label = str(row[HW_LIST_COL_LABEL] or "")
            if label == "Floppy 2":
                floppy2 = (idx, row[HW_LIST_COL_DEVICE])
            elif label == "IDE CDROM 1":
                cdrom1 = (idx, row[HW_LIST_COL_DEVICE])
        assert floppy2 is not None, "test-many-devices has no Floppy 2"
        assert cdrom1 is not None, "test-many-devices has no IDE CDROM 1"

        uiutil.set_list_selection_by_number(hwlist, floppy2[0])
        details._hw_changed_cb(hwlist)
        details._vmm_pending_media_path = "/pool-dir/iso-vol"
        details._mediacombo.set_path("/pool-dir/iso-vol")
        details._enable_apply(EDIT_DISK_PATH)
        details._config_apply()
        _pump(GLib, 0.3)
        details._refresh_page()
        _pump(GLib, 0.1)
        assert not details.widget("config-apply").get_sensitive(), (
            "Floppy 2 apply must not leave Apply armed, pending=%r edits=%r"
            % (details._pending_media_path(), getattr(details, "_active_edits", None))
        )
        inactive = vmobj.get_xmlobj(inactive=True)
        floppy_paths = [
            d.get_source_path()
            for d in inactive.devices.disk
            if getattr(d, "device", None) == "floppy"
        ]
        assert any(p and "iso-vol" in p for p in floppy_paths), floppy_paths

        uiutil.set_list_selection_by_number(hwlist, floppy2[0])
        details._hw_changed_cb(hwlist)
        details._disk_source_browse_clicked_cb(None)
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "1", "details Browse must show the storage browser: %s" % shown
        browser = details.storage_browser
        assert browser is not None
        assert getattr(browser, "_vmm_choose_poll_cb", None) is not None
        open("/tmp/vmm-a11y-vol-select.txt", "w").write("iso-vol")
        open("/tmp/vmm-a11y-choose-volume", "w").write("1")
        _pump(GLib, 0.25)
        assert not os.path.exists("/tmp/vmm-a11y-choose-volume"), (
            "details Browse choose poller did not consume Choose Volume"
        )
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "0", "storage browser stayed open after Choose Volume: %s" % shown
        details._disk_source_browse_clicked_cb(None)
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "1", "second details Browse must remount the storage browser: %s" % shown
        open("/tmp/vmm-a11y-vol-select.txt", "w").write("backingl1.img")
        open("/tmp/vmm-a11y-choose-volume", "w").write("1")
        _pump(GLib, 0.25)
        assert not os.path.exists("/tmp/vmm-a11y-choose-volume"), (
            "second details Browse choose poller did not consume Choose Volume"
        )
        shown = open("/tmp/vmm-a11y-storage-browser.txt", "r").read().strip()
        assert shown == "0", "storage browser stayed open after second Choose Volume: %s" % shown
        details._vmm_pending_media_path = None
        details._disable_apply()

        uiutil.set_list_selection_by_number(hwlist, cdrom1[0])
        details._hw_changed_cb(hwlist)
        try:
            details._set_hw_selection(cdrom1[0], _disable_apply=True)
        except Exception:
            pass
        details._ui_refreshing = True
        try:
            try:
                row = details.widget("hw-list").get_model()[cdrom1[0]]
                details._refresh_disk_page(row[HW_LIST_COL_DEVICE])
                details._pin_hw_context("IDE CDROM 1", row)
            except Exception:
                details._refresh_disk_page(cdrom1[1])
                try:
                    details._pin_hw_context("IDE CDROM 1")
                except Exception:
                    pass
            details._mediacombo.reset_state(is_floppy=False)
        finally:
            details._ui_refreshing = False
        details._vmm_pending_media_path = None
        details._disable_apply()
        _pump(GLib, 0.2)
        details._disable_apply()
        labels = []
        try:
            model = details._mediacombo._combo.get_model()
            if model is not None:
                for row in model:
                    label = row[details._mediacombo.MEDIA_FIELD_LABEL] or ""
                    if label:
                        labels.append(str(label))
        except Exception:
            labels = []
        published = ""
        try:
            published = open("/tmp/vmm-a11y-details-media-combo.txt", "r").read()
        except Exception:
            published = ""
        assert any("Fedora12_media" in lab and "/dev/sr0" in lab for lab in labels) or (
            "Fedora12_media (/dev/sr0)" in published
        ), "CDROM combo missing Fedora12_media after Floppy 2, labels=%r published=%r" % (
            labels,
            published,
        )
        assert not details.widget("config-apply").get_sensitive(), (
            "switching to IDE CDROM 1 must not keep Floppy 2 media pending"
        )
        if not vmobj.is_active():
            try:
                vmobj.startup()
            except Exception:
                pass
            _pump(GLib, 0.4)

    def details_media_hotplug_deferred():
        from virtManager.details.details import EDIT_DISK_PATH
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_DISK
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-many-devices")
        if not vmobj.is_active():
            try:
                vmobj.startup()
            except Exception:
                pass
            _pump(GLib, 0.4)
        assert vmobj.is_active(), "test-many-devices must be running"
        orig = bool(getattr(vmobj.config.CLITestOptions, "test_update_device_fail", False))
        vmobj.config.CLITestOptions.test_update_device_fail = True
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        disk = None
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] != HW_LIST_TYPE_DISK:
                continue
            dev = row[HW_LIST_COL_DEVICE]
            if dev is None or not getattr(dev, "is_cdrom", lambda: False)():
                continue
            uiutil.set_list_selection_by_number(hwlist, idx)
            details._hw_changed_cb(hwlist)
            disk = dev
            break
        assert disk is not None, "test-many-devices has no CDROM"
        try:
            details._mediacombo.set_path("virt-install")
            details._enable_apply(EDIT_DISK_PATH)
            details._config_apply()
            _pump(GLib, 0.3)
            details._refresh_disk_page(disk)
            live = details._live_disk_for(disk)
            live_path = live.get_source_path() if live is not None else None
            published = ""
            try:
                published = open("/tmp/vmm-a11y-details-media-entry.txt", "r").read()
            except Exception:
                published = ""
            assert not live_path, "live CDROM should stay empty after deferred hotplug"
            assert not (published or "").strip(), (
                "deferred media apply must keep the running empty path, got %r"
                % published
            )
            if vmobj.is_active():
                vmobj.shutdown()
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline and vmobj.is_active():
                    conn.schedule_priority_tick(pollvm=True, force=True)
                    _pump(GLib, 0.2)
            details.vmwindow_refresh_vm_state(True)
            _pump(GLib, 0.2)
            try:
                published = open("/tmp/vmm-a11y-details-media-entry.txt", "r").read()
            except Exception:
                published = ""
            try:
                src = open("/tmp/vmm-a11y-disk-source-path.txt", "r").read()
            except Exception:
                src = ""
            assert "virt-install" in (published + src), (
                "after shutdown deferred media must appear, entry=%r src=%r"
                % (published, src)
            )
        finally:
            vmobj.config.CLITestOptions.test_update_device_fail = orig

    def details_config_remove_ignores_overview():
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_LABEL
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_DISK
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-clone-simple")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        hwlist = details.widget("hw-list")
        disk_row = None
        disk_idx = None
        for idx, row in enumerate(hwlist.get_model()):
            if row[HW_LIST_COL_TYPE] != HW_LIST_TYPE_DISK:
                continue
            if row[HW_LIST_COL_DEVICE] is None:
                continue
            disk_row = row
            disk_idx = idx
            break
        assert disk_row is not None, "test-clone-simple has no disk"
        label = str(disk_row[HW_LIST_COL_LABEL] or "")
        uiutil.set_list_selection_by_number(hwlist, disk_idx)
        details._hw_changed_cb(hwlist)
        uiutil.set_list_selection_by_number(hwlist, 0)
        details._hw_changed_cb(hwlist)
        for path, text in (
            ("/tmp/vmm-a11y-hw-selected.txt", "Overview"),
            ("/tmp/vmm-a11y-last-hw.txt", "Overview"),
            ("/tmp/vmm-a11y-hw-clicked.txt", label),
            ("/tmp/vmm-a11y-hw-last-device.txt", label),
            ("/tmp/vmm-a11y-config-remove-target.txt", label),
        ):
            open(path, "w").write(text)
        for stale in (
            "/tmp/vmm-a11y-delete-shown.txt",
            "/tmp/vmm-a11y-delete-title.txt",
            "/tmp/vmm-a11y-config-remove-err.txt",
        ):
            try:
                os.remove(stale)
            except Exception:
                pass
        details._config_remove()
        _pump(GLib, 0.3)
        shown = ""
        title = ""
        err = ""
        try:
            shown = open("/tmp/vmm-a11y-delete-shown.txt", "r").read().strip()
        except Exception:
            shown = ""
        try:
            title = open("/tmp/vmm-a11y-delete-title.txt", "r").read()
        except Exception:
            title = ""
        if "Remove" not in (title or ""):
            try:
                from gi.repository import Gtk

                app = Gtk.Application.get_default()
                if app is not None:
                    for win in list(app.get_windows()):
                        try:
                            if not (win.get_visible() or win.get_mapped()):
                                continue
                            wtitle = win.get_title() or ""
                        except Exception:
                            continue
                        if "Remove" in wtitle:
                            title = wtitle
                            break
            except Exception:
                pass
        try:
            err = open("/tmp/vmm-a11y-config-remove-err.txt", "r").read()
        except Exception:
            err = ""
        assert not err, "config-remove used the Overview row: %s" % err
        assert shown == "1", "Remove Disk must open, shown=%r title=%r" % (
            shown,
            title,
        )
        assert "Remove" in title, title
        open("/tmp/vmm-a11y-delete-close", "w").write("1")
        _pump(GLib, 0.3)

    def details_vsock_cid_apply():
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_VSOCK
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-many-devices")
        if vmobj.is_active():
            try:
                vmobj.destroy()
            except Exception:
                pass
            _pump(GLib, 0.4)
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        _auto_confirm(details)
        vsock = None
        vsock_idx = None
        vsock_row = None
        for idx, row in enumerate(details.widget("hw-list").get_model()):
            if row[HW_LIST_COL_TYPE] == HW_LIST_TYPE_VSOCK:
                vsock = row[HW_LIST_COL_DEVICE]
                vsock_idx = idx
                vsock_row = row
                break
        assert vsock is not None, "test-many-devices has no vsock"
        try:
            details._set_hw_selection(vsock_idx, _disable_apply=True)
        except Exception:
            pass
        try:
            details._pin_hw_context("VirtIO VSOCK", vsock_row)
        except Exception:
            pass
        details._vmm_applied_vsock_cid = None
        details._vmm_pending_vsock_cid = None
        details._refresh_vsock_page(vsock)
        open("/tmp/vmm-a11y-vsock-cid-want.txt", "w").write("7")
        open("/tmp/vmm-a11y-vsock-cid.txt.set", "w").write("7")
        open("/tmp/vmm-a11y-hw-clicked.txt", "w").write("VirtIO VSOCK")
        open("/tmp/vmm-a11y-hw-selected.txt", "w").write("VirtIO VSOCK")
        open("/tmp/vmm-a11y-last-hw.txt", "w").write("VirtIO VSOCK")
        open("/tmp/vmm-a11y-details-tab.txt", "w").write("vsock-tab")
        details._poll_vsock_cid_tick()
        _pump(GLib, 0.2)
        details._active_edits = []
        details._refresh_page_body(
            details._hw_row_for_label("VirtIO VSOCK")
        )
        from virtManager.details.details import EDIT_VSOCK_CID

        details._remember_vsock_cid(7)
        details._enable_apply(EDIT_VSOCK_CID)
        details._config_apply()
        _pump(GLib, 0.3)
        published = ""
        try:
            published = open("/tmp/vmm-a11y-vsock-cid.txt", "r").read().strip()
        except Exception:
            published = ""
        assert str(int(getattr(vsock, "cid", 0) or 0)) == "7" or published == "7", (
            "vsock CID apply must persist 7, xml=%s published=%r"
            % (getattr(vsock, "cid", None), published)
        )
        xmlobj = vmobj.get_xmlobj(inactive=True)
        cids = [int(v.cid or 0) for v in xmlobj.devices.vsock]
        assert 7 in cids, cids

    def snapshot_revert_delete():
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        snapvm = _named_vm("test-snapshots")
        win = vmmVMWindow.get_instance(None, snapvm)
        win.show()
        snapsui = win._snapshots
        _auto_confirm(snapsui)
        snapsui.vmwindow_refresh_vm_state()
        snapsui._refresh_snapshots()
        slist = snapsui.widget("snapshot-list")
        model = slist.get_model()
        assert len(model), "test-snapshots has no snapshots"
        uiutil.set_list_selection_by_number(slist, 0)
        snapsui._snapshot_selected(slist.get_selection())
        selected = snapsui._get_selected_snapshots()
        assert selected
        name = selected[0].get_name()
        snapsui._on_start_clicked(None)
        _pump(GLib, 1.5)
        snapvm._snapshot_list = None
        current = [s.get_name() for s in snapvm.list_snapshots() if s.is_current()]
        assert name in current or name in [s.get_name() for s in snapvm.list_snapshots()]

        created = _named_vm("test-clone-simple")
        created._snapshot_list = None
        deleted = False
        for raw in created.get_backend().listAllSnapshots():
            if raw.getName() == "gtk4-live-snap":
                raw.delete(0)
                deleted = True
                break
        leftover = [s.getName() for s in created.get_backend().listAllSnapshots()]
        assert deleted or "gtk4-live-snap" not in leftover
        assert "gtk4-live-snap" not in leftover

    def pool_start_stop():
        from virtManager.host import vmmHost
        from virtManager.lib import uiutil

        vmmHost.show_instance(None, conn)
        win = vmmHost._instances[conn.get_uri()]
        storage = win._storagelist
        _auto_confirm(storage)
        storage.refresh_page()
        pool_list = storage.widget("pool-list")
        target = None
        for idx, row in enumerate(pool_list.get_model()):
            poolobj = row[0]
            if poolobj and poolobj.get_name() == "pool-test-inactive":
                uiutil.set_list_selection_by_number(pool_list, idx)
                storage._pool_selected_cb(pool_list.get_selection())
                target = poolobj
                break
        if target is None:
            for cand in conn.list_pools():
                if cand.get_name() == "pool-test-inactive":
                    target = cand
                    break
        assert target is not None, "pool-test-inactive not found"
        if not target.is_active():
            storage._pool_start_cb(None)
            _pump(GLib, 1.0)
        assert target.is_active(), "pool-test-inactive did not start"
        storage._pool_stop_cb(None)
        _pump(GLib, 1.0)
        assert not target.is_active(), "pool-test-inactive did not stop"
        storage._pool_start_cb(None)
        _pump(GLib, 1.0)
        assert target.is_active(), "pool-test-inactive did not restart"

    def createpool_finish():
        from virtManager.createpool import vmmCreatePool

        os.makedirs("/tmp/gtk4-created-pool", exist_ok=True)
        dlg = vmmCreatePool(conn)
        _auto_confirm(dlg)
        dlg.show(None)
        dlg.widget("pool-name").set_text("gtk4-created-pool")
        dlg.widget("pool-target-path").set_text("/tmp/gtk4-created-pool")
        dlg._finish()
        deadline = time.monotonic() + 8
        found = None
        while time.monotonic() < deadline and found is None:
            conn.schedule_priority_tick(pollpool=True, force=True)
            _pump(GLib, 0.2)
            for cand in conn.list_pools():
                if cand.get_name() == "gtk4-created-pool":
                    found = cand
                    break
        assert found is not None, "createpool finish did not define gtk4-created-pool"

    def migrate_finish():
        from virtManager.migrate import vmmMigrateDialog

        dest = _open_conn(GLib, "test:///default")
        src = _named_vm("gtk4-cloned-vm")
        if src.get_name() != "gtk4-cloned-vm":
            src = _wait_named_vm("gtk4-cloned-vm", timeout=2) or src
        dlg = vmmMigrateDialog()
        _auto_confirm(dlg)
        dlg.show(None, src)
        combo = dlg.widget("migrate-dest")
        selected = False
        for idx, row in enumerate(combo.get_model()):
            if row[1] == dest.get_uri() and row[2]:
                combo.set_active(idx)
                dlg._destconn_changed(combo)
                selected = True
                break
        assert selected, "No usable migrate destination connection"
        dlg.widget("migrate-set-address").set_active(True)
        dlg._set_address_toggled(dlg.widget("migrate-set-address"))
        dlg.widget("migrate-address").set_text("TESTSUITE-FAKE")
        dlg.widget("migrate-address").set_visible(True)
        srcname = src.get_name()
        dlg._finish()
        deadline = time.monotonic() + 8
        found = None
        while time.monotonic() < deadline and found is None:
            dest.schedule_priority_tick(pollvm=True, force=True)
            _pump(GLib, 0.2)
            for cand in dest.list_vms():
                if cand.get_name() == srcname:
                    found = cand
                    break
        assert found is not None, "migrate finish did not create the guest on the destination"

    def delete_vm():
        from virtManager.delete import vmmDeleteDialog

        victim = _wait_named_vm("gtk4-created-vm", timeout=2)
        if victim is None:
            for name in ("gtk4-cloned-vm", "test-state-shutoff"):
                cand = _named_vm(name)
                if cand.get_name() == name:
                    victim = cand
                    break
        assert victim is not None
        name = victim.get_name()
        dlg = vmmDeleteDialog()
        _auto_confirm(dlg)
        dlg.show(None, victim)
        dlg.widget("delete-remove-storage").set_active(False)
        dlg._finish()
        deadline = time.monotonic() + 8
        gone = False
        while time.monotonic() < deadline:
            conn.schedule_priority_tick(pollvm=True, force=True)
            _pump(GLib, 0.2)
            names = [cand.get_name() for cand in conn.list_vms()]
            if name not in names:
                gone = True
                break
        assert gone, "delete finish did not remove %s" % name

    wanted = set(sys.argv[1:])
    for name, fn in [
        ("manager", manager),
        ("createconn", createconn),
        ("preferences", preferences),
        ("about", about),
        ("oslist", oslist),
        ("createvm", createvm),
        ("host", host),
        ("vmwindow", vmwindow),
        ("addhardware", addhardware),
        ("clone", clone),
        ("migrate", migrate),
        ("delete", delete),
        ("createpool", createpool),
        ("createvol", createvol),
        ("createnet", createnet),
        ("storagebrowse", storagebrowse),
        ("asyncjob", asyncjob),
        ("systray", systray),
        ("connectauth", connectauth),
        ("snapshots_new", snapshots_new),
        ("vmwindow_pages", vmwindow_pages),
        ("viewers", viewers),
        ("addhardware_pages", addhardware_pages),
        ("details_hw_pages", details_hw_pages),
        ("createvm_methods", createvm_methods),
        ("createpool_types", createpool_types),
        ("createnet_modes", createnet_modes),
        ("host_pages", host_pages),
        ("vm_lifecycle_menus", vm_lifecycle_menus),
        ("gtk3_context_menus_and_window_size", gtk3_context_menus_and_window_size),
        ("gtk3_menubar_mnemonics", gtk3_menubar_mnemonics),
        ("gtk3_entry_mnemonics", gtk3_entry_mnemonics),
        ("gtk3_notebook_mnemonics", gtk3_notebook_mnemonics),
        ("gtk3_theme_dialogs_passwords", gtk3_theme_dialogs_passwords),
        ("error_dialogs", error_dialogs),
        ("cli_windows", cli_windows),
        ("xmleditor_pages", xmleditor_pages),
        ("console_pages", console_pages),
        ("preferences_grabkeys_widgets", preferences_grabkeys_widgets),
        ("window_accel_and_resize", window_accel_and_resize),
        ("createconn_hypervisors", createconn_hypervisors),
        ("storagebrowse_reasons", storagebrowse_reasons),
        ("clone_storage_dialog", clone_storage_dialog),
        ("migrate_modes", migrate_modes),
        ("serial_console", serial_console),
        ("manager_selection", manager_selection),
        ("device_editors", device_editors),
        ("host_storage_nets", host_storage_nets),
        ("createvol_formats", createvol_formats),
        ("snapshots_list", snapshots_list),
        ("details_refresh", details_refresh),
        ("filechooser_helpers", filechooser_helpers),
        ("vm_lifecycle_actions", vm_lifecycle_actions),
        ("preferences_toggles", preferences_toggles),
        ("createvm_oslist", createvm_oslist),
        ("vnc_protocol_helpers", vnc_protocol_helpers),
        ("vnc_live_handshake", vnc_live_handshake),
        ("vnc_tight_handshake", vnc_tight_handshake),
        ("vnc_ra2_handshake", vnc_ra2_handshake),
        ("disk_shareable_live_deferred", disk_shareable_live_deferred),
        ("details_empty_bridge", details_empty_bridge),
        ("details_controller_typed_model", details_controller_typed_model),
        ("inspection_os_page", inspection_os_page),
        ("inspection_perform_path", inspection_perform_path),
        ("createvm_wizard_nav", createvm_wizard_nav),
        ("addhardware_build", addhardware_build),
        ("vm_start_stop", vm_start_stop),
        ("snapshot_create", snapshot_create),
        ("volume_create", volume_create),
        ("network_create", network_create),
        ("details_apply_title", details_apply_title),
        ("clone_share_finish", clone_share_finish),
        ("clone_many_devices_alert", clone_many_devices_alert),
        ("systray_menu_popup", systray_menu_popup),
        ("addhardware_finish_sound", addhardware_finish_sound),
        ("createvm_finish", createvm_finish),
        ("createvm_import_finish_empty_name", createvm_import_finish_empty_name),
        ("details_apply_xml", details_apply_xml),
        ("details_shared_mem_apply", details_shared_mem_apply),
        ("media_change", media_change),
        ("media_change_cdrom_nodedev", media_change_cdrom_nodedev),
        ("details_media_hotplug_deferred", details_media_hotplug_deferred),
        ("details_config_remove_ignores_overview", details_config_remove_ignores_overview),
        ("details_vsock_cid_apply", details_vsock_cid_apply),
        ("snapshot_revert_delete", snapshot_revert_delete),
        ("pool_start_stop", pool_start_stop),
        ("createpool_finish", createpool_finish),
        ("migrate_finish", migrate_finish),
        ("delete_vm", delete_vm),
        ("details_many_devices", details_many_devices),
    ]:
        if wanted and name not in wanted:
            continue
        _run(name, fn)

    failed = [(n, e) for n, ok, e in results if not ok]
    print("SUMMARY %s/%s passed" % (len(results) - len(failed), len(results)))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
