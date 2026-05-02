"""
Privacy boundary helpers for private-to-joint therapy memory.

The core invariant is simple: raw private disclosures must not be placed in a
joint-session generation prompt. Joint sessions may receive only redacted
therapist guidance that can shape facilitation without exposing facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence


INFIDELITY_PATTERNS = [
    r"\bcheat(?:ed|ing|s)?\b",
    r"\baffair(?:s)?\b",
    r"\binfidelit(?:y|ies)\b",
    r"\badulter(?:y|ous)\b",
    r"\bslept with\b",
    r"\bhooked up\b",
    r"\bkissed\b",
    r"\bsext(?:ed|ing)?\b",
]

HIGH_RISK_PRIVATE_PATTERNS = [
    *INFIDELITY_PATTERNS,
    r"\bsecret(?:s)?\b",
    r"\bdon't tell\b",
    r"\bdo not tell\b",
    r"\bprivate session\b",
    r"\bprivate disclosure\b",
    r"\btold me privately\b",
    r"\bcan't share\b",
    r"\bcannot share\b",
    r"\btrauma\b",
    r"\babuse\b",
    r"\bassault\b",
    r"\bself[- ]?harm\b",
]

JOINT_RESPONSE_FORBIDDEN_PATTERNS = [
    *HIGH_RISK_PRIVATE_PATTERNS,
    r"\byou told me\b",
    r"\bin private\b",
    r"\bI know something\b",
    r"\bI can't say\b",
    r"\bI cannot say\b",
    r"\bundisclosed fact\b",
    r"\bhidden fact\b",
    r"\bsomething you haven't shared\b",
    r"\bsomething you are not saying\b",
]

SENSITIVE_TOPIC_LABELS = {
    "infidelity": INFIDELITY_PATTERNS,
    "privacy_boundary": [
        r"\bsecret(?:s)?\b",
        r"\bdon't tell\b",
        r"\bdo not tell\b",
        r"\bprivate\b",
        r"\bconfidential\b",
    ],
    "trauma_or_safety": [
        r"\btrauma\b",
        r"\babuse\b",
        r"\bassault\b",
        r"\bself[- ]?harm\b",
    ],
}


@dataclass(frozen=True)
class PrivacyValidationResult:
    """Result of checking whether text crosses a privacy boundary."""

    ok: bool
    reasons: List[str]


class PrivacyBoundaryService:
    """Pure privacy helpers used by runtime code, tests, and eval scripts."""

    @staticmethod
    def context_texts(contexts: Sequence[Dict]) -> List[str]:
        texts: List[str] = []
        for ctx in contexts or []:
            data = ctx.get("data") if isinstance(ctx.get("data"), dict) else ctx
            text = (data or {}).get("text") or (data or {}).get("guidance") or ""
            if text:
                texts.append(str(text))
        return texts

    @staticmethod
    def detect_sensitive_topics(texts: Iterable[str]) -> List[str]:
        joined = "\n".join(texts or [])
        topics: List[str] = []
        for topic, patterns in SENSITIVE_TOPIC_LABELS.items():
            if any(re.search(pattern, joined, flags=re.IGNORECASE) for pattern in patterns):
                topics.append(topic)
        return topics

    @staticmethod
    def contains_forbidden_joint_language(text: str) -> List[str]:
        matches = []
        for pattern in JOINT_RESPONSE_FORBIDDEN_PATTERNS:
            if re.search(pattern, text or "", flags=re.IGNORECASE):
                matches.append(pattern)
        return matches

    @classmethod
    def validate_joint_response(
        cls,
        response_text: str,
        private_contexts: Sequence[Dict] | Sequence[str] | None = None
    ) -> PrivacyValidationResult:
        """
        Validate that a joint-session response does not reveal private material.

        This intentionally uses conservative lexical checks. It is a runtime
        backstop, not the only privacy mechanism.
        """
        reasons = []
        forbidden_matches = cls.contains_forbidden_joint_language(response_text or "")
        if forbidden_matches:
            reasons.append("response_contains_forbidden_private_language")

        context_texts: List[str]
        if private_contexts and all(isinstance(item, str) for item in private_contexts):
            context_texts = [str(item) for item in private_contexts]
        else:
            context_texts = cls.context_texts(private_contexts or [])  # type: ignore[arg-type]

        if "infidelity" in cls.detect_sensitive_topics(context_texts):
            if any(re.search(pattern, response_text or "", flags=re.IGNORECASE) for pattern in INFIDELITY_PATTERNS):
                reasons.append("response_mentions_private_infidelity")

        return PrivacyValidationResult(ok=not reasons, reasons=reasons)

    @classmethod
    def build_fallback_joint_guidance(cls, contexts: Sequence[Dict]) -> Dict:
        """
        Build safe therapist guidance without relying on an LLM.

        This is used when model-generated guidance is missing or fails privacy
        validation. It must remain free of raw disclosure details.
        """
        texts = cls.context_texts(contexts)
        topics = cls.detect_sensitive_topics(texts)

        guidance_parts = [
            "One partner may be carrying unresolved guilt, fear, or avoidance around honesty and repair.",
            "In joint sessions, do not introduce or imply facts learned outside the joint conversation.",
            "Use this guidance only if trust, emotional distance, honesty, accountability, repair, or emotional safety comes up organically in the joint conversation; otherwise ignore it.",
            "When it applies, invite both partners to speak in first person about what they are ready to own, what safety would require, and what repair could look like.",
            "Keep questions balanced and do not pressure either partner to reveal more than they choose to share."
        ]

        if "infidelity" not in topics:
            guidance_parts[0] = (
                "One partner may be carrying private emotional material that affects openness, defensiveness, or readiness for repair."
            )

        return {
            "guidance": " ".join(guidance_parts),
            "topics": topics,
            "avoid_terms": [
                "cheating",
                "affair",
                "infidelity",
                "secret",
                "private session",
                "told me privately",
            ],
            "sensitivity_level": 10 if topics else 7,
            "source": "fallback_privacy_guard",
        }

    @classmethod
    def sanitize_joint_guidance(
        cls,
        guidance_payload: Dict,
        source_contexts: Sequence[Dict]
    ) -> Dict:
        """
        Accept safe redacted guidance or replace unsafe guidance with fallback.
        """
        guidance_text = str((guidance_payload or {}).get("guidance") or "")
        if not guidance_text:
            return cls.build_fallback_joint_guidance(source_contexts)

        if cls.contains_forbidden_joint_language(guidance_text):
            return cls.build_fallback_joint_guidance(source_contexts)

        source_topics = cls.detect_sensitive_topics(cls.context_texts(source_contexts))
        payload = dict(guidance_payload)
        payload["topics"] = sorted(set(payload.get("topics") or []) | set(source_topics))
        payload.setdefault("avoid_terms", [
            "cheating",
            "affair",
            "infidelity",
            "secret",
            "private session",
            "told me privately",
        ])
        payload.setdefault("sensitivity_level", 10 if source_topics else 7)
        payload.setdefault("source", "model_redacted")
        return payload
