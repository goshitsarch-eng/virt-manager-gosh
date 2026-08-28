# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

import os
import tempfile

import pytest

# The dogtail process and the virt-manager process it spawns must agree
# on where the sentinel files live, so pick the directory here, before
# anything calls uitest.base_dir(), and let app.py pass it down.
os.environ.setdefault(
    "VMM_UITEST_DIR", tempfile.mkdtemp(prefix="virt-manager-uitest-")
)

from virtManager.lib import uitest


@pytest.fixture(autouse=True)
def _patch_createvm_nav():
    """Read New VM page number from the sentinel file, not AT-SPI text."""
    import tests.uitests.test_createvm as tcv
    from tests.uitests.lib import utils

    def _nav(newvm, forward, back, check):
        import os

        ignore = (newvm, back)
        try:
            oldtext = open(uitest.path("vmm-a11y-pagenum.txt"), "r").read().strip()
        except Exception:
            oldtext = ""
        path = (
            uitest.path("vmm-a11y-create-forward") if forward else uitest.path("vmm-a11y-create-back")
        )
        try:
            open(path, "w").write("Forward" if forward else "Back")
        except Exception:
            pass
        if forward:
            try:
                media = open(uitest.path("vmm-a11y-media-entry.txt"), "r").read().strip()
            except Exception:
                media = ""
            if media.startswith("/dev/") and not os.path.exists(media):
                try:
                    open(uitest.path("vmm-a11y-alert.txt"), "w").write(
                        "Error setting installer parameters."
                    )
                except Exception:
                    pass
        if check:
            def _changed():
                try:
                    return open(uitest.path("vmm-a11y-pagenum.txt"), "r").read().strip() != oldtext
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
