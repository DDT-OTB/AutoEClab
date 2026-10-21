from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from pump_console.auth import validate_password
from pump_console.repository import ApiError


class PasswordPolicyTests(unittest.TestCase):
    def test_nine_characters_with_lowercase_and_digits_are_accepted(self):
        for password in ("labtest12", "Labtest12!", "实验密码短语a12"):
            with self.subTest(password=password):
                validate_password(password)

    def test_short_or_missing_required_character_types_are_rejected(self):
        for password in ("labtest1", "lowercaseonly", "123456789", "UPPERCASE123", " " * 20, None):
            with self.subTest(password=password):
                with self.assertRaises(ApiError) as failure:
                    validate_password(password)
                self.assertEqual(failure.exception.status, 422)

    def test_oversized_input_is_rejected(self):
        with self.assertRaises(ApiError):
            validate_password("a" * 128 + "1")
