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

    conn = vmmConnectionManager.get_instance().add_conn(uri)
    done = []

    def _done(_src, err):
        done.append(err)

    conn.connect("open-completed", _done)
    conn.open()
    deadline = time.monotonic() + timeout
    ctx = GLib.MainContext.default()
    while not done and time.monotonic() < deadline:
        ctx.iteration(True)
    if not done:
        raise RuntimeError("Timed out opening %s" % uri)
    if done[0]:
        raise RuntimeError("Failed to open %s: %s" % (uri, done[0]))
    _pump(GLib, 0.2)
    return conn


def _first_vm(conn):
    vms = conn.list_vms()
    if not vms:
        raise RuntimeError("No VMs on testdriver connection")
    return vms[0]


def _first_pool(conn):
    pools = conn.list_pools()
    return pools[0] if pools else None


def main():
    _compile_schemas()
    Adw, GLib, Gtk = _init_gtk()
    from virtinst import BuildConfig
    from virtManager.config import vmmConfig
    from virtManager.lib.testmock import CLITestOptionsClass

    vmmConfig.get_instance(BuildConfig, CLITestOptionsClass([]))

    from virtManager.engine import vmmEngine

    engine = vmmEngine.get_instance()
    ignore = engine

    testdriver = os.path.join(TOPDIR, "tests", "data", "testdriver", "testdriver.xml")
    uris = []
    if os.path.exists(testdriver):
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
        dlg.show(None, vm)

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
    ]:
        _run(name, fn)

    failed = [(n, e) for n, ok, e in results if not ok]
    print("SUMMARY %s/%s passed" % (len(results) - len(failed), len(results)))
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
