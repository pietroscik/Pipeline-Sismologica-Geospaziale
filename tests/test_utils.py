import unittest
from scripts.utils import setup_logger


class TestUtils(unittest.TestCase):
    """Test per funzioni utility"""
    
    def test_setup_logger(self):
        """Test che setup_logger restituisca un logger valido"""
        logger = setup_logger("test")
        self.assertIsNotNone(logger)
        self.assertTrue(hasattr(logger, 'info'))
        self.assertTrue(hasattr(logger, 'error'))


if __name__ == '__main__':
    unittest.main()