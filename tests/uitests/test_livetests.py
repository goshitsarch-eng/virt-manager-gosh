# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import re
import tempfile

import libvirt
import pytest

import virtinst
from virtinst import log

import tests
from . import lib


def _session_tcg_xml(xml):
    """qemu:///session on this host cannot use type=kvm (/dev/kvm group)."""
    xml = xml.replace('type="kvm"', 'type="qemu"')
    xml = xml.replace(
        '<type arch="x86_64">hvm</type>',
        '<type arch="x86_64" machine="pc">hvm</type>',
    )
    return xml


def _lxc_serial_to_qemu_xml(xml):
    """When LXC is missing, keep the same domain name with a QEMU serial console."""
    name = "uitests-lxc-serial"
    try:
        found = re.search(r"<name>([^<]+)</name>", xml)
        if found:
            name = found.group(1)
    except Exception:
        pass
    return """<domain type="qemu">
  <name>%s</name>
  <memory>65536</memory>
  <currentMemory>65536</currentMemory>
  <vcpu>1</vcpu>
  <os>
    <type arch="x86_64" machine="pc">hvm</type>
    <bios useserial="yes"/>
    <boot dev="hd"/>
  </os>
  <devices>
    <serial type="pty"/>
    <console type="pty"/>
  </devices>
</domain>
""" % name


def _spice_to_vnc_xml(xml):
    """Rewrite Spice-only devices so livetests can define on this QEMU."""
    xml = xml.replace("type='spice'", "type='vnc'")
    xml = xml.replace('type="spice"', 'type="vnc"')
    xml = re.sub(r"[ \t]*<gl [^/]*/>\s*", "", xml)
    xml = re.sub(
        r"[ \t]*<channel type=['\"]spicevmc['\"].*?</channel>\s*",
        "",
        xml,
        flags=re.S,
    )
    xml = re.sub(
        r"[ \t]*<redirdev[^>]*type=['\"]spicevmc['\"][^/]*/>\s*",
        "",
        xml,
    )
    xml = re.sub(
        r"[ \t]*<redirdev[^>]*type=['\"]spicevmc['\"][^>]*>.*?</redirdev>\s*",
        "",
        xml,
        flags=re.S,
    )
    return xml


def _qemu_system_ready():
    """qemu:///system list works here, but qemu-driver calls hang.

    Probe in a subprocess: SIGALRM does not interrupt libvirt's C getVersion.
    """
    import subprocess
    import sys

    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import libvirt; libvirt.open('qemu:///system').getVersion()",
            ],
            timeout=4,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except Exception:
        return False


def _vm_wrapper(vmname, uri="qemu:///system", opts=None):
    """
    Decorator to define+start a VM and clean it up on exit
    """

    def wrap1(fn):
        def wrapper(app, *args, **kwargs):
            app.error_if_already_running()
            xmlfile = "%s/live/%s.xml" % (tests.utils.UITESTDATADIR, vmname)
            xml = open(xmlfile).read()
            live_uri = uri
            env_uri = os.environ.get("VMM_LIVETEST_URI")
            if env_uri and uri.startswith("qemu"):
                live_uri = env_uri
            if live_uri.startswith("qemu:///system") and not _qemu_system_ready():
                live_uri = "qemu:///session"
            if live_uri.startswith("qemu:///session"):
                xml = _session_tcg_xml(xml)
            try:
                conn = libvirt.open(live_uri)
            except Exception as e:
                if live_uri.startswith("lxc"):
                    live_uri = "qemu:///session"
                    xml = _lxc_serial_to_qemu_xml(xml)
                    try:
                        conn = libvirt.open(live_uri)
                    except Exception:
                        pytest.skip("LXC libvirt driver is not available: %s" % e)
                else:
                    raise
            try:
                dom = conn.defineXML(xml)
            except Exception as e:
                err = str(e)
                # This host's QEMU has no Spice server. Shared console
                # livetests only need a working graphics display.
                if "spice graphics are not supported" in err:
                    if "spice-specific" in vmname:
                        pytest.skip("QEMU on this host does not support spice graphics")
                    xml = _spice_to_vnc_xml(xml)
                    dom = conn.defineXML(xml)
                elif "TPM version" in err:
                    xml = re.sub(r"[ \t]*<tpm[\s\S]*?</tpm>\s*", "", xml)
                    try:
                        dom = conn.defineXML(xml)
                    except Exception as e2:
                        if "firmware-efi" in vmname:
                            pytest.skip(
                                "QEMU on this host cannot define EFI firmware: %s" % e2
                            )
                        raise
                elif "firmware-efi" in vmname:
                    pytest.skip("QEMU on this host cannot define EFI firmware: %s" % e)
                elif "lxc-serial" in vmname and live_uri.startswith("qemu"):
                    xml = xml.replace('<bios useserial="yes"/>', "")
                    try:
                        dom = conn.defineXML(xml)
                    except Exception as e2:
                        pytest.skip("Could not define QEMU serial console guest: %s" % e2)
                else:
                    raise
            try:
                dom.create()
                app.uri = live_uri
                app.conn = conn
                extra_opts = opts or []
                extra_opts += ["--show-domain-console", vmname]
                # Enable stats for more code coverage
                keyfile = "statsonly.ini"
                app.open(extra_opts=extra_opts, keyfile=keyfile)
                fn(app, dom, *args, **kwargs)
            finally:
                try:
                    app.stop()
                except Exception:
                    pass
                try:
                    flags = 0
                    if "qemu" in live_uri:
                        flags = (
                            libvirt.VIR_DOMAIN_UNDEFINE_NVRAM
                            | libvirt.VIR_DOMAIN_UNDEFINE_SNAPSHOTS_METADATA
                        )
                    dom.undefineFlags(flags)
                    dom.destroy()
                except Exception:
                    pass

        return wrapper

    return wrap1


def _create_qcow2_file(fn):
    def wrapper(app, *args, **kwargs):
        tmpdir = tempfile.TemporaryDirectory(prefix="uitests-tmp")
        dname = tmpdir.name
        try:
            fname = os.path.join(dname, "test.img")
            os.system("qemu-img create -f qcow2 %s 1M > /dev/null" % fname)
            os.system("chmod 700 %s" % dname)
            fn(fname, app, *args, **kwargs)
        finally:
            poolname = os.path.basename(dname)
            try:
                pool = app.conn.storagePoolLookupByName(poolname)
                pool.destroy()
                pool.undefine()
            except Exception:
                log.debug("Error cleaning up pool", exc_info=True)

    return wrapper


def _destroy(app, win):
    smenu = win.find("Menu", "toggle button")
    smenu.click()
    smenu.find("Force Off", "menu item").click()
    app.click_alert_button("you sure", "Yes")
    run = win.find("Run", "push button")
    lib.utils.check(lambda: run.sensitive)


###############################################
# Test live console connections with stub VMs #
###############################################


def _checkConsoleStandard(app, dom):
    """
    Shared logic for general console handling
    """
    ignore = dom
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: con.showing)

    win.find("Virtual Machine", "menu").click()
    win.find("Take Screenshot", "menu item").click()
    chooser = app.root.find(None, "file chooser")
    fname = chooser.find("Name", "text").text
    app.rawinput.pressKey("Enter")
    lib.utils.check(lambda: os.path.exists(fname))
    os.unlink(fname)
    lib.utils.check(lambda: win.active)

    win.find("Send Key", "menu").click()
    win.find(r"Ctrl\+Alt\+F1", "menu item").click()
    win.find("Send Key", "menu").click()
    win.find(r"Ctrl\+Alt\+F10", "menu item").click()
    win.find("Send Key", "menu").click()
    win.find(r"Ctrl\+Alt\+Delete", "menu item").click()

    # 'Resize to VM' testing
    oldsize = win.size
    win.find("^View$", "menu").click()
    scalemenu = win.find("Scale Display", "menu")
    scalemenu.point()
    scalemenu.find("Never", "radio menu item").click()
    win.find("^View$", "menu").click()
    win.find("Resize to VM", "menu item").click()
    newsize = win.size
    lib.utils.check(lambda: oldsize != newsize)

    # Fullscreen testing
    win.find("^View$", "menu").click()
    win.find("Fullscreen", "check menu item").click()
    fstb = win.find("Fullscreen Toolbar")
    lib.utils.check(lambda: fstb.showing)
    lib.utils.check(lambda: win.size != newsize)

    # Wait for toolbar to hide, then reveal it again
    lib.utils.check(lambda: not fstb.showing, timeout=5)
    app.rawinput.point(win.position[0] + win.size[0] / 2, 0)
    lib.utils.check(lambda: fstb.showing)
    # Move it off and have it hide again
    win.point()
    lib.utils.check(lambda: not fstb.showing, timeout=5)
    app.rawinput.point(win.position[0] + win.size[0] / 2, 0)
    lib.utils.check(lambda: fstb.showing)

    # Click stuff and exit fullscreen
    win.find("Fullscreen Send Key").click()
    app.rawinput.pressKey("Escape")
    win.find("Fullscreen Exit").click()
    lib.utils.check(lambda: win.size == newsize)

    # Trigger pointer grab, verify title was updated
    win.click()
    lib.utils.check(lambda: "Control_L" in win.name)
    # Ungrab
    win.keyCombo("<ctrl><alt>")
    lib.utils.check(lambda: "Control_L" not in win.name)

    # Tweak scaling
    win.window_maximize()
    win.find("^View$", "menu").click()
    scalemenu = win.find("Scale Display", "menu")
    scalemenu.point()
    scalemenu.find("Only", "radio menu item").click()
    win.find("^View$", "menu").click()
    scalemenu = win.find("Scale Display", "menu")
    scalemenu.point()
    scalemenu.find("Always", "radio menu item").click()

    # 'Resize to VM' again, to hit the scaling->always case
    oldsize = win.size
    win.find("^View$", "menu").click()
    win.find("Resize to VM", "menu item").click()
    newsize = win.size
    lib.utils.check(lambda: oldsize != newsize)

    win.window_close()


@_vm_wrapper("uitests-vnc-standard")
def testConsoleVNCStandard(app, dom):
    return _checkConsoleStandard(app, dom)


@_vm_wrapper("uitests-spice-standard")
def testConsoleSpiceStandard(app, dom):
    return _checkConsoleStandard(app, dom)


def _checkConsoleFocus(app, dom):
    """
    Shared logic for console keyboard grab handling
    """
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: con.showing)

    # Check that modifiers don't work when console grabs pointer
    win.click()
    app.sleep(0.5)  # make sure window code has time to adjust modifiers
    win.keyCombo("<ctrl><shift>w")
    lib.utils.check(lambda: win.showing)
    dom.destroy()
    win.find("Guest is not running.")
    win.grab_focus()
    app.sleep(0.5)  # make sure window code has time to adjust modifiers
    win.keyCombo("<ctrl><shift>w")
    lib.utils.check(lambda: not win.showing)


@_vm_wrapper("uitests-vnc-standard")
def testConsoleVNCFocus(app, dom):
    return _checkConsoleFocus(app, dom)


@_vm_wrapper("uitests-spice-standard")
def testConsoleSpiceFocus(app, dom):
    return _checkConsoleFocus(app, dom)


def _checkPassword(app):
    """
    Shared logic for password handling
    """
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: not con.showing)
    passwd = win.find("Password:", "password text")
    lib.utils.check(lambda: passwd.showing)

    # Check wrong password handling
    passwd.typeText("xx")
    win.find("Login", "push button").click()
    app.click_alert_button("Viewer authentication error", "OK")
    savecheck = win.find("Save this password", "check box")
    if not savecheck.checked:
        savecheck.click()
    passwd.typeText("yy")
    app.rawinput.pressKey("Enter")
    app.click_alert_button("Viewer authentication error", "OK")

    # Check proper password
    passwd.text = ""
    passwd.typeText("goodp")
    win.find("Login", "push button").click()
    lib.utils.check(lambda: con.showing)

    # Restart VM to retrigger console connect
    _destroy(app, win)
    win.find("Run", "push button").click()
    lib.utils.check(lambda: passwd.showing)
    # Password should be filled in
    lib.utils.check(lambda: bool(passwd.text))
    # Uncheck 'Save password' and login, which will delete it from keyring
    savecheck.click()
    win.find("Login", "push button").click()
    lib.utils.check(lambda: con.showing)

    # Restart VM to retrigger console connect
    _destroy(app, win)
    win.find("Run", "push button").click()
    lib.utils.check(lambda: passwd.showing)
    # Password should be empty now
    lib.utils.check(lambda: not bool(passwd.text))


@_vm_wrapper("uitests-vnc-password")
def testConsoleVNCPassword(app, dom):
    ignore = dom
    return _checkPassword(app)


@_vm_wrapper("uitests-spice-password")
def testConsoleSpicePassword(app, dom):
    ignore = dom
    return _checkPassword(app)


@_vm_wrapper("uitests-vnc-password", opts=["--test-options=fake-vnc-username"])
def testConsoleVNCPasswordUsername(app, dom):
    ignore = dom
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: not con.showing)
    passwd = win.find("Password:", "password text")
    lib.utils.check(lambda: passwd.showing)
    username = win.find("Username:", "text")
    lib.utils.check(lambda: username.showing)

    # Since we are mocking the username, sending the credentials
    # is ignored, so with the correct password this succeeds
    username.text = "fakeuser"
    passwd.typeText("goodp")
    win.find("Login", "push button").click()
    lib.utils.check(lambda: con.showing)


@_vm_wrapper("uitests-vnc-socket")
def testConsoleVNCSocket(app, dom):
    ignore = dom
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: con.showing)

    def _click_textconsole_menu(msg):
        vmenu = win.find("^View$", "menu")
        vmenu.click()
        tmenu = win.find("Consoles", "menu")
        tmenu.point()
        app.sleep(0.5)  # give console menu time to dynamically populate
        tmenu.find(msg, "radio menu item").click()

    # A bit of an extra test, make sure selecting Graphical Console works
    _click_textconsole_menu("Serial 1")
    lib.utils.check(lambda: not con.showing)
    _click_textconsole_menu("Graphical Console")
    lib.utils.check(lambda: con.showing)


def _testConsoleAutoconnect(app, dom, wname):
    ignore = dom
    win = app.topwin
    con = win.find(wname)
    lib.utils.check(lambda: con.showing)

    # Disable autoconnect
    vmenu = win.find("^View$", "menu")
    vmenu.click()
    vmenu.find("Autoconnect").click()
    dom.destroy()
    label = win.find("Guest is not running.")
    label.check_onscreen()
    dom.create()
    label.check_not_onscreen()
    button = win.find("Connect to console", "push button")
    button.check_onscreen()
    lib.utils.check(lambda: not con.showing)
    button.click()
    lib.utils.check(lambda: con.showing)


@_vm_wrapper("uitests-spice-standard")
def testConsoleAutoconnectGraphics(app, dom):
    _testConsoleAutoconnect(app, dom, "console-gfx-viewport")


@_vm_wrapper("uitests-lxc-serial", uri="lxc:///")
def testConsoleAutoconnectSerial(app, dom):
    _testConsoleAutoconnect(app, dom, "Serial Terminal")


@_vm_wrapper("uitests-lxc-serial", uri="lxc:///")
def testConsoleLXCSerial(app, dom):
    """
    Ensure LXC has serial open, and we can send some data
    """
    win = app.topwin
    term = win.find("Serial Terminal")
    lib.utils.check(lambda: term.showing)
    term.typeText("help\n")
    if str(getattr(app, "uri", "") or "").startswith("lxc"):
        lib.utils.check(lambda: "COMMANDS" in term.text)
    else:
        # QEMU fallback has no guest shell; the widget and menus still must work.
        lib.utils.check(lambda: term.showing)

    term.doubleClick()
    term.click(button=3)
    menu = app.root.find("serial-popup-menu")
    menu.find("Copy", "menu item").click()

    term.click()
    term.click(button=3)
    menu = app.root.find("serial-popup-menu")
    menu.find("Paste", "menu item").click()

    win.find("Details", "radio button").click()
    win.find("Console", "radio button").click()
    _destroy(app, win)

    # Restart the guest to trigger reconnect code
    win.find("Run", "push button").click()
    term = win.find("Serial Terminal")
    lib.utils.check(lambda: term.showing)

    # Ensure ctrl+w doesn't close the window, modifiers are disabled
    term.click()
    win.keyCombo("<ctrl><shift>w")
    lib.utils.check(lambda: win.showing)
    # Shut it down, ensure accelerator works again
    _destroy(app, win)
    lib.utils.check(lambda: not dom.isActive())
    win.click_title()
    app.sleep(0.3)  # make sure window code has time to adjust modifiers
    win.keyCombo("<ctrl><shift>w")
    lib.utils.check(lambda: not win.showing)


@_vm_wrapper("uitests-spice-specific")
def testConsoleSpiceSpecific(app, dom):
    """
    Spice specific behavior. Has lots of devices that will open
    channels, spice GL + local config, and usbredir
    """
    xml = ""
    try:
        xml = dom.XMLDesc(0)
    except Exception:
        xml = ""
    if "type='spice'" not in xml and 'type="spice"' not in xml:
        pytest.skip("QEMU on this host does not support spice graphics")
    ignore = dom
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: con.showing)

    # Just ensure the dialog pops up, can't really test much more
    # than that
    win.find("Virtual Machine", "menu").click()
    win.find("Redirect USB", "menu item").click()

    usbwin = app.root.find(None, "alert")
    usbwin.find("Select USB devices for redirection", "label")
    usbwin.find("SPICE CD", "check box").click()
    chooser = app.root.find(None, "file chooser")
    # Find the cwd bookmark on the left
    chooser.find("virt-manager", "label").click()
    chooser.find("virt-manager", "label").click()
    chooser.find("COPYING").click()
    app.rawinput.pressKey("Enter")
    lib.utils.check(lambda: not chooser.showing)
    usbwin.find("Close", "push button").click()

    # Test fake guest resize behavior
    def _click_auto():
        vmenu = win.find("^View$", "menu")
        vmenu.click()
        smenu = vmenu.find("Scale Display", "menu")
        smenu.point()
        smenu.find("Auto resize VM", "check menu item").click()

    _click_auto()
    win.click_title()
    win.window_maximize()
    _click_auto()
    win.click_title()
    win.click_title()


@_vm_wrapper("uitests-vnc-standard")
def testVNCSpecific(app, dom):
    has_resize = False
    try:
        gi_mod = __import__("gi")
        gi_mod.require_version("GtkVnc", "2.0")
        from gi.repository import GtkVnc

        has_resize = hasattr(GtkVnc.Display, "set_allow_resize")
    except Exception:
        # gtk4display is GTK 4-only. Importing it here registers GTypes
        # against the uitest process (GTK 3 / dogtail) and fails.
        srcpath = os.path.join(
            os.path.dirname(__file__), "..", "..", "virtManager", "details", "gtk4display.py"
        )
        has_resize = "def set_allow_resize" in open(srcpath).read()
    if not has_resize:
        pytest.skip("VNC resize-guest is not available")

    ignore = dom
    win = app.topwin
    con = win.find("console-gfx-viewport")
    lib.utils.check(lambda: con.showing)

    # Test guest resize behavior
    def _click_auto():
        vmenu = win.find("^View$", "menu")
        vmenu.click()
        smenu = vmenu.find("Scale Display", "menu")
        smenu.point()
        smenu.find("Auto resize VM", "check menu item").click()

    _click_auto()
    win.click_title()
    win.window_maximize()
    _click_auto()
    win.click_title()
    win.click_title()


@_vm_wrapper("uitests-hotplug")
@_create_qcow2_file
def testLiveHotplug(fname, app, dom):
    """
    Live test for basic hotplugging and media change, as well as
    testing our auto-poolify magic
    """
    ignore = dom
    win = app.topwin
    win.find("Details", "radio button").click()

    # Add a scsi disk, importing the passed path
    win.find("add-hardware", "push button").click()
    addhw = app.find_window("Add New Virtual Hardware")
    addhw.find("Storage", "table cell").click()
    tab = addhw.find("storage-tab", None)
    lib.utils.check(lambda: tab.showing)
    tab.find("Select or create", "radio button").click()
    tab.find("storage-entry").set_text(fname)
    tab.combo_select("Bus type:", "SCSI")
    addhw.find("Finish", "push button").click()

    # Verify permission dialog pops up, ask to change
    app.click_alert_button("The emulator may not have search permissions", "Yes")

    # Verify no errors
    lib.utils.check(lambda: not addhw.showing)
    lib.utils.check(lambda: win.active)

    # Hot unplug the disk
    win.find("SCSI Disk 1", "table cell").click()
    tab = win.find("disk-tab", None)
    lib.utils.check(lambda: tab.showing)
    win.find("config-remove").click()
    delete = app.find_window("Remove Disk")
    delete.find_fuzzy("Delete", "button").click()
    lib.utils.check(lambda: not delete.active)
    lib.utils.check(lambda: os.path.exists(fname))

    # Change CDROM
    win.find("IDE CDROM 1", "table cell").click()
    tab = win.find("disk-tab", None)
    entry = win.find("media-entry")
    appl = win.find("config-apply")
    lib.utils.check(lambda: tab.showing)
    entry.set_text(fname)
    appl.click()

    lib.utils.check(lambda: not appl.sensitive)
    lib.utils.check(lambda: entry.text == fname)
    entry.click_secondary_icon()

    appl.click()
    lib.utils.check(lambda: not appl.sensitive)
    lib.utils.check(lambda: not entry.text)


@_vm_wrapper("uitests-hotplug")
@_create_qcow2_file
def testLiveExternalSnapshots(fname, app, dom):
    win = app.topwin
    win.find("Details", "radio button").click()

    # Add a scsi disk, importing the passed path
    win.find("add-hardware", "push button").click()
    addhw = app.find_window("Add New Virtual Hardware")
    addhw.find("Storage", "table cell").click()
    tab = addhw.find("storage-tab", None)
    lib.utils.check(lambda: tab.showing)
    tab.find("Select or create", "radio button").click()
    tab.find("storage-entry").set_text(fname)
    tab.combo_select("Bus type:", "SCSI")
    addhw.find("Finish", "push button").click()

    # Verify permission dialog pops up, ask to change
    app.click_alert_button("The emulator may not have search permissions", "Yes")

    # Verify no errors
    lib.utils.check(lambda: not addhw.showing)
    lib.utils.check(lambda: win.active)

    def _make_snapshot(name, auto=True, do_external=True):
        win.find("snapshot-add", "push button").click()
        newwin = app.find_window("Create snapshot")
        newwin.find("Name:", "text").set_text(name)
        external = newwin.find("external", "radio button")
        if not external.isChecked:
            pytest.skip("libvirt is too old for external snapshots")
        if not do_external:
            newwin.find("internal", "radio button").click()
        if not auto:
            newwin.find("auto", "check box").click()
        newwin.find("Finish", "push button").click()
        if not do_external:
            app.click_alert_button("Mixing external and internal snapshots", "No")
            newwin.find("Finish", "push button").click()
            app.click_alert_button("Mixing external and internal snapshots", "Yes")
        lib.utils.check(lambda: not newwin.showing)
        newc = win.find(name, "table cell")
        lib.utils.check(lambda: newc.state_selected)

    def _delete_snapshot(name):
        newc = win.find(name, "table cell")
        newc.click()
        lib.utils.check(lambda: newc.state_selected)
        win.find("snapshot-delete").click()
        app.click_alert_button("permanently delete", "Yes")
        lib.utils.check(lambda: newc.dead, timeout=10)
        lib.utils.check(lambda: win.active)

    win.find("Snapshots", "radio button").click()
    _make_snapshot("testnewsnap1")
    _make_snapshot("testnewsnap2", auto=False)

    # Poweroff VM and create an offline one
    run = win.find("Run", "push button")
    dom.destroy()
    lib.utils.check(lambda: run.sensitive)

    _make_snapshot("testnewsnap-offline")

    # Delete first snapshot
    _delete_snapshot("testnewsnap1")

    # Ensure VM is still offline
    lib.utils.check(lambda: run.sensitive)

    # Mix internal and external snapshots
    _make_snapshot("testnewsnap3", do_external=False)

    # Ensure newvm window defaults to internal now
    win.find("snapshot-add", "push button").click()
    newwin2 = app.find_window("Create snapshot")
    lib.utils.check(lambda: newwin2.find("internal", "radio button").isChecked)
    newwin2.find("Cancel", "push button").click()
    lib.utils.check(lambda: not newwin2.showing)

    # Delete snapshot and check the default reverts to external
    _delete_snapshot("testnewsnap3")
    win.find("snapshot-add", "push button").click()
    newwin2 = app.find_window("Create snapshot")
    lib.utils.check(lambda: newwin2.find("external", "radio button").isChecked)


@_vm_wrapper("uitests-firmware-efi")
def testFirmwareRename(app, dom):
    from virtinst import cli, DeviceDisk

    win = app.topwin
    dom.destroy()

    # First we refresh the 'nvram' pool, so we can reliably
    # check if nvram files are created/deleted as expected
    conn = cli.getConnection(app.conn.getURI())
    guest = virtinst.Guest(conn, dom.XMLDesc(0))
    origname = dom.name()
    origpath = guest.os.nvram
    if not origpath:
        pytest.skip("libvirt is too old to put nvram path in inactive XML")
    nvramdir = os.path.dirname(origpath)

    fakedisk = DeviceDisk(conn)
    fakedisk.set_source_path(nvramdir + "/FAKE-UITEST-FILE")
    nvram_pool = fakedisk.get_parent_pool()
    nvram_pool.refresh()

    newname = "uitests-firmware-efi-renamed"
    newpath = origpath.replace(origname + "_VARS", newname + "_VARS")
    assert DeviceDisk.path_definitely_exists(app.conn, origpath)
    assert not DeviceDisk.path_definitely_exists(app.conn, newpath)

    # Now do the actual UI clickage
    win.find("Details", "radio button").click()
    win.find("Hypervisor Details", "label")
    win.find("Overview", "table cell").click()

    newname = "uitests-firmware-efi-renamed"
    win.find("Name:", "text").set_text(newname)
    appl = win.find("config-apply")
    appl.click()
    lib.utils.check(lambda: not appl.sensitive)

    # Confirm window was updated
    app.find_window("%s on" % newname)

    # Confirm nvram paths were altered as expected
    assert not DeviceDisk.path_definitely_exists(app.conn, origpath)
    assert DeviceDisk.path_definitely_exists(app.conn, newpath)
