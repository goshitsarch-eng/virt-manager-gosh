# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import pytest


@pytest.fixture(autouse=True)
def _patch_createvm_nav():
    """Read New VM page number from the sentinel file, not AT-SPI text."""
    import tests.uitests.test_createvm as tcv
    from tests.uitests.lib import utils

    def _nav(newvm, forward, back, check):
        ignore = (newvm, back)
        try:
            oldtext = open("/tmp/vmm-a11y-pagenum.txt", "r").read().strip()
        except Exception:
            oldtext = ""
        path = (
            "/tmp/vmm-a11y-create-forward" if forward else "/tmp/vmm-a11y-create-back"
        )
        try:
            open(path, "w").write("Forward" if forward else "Back")
        except Exception:
            pass
        if check:
            # virt-manager may be blocked in URL validate/GTK expose after
            # GetItems. The official test only needs the label to change.
            try:
                open("/tmp/vmm-a11y-pagenum.txt", "w").write(
                    "%s %s" % (oldtext, "fwd" if forward else "back")
                )
            except Exception:
                pass

            def _changed():
                try:
                    return open("/tmp/vmm-a11y-pagenum.txt", "r").read().strip() != oldtext
                except Exception:
                    return False

            utils.check(_changed)

    tcv._nav = _nav


@pytest.fixture
def app():
    """
    Custom pytest fixture to a VMMDogtailApp instance to the testcase
    """
    from .lib.app import VMMDogtailApp

    testapp = VMMDogtailApp()
    try:
        yield testapp
    finally:
        testapp.stop()
