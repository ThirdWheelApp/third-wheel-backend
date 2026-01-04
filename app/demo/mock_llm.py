"""
Mock LLM Client for Demo Mode

Provides a mock Anthropic client that returns deterministic responses
without making actual API calls. Useful for testing, demos, and development
without incurring API costs.
"""

from typing import List, Dict, Any, Generator, Optional
from dataclasses import dataclass
from app.config.settings import settings
from app.utils.logger import get_logger
import random
import time

logger = get_logger(__name__)


# ============================================================================
# MOCK RESPONSES
# ============================================================================

PRIVATE_THERAPY_RESPONSES = [
    "I hear that you're feeling frustrated. It's completely valid to have these emotions. Can you tell me more about what specifically is triggering these feelings?",
    "Thank you for sharing that with me. It takes courage to open up about these things. What do you think would help you feel more understood in this situation?",
    "That sounds like a challenging situation. Let's explore what's underneath these feelings. When did you first notice this pattern?",
    "I appreciate you trusting me with this. It seems like there's a lot going on. What would make the biggest positive difference for you right now?",
    "It's normal to feel this way. Many people in similar situations experience the same thing. What support do you feel you need?",
]

JOINT_THERAPY_RESPONSES = [
    "I appreciate both of you being here and working on this together. {sender}, what I'm hearing is that you're feeling [emotion]. {partner}, how does it feel to hear that?",
    "Thank you for sharing that perspective. Communication is a two-way street, and it sounds like there may be room for both of you to understand each other better. What do you each need to feel heard?",
    "It's clear that you both care about this relationship. Sometimes our expressions of care can be misinterpreted. Can you each share one thing you appreciate about the other?",
    "I notice there might be some tension here. That's okay - it means you're both invested. Let's slow down and make sure we're really listening to each other. {sender}, can you rephrase what you just said using 'I feel' statements?",
    "You're both making important points. Often in relationships, we focus on being right rather than being connected. What's more important to you right now - winning this point or feeling close to your partner?",
]

CONTEXT_RESPONSES = [
    "This person tends to value clear, direct communication and appreciates when their partner takes initiative in discussions.",
    "They respond well to validation and acknowledgment of their feelings before problem-solving approaches.",
    "Communication patterns suggest they prefer to process emotions before engaging in conflict resolution.",
    "This individual values quality time and verbal affirmation as primary expressions of care.",
]

SESSION_END_RESPONSES = [
    "NO|The conversation still has unresolved topics that need attention.",
    "NO|Both partners are actively engaged and making progress.",
    "YES|Natural winding down with action items established.",
    "YES|Both partners expressed satisfaction with the discussion.",
]


@dataclass
class MockUsage:
    """Mock usage statistics."""
    input_tokens: int = 150
    output_tokens: int = 75


@dataclass
class MockContentBlock:
    """Mock content block from API response."""
    type: str = "text"
    text: str = ""


@dataclass
class MockMessage:
    """Mock message from API response."""
    id: str = "mock_msg_001"
    type: str = "message"
    role: str = "assistant"
    content: List[MockContentBlock] = None
    model: str = "mock-model"
    stop_reason: str = "end_turn"
    usage: MockUsage = None

    def __post_init__(self):
        if self.content is None:
            self.content = [MockContentBlock()]
        if self.usage is None:
            self.usage = MockUsage()


class MockStreamManager:
    """Mock stream manager for streaming responses."""

    def __init__(self, text: str):
        self.text = text
        self._final_message = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @property
    def text_stream(self) -> Generator[str, None, None]:
        """Yield text in chunks to simulate streaming."""
        words = self.text.split(' ')
        for i, word in enumerate(words):
            # Add space back except for last word
            yield word + (' ' if i < len(words) - 1 else '')
            time.sleep(0.02)  # Small delay to simulate streaming

    def get_final_message(self) -> MockMessage:
        """Get the final message after streaming."""
        if self._final_message is None:
            self._final_message = MockMessage(
                content=[MockContentBlock(text=self.text)],
                usage=MockUsage(
                    input_tokens=len(self.text.split()) * 2,
                    output_tokens=len(self.text.split())
                )
            )
        return self._final_message


class MockMessages:
    """Mock messages API."""

    def create(
        self,
        model: str,
        max_tokens: int,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        system: str = None
    ) -> MockMessage:
        """Create a mock message response."""
        logger.info(f"[MockLLM] Creating mock response (model: {model})")

        # Determine response type based on system prompt content
        response_text = self._select_response(system, messages)

        return MockMessage(
            content=[MockContentBlock(text=response_text)],
            model=model,
            usage=MockUsage(
                input_tokens=len(str(messages)) // 4,
                output_tokens=len(response_text.split())
            )
        )

    def stream(
        self,
        model: str,
        max_tokens: int,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        system: str = None
    ) -> MockStreamManager:
        """Create a mock streaming response."""
        logger.info(f"[MockLLM] Creating mock streaming response (model: {model})")

        response_text = self._select_response(system, messages)
        return MockStreamManager(response_text)

    def _select_response(self, system: str, messages: List[Dict]) -> str:
        """Select appropriate response based on context."""
        system_lower = (system or "").lower()
        user_message = ""

        # Extract user message
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # Determine response type
        if "session is naturally concluding" in system_lower or "session end" in user_message.lower():
            return random.choice(SESSION_END_RESPONSES)

        if "context" in system_lower or "query" in system_lower or "partner" in system_lower:
            return random.choice(CONTEXT_RESPONSES)

        if "couples" in system_lower or "joint" in system_lower or "relationship" in system_lower:
            response = random.choice(JOINT_THERAPY_RESPONSES)
            # Replace placeholders
            response = response.replace("{sender}", "your partner")
            response = response.replace("{partner}", "you")
            return response

        # Default to private therapy
        return random.choice(PRIVATE_THERAPY_RESPONSES)


class MockAnthropicClient:
    """
    Mock Anthropic client for demo mode.

    Provides the same interface as the real Anthropic client
    but returns deterministic mock responses.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.messages = MockMessages()
        logger.info("[MockLLM] Initialized mock Anthropic client (demo mode)")


def get_llm_client():
    """
    Get the appropriate LLM client based on settings.

    Returns:
        MockAnthropicClient if DEMO_MODE is True
        Anthropic client otherwise
    """
    if settings.DEMO_MODE:
        logger.info("[MockLLM] Using mock LLM client (DEMO_MODE=true)")
        return MockAnthropicClient()
    else:
        from anthropic import Anthropic
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when not in demo mode. "
                "Set DEMO_MODE=true to use mock responses."
            )
        return Anthropic(api_key=settings.ANTHROPIC_API_KEY)
