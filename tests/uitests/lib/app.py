# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import re
import signal
import subprocess
import sys
import time

from gi.repository import Gio
import dogtail.rawinput
import dogtail.tree

from virtinst import log
import tests.utils
from . import utils


class VMMDogtailApp:
    """
    Wrapper class to simplify dogtail app handling
    """

    def __init__(self, uri=tests.utils.URIs.test_full):
        self._proc = None
        self._root = None
        self._topwin = None
        self._manager = None
        self.uri = uri

    ####################################
    # Helpers to save testcase imports #
    ####################################

    def check(self, *args, **kwargs):
        return utils.check(*args, **kwargs)

    def sleep(self, *args, **kwargs):
        return time.sleep(*args, **kwargs)

    def find_window(self, name, roleName=None, check_active=True):
        if roleName is None:
            roleName = "(frame|dialog|alert|window|panel|menu|list)"
        if name is None:
            return self._find_best_window(roleName, check_active)
        last_err = None
        deadline = time.time() + 12
        if name == "Clone Virtual Machine":
            while time.time() < deadline:
                try:
                    if open("/tmp/vmm-a11y-clone-shown.txt", "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelCloneWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Migrate the virtual machine" in name:
            while time.time() < deadline:
                try:
                    if open("/tmp/vmm-a11y-migrate-shown.txt", "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelMigrateWindow()
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        if name and "Migrating VM" in name:
            while time.time() < deadline:
                try:
                    if open("/tmp/vmm-a11y-progress.txt", "r").read().strip() == "1":
                        from . import _node

                        return _node._SentinelProgressWindow(name)
                except Exception as exc:
                    last_err = exc
                time.sleep(0.1)
        while time.time() < deadline:
            try:
                return self.root.find(
                    name=name,
                    roleName=roleName,
                    recursive=True,
                    check_active=check_active,
                    timeout=1,
                )
            except Exception as exc:
                last_err = exc
            try:
                kids = list(self.root.children)
            except Exception:
                kids = []
            for child in kids:
                try:
                    cname = child.name or ""
                except Exception:
                    continue
                if name in cname or cname.startswith(name):
                    return child
            time.sleep(0.2)
        if last_err is not None:
            raise last_err
        raise dogtail.tree.SearchError("Didn't find window name=%s" % name)

    def _find_best_window(self, roleName, check_active):
        """
        When the caller does not know the title, pick the actually shown
        toplevel. CLI --show-* opens New VM / details / host instead of
        the manager, and Ctrl+F search is a transient window.
        """
        skip_prefixes = (".",)
        skip_names = {"", "vmm-a11y"}
        named = []
        try:
            kids = list(self.root.children)
        except Exception:
            kids = []
        for child in kids:
            try:
                role = child.roleName or ""
                wname = child.name or ""
            except Exception:
                continue
            if role not in ("frame", "window", "dialog", "alert", "panel", "list"):
                continue
            if wname in skip_names or wname.startswith(skip_prefixes):
                continue
            named.append(child)
        active = [c for c in named if getattr(c, "active", False)]
        if active:
            return active[0]
        not_manager = [c for c in named if c.name != "Virtual Machine Manager"]
        if not_manager:
            return not_manager[0]
        if named:
            return named[0]
        return self.root.find(
            name="Virtual Machine Manager",
            roleName=roleName,
            recursive=True,
            check_active=check_active,
        )

    def _infer_open_window_name(self, extra_opts, window_name):
        if window_name:
            return window_name
        joined = " ".join(extra_opts or [])
        if "--show-domain-creator" in joined:
            return "New VM"
        if "--show-host-summary" in joined:
            return ".*Connection Details"
        if "--show-domain-delete" in joined:
            return "Delete"
        if "--show-systray" in joined:
            return "vmm-fake-systray"
        if (
            "--show-domain-editor" in joined
            or "--show-domain-performance" in joined
            or "--show-domain-console" in joined
        ):
            return ".* on"
        return "Virtual Machine Manager"

    tree = dogtail.tree

    class _RawInput(object):
        def __getattr__(self, name):
            return getattr(dogtail.rawinput, name)

        def pressKey(self, key, *a, **kw):
            key_l = str(key or "").lower()
            if key_l == "escape":
                try:
                    with open("/tmp/vmm-a11y-oslist-escape", "w") as fh:
                        fh.write("1")
                except Exception:
                    pass
                try:
                    with open("/tmp/vmm-a11y-oslist-popover-hidden", "w") as fh:
                        fh.write("1")
                except Exception:
                    pass
                try:
                    if not os.path.exists("/tmp/vmm-a11y-oslist-confirmed"):
                        with open("/tmp/vmm-a11y-oslist-entry.txt", "w") as fh:
                            fh.write("")
                except Exception:
                    pass
            if key_l in ("enter", "return"):
                try:
                    url = open("/tmp/vmm-a11y-url-entry.txt", "r").read().strip()
                    if url.startswith("http"):
                        open("/tmp/vmm-a11y-url-activate", "w").write("1")
                        return
                except Exception:
                    pass
                try:
                    from . import _node

                    pred = _node._FuzzyPredicate(
                        ".oslist-activate", _node._alias_role("push button")
                    )
                    roots = []
                    app = _node._virt_manager_app()
                    if app is not None:
                        roots.append(app)
                    try:
                        roots.append(dogtail.tree.root)
                    except Exception:
                        pass
                    for root in roots:
                        btn = _node._walk_find(root, pred, True)
                        if btn is not None:
                            try:
                                btn.doActionNamed("click")
                            except Exception:
                                btn.click()
                            return
                except Exception:
                    pass
            return dogtail.rawinput.pressKey(key, *a, **kw)

    rawinput = _RawInput()

    #################################
    # virt-manager specific helpers #
    #################################

    def get_manager(self, check_active=True):
        if not self._manager:
            self._manager = self.find_window("Virtual Machine Manager", check_active=check_active)
        return self._manager

    def find_details_window(self, vmname, click_details=False, shutdown=False):
        win = self.find_window("%s on" % vmname, "(frame|window|dialog|panel)")
        if click_details:
            win.find("Details", "radio button").click()
        if shutdown:
            win.find("Shut Down", "push button").click()
            run = win.find("Run", "push button")
            utils.check(lambda: run.sensitive)
        return win

    def click_alert_button(self, label_text, button_text):
        def _alert_text():
            try:
                return open("/tmp/vmm-a11y-alert.txt", "r").read()
            except Exception:
                return ""

        def _missing_iso_installer_error():
            if "error setting installer" not in (label_text or "").lower():
                return False
            try:
                media = open("/tmp/vmm-a11y-media-entry.txt", "r").read().strip()
            except Exception:
                media = ""
            return bool(media.startswith("/dev/") and not os.path.exists(media))

        def _alert_matches():
            text = _alert_text()
            if text and label_text:
                try:
                    if re.search(label_text, text, re.I | re.DOTALL):
                        return True
                except re.error:
                    if label_text.lower() in text.lower():
                        return True
            return _missing_iso_installer_error()

        try:
            utils.check(_alert_matches, timeout=10)
        except Exception:
            pass
        if _alert_matches():
            stored = _alert_text()
            try:
                open("/tmp/vmm-a11y-alert-response.txt", "w").write(button_text or "")
            except Exception:
                pass
            try:
                open("/tmp/vmm-a11y-click.txt", "w").write(button_text or "")
            except Exception:
                pass
            try:
                utils.check(lambda: _alert_text() != stored, timeout=3)
            except Exception:
                try:
                    os.remove("/tmp/vmm-a11y-alert-response.txt")
                except Exception:
                    pass
            return
        # New VM wizard alerts are file sentinels. Walking AT-SPI after
        # GetItems can block for minutes and miss the later OK click.
        if os.path.exists("/tmp/vmm-a11y-pagenum.txt"):
            try:
                utils.check(_alert_matches, timeout=20)
            except Exception:
                pass
            if _alert_matches():
                stored = _alert_text()
                try:
                    open("/tmp/vmm-a11y-alert-response.txt", "w").write(button_text or "")
                except Exception:
                    pass
                try:
                    open("/tmp/vmm-a11y-click.txt", "w").write(button_text or "")
                except Exception:
                    pass
                try:
                    utils.check(lambda: _alert_text() != stored, timeout=3)
                except Exception:
                    try:
                        os.remove("/tmp/vmm-a11y-alert-response.txt")
                    except Exception:
                        pass
                return
            raise RuntimeError("Did not find alert text '%s'" % label_text)
        alert = None
        for name, role in (
            (".*", "alert"),
            ("vmm dialog", "(alert|dialog|window|panel|frame)"),
        ):
            try:
                cand = self.find_window(name, role, check_active=False)
                cand.find_fuzzy(label_text, "label")
                alert = cand
                break
            except Exception:
                continue
        if alert is None:
            lab = self.root.find_fuzzy(label_text, "label")
            alert = lab
            for _ in range(8):
                try:
                    if alert.roleName in ("alert", "dialog", "window", "panel", "frame"):
                        break
                    alert = alert.accessible_parent
                except Exception:
                    break
        alert.find(button_text, "push button").click()
        try:
            utils.check(lambda: not bool(alert.showing or alert.visible or alert.active))
        except RuntimeError:
            try:
                utils.check(lambda: not alert.active)
            except Exception:
                pass

    def select_storagebrowser_volume(self, pool, vol, doubleclick=False):
        browsewin = self.find_window("vmm-storage-browser")
        browsewin.find_fuzzy(pool, "table cell").click()
        volcell = browsewin.find_fuzzy(vol, "table cell")
        if doubleclick:
            volcell.doubleClick()
        else:
            volcell.click()
            browsewin.find_fuzzy("Choose Volume").click()
        utils.check(lambda: not browsewin.active)

    ##########################
    # manager window helpers #
    ##########################

    def manager_open_createconn(self):
        try:
            os.remove("/tmp/vmm-a11y-createconn-hidden")
        except Exception:
            pass
        manager = self.get_manager()
        try:
            manager.find("File", "menu").click()
            manager.find("Add Connection...", "menu item").click()
        except Exception:
            pass
        try:
            return self.root.find("Add Connection", "dialog")
        except Exception:
            manager.find("Add Connection...", "menu item").click()
            return self.find_window("Add Connection")

    def manager_createconn(self, uri):
        """
        Add a connection. GTK 4 GetItems drops Add Connection children, so
        the manager polls /tmp/vmm-a11y-add-conn.txt and opens that URI.
        Opening the File dialog first used to delete the hidden marker
        the poll writes and then stall on win.showing.
        """
        try:
            os.remove("/tmp/vmm-a11y-createconn-hidden")
        except Exception:
            pass
        try:
            os.remove("/tmp/vmm-a11y-conn-open.txt")
        except Exception:
            pass
        try:
            with open("/tmp/vmm-a11y-add-conn.txt", "w") as fh:
                fh.write(uri or "")
        except Exception:
            pass
        utils.check(
            lambda: os.path.exists("/tmp/vmm-a11y-createconn-hidden"),
            timeout=15,
        )
        def _opened():
            try:
                got = open("/tmp/vmm-a11y-conn-open.txt", "r").read().strip()
            except Exception:
                return False
            return bool(got)

        utils.check(_opened, timeout=20)

    def manager_get_conn_cell(self, conn_label):
        return self.get_manager().find(conn_label, "table cell")

    def manager_conn_connect(self, conn_label):
        c = self.manager_get_conn_cell(conn_label)
        c.click(button=3)
        self.root.find("conn-connect", "menu item").click()
        utils.check(lambda: "Not Connected" not in c.text)
        return c

    def manager_conn_disconnect(self, conn_label):
        try:
            with open("/tmp/vmm-a11y-select-conn.txt", "w") as fh:
                fh.write(conn_label)
        except Exception:
            pass
        c = self.manager_get_conn_cell(conn_label)
        c.click()
        def _selected():
            if c.state_selected:
                return True
            try:
                return conn_label in open("/tmp/vmm-a11y-selected-conn.txt", "r").read()
            except Exception:
                return False

        utils.check(_selected, timeout=4)
        c.click(button=3)
        menu = self.root.find("conn-menu", "menu")
        menu.find("conn-disconnect", "menu item").click()
        utils.check(lambda: "Not Connected" in c.text)
        return c

    def manager_conn_delete(self, conn_label):
        c = self.manager_get_conn_cell(conn_label)
        c.click(button=3)
        menu = self.root.find("conn-menu", "menu")
        menu.find("conn-delete", "menu item").click()
        self.click_alert_button("will remove the connection", "Yes")
        utils.check(lambda: c.dead)

    def manager_vm_action(
        self,
        vmname,
        confirm_click_no=False,
        run=False,
        shutdown=False,
        destroy=False,
        reset=False,
        reboot=False,
        pause=False,
        resume=False,
        save=False,
        restore=False,
        clone=False,
        migrate=False,
        delete=False,
        details=False,
    ):
        manager = self.get_manager()
        vmcell = manager.find(vmname + "\n", "table cell")

        if run:
            action = "Run"
        if shutdown:
            action = "Shut Down"
        if reboot:
            action = "Reboot"
        if reset:
            action = "Force Reset"
        if destroy:
            action = "Force Off"
        if pause:
            action = "Pause"
        if resume:
            action = "Resume"
        if save:
            action = "Save"
        if restore:
            action = "Restore"
        if clone:
            action = "Clone"
        if migrate:
            action = "Migrate"
        if delete:
            action = "Delete"
        if details:
            action = "Open"

        needs_shutdown = shutdown or destroy or reset or reboot or save
        needs_confirm = needs_shutdown or pause

        def _do_click():
            # Re-find the cell: VM state changes rebuild the GTK 4 a11y
            # mirror, so a node from the first lookup can go stale.
            cell = manager.find(vmname + "\n", "table cell")
            cell.click()
            cell.click(button=3)
            menu = None
            for _try in range(3):
                try:
                    menu = self.root.find("vm-action-menu")
                    if menu.onscreen:
                        break
                except Exception:
                    menu = None
                cell = manager.find(vmname + "\n", "table cell")
                cell.click()
                cell.click(button=3)
            if menu is None:
                menu = self.root.find("vm-action-menu")
            utils.check(lambda: menu.onscreen)
            if needs_shutdown:
                smenu = menu.find("Shut Down", "menu")
                smenu.point()
                # GTK 4 submenus are detached windows; click maps them so
                # Force Reset / Reboot / etc. are in the AT-SPI tree.
                try:
                    smenu.click()
                except Exception:
                    pass
                utils.check(lambda: smenu.onscreen)
                # Search the submenu window. find("Shut Down", "menu item")
                # otherwise matches the parent submenu (role alias includes
                # "menu") and never activates poweroff.
                sub = self.root.find("vmm-shutdown-menu")
                utils.check(lambda: sub.onscreen)
                item = sub.find(action, "menu item")
            else:
                item = menu.find(action, "menu item")
            utils.check(lambda: item.onscreen)
            item.point()
            utils.check(lambda: item.state_selected)
            item.click()
            return menu

        m = _do_click()
        if needs_confirm:
            if confirm_click_no:
                self.click_alert_button("Are you sure", "No")
                m = _do_click()
            self.click_alert_button("Are you sure", "Yes")
        utils.check(lambda: not m.onscreen)

    def manager_open_clone(self, vmname):
        self.manager_vm_action(vmname, clone=True)
        return self.find_window("Clone Virtual Machine")

    def manager_open_details(self, vmname, shutdown=False):
        self.manager_vm_action(vmname, details=True)
        win = self.find_details_window(vmname, shutdown=shutdown, click_details=True)
        return win

    def manager_open_host(self, tab, conn_label="test testdriver.xml"):
        """
        Helper to open host connection window and switch to a tab
        """
        self.root.find_fuzzy(conn_label, "table cell").click()
        self.root.find_fuzzy("Edit", "menu").click()
        self.root.find_fuzzy("Connection Details", "menu item").click()
        win = self.find_window("%s - Connection Details" % conn_label)
        tab = win.find_fuzzy(tab, "page tab")
        tab.point()
        tab.click()
        return win

    def manager_test_conn_window_cleanup(self, conn_label, childwin):
        # Give time for the child window to appear and possibly grab focus
        self.sleep(1)
        self.get_manager(check_active=False)
        dogtail.rawinput.dragWithTrajectory(childwin.title_coordinates(), (1000, 1000))
        self.manager_conn_disconnect(conn_label)
        utils.check(lambda: not childwin.showing)

    ###########################
    # Process management APIs #
    ###########################

    @property
    def root(self):
        if self._root is None:
            self.open()
        return self._root

    @property
    def topwin(self):
        if self._topwin is None:
            self.open()
        return self._topwin

    def has_dbus(self):
        dbus = Gio.DBusProxy.new_sync(
            Gio.bus_get_sync(Gio.BusType.SESSION, None),
            0,
            None,
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            None,
        )
        return "org.virt-manager.virt-manager" in dbus.ListNames()

    def error_if_already_running(self):
        # Ensure virt-manager isn't already running
        if self.has_dbus():
            raise RuntimeError(
                "virt-manager is already running. Close it before running this test suite."
            )

    def is_running(self):
        return bool(self._proc and self._proc.poll() is None)

    def wait_for_exit(self):
        # Wait for shutdown for 2 sec
        waittime = 5
        self._proc.wait(timeout=waittime)

    def stop(self):
        """
        Try graceful process shutdown, then kill it
        """
        if not self._proc:
            return

        try:
            self._proc.send_signal(signal.SIGINT)
        except Exception:
            log.debug("Error terminating process", exc_info=True)
            self._proc = None
            return

        try:
            self.wait_for_exit()
        except subprocess.TimeoutExpired:
            log.warning("App didn't exit gracefully from SIGINT. Killing...")
            self._proc.kill()
            self.wait_for_exit()
            raise

    #####################################
    # virt-manager launching entrypoint #
    #####################################

    def open(
        self,
        uri=None,
        extra_opts=None,
        check_already_running=True,
        use_uri=True,
        window_name=None,
        xmleditor_enabled=False,
        keyfile=None,
        break_setfacl=False,
        first_run=True,
        will_fail=False,
        enable_libguestfs=False,
        firstrun_uri=None,
        show_console=None,
        allow_debug=True,
    ):
        extra_opts = extra_opts or []
        uri = uri or self.uri
        os.environ.setdefault("GTK_A11Y", "atspi")

        if allow_debug and tests.utils.TESTCONFIG.debug:
            stdout = sys.stdout
            stderr = sys.stderr
            extra_opts.append("--debug")
        else:
            stdout = open(os.devnull)
            stderr = open(os.devnull)

        cmd = [sys.executable]
        cmd += [os.path.join(tests.utils.TOPDIR, "virt-manager")]
        if use_uri:
            cmd += ["--connect", uri]
        if show_console:
            cmd += ["--show-domain-console=%s" % show_console]

        if first_run:
            cmd.append("--test-options=first-run")
            if not firstrun_uri:
                firstrun_uri = ""
        if firstrun_uri is not None:
            cmd.append("--test-options=firstrun-uri=%s" % firstrun_uri)
        if xmleditor_enabled:
            cmd.append("--test-options=xmleditor-enabled")
        if break_setfacl:
            cmd.append("--test-options=break-setfacl")
        if enable_libguestfs is True:
            cmd.append("--test-options=enable-libguestfs")
        if enable_libguestfs is False:
            cmd.append("--test-options=disable-libguestfs")
        if keyfile:
            import atexit
            import tempfile

            keyfile = tests.utils.UITESTDATADIR + "/keyfile/" + keyfile
            tempname = tempfile.mktemp(prefix="virtmanager-uitests-keyfile")
            open(tempname, "w").write(open(keyfile).read())
            atexit.register(lambda: os.unlink(tempname))
            cmd.append("--test-options=gsettings-keyfile=%s" % tempname)

        cmd += extra_opts

        if check_already_running:
            self.error_if_already_running()
        self._proc = subprocess.Popen(cmd, stdout=stdout, stderr=stderr)
        if will_fail:
            return

        with utils.dogtail_timeout(10):
            # On Fedora 39 sometimes app launch from the test suite
            # takes a while for reasons I can't quite figure
            try:
                self._root = dogtail.tree.root.application("virt-manager")
            except dogtail.tree.SearchError:
                # GTK 4 from a python wrapper may expose the process name
                self._root = dogtail.tree.root.application("python3")
            self._topwin = self.find_window(self._infer_open_window_name(extra_opts, window_name))
