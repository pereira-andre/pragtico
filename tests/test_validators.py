"""Unit tests for the validators module."""

import unittest
from datetime import datetime, timezone

from core.validators import (
    validate_choice,
    validate_datetime_range,
    validate_email,
    validate_feedback_status,
    validate_imo,
    validate_not_past_datetime,
    validate_optional_positive_number,
    validate_optional_text,
    validate_operational_feedback_status,
    validate_password,
    validate_phone,
    validate_positive_number,
    validate_required_text,
    validate_role,
    validate_tug_count,
    validate_vessel_dimensions,
)


class TestRequiredText(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_required_text("  hello  ", "Label"), "hello")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            validate_required_text("", "Label")

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            validate_required_text(None, "Label")

    def test_max_length(self):
        with self.assertRaises(ValueError):
            validate_required_text("a" * 501, "Label", max_length=500)

    def test_within_max_length(self):
        self.assertEqual(validate_required_text("abc", "Label", max_length=10), "abc")


class TestOptionalText(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(validate_optional_text(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(validate_optional_text(None), "")

    def test_exceeds_max(self):
        with self.assertRaises(ValueError):
            validate_optional_text("x" * 3000, max_length=2000)


class TestPositiveNumber(unittest.TestCase):
    def test_valid_integer(self):
        self.assertEqual(validate_positive_number("42", "LOA"), "42")

    def test_valid_float(self):
        self.assertEqual(validate_positive_number("9.94", "Calado"), "9.94")

    def test_comma_decimal(self):
        self.assertEqual(validate_positive_number("9,94", "Calado"), "9.94")

    def test_empty_required_raises(self):
        with self.assertRaises(ValueError):
            validate_positive_number("", "LOA")

    def test_empty_optional_returns_empty(self):
        self.assertEqual(validate_positive_number("", "LOA", required=False), "")

    def test_not_a_number_raises(self):
        with self.assertRaises(ValueError):
            validate_positive_number("abc", "LOA")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            validate_positive_number("-5", "LOA", min_value=0.0)

    def test_exceeds_max_raises(self):
        with self.assertRaises(ValueError):
            validate_positive_number("600", "LOA", max_value=500.0)

    def test_zero_allowed_by_default(self):
        self.assertEqual(validate_positive_number("0", "LOA"), "0")


class TestOptionalPositiveNumber(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(validate_optional_positive_number("", "Calado"), "")

    def test_valid(self):
        self.assertEqual(validate_optional_positive_number("5.5", "Calado"), "5.5")


class TestTugCount(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_tug_count("2"), "2")

    def test_empty_returns_empty(self):
        self.assertEqual(validate_tug_count(""), "")

    def test_zero(self):
        self.assertEqual(validate_tug_count("0"), "0")

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            validate_tug_count("-1")

    def test_exceeds_max_raises(self):
        with self.assertRaises(ValueError):
            validate_tug_count("11")

    def test_not_integer_raises(self):
        with self.assertRaises(ValueError):
            validate_tug_count("2.5")


class TestDatetimeRange(unittest.TestCase):
    def test_valid_range(self):
        validate_datetime_range("2026-03-24T10:00:00+00:00", "2026-03-24T12:00:00+00:00")

    def test_reversed_raises(self):
        with self.assertRaises(ValueError):
            validate_datetime_range("2026-03-24T12:00:00+00:00", "2026-03-24T10:00:00+00:00")

    def test_equal_raises(self):
        with self.assertRaises(ValueError):
            validate_datetime_range("2026-03-24T10:00:00+00:00", "2026-03-24T10:00:00+00:00")

    def test_empty_values_skip(self):
        validate_datetime_range("", "2026-03-24T12:00:00+00:00")
        validate_datetime_range("2026-03-24T10:00:00+00:00", "")
        validate_datetime_range("", "")


class TestNotPastDatetime(unittest.TestCase):
    def test_current_minute_is_allowed(self):
        validate_not_past_datetime(
            "2026-03-31T10:15:00+00:00",
            "ETA",
            reference=datetime(2026, 3, 31, 10, 15, 42, tzinfo=timezone.utc),
        )

    def test_past_minute_raises(self):
        with self.assertRaisesRegex(ValueError, "ETA não pode ser anterior à data/hora presente"):
            validate_not_past_datetime(
                "2026-03-31T10:14:59+00:00",
                "ETA",
                reference=datetime(2026, 3, 31, 10, 15, 0, tzinfo=timezone.utc),
            )


class TestEmail(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_email("User@Example.COM"), "user@example.com")

    def test_empty_required_raises(self):
        with self.assertRaises(ValueError):
            validate_email("")

    def test_empty_optional(self):
        self.assertEqual(validate_email("", required=False), "")

    def test_invalid_format_raises(self):
        with self.assertRaises(ValueError):
            validate_email("not-an-email")

    def test_missing_domain_raises(self):
        with self.assertRaises(ValueError):
            validate_email("user@")


class TestPhone(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_phone("+351 912 345 678"), "+351 912 345 678")

    def test_empty_required_raises(self):
        with self.assertRaises(ValueError):
            validate_phone("")

    def test_empty_optional(self):
        self.assertEqual(validate_phone("", required=False), "")

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            validate_phone("123")


class TestChoice(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_choice("Admin", {"admin", "user"}, "Role"), "admin")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            validate_choice("superuser", {"admin", "user"}, "Role")

    def test_empty_required_raises(self):
        with self.assertRaises(ValueError):
            validate_choice("", {"admin"}, "Role")


class TestRole(unittest.TestCase):
    def test_valid_roles(self):
        self.assertEqual(validate_role("admin"), "admin")
        self.assertEqual(validate_role("Agente"), "agente")
        self.assertEqual(validate_role("PILOTO"), "piloto")

    def test_invalid_role_raises(self):
        with self.assertRaises(ValueError):
            validate_role("superuser")


class TestFeedbackStatus(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_feedback_status("approved"), "approved")
        self.assertEqual(validate_feedback_status("review"), "review")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            validate_feedback_status("rejected")


class TestOperationalFeedbackStatus(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_operational_feedback_status("approved"), "approved")
        self.assertEqual(validate_operational_feedback_status("avoid"), "avoid")
        self.assertEqual(validate_operational_feedback_status("review"), "review")

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            validate_operational_feedback_status("rejected")


class TestPassword(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_password("secret123"), "secret123")

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            validate_password("abc")

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            validate_password("")


class TestIMO(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(validate_imo("9152923"), "9152923")

    def test_with_prefix(self):
        self.assertEqual(validate_imo("IMO 9152923"), "9152923")

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            validate_imo("12345")

    def test_empty_required_raises(self):
        with self.assertRaises(ValueError):
            validate_imo("")

    def test_empty_optional(self):
        self.assertEqual(validate_imo("", required=False), "")


class TestVesselDimensions(unittest.TestCase):
    def test_valid(self):
        result = validate_vessel_dimensions({
            "vessel_loa_m": "179.23",
            "vessel_beam_m": "25.3",
            "vessel_gt_t": "16281",
            "vessel_max_draft_m": "9.94",
            "vessel_dwt_t": "22330",
        })
        self.assertEqual(result["vessel_loa_m"], "179.23")
        self.assertEqual(result["vessel_beam_m"], "25.3")

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            validate_vessel_dimensions({
                "vessel_loa_m": "",
                "vessel_beam_m": "25.3",
                "vessel_gt_t": "16281",
                "vessel_max_draft_m": "9.94",
                "vessel_dwt_t": "22330",
            })

    def test_exceeds_max_raises(self):
        with self.assertRaises(ValueError):
            validate_vessel_dimensions({
                "vessel_loa_m": "600",
                "vessel_beam_m": "25.3",
                "vessel_gt_t": "16281",
                "vessel_max_draft_m": "9.94",
                "vessel_dwt_t": "22330",
            })


if __name__ == "__main__":
    unittest.main()
