# Unit tests of the statement of the AS on L4S support (TS 26.512 V19.3.0 clause 4.6.0.1: the AS is required to support L4S at M4d once an
# ECN marking for L4S Dynamic Policy Instance is active). The option supports_l4s only states; the AS has no L4S network stack.
# License: 5G-MAG Public License (v1.0)
import logging
import os
import tempfile
import unittest

from rt_5gms_as.context import Context

from tests.unit.test_context import CONFIG


class SupportsL4s(unittest.TestCase):
    def context(self, option=None):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        config = os.path.join(d.name, 'as.conf')
        with open(config, 'w') as fh:
            fh.write(CONFIG.format(d=d.name) + ('supports_l4s = %s\n' % option if option is not None else ''))
        ctx = Context(config)
        ctx.setAppLog(logging.getLogger('test'))
        return ctx

    def test_by_default_the_as_does_not_claim_l4s(self):
        self.assertFalse(self.context().supportsL4s())

    def test_the_option_says_yes_or_no(self):
        for value in ('true', 'yes', '1', 'on'):
            self.assertTrue(self.context(value).supportsL4s(), value)
        for value in ('false', 'no', '0', 'off'):
            self.assertFalse(self.context(value).supportsL4s(), value)

    def test_a_value_that_is_not_a_boolean_is_an_error(self):
        with self.assertRaises(Context.ConfigError):
            self.context('maybe').supportsL4s()


if __name__ == '__main__':
    unittest.main()
