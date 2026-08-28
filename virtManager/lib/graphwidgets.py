# Copyright (C) 2013, 2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import gi

from gi.repository import GObject
from gi.repository import Gtk

try:
    gi.require_foreign("cairo")
except (ImportError, ValueError):  # pragma: no cover
    pass

# pylint: disable=arguments-differ
# Newer pylint can detect, but warns that overridden arguments are wrong

class _RGB:
    red = 1.0
    green = 1.0
    blue = 1.0


def _adw_base_rgb():
    """libadwaita window background when StyleContext named colors are missing."""
    try:
        from gi.repository import Adw

        if Adw.StyleManager.get_default().get_dark():
            return 0.18, 0.18, 0.18
    except Exception:
        pass
    return 1.0, 1.0, 1.0


def _theme_base_rgb(widget=None):
    """GTK 3 used theme_base_color so sparklines match light/dark themes."""
    ctx = None
    if widget is not None and hasattr(widget, "get_style_context"):
        try:
            ctx = widget.get_style_context()
        except Exception:
            ctx = None
    names = (
        "theme_base_color",
        "theme_bg_color",
        "view_bg_color",
        "window_bg_color",
    )
    if ctx is not None:
        for name in names:
            try:
                found, color = ctx.lookup_color(name)
            except Exception:
                found, color = False, None
            if found and color is not None:
                try:
                    return float(color.red), float(color.green), float(color.blue)
                except Exception:
                    continue
    return _adw_base_rgb()


def _theme_border_rgba(widget=None):
    """A subtle graph outline that reads on light *and* dark backgrounds.

    GTK 3 hardcoded a light grey here. Against the Adwaita dark window
    background that is a near-white box drawn around every sparkline, so
    derive the outline from the fill it sits on instead.
    """
    if widget is not None and hasattr(widget, "get_style_context"):
        try:
            found, color = widget.get_style_context().lookup_color("borders")
        except Exception:
            found, color = False, None
        if found and color is not None:
            try:
                return (
                    float(color.red),
                    float(color.green),
                    float(color.blue),
                    float(color.alpha),
                )
            except Exception:
                pass
    red, green, blue = _theme_base_rgb(widget)
    luminance = (0.299 * red) + (0.587 * green) + (0.114 * blue)
    shade = 0.0 if luminance > 0.5 else 1.0
    return shade, shade, shade, 0.22


def _theme_accent_rgb(widget=None):
    """The Adwaita accent colour, or virt-manager's traditional blue.

    Drawing the graphs in the user's accent keeps them at home in both
    light and dark; the hardcoded pale-blue fill below was tuned for a
    white background and washed out over the dark one.
    """
    if widget is not None and hasattr(widget, "get_style_context"):
        ctx = None
        try:
            ctx = widget.get_style_context()
        except Exception:
            ctx = None
        if ctx is not None:
            for name in ("accent_color", "accent_bg_color", "theme_selected_bg_color"):
                try:
                    found, color = ctx.lookup_color(name)
                except Exception:
                    found, color = False, None
                if found and color is not None:
                    try:
                        return float(color.red), float(color.green), float(color.blue)
                    except Exception:
                        continue
    return 0.421875, 0.640625, 0.73046875


BASECOLOR = _RGB()


def rect_print(name, rect):  # pragma: no cover
    # For debugging
    print("%s: height=%d, width=%d, x=%d, y=%d" % (name, rect.height, rect.width, rect.x, rect.y))


def _line_helper(cairo_ct, bottom_baseline, points, for_fill=False):
    last_was_zero = False
    last_point = None

    for index, (x, y) in enumerate(points):

        # If stats value == 0, we don't want to draw a line
        is_zero = bool(y == bottom_baseline)

        # If the line is for filling, alter the coords so that fill covers
        # the same area as the parent sparkline: fill is one pixel short
        # to not overwrite the spark line
        if for_fill:
            if index == 0:
                x -= 1
            elif index == (len(points) - 1):
                x += 1
            elif last_was_zero and is_zero:
                y += 1

        if index == 0:
            cairo_ct.move_to(x, y)
        elif last_was_zero and is_zero and not for_fill:
            cairo_ct.move_to(x, y)
        else:
            cairo_ct.line_to(x, y)
            last_point = (x, y)

        last_was_zero = is_zero

    return last_point


def draw_line(cairo_ct, y, h, points):
    if not len(points):
        return  # pragma: no cover

    last_point = _line_helper(cairo_ct, y + h, points)
    if not last_point:
        # Nothing to draw
        return

    # Paint the line
    cairo_ct.stroke()


def draw_fill(cairo_ct, x, y, w, h, points, taper=False):
    if not len(points):
        return  # pragma: no cover

    _line_helper(cairo_ct, y + h, points, for_fill=True)

    baseline_y = h + y + 1
    if taper:
        start_x = w + x
    else:
        start_x = points[-1][0]

    # Box out the area to fill
    cairo_ct.line_to(start_x + 1, baseline_y)
    cairo_ct.line_to(x - 1, baseline_y)

    # Paint the fill
    cairo_ct.fill()


class CellRendererSparkline(Gtk.CellRenderer):
    __gproperties__ = {
        # 'name': (GObject.TYPE_*,
        #           nickname, long desc, (type related args), mode)
        # Type related args can be min, max for int (etc.), or default value
        # for strings and bool
        "data_array": (
            GObject.TYPE_PYOBJECT,
            "Data Array",
            "Array of data points for the graph",
            GObject.PARAM_READWRITE,
        ),
        "reversed": (
            GObject.TYPE_BOOLEAN,
            "Reverse data",
            "Process data from back to front.",
            0,
            GObject.PARAM_READWRITE,
        ),
    }

    def __init__(self):
        Gtk.CellRenderer.__init__(self)

        self.data_array = []
        self.num_sets = 0
        self.filled = True
        self.reversed = False
        self.rgb = None

    def do_snapshot(self, snapshot, widget, background_area, cell_area, flags):
        from gi.repository import Graphene

        rect = Graphene.Rect()
        rect.init(cell_area.x, cell_area.y, cell_area.width, cell_area.height)
        cr = snapshot.append_cairo(rect)
        self._render_cairo(cr, widget, background_area, cell_area, flags)

    def do_render(self, cr, widget, background_area, cell_area, flags):
        self._render_cairo(cr, widget, background_area, cell_area, flags)

    def _render_cairo(self, cr, widget, background_area, cell_area, flags):
        # cr                : Cairo context
        # widget            : GtkWidget instance
        # background_area   : GdkRectangle: entire cell area
        # cell_area         : GdkRectangle: area normally rendered by cell
        # flags             : flags that affect rendering
        ignore = background_area
        ignore = flags

        # Indent of the gray border around the graph
        BORDER_PADDING = 2
        # Indent of graph from border
        GRAPH_INDENT = 2
        GRAPH_PAD = BORDER_PADDING + GRAPH_INDENT

        # We don't use yalign, since we expand to the entire height
        ignore = self.get_property("yalign")
        xalign = self.get_property("xalign")

        # Set up graphing bounds
        graph_x = cell_area.x + GRAPH_PAD
        graph_y = cell_area.y + GRAPH_PAD
        graph_width = cell_area.width - (GRAPH_PAD * 2)
        graph_height = cell_area.height - (GRAPH_PAD * 2)

        pixels_per_point = graph_width // max(1, len(self.data_array) - 1)

        # Graph width needs to be some multiple of the amount of data points
        # we have
        graph_width = pixels_per_point * max(1, len(self.data_array) - 1)

        # Recalculate border width based on the amount we are graphing
        border_width = graph_width + (GRAPH_INDENT * 2)

        # Align the widget
        empty_space = cell_area.width - border_width - (BORDER_PADDING * 2)
        if empty_space:
            xalign_space = int(empty_space * xalign)
            cell_area.x += xalign_space
            graph_x += xalign_space

        cr.set_line_width(3)
        # 1 == LINE_CAP_ROUND
        cr.set_line_cap(1)

        # Draw the graph border
        cr.set_source_rgba(*_theme_border_rgba(widget))
        cr.rectangle(
            cell_area.x + BORDER_PADDING,
            cell_area.y + BORDER_PADDING,
            border_width,
            cell_area.height - (BORDER_PADDING * 2),
        )
        cr.stroke()

        # Fill in theme-base box inside graph outline (GTK 3 theme_base_color)
        red, green, blue = _theme_base_rgb(widget)
        cr.set_source_rgb(red, green, blue)
        cr.rectangle(
            cell_area.x + BORDER_PADDING,
            cell_area.y + BORDER_PADDING,
            border_width,
            cell_area.height - (BORDER_PADDING * 2),
        )
        cr.fill()

        def get_y(index):
            baseline_y = graph_y + graph_height

            n = index
            if self.reversed:
                n = len(self.data_array) - index - 1

            val = self.data_array[n]
            y = baseline_y - (graph_height * val)

            y = max(graph_y, y)
            y = min(graph_y + graph_height, y)
            return y

        points = []
        for index in range(0, len(self.data_array)):
            x = int((index * pixels_per_point) + graph_x)
            y = int(get_y(index))

            points.append((x, y))

        cell_area.x = graph_x
        cell_area.y = graph_y
        cell_area.width = graph_width
        cell_area.height = graph_height

        # The sparkline and its fill, in the theme's accent colour.
        cr.set_line_width(2)
        accent = _theme_accent_rgb(widget)
        cr.set_source_rgb(*accent)
        draw_line(cr, cell_area.y, cell_area.height, points)

        cr.set_source_rgba(accent[0], accent[1], accent[2], 0.28)

        draw_fill(cr, cell_area.x, cell_area.y, cell_area.width, cell_area.height, points)
        return

    def _fixed_size(self):
        FIXED_WIDTH = max(1, len(self.data_array))
        FIXED_HEIGHT = 15
        xpad = self.get_property("xpad")
        ypad = self.get_property("ypad")
        return (xpad * 2) + FIXED_WIDTH, (ypad * 2) + FIXED_HEIGHT

    def do_get_preferred_width(self, widget):
        ignore = widget
        width, _height = self._fixed_size()
        return width, width

    def do_get_preferred_height(self, widget):
        ignore = widget
        _width, height = self._fixed_size()
        return height, height

    def do_get_size(self, widget, cell_area=None):
        ignore = widget
        ignore = cell_area
        width, height = self._fixed_size()
        return (0, 0, width, height)

    # Properties are passed to use with "-" in the name, but python
    # variables can't be named like that
    def _sanitize_param_spec_name(self, name):
        return name.replace("-", "_")

    def do_get_property(self, param_spec):  # pragma: no cover
        name = self._sanitize_param_spec_name(param_spec.name)
        return getattr(self, name)

    def do_set_property(self, param_spec, value):
        name = self._sanitize_param_spec_name(param_spec.name)
        setattr(self, name, value)

    def set_property(self, *args, **kwargs):
        # Make pylint happy
        return Gtk.CellRenderer.set_property(self, *args, **kwargs)


class Sparkline(Gtk.DrawingArea):
    __gproperties__ = {
        # 'name': (GObject.TYPE_*,
        #           nickname, long desc, (type related args), mode)
        # Type related args can be min, max for int (etc.), or default value
        # for strings and bool
        "data_array": (
            GObject.TYPE_PYOBJECT,
            "Data Array",
            "Array of data points for the graph",
            GObject.PARAM_READWRITE,
        ),
        "filled": (
            GObject.TYPE_BOOLEAN,
            "Filled",
            "the foo of the object",
            1,
            GObject.PARAM_READWRITE,
        ),
        "num_sets": (
            GObject.TYPE_INT,
            "Number of sets",
            "Number of data sets to graph",
            1,
            2,
            1,
            GObject.PARAM_READWRITE,
        ),
        "reversed": (
            GObject.TYPE_BOOLEAN,
            "Reverse data",
            "Process data from back to front.",
            0,
            GObject.PARAM_READWRITE,
        ),
        "rgb": (GObject.TYPE_PYOBJECT, "rgb array", "List of rgb values", GObject.PARAM_READWRITE),
    }

    def __init__(self):
        Gtk.DrawingArea.__init__(self)

        self._data_array = []
        self.num_sets = 1
        self.filled = True
        self.reversed = False
        self.rgb = []

        self.add_css_class("entry")
        # GTK 3 gave this a size request and let the container stretch it.
        # do_size_request is not a GTK 4 vfunc, so ask for the height here.
        self.set_vexpand(True)
        self.set_content_height(80)
        self.set_draw_func(self._draw_func)

    def set_data_array(self, val):
        self._data_array = val
        self.queue_draw()

    def get_data_array(self):
        return self._data_array

    data_array = property(get_data_array, set_data_array)

    def do_draw(self, cr):
        self._draw_func(self, cr, self.get_width(), self.get_height(), None)

    def _draw_func(self, _area, cr, w, h, _data=None):
        cr.save()

        points_per_set = len(self.data_array) // self.num_sets
        pixels_per_point = float(w) / (float((points_per_set - 1) or 1))

        widget = self

        # GTK 3 drew the backing rectangle, ticks and frame through the
        # "entry" style class. In GTK 4 that class paints nothing on a
        # GtkDrawingArea node, which left the graph as three bare tick
        # lines on the window background, so draw them directly -- from
        # theme colours, so this still follows light/dark.
        border = _theme_border_rgba(widget)

        red, green, blue = _theme_base_rgb(widget)
        cr.set_source_rgb(red, green, blue)
        cr.rectangle(0, 0, w - 1, h - 1)
        cr.fill()

        cr.set_line_width(1)
        cr.set_source_rgba(border[0], border[1], border[2], border[3] * 0.5)
        max_ticks = 4
        for index in range(1, max_ticks):
            tick_y = (h // max_ticks) * index + 0.5
            cr.move_to(1, tick_y)
            cr.line_to(w - 2, tick_y)
            cr.stroke()

        cr.set_source_rgba(*border)
        cr.rectangle(0.5, 0.5, w - 2, h - 2)
        cr.stroke()

        # Draw the actual sparkline
        def get_y(dataset, index):
            baseline_y = h

            n = dataset * points_per_set
            if self.reversed:
                n += points_per_set - index - 1
            else:
                n += index

            val = self.data_array[n]
            return baseline_y - ((h - 1) * val)

        cr.set_line_width(2)

        for dataset in range(0, self.num_sets):
            cr.set_source_rgb(*_theme_accent_rgb(widget))
            if len(self.rgb) == (self.num_sets * 3):
                cr.set_source_rgb(
                    self.rgb[(dataset * 3)],
                    self.rgb[(dataset * 3) + 1],
                    # Was (dataset * 1) + 2, so the second data set drew
                    # its blue channel from the first set's.
                    self.rgb[(dataset * 3) + 2],
                )
            points = []
            for index in range(0, points_per_set):
                x = index * pixels_per_point
                y = get_y(dataset, index)

                points.append((int(x), int(y)))

            if self.num_sets == 1:
                pass

            draw_line(cr, 0, h, points)
            if self.filled:
                # Fixes a fully filled graph from having an oddly
                # tapered in end (bug 560913). Need to figure out
                # what's really going on.
                points = [(0, h)] + points
                draw_fill(cr, 0, 0, w, h, points, taper=True)

        cr.restore()

        return 0

    def do_size_request(self, requisition):  # pragma: no cover
        width = len(self.data_array) / self.num_sets
        height = 20

        requisition.width = width
        requisition.height = height

    # Properties are passed to use with "-" in the name, but python
    # variables can't be named like that
    def _sanitize_param_spec_name(self, name):
        return name.replace("-", "_")

    def do_get_property(self, param_spec):  # pragma: no cover
        name = self._sanitize_param_spec_name(param_spec.name)
        return getattr(self, name)

    def do_set_property(self, param_spec, value):
        name = self._sanitize_param_spec_name(param_spec.name)
        setattr(self, name, value)

    # These make pylint happy
    def set_property(self, *args, **kwargs):
        return Gtk.DrawingArea.set_property(self, *args, **kwargs)

    def show(self, *args, **kwargs):
        return Gtk.DrawingArea.show(self, *args, **kwargs)

    def destroy(self, *args, **kwargs):
        return Gtk.DrawingArea.destroy(self, *args, **kwargs)
