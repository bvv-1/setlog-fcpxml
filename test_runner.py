from __future__ import annotations

import unittest


def main() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner().run(suite)
    return 0 if result.wasSuccessful() else 1
