from app.services.privacy_boundary_service import PrivacyBoundaryService


def test_joint_privacy_circuit_breaker_is_safe():
    from app.services.chat_service import ChatService

    response = ChatService._joint_privacy_circuit_breaker_response()
    result = PrivacyBoundaryService.validate_joint_response(
        response,
        ["User has a gambling debt and cheated."],
        joint_conversation_text="Jordan: I feel like something is wrong.",
    )

    assert result.ok


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

    assert "infidelity" not in guidance["topics"]
    assert "trust_repair" in guidance["topics"]
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


def test_arbitrary_private_specifics_are_removed_from_model_guidance():
    contexts = [
        {
            "text": "User has a gambling debt from a hidden bank account and asked for privacy.",
            "secret_level": 10,
        }
    ]
    unsafe_payload = {
        "guidance": "Support disclosure pacing around the gambling debt and hidden bank account.",
        "topics": ["financial_secret"],
    }

    sanitized = PrivacyBoundaryService.sanitize_joint_guidance(
        unsafe_payload,
        source_contexts=contexts,
    )

    assert sanitized["source"] == "fallback_privacy_guard"
    lowered = sanitized["guidance"].lower()
    assert "gambling" not in lowered
    assert "bank" not in lowered
    assert "financial_secret" not in sanitized["topics"]


def test_joint_response_validator_flags_private_infidelity_leak():
    result = PrivacyBoundaryService.validate_joint_response(
        "It sounds like the cheating is weighing on you, and your partner deserves honesty.",
        ["User A cheated and feels guilty."],
    )

    assert not result.ok
    assert "response_contains_forbidden_private_language" in result.reasons


def test_joint_response_validator_allows_opened_infidelity_topic():
    result = PrivacyBoundaryService.validate_joint_response(
        "Since you named the cheating here, let's slow down and focus on accountability.",
        ["User A cheated and feels guilty."],
        joint_conversation_text="Alex: I cheated, and I need help saying that here.",
    )

    assert result.ok


def test_partner_guess_does_not_open_private_infidelity_topic():
    alex_context = {
        "data": {"text": "Alex cheated and feels guilty."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "The cheating is weighing on both of you now.",
        [alex_context],
        joint_conversation_text="Jordan: Did Alex cheat?",
        joint_conversation_by_user={
            "jordan-id": "Did Alex cheat?",
            "alex-id": "",
        },
    )

    assert not result.ok
    assert "response_mentions_private_infidelity" in result.reasons


def test_subject_disclosure_opens_private_infidelity_topic():
    alex_context = {
        "data": {"text": "Alex cheated and feels guilty."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "Since you named the cheating here, let's slow down and focus on accountability.",
        [alex_context],
        joint_conversation_text="Alex: I cheated, and I need help saying that here.",
        joint_conversation_by_user={
            "alex-id": "I cheated, and I need help saying that here.",
            "jordan-id": "",
        },
    )

    assert result.ok


def test_subject_disclosure_allows_infidelity_synonym():
    alex_context = {
        "data": {"text": "Alex had an affair two months ago and feels guilty."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "This infidelity disclosure is painful, so let's slow down and focus on accountability.",
        [alex_context],
        joint_conversation_text="Alex: I cheated, and I need help saying that here.",
        joint_conversation_by_user={
            "alex-id": "I cheated, and I need help saying that here.",
            "jordan-id": "",
        },
    )

    assert result.ok


def test_opened_sensitive_terms_only_for_source_subject():
    alex_context = {
        "data": {"text": "Alex had an affair two months ago and feels guilty."},
        "subject_user_id": "alex-id",
    }

    assert PrivacyBoundaryService.opened_sensitive_terms_for_subject(
        "I cheated, and I need help saying that here.",
        [alex_context],
        "alex-id",
    ) == ["cheated"]

    assert PrivacyBoundaryService.opened_sensitive_terms_for_subject(
        "Is this about cheating?",
        [alex_context],
        "jordan-id",
    ) == []


def test_joint_response_validator_flags_arbitrary_private_specifics():
    result = PrivacyBoundaryService.validate_joint_response(
        "The gambling debt is creating pressure, and the hidden bank account needs care.",
        ["User has a gambling debt from a hidden bank account and asked for privacy."],
        joint_conversation_text="Partner: I feel distance but do not know why.",
    )

    assert not result.ok
    assert "response_mentions_private_source_specifics" in result.reasons


def test_partner_guess_does_not_open_arbitrary_private_specifics():
    alex_context = {
        "data": {"text": "Alex has a gambling debt from a hidden bank account."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "The gambling debt is the pressure point.",
        [alex_context],
        joint_conversation_text="Jordan: Is there gambling debt?",
        joint_conversation_by_user={
            "jordan-id": "Is there gambling debt?",
            "alex-id": "",
        },
    )

    assert not result.ok
    assert "response_mentions_private_source_specifics" in result.reasons


def test_public_partner_names_are_not_private_specifics():
    alex_context = {
        "data": {"text": "Alex has a gambling debt from a hidden bank account."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "Alex, I want to understand what feels hard to name today.",
        [alex_context],
        joint_conversation_text="Jordan: Alex seems distant.",
        joint_conversation_by_user={
            "jordan-id": "Alex seems distant.",
            "alex-id": "",
        },
        public_terms={"alex", "jordan"},
    )

    assert result.ok


def test_public_names_do_not_create_private_phrase_matches():
    alex_context = {
        "data": {"text": "Alex wants to work toward honesty and talk directly."},
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "Jordan, ask Alex directly and give them room to answer in their own words.",
        [alex_context],
        joint_conversation_text="Jordan: Are you keeping something from me?",
        joint_conversation_by_user={
            "jordan-id": "Are you keeping something from me?",
            "alex-id": "",
        },
        public_terms={"alex", "jordan"},
    )

    assert result.ok


def test_joint_response_validator_allows_arbitrary_specifics_after_joint_disclosure():
    result = PrivacyBoundaryService.validate_joint_response(
        "The gambling debt is creating pressure, so let's talk about accountability carefully.",
        ["User has a gambling debt from a hidden bank account and asked for privacy."],
        joint_conversation_text="Alex: I have a gambling debt and need to talk about it.",
    )

    assert result.ok


def test_subject_disclosure_still_blocks_unintroduced_private_details():
    alex_context = {
        "data": {
            "text": "Alex used the emergency fund for gambling debt through a hidden bank account."
        },
        "subject_user_id": "alex-id",
    }

    result = PrivacyBoundaryService.validate_joint_response(
        "The gambling debt is now in the room, but the hidden bank account also needs care.",
        [alex_context],
        joint_conversation_text="Alex: I used our emergency fund for gambling debt.",
        joint_conversation_by_user={
            "alex-id": "I used our emergency fund for gambling debt.",
            "jordan-id": "",
        },
    )

    assert not result.ok
    assert "response_mentions_private_source_specifics" in result.reasons


def test_joint_response_validator_still_blocks_private_source_claims():
    result = PrivacyBoundaryService.validate_joint_response(
        "Based on what you told me privately, the cheating is the real issue.",
        ["User A cheated and feels guilty."],
        joint_conversation_text="Alex: I cheated, and I need help saying that here.",
    )

    assert not result.ok
    assert "response_contains_forbidden_private_language" in result.reasons


def test_joint_response_validator_blocks_outside_room_source_claims():
    result = PrivacyBoundaryService.validate_joint_response(
        "I cannot tell you what Alex has said outside this room.",
        [],
        joint_conversation_text="Jordan: Did Alex tell you what happened elsewhere?",
    )

    assert not result.ok
    assert "response_contains_forbidden_private_language" in result.reasons


def test_joint_response_validator_blocks_said_to_me_source_claims():
    result = PrivacyBoundaryService.validate_joint_response(
        "I can't tell you what Alex hasn't said to me yet.",
        [],
        joint_conversation_text="Jordan: Did Alex tell you?",
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
