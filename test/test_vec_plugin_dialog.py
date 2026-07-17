# coding=utf-8
"""Dialog test.

.. note:: This program is free software; you can redistribute it and/or modify
     it under the terms of the GNU General Public License as published by
     the Free Software Foundation; either version 2 of the License, or
     (at your option) any later version.

"""

__author__ = 'FieldWatch'
__date__ = '2025-12-09'
__copyright__ = 'Copyright 2025, FieldWatch'

import unittest

from core.qt_compat import DialogRejected
from dialogs.vec_plugin_dialog import VecPluginDialog

from utilities import get_qgis_app
QGIS_APP = get_qgis_app()


class VecPluginDialogTest(unittest.TestCase):
    """Test dialog works."""

    def setUp(self):
        """Runs before each test."""
        self.dialog = VecPluginDialog(None)

    def tearDown(self):
        """Runs after each test."""
        self.dialog = None

    def test_dialog_run(self):
        """Run uses overridden accept(); without license/polygon the dialog does not close as Accepted."""
        self.assertEqual(self.dialog.runButton.text(), "Run")
        self.dialog.runButton.click()
        self.assertEqual(self.dialog.result(), DialogRejected)

    def test_dialog_cancel(self):
        """Test we can click cancel."""
        button = self.dialog.cancelButton
        button.click()
        result = self.dialog.result()
        self.assertEqual(result, DialogRejected)

if __name__ == "__main__":
    suite = unittest.makeSuite(VecPluginDialogTest)
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)

