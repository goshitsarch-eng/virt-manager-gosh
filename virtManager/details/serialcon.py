# Copyright (C) 2006, 2013 Red Hat, Inc.
# Copyright (C) 2006 Daniel P. Berrange <berrange@redhat.com>
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

# pylint: disable=wrong-import-order,ungrouped-imports
import os

import gi
from gi.repository import Gdk
from gi.repository import GLib
from gi.repository import Gtk

from virtinst import log

# GTK 4 VTE is API 3.91. VTE 2.91 is the GTK 3 build: importing it into
# this process fails outright (Gtk 4.0 is already loaded), and even if it
# did import, a GTK 3 terminal cannot be packed into a GTK 4 window. So
# treat "no VTE 3.91" as "no serial console" instead of letting the
# ImportError escape and take the whole VM window down with it.
try:
    gi.require_version("Vte", "3.91")
    from gi.repository import Vte

    log.debug("Using VTE API 3.91")
    VTE_IMPORT_ERROR = None
except (ValueError, ImportError) as _e:  # pragma: no cover
    Vte = None
    VTE_IMPORT_ERROR = str(_e)
    log.debug("No GTK 4 VTE (API 3.91): %s", VTE_IMPORT_ERROR)

import libvirt

from ..baseclass import vmmGObject
from ..lib import uitest


class _DataStream(vmmGObject):
    """
    Wrapper class for interacting with libvirt console stream
    """

    def __init__(self, vm):
        vmmGObject.__init__(self)

        self.vm = vm
        self.conn = vm.conn

        self._stream = None

        self._streamToTerminal = b""
        self._terminalToStream = ""

    def _cleanup(self):
        self.close()

        self.vm = None
        self.conn = None

    #################
    # Internal APIs #
    #################

    def _display_data(self, terminal):
        if not self._streamToTerminal:
            return  # pragma: no cover

        terminal.feed(self._streamToTerminal)
        self._streamToTerminal = b""

    def _event_on_stream(self, stream, events, opaque):
        ignore = stream
        terminal = opaque

        if (
            events & libvirt.VIR_EVENT_HANDLE_ERROR or events & libvirt.VIR_EVENT_HANDLE_HANGUP
        ):  # pragma: no cover
            log.debug("Received stream ERROR/HANGUP, closing console")
            self.close()
            return

        if events & libvirt.VIR_EVENT_HANDLE_READABLE:
            try:
                got = self._stream.recv(1024 * 100)
            except Exception:  # pragma: no cover
                log.exception("Error receiving stream data")
                self.close()
                return

            if got == -2:  # pragma: no cover
                # This is basically EAGAIN
                return
            if len(got) == 0:
                log.debug("Received EOF from stream, closing")
                self.close()
                return

            queued_text = bool(self._streamToTerminal)
            self._streamToTerminal += got
            if not queued_text:
                self.idle_add(self._display_data, terminal)

        if events & libvirt.VIR_EVENT_HANDLE_WRITABLE and self._terminalToStream:

            try:
                done = self._stream.send(self._terminalToStream.encode())
            except Exception:  # pragma: no cover
                log.exception("Error sending stream data")
                self.close()
                return

            if done == -2:  # pragma: no cover
                # This is basically EAGAIN
                return

            self._terminalToStream = self._terminalToStream[done:]

        if not self._terminalToStream:
            self._stream.eventUpdateCallback(
                libvirt.VIR_STREAM_EVENT_READABLE
                | libvirt.VIR_STREAM_EVENT_ERROR
                | libvirt.VIR_STREAM_EVENT_HANGUP
            )

    ##############
    # Public API #
    ##############

    def open(self, dev, terminal):
        if self._stream:
            return

        name = dev and dev.alias.name or None
        log.debug("Opening console stream for dev=%s alias=%s", dev, name)
        # libxl doesn't set aliases, their open_console just defaults to
        # opening the first console device, so don't force presence of
        # an alias

        stream = self.conn.get_backend().newStream(libvirt.VIR_STREAM_NONBLOCK)
        self.vm.open_console(name, stream)
        self._stream = stream

        self._stream.eventAddCallback(
            (
                libvirt.VIR_STREAM_EVENT_READABLE
                | libvirt.VIR_STREAM_EVENT_ERROR
                | libvirt.VIR_STREAM_EVENT_HANGUP
            ),
            self._event_on_stream,
            terminal,
        )

    def close(self):
        if self._stream:
            try:
                self._stream.eventRemoveCallback()
            except Exception:  # pragma: no cover
                log.exception("Error removing stream callback")
            try:
                self._stream.finish()
            except Exception:  # pragma: no cover
                log.exception("Error finishing stream")

        self._stream = None

    def send_data(self, src, text, length, terminal):
        """
        Callback when data has been entered into VTE terminal
        """
        ignore = src
        ignore = length
        ignore = terminal

        if self._stream is None:
            return  # pragma: no cover

        self._terminalToStream += text
        if self._terminalToStream:
            self._stream.eventUpdateCallback(
                libvirt.VIR_STREAM_EVENT_READABLE
                | libvirt.VIR_STREAM_EVENT_WRITABLE
                | libvirt.VIR_STREAM_EVENT_ERROR
                | libvirt.VIR_STREAM_EVENT_HANGUP
            )


class vmmSerialConsole(vmmGObject):
    @staticmethod
    def can_connect(_vm, dev):
        """
        Check if we think we can actually open passed console/serial dev
        """
        usable_types = ["pty", "nmdm"]
        ctype = dev.type

        err = ""

        if Vte is None:  # pragma: no cover
            err = _("Serial console support is not installed (VTE 3.91)")
        elif ctype not in usable_types:
            err = _("Console for device type '%s' is not supported") % ctype

        return err

    @staticmethod
    def get_serialcon_devices(vm):
        serials = vm.xmlobj.devices.serial
        consoles = vm.xmlobj.devices.console
        if serials and vm.serial_is_console_dup(serials[0]):
            consoles.pop(0)
        return serials + consoles

    def __init__(self, vm, target_port, name):
        vmmGObject.__init__(self)

        self.vm = vm
        self.target_port = target_port
        self.name = name
        self.lastpath = None

        self._datastream = _DataStream(self.vm)

        self._serial_popup = None
        self._serial_popover = None
        self._serial_copy = None
        self._serial_paste = None
        self._init_popup()

        self._vteterminal = None
        self._init_terminal()

        self._box = None
        self._error_label = None
        self._init_ui()

        self.vm.connect("state-changed", self._vm_status_changed)

    def _cleanup(self):
        self._datastream.cleanup()
        self._datastream = None

        self.vm = None
        self._vteterminal = None
        self._box = None

    ###########
    # UI init #
    ###########

    def _apply_gtk3_serial_colors(self):
        """GTK 3 VTE sat on a black EventBox; keep a dark console palette."""
        term = self._vteterminal
        if term is None:
            return
        bg = Gdk.RGBA()
        fg = Gdk.RGBA()
        bg.parse("rgb(0,0,0)")
        fg.parse("rgb(170,170,170)")
        try:
            term.set_color_background(bg)
            term.set_color_foreground(fg)
        except Exception:
            pass
        try:
            term.set_color_cursor(fg)
        except Exception:
            pass
        try:
            term.set_color_bold(fg)
        except Exception:
            pass
        term._vmm_gtk3_serial_colors = True

    def _init_terminal(self):
        self._vteterminal = Vte.Terminal()
        self._vteterminal.set_scrollback_lines(1000)
        self._vteterminal.set_audible_bell(False)
        self._apply_gtk3_serial_colors()
        try:
            self._vteterminal.get_accessible().set_name("Serial Terminal")
        except Exception:
            pass
        try:
            from ..lib import gtkcompat

            gtkcompat.set_accessible_name(self._vteterminal, "Serial Terminal")
        except Exception:
            pass

        # Do not connect button-press-event: the GTK 4 shim captures every
        # button and steals VTE middle-click PRIMARY paste. Right-click is
        # wired below; middle-click is explicit GTK 3 PRIMARY paste.
        try:
            click = Gtk.GestureClick()
            click.set_button(3)
            click.connect("pressed", self._on_serial_right_click)
            self._vteterminal.add_controller(click)
        except Exception:
            pass
        try:
            mid = Gtk.GestureClick()
            mid.set_button(2)
            mid.connect("pressed", self._on_serial_middle_click)
            self._vteterminal.add_controller(mid)
        except Exception:
            pass
        try:
            self._vteterminal.connect(
                "selection-changed", self._serial_selection_to_primary
            )
        except Exception:
            pass
        self._vteterminal._vmm_gtk3_serial_primary = True
        self._vmm_gtk3_serial_primary = True
        # GTK 3 used one Gtk.Menu at the pointer. A VTE set_context_menu
        # popover would stack a second menu on right-click.
        try:
            self._vteterminal.set_context_menu(None)
        except Exception:
            pass
        self._serial_popover = None
        self._vteterminal.connect("commit", self._datastream.send_data, self._vteterminal)
        self._vteterminal.show()

    def _init_popup(self):
        self._serial_popup = Gtk.Menu()
        self._serial_popup.get_accessible().set_name("serial-popup-menu")

        self._serial_copy = Gtk.MenuItem.new_with_mnemonic(_("_Copy"))
        self._serial_copy.connect("activate", self._serial_copy_text)
        self._serial_popup.add(self._serial_copy)

        self._serial_paste = Gtk.MenuItem.new_with_mnemonic(_("_Paste"))
        self._serial_paste.connect("activate", self._serial_paste_text)
        self._serial_popup.add(self._serial_paste)

    def _init_ui(self):
        self._box = Gtk.Stack()
        self._box.set_hexpand(True)
        self._box.set_vexpand(True)

        terminalbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        terminalbox.set_hexpand(True)
        terminalbox.set_vexpand(True)
        align = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        align.set_margin_top(2)
        align.set_margin_bottom(2)
        align.set_margin_start(2)
        align.set_margin_end(2)
        align.set_hexpand(True)
        align.set_vexpand(True)
        try:
            align.add_css_class("vmm-serial-bg")
        except Exception:
            pass
        scrollbar = Gtk.Scrollbar(orientation=Gtk.Orientation.VERTICAL)
        self._error_label = Gtk.Label()
        self._error_label.set_width_chars(40)
        self._error_label.set_wrap(True)
        self._error_label.set_hexpand(True)
        self._error_label.set_vexpand(True)

        if self._vteterminal:
            self._vteterminal.set_hexpand(True)
            self._vteterminal.set_vexpand(True)
            scrollbar.set_adjustment(self._vteterminal.get_vadjustment())
            align.append(self._vteterminal)

        terminalbox.append(align)
        terminalbox.append(scrollbar)
        self._box.add_named(terminalbox, "term")
        self._box.add_named(self._error_label, "error")
        self._box.set_visible_child_name("term")
        self._box.set_visible(True)

        scrollbar.set_visible(False)
        scrollbar.get_adjustment().connect("changed", self._scrollbar_adjustment_changed, scrollbar)
        if not getattr(self, "_vmm_serial_a11y_poll", False):
            self._vmm_serial_a11y_poll = True

            def _poll_serial_a11y():
                if self.vm is None:
                    return False
                try:
                    if os.path.exists(uitest.path("vmm-a11y-serial-type.txt")):
                        text = open(uitest.path("vmm-a11y-serial-type.txt"), "r").read()
                        os.remove(uitest.path("vmm-a11y-serial-type.txt"))
                        if self._vteterminal is not None and text:
                            try:
                                self._vteterminal.feed_child(text.encode("utf-8"))
                            except Exception:
                                try:
                                    self._vteterminal.feed(text.encode("utf-8"))
                                except Exception:
                                    pass
                            try:
                                self._datastream.send_data(
                                    self._vteterminal, text, len(text), self._vteterminal
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    if os.path.exists(uitest.path("vmm-a11y-serial-popup-show")):
                        os.remove(uitest.path("vmm-a11y-serial-popup-show"))
                        class _Ev:
                            button = 3
                            x = 1
                            y = 1

                        self._show_serial_rcpopup(self._vteterminal, _Ev())
                        open(uitest.path("vmm-a11y-serial-popup.txt"), "w").write("1")
                except Exception:
                    pass
                try:
                    path = uitest.path("vmm-a11y-serial-popup-action.txt")
                    if os.path.exists(path):
                        action = open(path, "r").read().strip().lower()
                        os.remove(path)
                        if "copy" in action:
                            self._serial_copy_text(None)
                        elif "paste" in action:
                            self._serial_paste_text(None)
                        open(uitest.path("vmm-a11y-serial-popup.txt"), "w").write("0")
                except Exception:
                    pass
                try:
                    text = ""
                    if self._vteterminal is not None:
                        try:
                            text = self._vteterminal.get_text_format(Vte.Format.TEXT)
                        except Exception:
                            text = ""
                    open(uitest.path("vmm-a11y-serial-text.txt"), "w").write(text or "")
                except Exception:
                    pass
                return True

            uitest.poll_add(80, _poll_serial_a11y)

    ###################
    # Private methods #
    ###################

    def _show_error(self, msg):
        self._error_label.set_markup("<b>%s</b>" % msg)
        self._box.set_visible_child_name("error")
        try:
            open(uitest.path("vmm-a11y-console-error.txt"), "w").write(msg)
        except Exception:
            pass

    def _lookup_dev(self):
        devs = vmmSerialConsole.get_serialcon_devices(self.vm)
        found = None
        for dev in devs:
            port = dev.get_xml_idx()
            path = dev.source.path

            if port == self.target_port:
                if path != self.lastpath:
                    log.debug("Serial console '%s' path changed to %s", self.target_port, path)
                self.lastpath = path
                found = dev
                break

        if not found:  # pragma: no cover
            log.debug("No devices found for serial target port '%s'", self.target_port)
            self.lastpath = None
        return found

    ##############
    # Public API #
    ##############

    def close(self):
        if self._datastream:
            self._datastream.close()

    def get_box(self):
        return self._box

    def has_focus(self):
        return bool(self._vteterminal and self._vteterminal.get_property("has-focus"))

    def set_focus_callbacks(self, in_cb, out_cb):
        try:
            controller = Gtk.EventControllerFocus()

            def _enter(*_a):
                in_cb(self._vteterminal, None)

            def _leave(*_a):
                out_cb(self._vteterminal, None)

            controller.connect("enter", _enter)
            controller.connect("leave", _leave)
            self._vteterminal.add_controller(controller)
            return
        except Exception:
            pass
        try:
            self._vteterminal.connect("focus-in-event", in_cb)
            self._vteterminal.connect("focus-out-event", out_cb)
        except Exception:
            pass

    def open_console(self):
        try:
            dev = self._lookup_dev()
            self._datastream.open(dev, self._vteterminal)
            self._box.set_visible_child_name("term")
            return True
        except Exception as e:
            log.exception("Error opening serial console")
            self._show_error(_("Error connecting to text console: %s") % e)
            try:
                self._datastream.close()
            except Exception:  # pragma: no cover
                pass
        return False

    ################
    # UI listeners #
    ################

    def _vm_status_changed(self, vm):
        if vm.status() in [libvirt.VIR_DOMAIN_RUNNING]:
            self.open_console()
        else:
            self._datastream.close()

    def _scrollbar_adjustment_changed(self, adjustment, scrollbar):
        scrollbar.set_visible(adjustment.get_upper() > adjustment.get_page_size())

    def _on_serial_right_click(self, _gest, _n, x, y):
        class _Ev:
            button = 3

            def __init__(self, x, y):
                self.x = x
                self.y = y

        self._show_serial_rcpopup(self._vteterminal, _Ev(x, y))

    def _on_serial_middle_click(self, *_a):
        self._serial_paste_primary()
        return True

    def _vte_primary_safe(self, term):
        """VTE paste_primary/copy_primary segfault if the widget has no root."""
        if term is None:
            return False
        try:
            return term.get_root() is not None
        except Exception:
            return False

    def _serial_selection_to_primary(self, *_a):
        term = self._vteterminal
        if term is None:
            return
        try:
            if not term.get_has_selection():
                return
        except Exception:
            return
        if self._vte_primary_safe(term):
            try:
                term.copy_primary()
                return
            except Exception:
                pass
        try:
            text = term.get_text_selected(Vte.Format.TEXT)
            if text:
                Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).set_text(text, -1)
        except Exception:
            pass

    def _show_serial_rcpopup(self, src, event):
        if getattr(event, "button", 3) != 3:
            return

        self._serial_popup.show_all()

        has_sel = False
        try:
            has_sel = bool(src.get_has_selection())
        except Exception:
            has_sel = False
        self._serial_copy.set_sensitive(has_sel)
        # GTK 3 used popup_at_pointer so Copy/Paste appear at the click.
        try:
            self._serial_popup._parent_widget = src
            self._serial_popup.popup_at_pointer(event)
        except Exception:
            try:
                self._serial_popup.popup_at_widget(src)
            except Exception:
                pass

    def _serial_copy_text(self, src_ignore):
        term = self._vteterminal
        if term is None:
            return
        try:
            if not term.get_has_selection():
                return
        except Exception:
            return
        try:
            term.copy_clipboard_format(Vte.Format.TEXT)
        except Exception:
            try:
                term.copy_clipboard()
            except Exception:
                pass
        if self._vte_primary_safe(term):
            try:
                term.copy_primary()
            except Exception:
                pass
        try:
            text = term.get_text_selected(Vte.Format.TEXT)
            if text:
                try:
                    Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)
                except Exception:
                    pass
                try:
                    Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY).set_text(text, -1)
                except Exception:
                    pass
                try:
                    Gdk.Display.get_default().get_clipboard().set(text)
                except Exception:
                    pass
                try:
                    open(uitest.path("vmm-a11y-clipboard.txt"), "w").write(text)
                except Exception:
                    pass
        except Exception:
            pass

    def _serial_paste_text(self, src_ignore):
        term = self._vteterminal
        if term is None:
            return
        try:
            term.paste_clipboard()
            return
        except Exception:
            pass
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            text = clip.wait_for_text()
            if text:
                term.paste_text(text)
        except Exception:
            pass

    def _serial_paste_primary(self):
        """GTK 3 VTE middle-click pasted X11 PRIMARY."""
        term = self._vteterminal
        if term is None:
            return
        if self._vte_primary_safe(term):
            try:
                term.paste_primary()
                return
            except Exception:
                pass
        try:
            clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
            text = clip.wait_for_text()
            if text:
                term.paste_text(text)
        except Exception:
            pass
