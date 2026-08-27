#!/usr/bin/env python3
# Convert GTK3 Glade UI files to GTK4 Builder XML.
# This work is licensed under the GNU GPLv2 or later.

import pathlib
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI_DIR = ROOT / "ui"

REMOVE_PROPS = {
    "gravity",
    "type-hint",
    "can-default",
    "can-focus",
    "show-arrow",
    "is-important",
    "shadow-type",
    "layout-style",
    "has-separator",
    "window-position",
    "skip-taskbar-hint",
    "skip-pager-hint",
    "urgency-hint",
    "decorated",
    "hide-titlebar-when-maximized",
    "track-visited-links",
    "resize-mode",
    "invisible-char",
    "primary-icon-name",
    "has-default",
    "double-buffered",
    "app-paintable",
    "no-show-all",
    "events",
    "image",
    "primary-icon-activatable",
    "primary-icon-sensitive",
    "secondary-icon-name",
    "secondary-icon-activatable",
    "invisible-char-set",
    "caps-lock-warning",
    "populate-all",
    "xalign",
    "yalign",
}

SIGNAL_RENAME = {
    "delete-event": "close-request",
}

REMOVE_SIGNALS = {
    "configure-event",
    "button-press-event",
    "key-press-event",
    "key-release-event",
    "enter-notify-event",
    "leave-notify-event",
    "size-allocate",
    "map-event",
    "unmap-event",
    "focus-in-event",
    "focus-out-event",
}


def convert_file(src: pathlib.Path, dest: pathlib.Path) -> None:
    with tempfile.NamedTemporaryFile(suffix=".ui", delete=False) as tmp:
        tmp_path = pathlib.Path(tmp.name)
    try:
        proc = subprocess.run(
            ["gtk4-builder-tool", "simplify", "--3to4", str(src)],
            capture_output=True,
            text=True,
            check=False,
        )
        text = proc.stdout or src.read_text(encoding="utf-8")
        tmp_path.write_text(text, encoding="utf-8")
        cleanup_xml(tmp_path, dest)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def cleanup_xml(src: pathlib.Path, dest: pathlib.Path) -> None:
    # Preserve comments by working as text first for a few regex fixes
    text = src.read_text(encoding="utf-8")
    text = text.replace('<requires lib="gtk+" version="3.22"/>', '<requires lib="gtk" version="4.0"/>')
    if 'lib="gtk"' not in text:
        text = text.replace("<interface>", '<interface>\n  <requires lib="gtk" version="4.0"/>', 1)
    if "libadwaita" not in text:
        text = text.replace(
            '<requires lib="gtk" version="4.0"/>',
            '<requires lib="gtk" version="4.0"/>\n  <requires lib="libadwaita" version="1.4"/>',
            1,
        )
    text = re.sub(
        r'\s*<property name="AtkObject::accessible-name">([^<]+)</property>',
        r"",
        text,
    )
    dest.write_text(text, encoding="utf-8")

    ET.register_namespace("", "")
    tree = ET.parse(dest)
    root = tree.getroot()

    # Convert AtkObject children to accessible-label on parent
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag != "child":
                continue
            obj = child.find("object")
            if obj is None:
                continue
            if obj.get("class") == "AtkObject":
                name = None
                for prop in obj.findall("property"):
                    if "accessible-name" in (prop.get("name") or ""):
                        name = (prop.text or "").strip()
                if name is not None:
                    # parent of this <child> is the widget object
                    label_prop = ET.SubElement(parent, "property", {"name": "accessible-label"})
                    label_prop.text = name
                parent.remove(child)

    for obj in root.iter("object"):
        for prop in list(obj.findall("property")):
            name = prop.get("name")
            if name == "border-width":
                val = (prop.text or "").strip()
                obj.remove(prop)
                if val and val not in ("0", "0.0"):
                    have = {p.get("name") for p in obj.findall("property")}
                    for mname in (
                        "margin-top",
                        "margin-bottom",
                        "margin-start",
                        "margin-end",
                    ):
                        if mname not in have:
                            extra = ET.SubElement(obj, "property", {"name": mname})
                            extra.text = val
            elif name == "shadow-type":
                val = (prop.text or "").strip()
                obj.remove(prop)
                if val and val not in ("none", "None"):
                    have = {p.get("name") for p in obj.findall("property")}
                    if "css-classes" not in have:
                        extra = ET.SubElement(obj, "property", {"name": "css-classes"})
                        extra.text = "vmm-scroll-shadow"
            elif name in REMOVE_PROPS:
                obj.remove(prop)
            elif name == "visible" and (prop.text or "").lower() in ("true", "1", "yes"):
                # GTK4 widgets are visible by default; keep anyway
                pass
            elif name == "can-focus":
                obj.remove(prop)
            elif name.startswith("AtkObject::"):
                obj.remove(prop)
            elif name in ("icon-size", "icon_size"):
                val = (prop.text or "").strip()
                if val in ("1", "2", "3", "menu", "button"):
                    prop.text = "normal"
                elif val in ("4", "5", "6", "large-toolbar", "dnd", "dialog"):
                    prop.text = "large"

        for sig in list(obj.findall("signal")):
            sname = sig.get("name")
            if sname in REMOVE_SIGNALS:
                obj.remove(sig)
            elif sname in SIGNAL_RENAME:
                sig.set("name", SIGNAL_RENAME[sname])
            elif sname == "clicked" and obj.get("class") in (
                "GtkCheckButton",
                "GtkRadioButton",
                "GtkCheckMenuItem",
                "GtkRadioMenuItem",
            ):
                sig.set("name", "toggled")
            elif sname == "response" and obj.get("class") == "GtkAboutDialog":
                obj.remove(sig)

        for accel in list(obj.findall("accelerator")):
            obj.remove(accel)

        # GtkWindow child -> GTK4 uses child without packing for window
        cls = obj.get("class")
        if cls == "GtkScrolledWindow":
            for prop in list(obj.findall("property")):
                if prop.get("name") in ("shadow-type", "window-placement"):
                    obj.remove(prop)

    # Remove leftover packing properties GTK4 does not understand
    for packing in list(root.iter("packing")):
        for prop in list(packing):
            if prop.get("name") in ("expand", "fill", "position", "pack-type", "homogeneous"):
                packing.remove(prop)
        if len(list(packing)) == 0:
            parent = None
            for cand in root.iter():
                if packing in list(cand):
                    parent = cand
                    break
            if parent is not None:
                parent.remove(packing)

    tree.write(dest, encoding="utf-8", xml_declaration=True)
    # gtk-builder prefers pretty-ish XML; re-indent lightly
    text = dest.read_text(encoding="utf-8")
    if not text.startswith("<?xml"):
        text = '<?xml version="1.0" encoding="UTF-8"?>\n' + text
    dest.write_text(text, encoding="utf-8")


def main():
    files = sorted(UI_DIR.glob("*.ui"))
    if not files:
        print("No UI files found", file=sys.stderr)
        sys.exit(1)
    for src in files:
        print("converting", src.name)
        convert_file(src, src)
    print("converted", len(files), "files")


if __name__ == "__main__":
    main()
