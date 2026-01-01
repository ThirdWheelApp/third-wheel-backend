"""
Private Agent

AI therapist for individual users.
Has access to private user context and provides personalized therapy.
"""

from anthropic import Anthropic
from app.config.settings import settings
from app.config.prompts import (
    PRIVATE_AGENT_SYSTEM_PROMPT,
    PRIVATE_AGENT_GROUP_QUERY_PROMPT,
    format_context_for_llm,
    format_messages_for_llm
)
from app.agents.private_agent.repo import PrivateAgentRepository
from typing import List, Dict, AsyncIterator
import time


class PrivateAgent:
    """
    Private Agent serves an individual user with personalized therapy.

    Two modes of operation:
    1. get_private_message(): Full context access for private sessions
    2. get_group_message(): Filtered context for joint session queries

    The agent NEVER reveals private information (high secret_level) in
    group contexts, maintaining user privacy while being helpful.
    """

    def __init__(
        self,
        user_id: str,
        group_id: str,
        repo: PrivateAgentRepository
    ):
        """
        Initialize Private Agent for a specific user.

        Args:
            user_id: UUID of the user this agent serves
            group_id: UUID of the relationship/group
            repo: Repository for data access
        """
        self.user_id = user_id
        self.group_id = group_id
        self.repo = repo
        self.client = Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def get_private_message(
        self,
        user_input: str,
        messages_history: List[Dict]
    ) -> str:
        """
        Generate response for private therapy session.

        Uses ALL context (no filtering by secret level) since this is
        a private conversation between user and therapist.

        Args:
            user_input: User's current message
            messages_history: Previous messages in the session

        Returns:
            Therapist's response as text

        Example:
            response = await agent.get_private_message(
                "I'm feeling frustrated about our communication",
                previous_messages
            )
        """
        # Load all context for this user
        contexts = self.repo.get_all_context(self.user_id, self.group_id)
        context_text = format_context_for_llm(contexts)
        history_text = format_messages_for_llm(messages_history[-10:])  # Last 10 messages

        # Build prompt
        prompt = f"""Previous context about this user:
{context_text}

Recent conversation history:
{history_text}

User: {user_input}

Provide a warm, empathetic response as their therapist:"""

        # Record start time for latency tracking
        start_time = time.time()

        try:
            # Call Anthropic API
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=settings.MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                system=PRIVATE_AGENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Extract response text
            response_text = response.content[0].text

            # Log metrics (will be used by chat service)
            self._log_metrics = {
                'input_tokens': response.usage.input_tokens,
                'output_tokens': response.usage.output_tokens,
                'latency_ms': latency_ms,
                'model': settings.LLM_MODEL,
                'agent_type': 'private'
            }

            return response_text

        except Exception as e:
            print(f"Error in Private Agent LLM call: {e}")
            return "I'm having trouble processing that right now. Could you try rephrasing?"

    async def get_group_message(self, query: str) -> str:
        """
        Respond to query from Joint Agent about this user.

        Only shares contexts with secret_level <= threshold.
        Provides helpful information without revealing private details.

        Args:
            query: Question from joint therapy agent

        Returns:
            Response with privacy-filtered information

        Example:
            # Joint Agent asks about communication patterns
            context = await agent.get_group_message(
                "What are User A's communication preferences?"
            )
        """
        # Load all context
        contexts = self.repo.get_all_context(self.user_id, self.group_id)

        # Filter to only shareable contexts
        filtered = self.repo.filter_by_secret_level(
            contexts,
            settings.SECRET_LEVEL_THRESHOLD
        )

        context_text = format_context_for_llm(filtered)

        # Build prompt with privacy instructions
        prompt = f"""Available context (privacy-filtered, secret_level <= {settings.SECRET_LEVEL_THRESHOLD}):
{context_text}

Query from joint therapy session: {query}

Provide helpful context about this user's patterns, preferences, and needs.
Be general and avoid revealing specific private events.
Focus on patterns, not secrets.

Response:"""

        start_time = time.time()

        try:
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=settings.MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                system=PRIVATE_AGENT_GROUP_QUERY_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            )

            latency_ms = int((time.time() - start_time) * 1000)

            response_text = response.content[0].text

            # Log metrics
            self._log_metrics = {
                'input_tokens': response.usage.input_tokens,
                'output_tokens': response.usage.output_tokens,
                'latency_ms': latency_ms,
                'model': settings.LLM_MODEL,
                'agent_type': 'private_group_query'
            }

            return response_text

        except Exception as e:
            print(f"Error in Private Agent group query: {e}")
            return "Unable to retrieve context at this time."

    def get_last_metrics(self) -> Dict:
        """
        Get metrics from last LLM call for logging.

        Returns:
            Dictionary with tokens, latency, model info
        """
        return getattr(self, '_log_metrics', {})
