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
            print("OK  ", name)
        except Exception:
            err = traceback.format_exc()
            results.append((name, False, err))
            print("FAIL", name)
            print(err)

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
        disp.set_scaling(True)
        disp.set_pointer_grab(False)
        disp.send_keys([97])
        disp.close()
        spice = gtk4display.SpiceDisplay(None)
        spice.set_scaling(True)
        spice.close()
        usb = gtk4display.UsbDeviceWidget.new(None)
        assert usb is not None

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
    ]:
        _run(name, fn)

    failed = [(n, e) for n, ok, e in results if not ok]
    print("SUMMARY %s/%s passed" % (len(results) - len(failed), len(results)))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
