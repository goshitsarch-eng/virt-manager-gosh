# Copyright (C) 2007, 2013-2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import sys
import textwrap
import traceback

from gi.repository import Gtk

from virtinst import log

from .baseclass import vmmGObject
from .lib import gtkcompat


def _launch_dialog(dialog, primary_text, secondary_text, title, widget=None, modal=True):
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

    primary_text = fix_text(primary_text)
    secondary_text = fix_text(secondary_text)

    dialog.set_property("text", primary_text)
    dialog.format_secondary_text(secondary_text or None)
    dialog.set_title(title)

    if widget:
        dialog.get_content_area().add(widget)

    res = False
    if modal:
        res = dialog.run()
        res = bool(res in [Gtk.ResponseType.YES, Gtk.ResponseType.OK])
        dialog.destroy()
    else:

        def response_destroy(src, ignore):
            src.destroy()

        dialog.connect("response", response_destroy)
        dialog.show()

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
            primary_text=summary, secondary_text=text2, details=details, title=title, modal=modal
        )

    ###################################
    # Simple one shot message dialogs #
    ###################################

    def _simple_dialog(self, dialog_type, buttons, text1, text2, title, widget=None, modal=True):

        dialog = Gtk.MessageDialog(
            transient_for=self.get_parent(),
            modal=True,
            destroy_with_parent=True,
            message_type=dialog_type,
            buttons=buttons,
        )
        if self._simple:
            self._simple.destroy()
        self._simple = dialog
        self._simple.get_accessible().set_name("vmm dialog")

        return _launch_dialog(
            self._simple, text1, text2 or "", title or "", widget=widget, modal=modal
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
        return self.chkbox_helper(
            self.config.get_confirm_unapplied,
            self.config.set_confirm_unapplied,
            text1=(_("There are unapplied changes. Would you like to apply them now?")),
            chktext=_("Don't warn me again."),
            default=False,
        )

    ##########################################
    # One shot dialog with a checkbox prompt #
    ##########################################

    def warn_chkbox(self, text1, text2=None, chktext=None, buttons=None):
        dtype = Gtk.MessageType.WARNING
        buttons = buttons or Gtk.ButtonsType.OK_CANCEL
        chkbox = _errorDialog(
            parent=self.get_parent(), flags=0, message_type=dtype, buttons=buttons
        )
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
        do_prompt = getcb()
        if not do_prompt:
            return default

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


class _errorDialog(Gtk.MessageDialog):
    """
    Custom error dialog with optional check boxes or details drop down
    """

    def __init__(self, parent=None, flags=0, message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE):
        ignore = flags
        Gtk.MessageDialog.__init__(
            self,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
            message_type=message_type,
            buttons=buttons,
        )

        self.set_title("")
        msg_area = self.get_message_area()
        child = msg_area.get_first_child()
        while child:
            if hasattr(child, "set_max_width_chars"):
                child.set_max_width_chars(40)
            child = child.get_next_sibling()

        gtkcompat.set_accessible_name(self, "vmm dialog")

        self.chk_vbox = None
        self.init_chkbox()

        self.buffer = None
        self.buf_expander = None
        self.init_details()

    def init_chkbox(self):
        # Init check items
        self.chk_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.chk_vbox.set_visible(True)
        self.get_content_area().append(self.chk_vbox)

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
        details.set_wrap_mode(Gtk.WrapMode.WORD)
        details.set_margin_top(6)
        details.set_margin_bottom(6)
        details.set_margin_start(6)
        details.set_margin_end(6)
        sw.set_child(details)
        self.buf_expander.set_child(sw)
        self.get_content_area().append(self.buf_expander)
        self.buf_expander.set_visible(True)

    def show_dialog(
        self, primary_text, secondary_text="", title="", details="", chktext="", modal=True
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
            chkbox = Gtk.CheckButton(chktext)
            self.chk_vbox.add(chkbox)
            chkbox.show()

        res = _launch_dialog(self, primary_text, secondary_text or "", title, modal=modal)

        if chktext:
            res = [res, chkbox.get_active()]

        return res
