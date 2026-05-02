from app.config.prompts import format_context_for_llm, format_joint_guidance_for_llm


def test_format_context_handles_raw_context_data():
    formatted = format_context_for_llm([
        {
            "text": "Values direct but gentle communication",
            "tags": ["communication"],
        }
    ])

    assert "Values direct but gentle communication" in formatted
    assert "communication" in formatted


def test_format_context_handles_wrapped_context_data():
    formatted = format_context_for_llm([
        {
            "data": {
                "text": "Feels overwhelmed by fast conflict escalation",
                "tags": ["conflict"],
            }
        }
    ])

    assert "Feels overwhelmed" in formatted
    assert "conflict" in formatted


def test_format_joint_guidance_handles_raw_guidance_data():
    formatted = format_joint_guidance_for_llm([
        {
            "guidance": "Invite accountability without implying hidden facts.",
            "topics": ["trust"],
            "avoid_terms": ["cheating"],
        }
    ])

    assert "Invite accountability" in formatted
    assert "trust" not in formatted
    assert "cheating" not in formatted
