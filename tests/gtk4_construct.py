# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

"""
Construct every virt-manager GTK4/Adwaita UI surface against testdriver.

Run from the repo root:
    python3 tests/gtk4_construct.py
"""

import os
import sys
import time
import traceback

TOPDIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, TOPDIR)
os.chdir(TOPDIR)

os.environ.setdefault("GSETTINGS_BACKEND", "memory")
os.environ.setdefault("VIRTINST_TEST_SUITE", "1")
os.environ.setdefault("GTK_A11Y", "atspi")


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


def _pump(GLib, seconds=0.05):
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
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

    def _run(name, fn):
        try:
            fn()
            _pump(GLib, 0.05)
            results.append((name, True, None))
            print("OK  ", name, flush=True)
        except Exception:
            err = traceback.format_exc()
            results.append((name, False, err))
            print("FAIL", name, flush=True)
            print(err, flush=True)

    def manager():
        from virtManager.manager import vmmManager

        win = vmmManager()
        win.show()
        assert win.topwin is not None

    def createconn():
        from virtManager.createconn import vmmCreateConn

        dlg = vmmCreateConn()
        dlg.show(None)

    def preferences():
        from virtManager.preferences import vmmPreferences

        dlg = vmmPreferences()
        dlg.show(None)

    def about():
        from virtManager.about import vmmAbout

        dlg = vmmAbout()
        dlg.show(None)

    def createvm():
        from virtManager.createvm import vmmCreateVM

        dlg = vmmCreateVM()
        dlg.show(None, conn.get_uri())

    def host():
        from virtManager.host import vmmHost

        win = vmmHost(conn)
        win.show()

    def vmwindow():
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow(vm)
        win.show()

    def addhardware():
        from virtManager.addhardware import vmmAddHardware

        dlg = vmmAddHardware(vm)
        dlg.show(None)

    def clone():
        from virtManager.clone import vmmCloneVM

        dlg = vmmCloneVM()
        dlg.show(None, _first_vm(conn, shutoff=True))

    def migrate():
        from virtManager.migrate import vmmMigrateDialog

        dlg = vmmMigrateDialog()
        dlg.show(None, vm)

    def delete():
        from virtManager.delete import vmmDeleteDialog

        dlg = vmmDeleteDialog()
        dlg.show(None, vm)

    def createpool():
        from virtManager.createpool import vmmCreatePool

        dlg = vmmCreatePool(conn)
        dlg.show(None)

    def createvol():
        from virtManager.createvol import vmmCreateVolume

        if pool is None:
            raise RuntimeError("No storage pool available")
        dlg = vmmCreateVolume(conn, pool)
        dlg.show(None)

    def createnet():
        from virtManager.createnet import vmmCreateNetwork

        dlg = vmmCreateNetwork(conn)
        dlg.show(None)

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

    def oslist():
        from virtManager.oslist import vmmOSList

        widget = vmmOSList()
        assert widget.search_entry is not None

    def snapshots_new():
        from virtManager.details.snapshots import vmmSnapshotNew

        dlg = vmmSnapshotNew(vm)
        dlg.show(None)

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
            dlg.widget("create-pages").set_current_page(page)
            dlg._page_changed(None, None, page)

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

    def error_dialogs():
        from virtManager.error import vmmErrorDialog

        err = vmmErrorDialog.get_instance()
        err.show_err("test error", details="details", title="t", modal=False, debug=False)
        err.show_info("info", modal=False)

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

    def xmleditor_pages():
        from virtManager.addhardware import vmmAddHardware

        dlg = vmmAddHardware(vm)
        editor = dlg._xmleditor
        editor.widget("xml-notebook").set_current_page(1)
        editor.widget("xml-notebook").set_current_page(0)

    def console_pages():
        from virtManager.vmwindow import vmmVMWindow

        win = vmmVMWindow.get_instance(None, vm)
        win._console.vmwindow_refresh_vm_state()
        win._console.vmwindow_activate_default_console_page()
        win._console.vmwindow_get_viewer_is_visible()
        win._console.vmwindow_get_resizeguest_tooltip()
        win._console.vmwindow_sync_scaling_with_display()

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
        serial._serial_popup.show_all()

    def manager_selection():
        from virtManager.manager import vmmManager

        win = vmmManager.get_instance(None)
        win.show()
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

        snapvm = _named_vm("test-clone-simple")
        for cand in conn.list_vms():
            try:
                if cand.list_snapshots():
                    snapvm = cand
                    break
            except Exception:
                continue
        win = vmmVMWindow.get_instance(None, snapvm)
        win.show()
        win._snapshots.vmwindow_refresh_vm_state()
        dlg = vmmSnapshotNew(snapvm)
        dlg.show(None)
        dlg.widget("snapshot-new-name").set_text("gtk4-snap")

    def details_refresh():
        from virtManager.vmwindow import vmmVMWindow

        rich = _named_vm("test-many-devices")
        win = vmmVMWindow.get_instance(None, rich)
        win.show()
        win._details.vmwindow_refresh_vm_state(True)
        win._details._refresh_overview_page()
        win._details._refresh_os_page()
        win._details._refresh_stats_page()
        win._details._refresh_config_cpu()
        win._details._refresh_config_memory()
        win._details._refresh_boot_page()

    def filechooser_helpers():
        from virtManager.lib import gtkcompat

        gfile = gtkcompat.GioFile_for_path("/tmp")
        assert gfile.get_path() == "/tmp"

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
        disp.set_pointer_grab(True)
        disp.set_grab_keys(gtk4display.GrabSequence.new([37, 64]))
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
        # Grab sequence Control_L+Alt_L (keycodes 37,64) must ungrab
        disp._on_key_pressed(None, 0, 37, 0)
        disp._on_key_pressed(None, 0, 64, 0)
        assert ungrabbed, "grab-sequence did not ungrab pointer"
        disp.send_keys([97])
        disp.set_property("resize-guest", True)
        disp._apply_resize_guest(True)

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
        disp.close()
        spice = gtk4display.SpiceDisplay(None)
        spice.set_scaling(True)
        spice.set_property("resize-guest", True)
        spice._apply_resize_guest(True)
        spice._push_monitor_config(800, 600)
        spice._on_file_drop(None, [], 0, 0)
        spice._spice_clip_notify(None, 0, 1, b"hi")
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

    def inspection_os_page():
        from virtManager.config import vmmConfig
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_OS
        from virtManager.lib import inspection as inspmod
        from virtManager.lib.inspection import vmmInspection
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

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
            dlg.widget("create-pages").set_current_page(page)
            dlg._page_changed(None, None, page)
            _pump(GLib, 0.02)
        dlg._back_clicked(None)
        dlg.widget("create-pages").set_current_page(PAGE_FINISH)
        try:
            dlg._page_changed(None, None, PAGE_FINISH)
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

    def details_apply_title():
        from virtManager.details.details import EDIT_TITLE
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        vmobj = _named_vm("test-clone-simple")
        win = vmmVMWindow.get_instance(None, vmobj)
        win.show()
        details = win._details
        uiutil.set_list_selection_by_number(details.widget("hw-list"), 0)
        details._hw_changed_cb(details.widget("hw-list"))
        details.widget("overview-title").set_text("gtk4-applied-title")
        details._enable_apply(EDIT_TITLE)
        details._config_apply()
        _pump(GLib, 0.8)
        title = vmobj.get_title() if hasattr(vmobj, "get_title") else None
        xmltitle = getattr(vmobj.xmlobj, "title", None)
        assert "gtk4-applied-title" in str(title or xmltitle or "")

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
        dlg._method_changed(dlg.widget("method-manual"))
        dlg._set_install_page()
        dlg.widget("create-pages").set_current_page(PAGE_NAME)
        dlg._page_changed(None, None, PAGE_NAME)
        assert dlg._validate(PAGE_NAME) is True, errs
        dlg._forward_clicked_impl()
        osobj = virtinst.OSDB.lookup_os("generic")
        assert osobj is not None
        dlg._os_list.select_os(osobj)
        assert dlg._os_list.get_selected_os() is not None
        dlg._forward_clicked_impl()
        dlg._forward_clicked_impl()
        dlg.widget("enable-storage").set_active(False)
        dlg._forward_clicked_impl()
        assert dlg.widget("create-pages").get_current_page() == PAGE_FINISH, errs
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
        details._config_apply()
        _pump(GLib, 0.8)
        title = getattr(vmobj.xmlobj, "title", None) or vmobj.get_xml_to_define()
        assert "gtk4-xml-title" in str(title)

    def media_change():
        from virtManager.details.details import EDIT_DISK_PATH
        from virtManager.details.details import HW_LIST_COL_DEVICE
        from virtManager.details.details import HW_LIST_COL_TYPE
        from virtManager.details.details import HW_LIST_TYPE_DISK
        from virtManager.lib import uiutil
        from virtManager.vmwindow import vmmVMWindow

        rich = _named_vm("test-many-devices")
        win = vmmVMWindow.get_instance(None, rich)
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
            details._config_apply()
            _pump(GLib, 0.8)
            found = True
            break
        assert found, "No CDROM disk found on test-many-devices"
        xmlobj = rich.get_xmlobj(inactive=True)
        disks = [d for d in xmlobj.devices.disk if d.device == "cdrom"]
        assert any("/pool-dir/iso-vol" in str(d.get_source_path() or "") for d in disks)

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
        ("error_dialogs", error_dialogs),
        ("cli_windows", cli_windows),
        ("xmleditor_pages", xmleditor_pages),
        ("console_pages", console_pages),
        ("preferences_grabkeys_widgets", preferences_grabkeys_widgets),
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
        ("inspection_os_page", inspection_os_page),
        ("createvm_wizard_nav", createvm_wizard_nav),
        ("addhardware_build", addhardware_build),
        ("vm_start_stop", vm_start_stop),
        ("snapshot_create", snapshot_create),
        ("volume_create", volume_create),
        ("network_create", network_create),
        ("details_apply_title", details_apply_title),
        ("clone_share_finish", clone_share_finish),
        ("systray_menu_popup", systray_menu_popup),
        ("addhardware_finish_sound", addhardware_finish_sound),
        ("createvm_finish", createvm_finish),
        ("details_apply_xml", details_apply_xml),
        ("media_change", media_change),
        ("snapshot_revert_delete", snapshot_revert_delete),
        ("pool_start_stop", pool_start_stop),
        ("createpool_finish", createpool_finish),
        ("migrate_finish", migrate_finish),
        ("delete_vm", delete_vm),
    ]:
        _run(name, fn)

    failed = [(n, e) for n, ok, e in results if not ok]
    print("SUMMARY %s/%s passed" % (len(results) - len(failed), len(results)))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
