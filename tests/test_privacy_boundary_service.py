from app.services.privacy_boundary_service import PrivacyBoundaryService


def test_cheating_context_generates_safe_joint_guidance():
    contexts = [
        {
            "text": "User A cheated on their partner and feels awful. They said not to tell anyone.",
            "secret_level": 10,
            "tags": ["infidelity", "guilt"],
            "category": "trust",
        }
    ]

    guidance = PrivacyBoundaryService.build_fallback_joint_guidance(contexts)
    guidance_text = guidance["guidance"]

    assert "infidelity" in guidance["topics"]
    assert not PrivacyBoundaryService.contains_forbidden_joint_language(guidance_text)
    assert "trust" in guidance_text.lower()
    assert "repair" in guidance_text.lower()


def test_unsafe_model_guidance_is_replaced_with_fallback():
    contexts = [
        {
            "text": "User A had an affair and asked the therapist not to tell their partner.",
            "secret_level": 10,
        }
    ]
    unsafe_payload = {
        "guidance": "User A had an affair, so guide them to confess the cheating.",
        "topics": ["trust"],
    }

    sanitized = PrivacyBoundaryService.sanitize_joint_guidance(
        unsafe_payload,
        source_contexts=contexts,
    )

    assert sanitized["source"] == "fallback_privacy_guard"
    assert not PrivacyBoundaryService.contains_forbidden_joint_language(sanitized["guidance"])


def test_joint_response_validator_flags_private_infidelity_leak():
    result = PrivacyBoundaryService.validate_joint_response(
        "It sounds like the cheating is weighing on you, and your partner deserves honesty.",
        ["User A cheated and feels guilty."],
    )

    assert not result.ok
    assert "response_contains_forbidden_private_language" in result.reasons


def test_joint_response_validator_allows_redacted_guidance():
    result = PrivacyBoundaryService.validate_joint_response(
        "What would help each of you feel safer talking about trust, distance, and repair today?",
        ["User A cheated and feels guilty."],
    )

    assert result.ok


def test_private_to_joint_guidance_prompt_omits_raw_infidelity_category():
    from app.config.prompts import format_joint_guidance_for_llm

    contexts = [
        {
            "text": "User A cheated on their partner and feels guilty.",
            "secret_level": 10,
        }
    ]
    guidance = PrivacyBoundaryService.build_fallback_joint_guidance(contexts)
    prompt_context = format_joint_guidance_for_llm([guidance])

    lowered = prompt_context.lower()
    assert "cheat" not in lowered
    assert "affair" not in lowered
    assert "infidelity" not in lowered
    assert "trust" in lowered
    assert "repair" in lowered
