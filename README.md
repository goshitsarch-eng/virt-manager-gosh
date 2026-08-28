# Virtual Machine Manager

`virt-manager` is a graphical tool for managing virtual machines
via [libvirt](https://libvirt.org). Most usage is with QEMU/KVM
virtual machines, but Xen and libvirt LXC containers are well
supported. Common operations for any libvirt driver should work.

Several command line tools are also provided:

 - `virt-install`: Create new libvirt virtual machines
 - `virt-clone`: Duplicate existing libvirt virtual machines
 - `virt-xml`: Edit existing libvirt virtual machines/manipulate libvirt XML

This tree carries a rewrite of the desktop application on **GTK 4** and
**libadwaita**, keeping the existing feature set, windows, wizards and
actions. The command line tools are unchanged. See [NEWS.md](NEWS.md)
for what landed in each release.

For dependency info and installation instructions, see the
[INSTALL.md](INSTALL.md) file. If you just want to quickly test the
code from a git checkout, you can launch any of the commands like:

```sh
./virt-manager --debug ...
```

## Contact

 - For IRC we use #virt on OFTC.
 - For bug reporting info, see
   [virt-manager bug reporting](https://virt-manager.org/bugs).
 - There are further project details on the
   [virt-manager](https://virt-manager.org/) website.
 - See the [CONTRIBUTING.md](CONTRIBUTING.md) file for info about submitting patches or
   contributing translations.

## Credits

This is a derivative of the upstream
[virt-manager](https://github.com/virt-manager/virt-manager) project,
Copyright (C) 2006-2026 Red Hat, Inc. and the virt-manager contributors,
and is redistributed under the same GNU GPL v2-or-later terms — see
[COPYING](COPYING). Upstream remains the canonical project; please report
bugs there unless they are specific to the GTK 4 port carried here.

Enormous thanks to the people and projects this work is built on:

 - **virt-manager** — the application, the command line tools, and the
   entire `virtinst` library, originally written by Daniel P. Berrange,
   Cole Robinson and Hugh O. Brock, and maintained since by a long list
   of contributors.
 - **[libvirt](https://libvirt.org)** and **libvirt-glib** — the
   virtualization management API that does all of the real work here.
 - **[GTK](https://gtk.org)** and
   **[libadwaita](https://gitlab.gnome.org/GNOME/libadwaita)** — the
   toolkit and platform library the interface is built with.
 - **[PyGObject](https://pygobject.gnome.org)** — the Python bindings
   that hold the whole thing together.
 - **[libosinfo](https://libosinfo.org)** — the OS database behind
   guest detection and defaults.
 - **[SPICE](https://www.spice-space.org)** and
   **[GTK-VNC](https://gitlab.gnome.org/GNOME/gtk-vnc)** — graphical
   console support.
 - **[VTE](https://gitlab.gnome.org/GNOME/vte)** — the serial console
   terminal, and **GtkSourceView** for XML editing.
 - Application artwork by Máirín Duffy, Mike Langlie, Jeremy Perry and
   Jakub Steiner.
 - The translators working through
   [Weblate](https://translate.fedoraproject.org/projects/virt-manager/virt-manager/),
   who are individually credited in the About dialog.
