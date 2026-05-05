"""
Privacy boundary helpers for private-to-joint therapy memory.

The core invariant is simple: raw private disclosures must not be placed in a
joint-session generation prompt. Joint sessions may receive only redacted
therapist guidance that can shape facilitation without exposing facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence

STOPWORDS = {
    "about", "above", "after", "again", "against", "being", "between",
    "been", "before", "both", "could", "doing", "each", "else", "even",
    "first", "from", "have",
    "having", "here", "into", "just", "more", "most", "need", "needs",
    "does", "done", "full", "move", "moving", "only", "other", "over",
    "past", "right", "said", "same", "some",
    "than", "that", "their", "them", "then", "there", "these", "they",
    "this", "those", "through", "today", "toward", "under", "very",
    "want", "wants", "were", "what", "when", "where", "which", "while",
    "with", "would", "your", "partner", "partners", "relationship", "couple",
    "user", "users", "person", "people", "thing", "things", "time", "weeks",
}

SAFE_PROCESS_TERMS = {
    "accountability", "avoidance", "balance", "boundaries", "boundary",
    "acknowledge", "acknowledged", "alone", "breath", "care", "carrying",
    "clarity", "communication", "conflict", "connection", "conversation",
    "courage", "defensiveness", "details", "direct", "directly", "disclose",
    "disclosed", "disclosure", "distance", "emotional", "emotion",
    "emotions", "experience", "experiencing", "feel", "feeling", "feelings", "feels",
    "felt", "guilt", "guilty", "fear", "fears", "fragile", "hard", "heard", "help",
    "helpful", "helping", "honest", "honesty", "hurt", "hurting",
    "intimacy", "openness", "pace", "pacing", "pattern", "patterns",
    "pain", "painful", "present", "ready", "readiness", "repair", "room",
    "safe", "safety", "scared",
    "secure", "security", "share", "shared", "sharing", "slow", "slowly",
    "stress", "stressed", "support", "tension", "transparency", "trust", "truth", "truthful",
    "uncertain", "uncertainty", "understand", "understanding", "values",
    "vulnerable", "vulnerability", "willing", "worry", "worried", "work",
    "working",
}

SHORT_SOURCE_SPECIFIC_TERMS = {"bank", "sex", "std", "sti", "hiv", "ivf", "gay"}

SAFE_GUIDANCE_TOPICS = {
    "accountability_capacity",
    "boundaries_and_pacing",
    "communication_readiness",
    "conflict_pacing",
    "emotional_safety",
    "repair_readiness",
    "support_needs",
    "trust_repair",
}

GENERIC_AVOID_TERMS = [
    "private-only specifics",
    "source details",
    "private-source references",
    "unintroduced sensitive topics",
]


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

PRIVACY_SOURCE_CLAIM_PATTERNS = [
    r"\bprivate session\b",
    r"\bprivate conversation(?:s)?\b",
    r"\bprivate chat(?:s)?\b",
    r"\bprivate material\b",
    r"\bprivate context\b",
    r"\bprivate disclosure\b",
    r"\btold me privately\b",
    r"\bprivately\b",
    r"\bin private\b",
    r"\bfrom private\b",
    r"\boutside this room\b",
    r"\boutside the room\b",
    r"\boutside this conversation\b",
    r"\boutside our time\b",
    r"\banywhere else\b",
    r"\bother conversation(?:s)?\b",
    r"\bwhat I might know\b",
    r"\bwhat I know\b",
    r"\bwhat .* hasn't shared with me\b",
    r"\bwhat .* has not shared with me\b",
    r"\bhasn't shared with me\b",
    r"\bhas not shared with me\b",
    r"\bhaven't shared with me\b",
    r"\bhave not shared with me\b",
    r"\bwhat .* hasn't said to me\b",
    r"\bwhat .* has not said to me\b",
    r"\bhasn't said to me\b",
    r"\bhas not said to me\b",
    r"\bhaven't said to me\b",
    r"\bhave not said to me\b",
    r"\binstead of asking me\b",
    r"\bconfirm or deny\b",
    r"\bfrom elsewhere\b",
    r"\bI know something\b",
    r"\bI can't say\b",
    r"\bI cannot say\b",
    r"\bI can't share\b",
    r"\bI cannot share\b",
    r"\bundisclosed fact\b",
    r"\bhidden fact\b",
    r"\bsomething you haven't shared\b",
    r"\bsomething you are not saying\b",
    r"\byou told me\b",
]

CONVERSATION_OPENED_TOPIC_PATTERNS = {
    "infidelity": INFIDELITY_PATTERNS,
    "privacy_boundary": [
        r"\bsecret(?:s)?\b",
        r"\bdon't tell\b",
        r"\bdo not tell\b",
        r"\bconfidential\b",
    ],
    "trauma_or_safety": [
        r"\btrauma\b",
        r"\babuse\b",
        r"\bassault\b",
        r"\bself[- ]?harm\b",
    ],
    "financial_or_addiction": [
        r"\bgambling\b",
        r"\bgamble(?:d|s)?\b",
        r"\bdebt(?:s)?\b",
        r"\bemergency fund\b",
        r"\bshared money\b",
        r"\baddiction\b",
    ],
}

JOINT_RESPONSE_FORBIDDEN_PATTERNS = [
    *HIGH_RISK_PRIVATE_PATTERNS,
    *PRIVACY_SOURCE_CLAIM_PATTERNS,
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
    "financial_or_addiction": [
        r"\bgambling\b",
        r"\bgamble(?:d|s)?\b",
        r"\bdebt(?:s)?\b",
        r"\bemergency fund\b",
        r"\bshared money\b",
        r"\baddiction\b",
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
    def _context_subject_id(context: Dict | str) -> str | None:
        if not isinstance(context, dict):
            return None
        data = context.get("data") if isinstance(context.get("data"), dict) else context
        subject_id = (
            context.get("subject_user_id")
            or context.get("user_id")
            or (data or {}).get("subject_user_id")
            or (data or {}).get("user_id")
        )
        return str(subject_id) if subject_id else None

    @staticmethod
    def detect_sensitive_topics(texts: Iterable[str]) -> List[str]:
        joined = "\n".join(texts or [])
        topics: List[str] = []
        for topic, patterns in SENSITIVE_TOPIC_LABELS.items():
            if any(re.search(pattern, joined, flags=re.IGNORECASE) for pattern in patterns):
                topics.append(topic)
        return topics

    @classmethod
    def opened_sensitive_terms_for_subject(
        cls,
        latest_text: str,
        source_contexts: Sequence[Dict] | Sequence[str] | None,
        subject_user_id: str,
    ) -> List[str]:
        """
        Return sensitive words/phrases the subject partner has just made public.

        This is a quality helper, not a privacy permission by itself. It only
        returns terms when the same broad topic exists in private source
        context for the same subject, which prevents a partner's guess from
        being treated as the source partner's disclosure.
        """
        if not latest_text or not source_contexts or not subject_user_id:
            return []

        subject_source_texts: List[str] = []
        for source_context in source_contexts:
            if isinstance(source_context, str):
                subject_source_texts.append(source_context)
                continue
            if cls._context_subject_id(source_context) == str(subject_user_id):
                data = source_context.get("data") if isinstance(source_context.get("data"), dict) else source_context
                source_text = str((data or {}).get("text") or (data or {}).get("guidance") or "")
                if source_text:
                    subject_source_texts.append(source_text)

        if not subject_source_texts:
            return []

        terms: List[str] = []
        seen = set()
        source_text = "\n".join(subject_source_texts)
        for patterns in CONVERSATION_OPENED_TOPIC_PATTERNS.values():
            if not cls._matches_any_pattern(source_text, patterns):
                continue
            for pattern in patterns:
                for match in re.finditer(pattern, latest_text or "", flags=re.IGNORECASE):
                    term = " ".join(match.group(0).lower().split())
                    if term and term not in seen:
                        terms.append(term)
                        seen.add(term)

        return terms

    @staticmethod
    def _words(text: str) -> List[str]:
        return re.findall(r"[a-zA-Z]+", text.lower())

    @staticmethod
    def _is_source_specific_word(word: str) -> bool:
        if word in SHORT_SOURCE_SPECIFIC_TERMS:
            return True
        if len(word) < 6:
            return False
        if word in STOPWORDS or word in SAFE_PROCESS_TERMS:
            return False
        return True

    @staticmethod
    def _matches_any_pattern(text: str, patterns: Sequence[str]) -> bool:
        return any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in patterns)

    @classmethod
    def _opened_topic_patterns_for_source(cls, source_text: str, opened_text: str) -> List[str]:
        """
        Return topic patterns that are present in private source material and
        have been opened by the same source subject in the joint conversation.

        This lets the runtime acknowledge the category after disclosure
        (for example "infidelity" after "I cheated") without allowing
        unintroduced concrete details from the private source.
        """
        opened_patterns: List[str] = []
        for patterns in CONVERSATION_OPENED_TOPIC_PATTERNS.values():
            source_has_topic = cls._matches_any_pattern(source_text, patterns)
            opened_by_subject = cls._matches_any_pattern(opened_text, patterns)
            if source_has_topic and opened_by_subject:
                opened_patterns.extend(patterns)
        return opened_patterns

    @classmethod
    def source_specific_matches(
        cls,
        text: str,
        source_contexts: Sequence[Dict] | Sequence[str] | None,
        joint_conversation_text: str | None = None,
        joint_conversation_by_user: Mapping[str, str] | None = None,
        public_terms: Iterable[str] | None = None,
    ) -> List[str]:
        """
        Find concrete words/phrases from private-only source context repeated in
        joint-facing text before the partners have introduced them.

        This is deliberately heuristic. It is a generic backstop for arbitrary
        private content; the primary safety mechanism is still keeping raw
        private memory out of joint prompts.
        """
        if not text or not source_contexts:
            return []

        response_text = text.lower()
        public_term_set = {term.lower() for term in (public_terms or [])}
        source_terms = set()
        source_phrases = set()
        matches: List[str] = []

        for source_context in source_contexts:
            if isinstance(source_context, str):
                source_text = source_context
                opened_text = joint_conversation_text or ""
            else:
                data = source_context.get("data") if isinstance(source_context.get("data"), dict) else source_context
                source_text = str((data or {}).get("text") or (data or {}).get("guidance") or "")
                subject_id = cls._context_subject_id(source_context)
                opened_text = (
                    (joint_conversation_by_user or {}).get(subject_id or "")
                    if subject_id and joint_conversation_by_user is not None
                    else joint_conversation_text
                ) or ""

            opened_text = opened_text.lower()
            opened_topic_patterns = cls._opened_topic_patterns_for_source(source_text, opened_text)
            source_terms.clear()
            source_phrases.clear()
            words = cls._words(source_text)
            specific_flags = [cls._is_source_specific_word(word) for word in words]
            for word, is_specific in zip(words, specific_flags):
                if (
                    is_specific
                    and word not in public_term_set
                    and not cls._matches_any_pattern(word, opened_topic_patterns)
                ):
                    source_terms.add(word)

            for size in (2, 3):
                for idx in range(0, max(0, len(words) - size + 1)):
                    phrase_words = words[idx:idx + size]
                    phrase_flags = specific_flags[idx:idx + size]
                    private_specific_words = [
                        word
                        for word, is_specific in zip(phrase_words, phrase_flags)
                        if (
                            is_specific
                            and word not in public_term_set
                            and not cls._matches_any_pattern(word, opened_topic_patterns)
                        )
                    ]
                    if not private_specific_words:
                        continue
                    if all(word in STOPWORDS for word in phrase_words):
                        continue
                    phrase = " ".join(phrase_words)
                    if cls._matches_any_pattern(phrase, opened_topic_patterns):
                        continue
                    source_phrases.add(phrase)

            for term in sorted(source_terms):
                pattern = rf"\b{re.escape(term)}\b"
                if re.search(pattern, response_text) and not re.search(pattern, opened_text):
                    matches.append(term)

            for phrase in sorted(source_phrases):
                pattern = rf"\b{re.escape(phrase)}\b"
                if re.search(pattern, response_text) and not re.search(pattern, opened_text):
                    matches.append(phrase)

        return matches[:10]

    @classmethod
    def _topic_opened_by_source_subject(
        cls,
        topic_patterns: Sequence[str],
        source_contexts: Sequence[Dict] | Sequence[str] | None,
        joint_conversation_text: str | None,
        joint_conversation_by_user: Mapping[str, str] | None,
    ) -> bool:
        if not source_contexts or joint_conversation_by_user is None:
            return any(
                re.search(pattern, joint_conversation_text or "", flags=re.IGNORECASE)
                for pattern in topic_patterns
            )

        for source_context in source_contexts:
            if isinstance(source_context, str):
                opened_text = joint_conversation_text or ""
            else:
                subject_id = cls._context_subject_id(source_context)
                opened_text = (joint_conversation_by_user or {}).get(subject_id or "", "")

            if any(re.search(pattern, opened_text, flags=re.IGNORECASE) for pattern in topic_patterns):
                return True
        return False

    @staticmethod
    def contains_forbidden_joint_language(
        text: str,
        joint_conversation_text: str | None = None
    ) -> List[str]:
        matches = []
        for pattern in PRIVACY_SOURCE_CLAIM_PATTERNS:
            if re.search(pattern, text or "", flags=re.IGNORECASE):
                matches.append(pattern)

        for _, patterns in CONVERSATION_OPENED_TOPIC_PATTERNS.items():
            topic_was_opened = any(
                re.search(pattern, joint_conversation_text or "", flags=re.IGNORECASE)
                for pattern in patterns
            )
            if topic_was_opened:
                continue

            for pattern in patterns:
                if re.search(pattern, text or "", flags=re.IGNORECASE):
                    matches.append(pattern)

        return matches

    @classmethod
    def validate_joint_response(
        cls,
        response_text: str,
        private_contexts: Sequence[Dict] | Sequence[str] | None = None,
        joint_conversation_text: str | None = None,
        joint_conversation_by_user: Mapping[str, str] | None = None,
        public_terms: Iterable[str] | None = None,
    ) -> PrivacyValidationResult:
        """
        Validate that a joint-session response does not reveal private material.

        This intentionally uses conservative lexical checks. It is a runtime
        backstop, not the only privacy mechanism. Sensitive topic words are
        allowed only after the partners have already introduced that topic in
        the live joint conversation; source claims remain forbidden.
        """
        reasons = []
        forbidden_matches = cls.contains_forbidden_joint_language(
            response_text or "",
            joint_conversation_text=joint_conversation_text,
        )
        if forbidden_matches:
            reasons.append("response_contains_forbidden_private_language")

        context_texts: List[str]
        if private_contexts and all(isinstance(item, str) for item in private_contexts):
            context_texts = [str(item) for item in private_contexts]
        else:
            context_texts = cls.context_texts(private_contexts or [])  # type: ignore[arg-type]

        if "infidelity" in cls.detect_sensitive_topics(context_texts):
            infidelity_opened = cls._topic_opened_by_source_subject(
                INFIDELITY_PATTERNS,
                private_contexts,
                joint_conversation_text,
                joint_conversation_by_user,
            )
            if not infidelity_opened and any(re.search(pattern, response_text or "", flags=re.IGNORECASE) for pattern in INFIDELITY_PATTERNS):
                reasons.append("response_mentions_private_infidelity")

        if cls.source_specific_matches(
            response_text or "",
            private_contexts,
            joint_conversation_text=joint_conversation_text,
            joint_conversation_by_user=joint_conversation_by_user,
            public_terms=public_terms,
        ):
            reasons.append("response_mentions_private_source_specifics")

        return PrivacyValidationResult(ok=not reasons, reasons=reasons)

    @classmethod
    def _broad_guidance_topics(cls, source_texts: Iterable[str]) -> List[str]:
        joined = "\n".join(source_texts or "").lower()
        topics = {"emotional_safety", "communication_readiness", "boundaries_and_pacing"}
        if any(term in joined for term in ["trust", "honest", "repair", "guilt", "accountability", "cheat", "affair", "infidel"]):
            topics.add("trust_repair")
            topics.add("accountability_capacity")
        if any(term in joined for term in ["conflict", "argue", "fight", "explode", "anger"]):
            topics.add("conflict_pacing")
        if any(term in joined for term in ["support", "alone", "overwhelm", "scared", "fear"]):
            topics.add("support_needs")
        return sorted(topics)

    @classmethod
    def build_fallback_joint_guidance(cls, contexts: Sequence[Dict]) -> Dict:
        """
        Build safe therapist guidance without relying on an LLM.

        This is used when model-generated guidance is missing or fails privacy
        validation. It must remain free of raw disclosure details.
        """
        texts = cls.context_texts(contexts)
        topics = cls.detect_sensitive_topics(texts)
        broad_topics = cls._broad_guidance_topics(texts)

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
            "topics": broad_topics,
            "avoid_terms": GENERIC_AVOID_TERMS,
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

        if cls.source_specific_matches(guidance_text, source_contexts):
            return cls.build_fallback_joint_guidance(source_contexts)

        source_texts = cls.context_texts(source_contexts)
        source_topics = cls.detect_sensitive_topics(source_texts)
        payload = dict(guidance_payload)
        payload["topics"] = [
            topic for topic in cls._broad_guidance_topics(source_texts)
            if topic in SAFE_GUIDANCE_TOPICS
        ]
        payload["avoid_terms"] = GENERIC_AVOID_TERMS
        payload.setdefault("sensitivity_level", 10 if source_topics else 7)
        payload.setdefault("source", "model_redacted")
        return payload
