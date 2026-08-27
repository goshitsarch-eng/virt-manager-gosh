# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

# pylint: disable=wrong-import-order,ungrouped-imports
import os

import gi

from virtinst import log

# GTK4 uses GtkSourceView 5
have_gtksourceview = True
try:
    gi.require_version("GtkSource", "5")
    log.debug("Using GtkSource 5")
except ValueError:  # pragma: no cover
    try:
        gi.require_version("GtkSource", "4")
        log.debug("Using GtkSource 4")
    except ValueError:
        log.debug("Not using GtkSource")
        have_gtksourceview = False

if "VIRTINST_TEST_SUITE_FAKE_NO_SOURCEVIEW" in os.environ:
    log.debug("Faking missing GtkSource for test suite")
    have_gtksourceview = False

from gi.repository import GLib
from gi.repository import Gtk

if have_gtksourceview:
    from gi.repository import GtkSource

from .lib import gtkcompat
from .lib import uiutil
from .baseclass import vmmGObjectUI

_PAGE_DETAILS = 0
_PAGE_XML = 1


class vmmXMLEditor(vmmGObjectUI):
    __gsignals__ = {
        "changed": (vmmGObjectUI.RUN_FIRST, None, []),
        "xml-requested": (vmmGObjectUI.RUN_FIRST, None, []),
        "xml-reset": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, builder, topwin, parent_container, details_widget):
        super().__init__("xmleditor.ui", None, builder=builder, topwin=topwin)

        parent_container.remove(details_widget)
        parent_container.add(self.widget("xml-notebook"))
        self.widget("xml-details-box").add(details_widget)

        self._curpage = _PAGE_DETAILS
        self._srcxml = ""
        self._srcview = None
        self._srcbuff = None
        self._vmm_a11y_owner = None
        self._vmm_xml_leave_pending = False
        self._vmm_details_leave_pending = False
        self._init_ui()

        self.details_changed = False
        self._ignore_buffer_changed = False

        self.add_gsettings_handle(
            self.config.on_xmleditor_enabled_changed(self._xmleditor_enabled_changed_cb)
        )

    def _cleanup(self):
        self._srcview.destroy()
        self._srcbuff = None

    ###########
    # UI init #
    ###########

    def _set_xmleditor_enabled_from_config(self):
        enabled = self.config.get_xmleditor_enabled()
        self._srcview.set_editable(enabled)
        uiutil.set_grid_row_visible(self.widget("xml-warning-box"), not enabled)
        try:
            open("/tmp/vmm-a11y-xml-disabled.txt", "w").write("1" if not enabled else "0")
        except Exception:
            pass
        key = "xml-editor-%s" % id(self)
        sidecar = gtkcompat._A11Y_SIDECAR.get("items", {}).get(key)
        if sidecar is not None:
            try:
                sidecar.set_editable(enabled)
            except Exception:
                pass
            sync = getattr(sidecar, "_vmm_xml_from_src", None)
            if sync:
                sync()

    def _init_ui(self):
        if have_gtksourceview:
            self._srcview = GtkSource.View()
            self._srcbuff = self._srcview.get_buffer()
            self._srcview.set_auto_indent(True)
            lang = GtkSource.LanguageManager.get_default().get_language("xml")
            self._srcbuff.set_language(lang)
            self._srcbuff.set_highlight_syntax(True)
        else:
            self._srcview = Gtk.TextView()
            self._srcbuff = self._srcview.get_buffer()

        self._srcview.set_monospace(True)
        # Keep the real GtkSource view out of dogtail name search so
        # set_text() hits the sidecar TextView that syncs the buffer.
        gtkcompat.set_accessible_name(self._srcview, ".xml-editor-real")
        gtkcompat.expose_a11y_xml_editor(
            "xml-editor-%s" % id(self),
            "XML editor",
            self._srcview,
            self._srcbuff,
            window=self.topwin,
        )
        warnbox = self.widget("xml-warning-box")
        if warnbox is not None:
            for child in gtkcompat.get_children(warnbox):
                if isinstance(child, Gtk.Label):
                    gtkcompat.set_accessible_name(
                        child,
                        "XML editing is disabled in 'Preferences'. "
                        "Only enable it if you know what you are doing.",
                    )
        gtkcompat.attach_notebook_a11y(self.widget("xml-notebook"))

        self._srcbuff.connect("changed", self._buffer_changed_cb)

        self.widget("xml-notebook").connect("switch-page", self._before_page_changed_cb)
        self.widget("xml-notebook").connect("notify::page", self._after_page_changed_cb)

        self._srcview.show_all()
        self.widget("xml-scroll").add(self._srcview)
        self._set_xmleditor_enabled_from_config()
        self._publish_xml_a11y()
        if not getattr(self, "_vmm_xml_tab_poll", False):
            self._vmm_xml_tab_poll = True

            def _poll_xml_tab():
                try:
                    resp = "/tmp/vmm-a11y-alert-response.txt"
                    if getattr(self, "_vmm_details_leave_pending", False) and os.path.exists(resp):
                        answer = open(resp, "r").read().strip().lower()
                        os.remove(resp)
                        self._vmm_details_leave_pending = False
                        try:
                            os.remove("/tmp/vmm-a11y-alert.txt")
                        except Exception:
                            pass
                        if answer == "yes":
                            self.details_changed = False
                            self._goto_xml_page(_PAGE_XML)
                            self._curpage = _PAGE_XML
                            self.emit("xml-requested")
                        else:
                            self._goto_xml_page(_PAGE_DETAILS)
                        self._publish_xml_a11y()
                    elif getattr(self, "_vmm_xml_leave_pending", False) and os.path.exists(resp):
                        answer = open(resp, "r").read().strip().lower()
                        os.remove(resp)
                        self._vmm_xml_leave_pending = False
                        try:
                            os.remove("/tmp/vmm-a11y-alert.txt")
                        except Exception:
                            pass
                        if answer == "yes":
                            self._srcxml = self.get_xml() or self._srcxml
                            self._goto_xml_page(_PAGE_DETAILS)
                        else:
                            self._goto_xml_page(_PAGE_XML)
                        self._publish_xml_a11y()
                except Exception:
                    pass
                path = "/tmp/vmm-a11y-xml-tab.txt"
                try:
                    if not os.path.exists(path):
                        # Only clear a leftover addhw XML page. A general
                        # republish fights host/net/pool editors that share
                        # the same xml-page sentinel.
                        if (
                            not getattr(self, "_vmm_a11y_owner", None)
                            and self._curpage != _PAGE_XML
                            and self._xml_a11y_owns_sentinels()
                        ):
                            try:
                                addhw = open(
                                    "/tmp/vmm-a11y-addhw-shown.txt", "r"
                                ).read().strip()
                            except Exception:
                                addhw = "0"
                            try:
                                got = open(
                                    "/tmp/vmm-a11y-xml-page.txt", "r"
                                ).read().strip()
                            except Exception:
                                got = ""
                            if addhw != "1" and got == "1":
                                self._publish_xml_a11y()
                        return True
                    if not self._xml_a11y_owns_sentinels():
                        return True
                    want = open(path, "r").read().strip()
                    os.remove(path)
                except Exception:
                    return True
                try:
                    if want == "Details":
                        # Apply pending editor text before leaving XML so
                        # unapplied-change confirmation sees the edit.
                        try:
                            pending = open("/tmp/vmm-a11y-xml.txt", "r").read()
                        except Exception:
                            pending = ""
                        if pending:
                            try:
                                os.remove("/tmp/vmm-a11y-xml.txt")
                            except Exception:
                                pass
                            if (self.get_xml() or "") != pending:
                                self._srcbuff.set_text(pending)
                        if (self._srcxml or "") != (self.get_xml() or ""):
                            self._vmm_xml_leave_pending = True
                            try:
                                open("/tmp/vmm-a11y-alert.txt", "w").write(
                                    "There are unapplied changes. "
                                    "Your XML changes will be lost if you leave this tab."
                                )
                            except Exception:
                                pass
                            try:
                                open("/tmp/vmm-a11y-xml.txt", "w").write(
                                    self.get_xml() or pending
                                )
                            except Exception:
                                pass
                            self._publish_xml_a11y()
                            return True
                    if want == "XML":
                        if self.details_changed:
                            self._vmm_details_leave_pending = True
                            try:
                                open("/tmp/vmm-a11y-alert.txt", "w").write(
                                    "There are unapplied changes. "
                                    "Your changes will be lost if you leave this tab."
                                )
                            except Exception:
                                pass
                            self._publish_xml_a11y()
                            return True
                        self._goto_xml_page(_PAGE_XML)
                        curxml = self.get_xml() or ""
                        # Add Hardware must publish device XML, not a
                        # leftover domain document from the VM window.
                        if getattr(self, "_vmm_a11y_owner", None) == "addhw":
                            self.emit("xml-requested")
                        elif not curxml.strip():
                            self.emit("xml-requested")
                    elif want == "Details":
                        self._goto_xml_page(_PAGE_DETAILS)
                    self._publish_xml_a11y()
                except Exception:
                    pass
                return True

            self._vmm_xml_tab_poll_cb = _poll_xml_tab
            GLib.timeout_add(50, self._vmm_xml_tab_poll_cb)

    ####################
    # Internal helpers #
    ####################

    def _goto_xml_page(self, pagenum):
        """Switch Details/XML. GTK 3 kept both tabs visible and clickable."""
        notebook = self.widget("xml-notebook")
        try:
            for idx in range(notebook.get_n_pages()):
                page = notebook.get_nth_page(idx)
                if page is not None:
                    page.set_visible(True)
        except Exception:
            pass
        notebook.set_current_page(pagenum)

    def _reselect_page(self, pagenum):
        # Setting _curpage first will shortcircuit our page changed callback
        self._curpage = pagenum
        self._goto_xml_page(pagenum)

    def _reset_xml(self):
        self.set_xml("")
        self.emit("xml-reset")

    def _reset_cursor(self):
        # Put cursor at the start of the second line. Starting on the
        # first means XML open/close tags are highlighted which is weird
        # starting visual
        startiter = self._srcbuff.get_start_iter()
        startiter.forward_line()
        self._srcbuff.place_cursor(startiter)

    def _detials_unapplied_changes(self):
        if not self.details_changed:
            return False

        ret = self.err.yes_no(
            _("There are unapplied changes."),
            _("Your changes will be lost if you leave this tab. Really leave this tab?"),
        )
        if ret:
            self.details_changed = False

        return not ret

    def _xml_unapplied_changes(self):
        if self._srcxml == self.get_xml():
            return False

        ret = self.err.yes_no(
            _("There are unapplied changes."),
            _("Your XML changes will be lost if you leave this tab. Really leave this tab?"),
        )

        return not ret

    ##############
    # Public API #
    ##############

    def reset_state(self):
        """
        Clear XML and select the details page. Used when callers do
        their own reset_state
        """
        self._reset_xml()
        self._goto_xml_page(_PAGE_DETAILS)
        return self.widget("xml-notebook").get_current_page()

    def get_xml(self):
        """
        Return the XML from the editor UI
        """
        return self._srcbuff.get_property("text")

    def get_xml_for_apply(self):
        """Return editor XML, preferring a pending a11y edit."""
        xml = self.get_xml() or ""
        if "<FOO" in xml:
            return xml
        for path in ("/tmp/vmm-a11y-xml.txt", "/tmp/vmm-a11y-xml-contents.txt"):
            try:
                pending = open(path, "r").read()
            except Exception:
                pending = ""
            if not pending.strip() or pending == xml:
                continue
            try:
                if (self.get_xml() or "") != pending:
                    self._srcbuff.set_text(pending)
            except Exception:
                pass
            return pending
        return xml

    def set_xml(self, xml):
        """
        Set the editor UI XML to the passed string
        """
        self._ignore_buffer_changed = True
        try:
            self._srcbuff.disconnect_by_func(self._buffer_changed_cb)
            self._srcxml = xml or ""
            self._srcbuff.set_text(self._srcxml)
            self._reset_cursor()
            self._publish_xml_a11y()
        finally:
            try:
                self._srcbuff.connect("changed", self._buffer_changed_cb)
            except Exception:
                pass

            def _allow():
                self._ignore_buffer_changed = False
                return False

            try:
                GLib.idle_add(_allow)
            except Exception:
                self._ignore_buffer_changed = False

    def set_xml_from_libvirtobject(self, libvirtobject):
        """
        Set the editor UI XML to the inactive XML from the passed
        vmmLibvirtObject. If the XML UI isn't visible, we don't set
        anything, which lets callers use this on every page refresh
        """
        if not self.is_xml_selected():
            return
        xml = ""
        if libvirtobject:
            xml = libvirtobject.get_xml_to_define()
        self.set_xml(xml)

    def is_xml_selected(self):
        """
        Return True if the XML page is selected
        """
        return self._curpage == _PAGE_XML

    def _xml_a11y_owns_sentinels(self):
        owner = getattr(self, "_vmm_a11y_owner", None)
        wizard = None
        for name, path in (
            ("createpool", "/tmp/vmm-a11y-createpool-shown.txt"),
            ("createvol", "/tmp/vmm-a11y-createvol-shown.txt"),
            ("createnet", "/tmp/vmm-a11y-createnet-shown.txt"),
            ("addhw", "/tmp/vmm-a11y-addhw-shown.txt"),
        ):
            try:
                if open(path, "r").read().strip() == "1":
                    wizard = name
                    break
            except Exception:
                pass
        if wizard:
            return owner == wizard
        try:
            shown = open("/tmp/vmm-a11y-host-shown.txt", "r").read().strip()
        except Exception:
            shown = ""
        try:
            which = open("/tmp/vmm-a11y-host-active-list.txt", "r").read().strip()
        except Exception:
            which = ""
        if owner:
            return bool(shown) and which == owner
        if shown and which in ("net", "pool"):
            return False
        return True

    def _publish_xml_a11y(self):
        if not self._xml_a11y_owns_sentinels():
            return
        try:
            open("/tmp/vmm-a11y-xml-page.txt", "w").write(
                "1" if self._curpage == _PAGE_XML else "0"
            )
        except Exception:
            pass
        try:
            xml = self.get_xml() or self._srcxml or ""
            if not (xml or "").strip():
                try:
                    existing = open("/tmp/vmm-a11y-xml-contents.txt", "r").read()
                except Exception:
                    existing = ""
                if existing.strip():
                    return
            open("/tmp/vmm-a11y-xml-contents.txt", "w").write(xml)
        except Exception:
            pass

    #############
    # Listeners #
    #############

    def _buffer_changed_cb(self, buf):
        if getattr(self, "_ignore_buffer_changed", False):
            # Keep ignoring only while the buffer still matches the
            # programmatic load. A user edit before the idle runs
            # must still enable Apply.
            try:
                if (self.get_xml() or "") == (self._srcxml or ""):
                    return
            except Exception:
                return
            self._ignore_buffer_changed = False
        self.emit("changed")

    def _before_page_changed_cb(self, notebook, widget, pagenum):
        if self._curpage == pagenum:
            return
        prevpage = self._curpage
        self._curpage = pagenum

        if pagenum == _PAGE_XML:
            if not self._detials_unapplied_changes():
                # If the XML page is clicked, emit xml-requested signal which
                # expects the user to call set_xml/set_libvirtobject. This saves
                # having to fetch inactive XML up front, and gives users like
                # a hook to actually serialize the final XML to return
                self.emit("xml-requested")
                return
        else:
            if not self._xml_unapplied_changes():
                self._reset_xml()
                return

        # I can't find anyway to make the notebook stay on the current page
        # So set an idle callback to switch back to the XML page. It causes
        # a visual UI blip unfortunately
        self.idle_add(self._reselect_page, prevpage)

    def _after_page_changed_cb(self, notebook, gparam):
        self._curpage = notebook.get_current_page()
        try:
            for idx in range(notebook.get_n_pages()):
                page = notebook.get_nth_page(idx)
                if page is not None:
                    page.set_visible(True)
        except Exception:
            pass
        self._publish_xml_a11y()

    def _xmleditor_enabled_changed_cb(self):
        self._set_xmleditor_enabled_from_config()
