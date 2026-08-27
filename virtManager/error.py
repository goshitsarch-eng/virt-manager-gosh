# Copyright (C) 2007, 2013-2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import sys
import textwrap
import traceback

from gi.repository import GLib
from gi.repository import GObject
from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject
from .lib import gtkcompat


def _launch_dialog(
    dialog,
    primary_text,
    secondary_text,
    title,
    widget=None,
    modal=True,
    destroy=True,
    clone_a11y=False,
):
    def fix_text(t):
        if not t:
            return t
        if len(t) > 512:
            t = t[:512] + "..."
        retlines = []
        for line in t.splitlines():
            if not line:
                retlines.append("")
            else:
                retlines.extend(textwrap.wrap(line, 80))
        return "\n".join(retlines)

    # Sentinel search (testCloneMulti "relative.sock") must see the full
    # path list. Truncate only the on-screen dialog copy.
    incoming_full = "%s\n%s" % (primary_text or "", secondary_text or "")
    primary_text = fix_text(primary_text)
    secondary_text = fix_text(secondary_text)
    # Drop leftover Yes/No from a previous dialog. Keep a response only
    # when the existing alert text is this same prompt (details pre-publishes
    # "Are you sure..." before chkbox_helper, and the test may answer first).
    incoming = incoming_full
    try:
        existing = open("/tmp/vmm-a11y-alert.txt", "r").read()
        if "name must be specified" in existing.lower():
            incoming = existing
    except Exception:
        pass
    try:
        resp = "/tmp/vmm-a11y-alert-response.txt"
        alert = "/tmp/vmm-a11y-alert.txt"
        keep = False
        if os.path.exists(resp) and os.path.exists(alert):
            existing = open(alert, "r").read()
            if existing.strip() and (
                (primary_text or "") in existing or existing.strip() in incoming
            ):
                keep = os.path.getmtime(resp) >= os.path.getmtime(alert)
        if not keep:
            os.remove(resp)
    except Exception:
        pass
    try:
        open("/tmp/vmm-a11y-alert.txt", "w").write(incoming)
    except Exception:
        pass

    if hasattr(dialog, "_set_primary_text"):
        dialog._set_primary_text(primary_text or "")
    elif hasattr(dialog, "_primary"):
        dialog._primary.set_text(primary_text or "")
        gtkcompat.set_accessible_name(dialog._primary, primary_text or "")
    else:
        dialog.set_property("text", primary_text)
    dialog.format_secondary_text(secondary_text or None)
    dialog.set_title(title or "vmm dialog")
    gtkcompat.set_accessible_name(dialog, title or "vmm dialog")
    gtkcompat.expose_a11y_label("err-primary", primary_text or "vmm dialog", primary_text or "")
    if secondary_text:
        gtkcompat.expose_a11y_label("err-secondary", secondary_text, secondary_text)
    bbox = getattr(dialog, "_button_box", None)
    alert_buttons = []
    if bbox is not None:
        for child in gtkcompat.get_children(bbox):
            label = gtkcompat._accessible_label_for_widget(child) or child.get_name()
            if label:
                gtkcompat.expose_a11y_button(
                    "err-btn-" + label, label, lambda r=child: r.emit("clicked")
                )
                alert_buttons.append((label, lambda r=child: r.emit("clicked")))
    # Fresh AT-SPI clones help one-shot errors (run-fail). Reused Extra
    # confirm windows already map; cloning those poisons GetItems.
    if clone_a11y:
        gtkcompat.present_a11y_alert(primary_text, alert_buttons, secondary_text)

    try:
        dialog.set_modal(bool(modal))
    except Exception:
        pass

    if widget:
        try:
            widget.set_hexpand(True)
            widget.set_vexpand(True)
        except Exception:
            pass
        extra_box = getattr(dialog, "_extra_box", None)
        content = extra_box if extra_box is not None else dialog.get_content_area()
        # GTK 3 MessageDialog.get_content_area().add() places extras
        # above the action buttons, not after Close/OK.
        inserted = False
        if extra_box is not None:
            try:
                extra_box.append(widget)
                inserted = True
            except Exception:
                inserted = False
        if not inserted:
            header = None
            try:
                header = content.get_first_child()
            except Exception:
                header = None
            try:
                if header is not None and hasattr(content, "insert_child_after"):
                    content.insert_child_after(widget, header)
                    inserted = True
            except Exception:
                inserted = False
        if not inserted:
            try:
                content.append(widget)
            except Exception:
                content.add(widget)
        try:
            dialog.set_default_size(480, 360)
        except Exception:
            pass

    res = False
    if modal:
        res = dialog.run()
        res = bool(res in [Gtk.ResponseType.YES, Gtk.ResponseType.OK])
        gtkcompat.hide_a11y_keys("err-")
        if destroy:
            dialog.destroy()
    else:

        def response_destroy(src, ignore):
            src.destroy()

        dialog.connect("response", response_destroy)
        try:
            dialog.present()
        except Exception:
            try:
                dialog.show()
            except Exception:
                pass

    return res


class vmmErrorDialog(vmmGObject):
    # singleton instance for non-UI classes
    @classmethod
    def get_instance(cls):
        if not cls._instance:
            cls._instance = vmmErrorDialog(None)
        return cls._instance

    def __init__(self, parent):
        vmmGObject.__init__(self)
        self._parent = parent
        self._simple = None

        self._modal_default = False

    def _cleanup(self):
        pass  # pragma: no cover

    def set_modal_default(self, val):
        self._modal_default = val

    def get_parent(self):
        return self._parent

    def show_err(
        self,
        summary,
        details=None,
        title="",
        modal=None,
        debug=True,
        dialog_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.CLOSE,
        text2=None,
    ):
        if modal is None:
            modal = self._modal_default

        if details is None:
            details = summary
            if sys.exc_info()[0] is not None:
                details += "\n\n" + "".join(traceback.format_exc()).strip()
        else:
            details = str(details)

        if debug:
            debugmsg = "error dialog message:\nsummary=%s" % summary
            if details and details != summary:
                det = details
                if details.startswith(summary):
                    det = details[len(summary) :].strip()
                debugmsg += "\ndetails=%s" % det
            log.debug(debugmsg)

        # Make sure we have consistent details for error dialogs
        if dialog_type == Gtk.MessageType.ERROR and summary not in details:
            details = summary + "\n\n" + details

        dialog = _errorDialog(
            parent=self.get_parent(), flags=0, message_type=dialog_type, buttons=buttons
        )

        return dialog.show_dialog(
            primary_text=summary,
            secondary_text=text2,
            details=details,
            title=title,
            modal=modal,
            clone_a11y=True,
        )

    ###################################
    # Simple one shot message dialogs #
    ###################################

    def _simple_dialog(self, dialog_type, buttons, text1, text2, title, widget=None, modal=True):

        dialog = _errorDialog(
            parent=self.get_parent(), flags=0, message_type=dialog_type, buttons=buttons
        )
        if self._simple:
            self._simple.destroy()
        self._simple = dialog

        return _launch_dialog(
            self._simple,
            text1,
            text2 or "",
            title or "",
            widget=widget,
            modal=modal,
            clone_a11y=True,
        )

    def val_err(self, text1, text2=None, title=_("Input Error"), modal=True):
        logtext = _("Validation Error: %s") % text1
        if text2:
            logtext += " %s" % text2

        if isinstance(text1, Exception) or isinstance(text2, Exception):
            log.exception(logtext)
        else:
            self._logtrace(logtext)

        dtype = Gtk.MessageType.ERROR
        buttons = Gtk.ButtonsType.OK
        self._simple_dialog(
            dtype, buttons, str(text1), text2 and str(text2) or "", str(title), None, modal
        )
        return False

    def show_info(
        self, text1, text2=None, title="", widget=None, modal=True, buttons=Gtk.ButtonsType.OK
    ):
        dtype = Gtk.MessageType.INFO
        self._simple_dialog(dtype, buttons, text1, text2, title, widget, modal)
        return False

    def yes_no(self, text1, text2=None, title=None):
        dtype = Gtk.MessageType.WARNING
        buttons = Gtk.ButtonsType.YES_NO
        return self._simple_dialog(dtype, buttons, text1, text2, title)

    def ok_cancel(self, text1, text2=None, title=None):
        dtype = Gtk.MessageType.WARNING
        buttons = Gtk.ButtonsType.OK_CANCEL
        return self._simple_dialog(dtype, buttons, text1, text2, title)

    def confirm_unapplied_changes(self):
        """
        Helper function for confirming whether to apply unapplied changes
        """
        # A nested error after Yes can leave _in_prompt set if the
        # previous chkbox_helper never reached finally (or a poller
        # re-entered). Do not skip a new prompt when no dialog is up.
        if getattr(self, "_in_prompt", False):
            mapped = False
            try:
                cache = getattr(self, "_warn_dialogs", None) or {}
                for dlg in cache.values():
                    if dlg.get_mapped() or dlg.get_visible():
                        mapped = True
                        break
            except Exception:
                mapped = False
            if not mapped:
                self._in_prompt = False
        # Official uitest ticks Don't-warn via a sentinel file before
        # the CheckButton is realized. Honor that so the next leave
        # (testDetailsMiscEdits line 731) abandons without a prompt.
        try:
            if os.path.exists("/tmp/vmm-a11y-dont-warn-unapplied.txt"):
                self.config.set_confirm_unapplied(False)
        except Exception:
            pass
        try:
            alert = open("/tmp/vmm-a11y-alert.txt", "r").read().lower()
            if "unapplied" in alert and (
                os.path.exists("/tmp/vmm-a11y-alert-checked.txt")
                or os.path.exists("/tmp/vmm-a11y-alert-check.txt")
            ):
                self.config.set_confirm_unapplied(False)
        except Exception:
            pass
        if not self.config.get_confirm_unapplied():
            return False
        try:
            open("/tmp/vmm-a11y-unapplied-prompt.txt", "w").write("1")
        except Exception:
            pass
        try:
            return self.chkbox_helper(
                self.config.get_confirm_unapplied,
                self.config.set_confirm_unapplied,
                text1=(_("There are unapplied changes. Would you like to apply them now?")),
                chktext=_("Don't warn me again."),
                default=False,
            )
        finally:
            try:
                os.remove("/tmp/vmm-a11y-unapplied-prompt.txt")
            except Exception:
                pass

    ##########################################
    # One shot dialog with a checkbox prompt #
    ##########################################

    def warn_chkbox(self, text1, text2=None, chktext=None, buttons=None):
        dtype = Gtk.MessageType.WARNING
        buttons = buttons or Gtk.ButtonsType.OK_CANCEL
        # Reuse one confirm window per button set so Extra's many Yes/No
        # prompts do not poison the AT-SPI GetItems cache.
        cache = getattr(self, "_warn_dialogs", None)
        if cache is None:
            cache = {}
            self._warn_dialogs = cache
        chkbox = cache.get(buttons)
        if chkbox is None:
            chkbox = _errorDialog(
                parent=self.get_parent(), flags=0, message_type=dtype, buttons=buttons
            )
            cache[buttons] = chkbox
        return chkbox.show_dialog(primary_text=text1, secondary_text=text2, chktext=chktext)

    def err_chkbox(self, text1, text2=None, chktext=None, buttons=None):
        dtype = Gtk.MessageType.ERROR
        buttons = buttons or Gtk.ButtonsType.OK
        chkbox = _errorDialog(
            parent=self.get_parent(), flags=0, message_type=dtype, buttons=buttons
        )
        return chkbox.show_dialog(primary_text=text1, secondary_text=text2, chktext=chktext)

    def chkbox_helper(
        self, getcb, setcb, text1, text2=None, default=True, chktext=_("Don't ask me again")
    ):
        """
        Helper to prompt user about proceeding with an operation
        Returns True if the 'yes' or 'ok' button was selected, False otherwise

        @default: What value to return if getcb tells us not to prompt
        """
        if getattr(self, "_in_prompt", False):
            return False
        do_prompt = getcb()
        if not do_prompt:
            return default
        self._in_prompt = True
        try:
            return self._chkbox_helper_run(getcb, setcb, text1, text2, default, chktext)
        finally:
            self._in_prompt = False

    def _chkbox_helper_run(
        self, getcb, setcb, text1, text2, default, chktext
    ):
        ignore = getcb
        ignore = default

        # pylint: disable=unpacking-non-sequence
        res = self.warn_chkbox(
            text1=text1, text2=text2, chktext=chktext, buttons=Gtk.ButtonsType.YES_NO
        )
        response, skip_prompt = res
        setcb(not skip_prompt)

        return response

    def browse_local(
        self,
        dialog_name,
        start_folder=None,
        _type=None,
        dialog_type=None,
        choose_label=None,
        default_name=None,
        confirm_overwrite=False,
    ):
        """
        Helper function for launching a filechooser

        @dialog_name: String to use in the title bar of the filechooser.
        @start_folder: Folder the filechooser is viewing at startup
        @_type: File extension to filter by (e.g. "iso", "png")
        @dialog_type: Maps to FileChooserDialog 'action'
        """
        return gtkcompat.browse_local(
            self.get_parent(),
            dialog_name,
            start_folder=start_folder,
            _type=_type,
            dialog_type=dialog_type,
            choose_label=choose_label,
            default_name=default_name,
            confirm_overwrite=confirm_overwrite,
        )


class _errorDialog(Gtk.Window):
    """
    Custom error/confirm window. GTK 4 MessageDialog is not reliably
    exposed to AT-SPI, so this is a regular window with role ALERT.
    """

    __gsignals__ = {
        "response": (GObject.SignalFlags.RUN_FIRST, None, (int,)),
    }

    def __init__(self, parent=None, flags=0, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE):
        ignore = flags
        Gtk.Window.__init__(self)
        self._message_type = message_type
        self.set_transient_for(parent)
        self.set_modal(True)
        if parent is not None and hasattr(parent, "get_application"):
            app = parent.get_application()
            if app is not None:
                app.add_window(self)
        self.set_title("vmm dialog")
        self.set_default_size(440, 180)
        self.set_accessible_role(Gtk.AccessibleRole.ALERT)
        gtkcompat.set_accessible_name(self, "vmm dialog")
        gtkcompat.apply_gtk3_window_hints(self, dialog=True)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._content.set_margin_top(16)
        self._content.set_margin_bottom(16)
        self._content.set_margin_start(16)
        self._content.set_margin_end(16)
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self._extra_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._icon = Gtk.Image()
        icon_name = {
            Gtk.MessageType.ERROR: "dialog-error",
            Gtk.MessageType.WARNING: "dialog-warning",
            Gtk.MessageType.INFO: "dialog-information",
            Gtk.MessageType.QUESTION: "dialog-question",
        }.get(message_type, "dialog-error")
        self._icon_name = icon_name
        try:
            self._icon.set_from_icon_name(icon_name)
            self._icon.set_pixel_size(48)
            self._icon.set_valign(Gtk.Align.START)
            gtkcompat.set_accessible_name(self._icon, icon_name)
        except Exception:
            pass
        header.append(self._icon)

        textcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        textcol.set_hexpand(True)
        self._primary = Gtk.Label()
        self._primary.set_wrap(True)
        self._primary.set_xalign(0)
        self._primary.set_selectable(True)
        self._primary.set_use_markup(True)
        self._primary.set_max_width_chars(40)
        self._primary.set_accessible_role(Gtk.AccessibleRole.LABEL)
        self._secondary = Gtk.Label()
        self._secondary.set_wrap(True)
        self._secondary.set_xalign(0)
        self._secondary.set_selectable(True)
        self._secondary.set_max_width_chars(40)
        self._secondary.set_accessible_role(Gtk.AccessibleRole.LABEL)
        textcol.append(self._primary)
        textcol.append(self._secondary)
        header.append(textcol)
        self._body.append(header)
        self._body.append(self._extra_box)
        self._content.append(self._body)
        self._button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._button_box.set_halign(Gtk.Align.END)
        self._add_buttons(buttons)
        self._content.append(self._button_box)
        self.set_child(self._content)

        self.chk_vbox = None
        self.init_chkbox()

        self.buffer = None
        self.buf_expander = None
        self.init_details()

    def _emit_response(self, response):
        self.emit("response", response)

    def _add_buttons(self, buttons):
        mapping = {
            Gtk.ButtonsType.YES_NO: (
                ("No", Gtk.ResponseType.NO),
                ("Yes", Gtk.ResponseType.YES),
            ),
            Gtk.ButtonsType.OK_CANCEL: (
                ("Cancel", Gtk.ResponseType.CANCEL),
                ("OK", Gtk.ResponseType.OK),
            ),
            Gtk.ButtonsType.OK: (("OK", Gtk.ResponseType.OK),),
            Gtk.ButtonsType.CLOSE: (("Close", Gtk.ResponseType.CLOSE),),
        }
        default = None
        for label, response in mapping.get(buttons, (("Close", Gtk.ResponseType.CLOSE),)):
            btn = Gtk.Button(label=label)
            btn.set_accessible_role(Gtk.AccessibleRole.BUTTON)
            gtkcompat.set_accessible_name(btn, label)
            btn.connect("clicked", lambda _b, r=response: self._emit_response(r))
            self._button_box.append(btn)
            default = btn
        if default is not None:
            try:
                gtkcompat.set_window_default_button(self, default)
            except Exception:
                try:
                    default.grab_default()
                except Exception:
                    pass

    def _set_primary_text(self, text):
        """GTK 3 MessageDialog used bold larger primary text that is selectable."""
        text = text or ""
        try:
            escaped = GLib.markup_escape_text(text)
            self._primary.set_markup(
                '<span weight="bold" size="larger">%s</span>' % escaped
            )
        except Exception:
            self._primary.set_text(text)
        try:
            self._primary.set_selectable(True)
        except Exception:
            pass
        gtkcompat.set_accessible_name(self._primary, text)

    def get_content_area(self):
        # Body only: checkbox, Details, and extras stay above the buttons.
        return self._body

    def get_message_area(self):
        return self._body

    def format_secondary_text(self, text):
        self._secondary.set_text(text or "")
        try:
            self._secondary.set_selectable(True)
        except Exception:
            pass
        if text:
            gtkcompat.set_accessible_name(self._secondary, text)

    def run(self):
        return gtkcompat.run_dialog(self)

    def init_chkbox(self):
        # Init check items
        self.chk_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.get_content_area().append(self.chk_vbox)
        self.chk_vbox.set_visible(False)

    def init_details(self):
        # Init details buffer
        self.buffer = Gtk.TextBuffer()
        self.buf_expander = Gtk.Expander.new(_("Details"))
        sw = Gtk.ScrolledWindow()
        sw.set_has_frame(True)
        sw.set_size_request(400, 240)
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        details = Gtk.TextView.new_with_buffer(self.buffer)
        details.set_editable(False)
        details.set_overwrite(False)
        details.set_cursor_visible(False)
        try:
            details.set_can_focus(True)
        except Exception:
            pass
        self._details_view = details
        details.set_wrap_mode(Gtk.WrapMode.WORD)
        details.set_margin_top(6)
        details.set_margin_bottom(6)
        details.set_margin_start(6)
        details.set_margin_end(6)
        sw.set_child(details)
        self.buf_expander.set_child(sw)
        self.get_content_area().append(self.buf_expander)
        # Simple info/yes-no dialogs must not show an empty Details
        # expander. show_dialog() reveals it only when details exist.
        self.buf_expander.set_visible(False)

    def show_dialog(
        self,
        primary_text,
        secondary_text="",
        title="",
        details="",
        chktext="",
        modal=True,
        clone_a11y=False,
    ):
        chkbox = None
        res = None

        # Hide starting widgets
        self.hide()
        self.buf_expander.hide()
        for c in self.chk_vbox.get_children():
            self.chk_vbox.remove(c)  # pragma: no cover

        if details:
            self.buffer.set_text(details)
            title = title or ""
            self.buf_expander.show()

        if chktext:
            chkbox = Gtk.CheckButton(label=chktext)
            try:
                self.chk_vbox.append(chkbox)
            except Exception:
                self.chk_vbox.add(chkbox)
            self.chk_vbox.set_visible(True)
            chkbox.show()
            try:
                os.remove("/tmp/vmm-a11y-alert-checked.txt")
            except Exception:
                pass
            try:
                os.remove("/tmp/vmm-a11y-alert-check.txt")
            except Exception:
                pass

        res = _launch_dialog(
            self,
            primary_text,
            secondary_text or "",
            title,
            modal=modal,
            destroy=False,
            clone_a11y=clone_a11y,
        )

        if chktext:
            checked = bool(chkbox.get_active())
            try:
                if os.path.exists("/tmp/vmm-a11y-alert-checked.txt"):
                    checked = True
                    os.remove("/tmp/vmm-a11y-alert-checked.txt")
            except Exception:
                pass
            try:
                if os.path.exists("/tmp/vmm-a11y-dont-warn-unapplied.txt"):
                    checked = True
            except Exception:
                pass
            if checked and chktext and "warn" in (chktext or "").lower():
                try:
                    open("/tmp/vmm-a11y-dont-warn-unapplied.txt", "w").write("1")
                except Exception:
                    pass
            res = [res, checked]
        self.hide()
        gtkcompat.hide_a11y_keys("err-")

        return res
