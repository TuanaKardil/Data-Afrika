"""Unit tests for phone, email, and domain validators."""


from afrienrich.models import PhoneResult
from afrienrich.validators.domain_validator import is_parked_or_generic
from afrienrich.validators.duplicate_detector import DuplicateDetector
from afrienrich.validators.phone_validator import validate_phones


class TestPhoneValidator:
    def test_valid_ci_mobile(self) -> None:
        phone = PhoneResult(number="+2250701234567", source="test", confidence=70)
        validated, whatsapp = validate_phones([phone], "CI")
        assert len(validated) == 1
        assert "+225" in validated[0].number

    def test_country_mismatch_reduces_confidence(self) -> None:
        # +44 is UK, but we expect CI
        phone = PhoneResult(number="+441234567890", source="test", confidence=80)
        validated, _ = validate_phones([phone], "CI")
        if validated:
            assert validated[0].confidence < 80

    def test_invalid_number_excluded(self) -> None:
        phone = PhoneResult(number="123", source="test", confidence=70)
        validated, _ = validate_phones([phone], "CI")
        assert len(validated) == 0

    def test_mobile_infers_whatsapp(self) -> None:
        phone = PhoneResult(
            number="+2250701234567", source="test", confidence=70, phone_type="MOBILE"
        )
        _, whatsapp = validate_phones([phone], "CI")
        assert len(whatsapp) == 1

    def test_empty_input(self) -> None:
        validated, whatsapp = validate_phones([], "CI")
        assert validated == []
        assert whatsapp == []


class TestDomainValidator:
    def test_parked_domain_detected(self) -> None:
        assert is_parked_or_generic("https://sedo.com/search/?query=example")

    def test_godaddy_parked(self) -> None:
        assert is_parked_or_generic("https://example.com.godaddy.com")

    def test_real_domain_not_flagged(self) -> None:
        assert not is_parked_or_generic("https://www.totalenergies.com")

    def test_empty_string_returns_true(self) -> None:
        assert is_parked_or_generic("")

    def test_facebook_is_generic(self) -> None:
        assert is_parked_or_generic("https://www.facebook.com")


class TestDuplicateDetector:
    def test_flags_phone_seen_too_many_times(self) -> None:
        det = DuplicateDetector(threshold=3)
        for i in range(4):
            det.record("+2250700000000", i)
        flagged = det.get_flagged()
        assert "+2250700000000" in flagged

    def test_does_not_flag_below_threshold(self) -> None:
        det = DuplicateDetector(threshold=5)
        for i in range(4):
            det.record("+2250700000001", i)
        flagged = det.get_flagged()
        assert "+2250700000001" not in flagged
