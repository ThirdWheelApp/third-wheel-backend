"""
Private Agent

AI therapist for individual users.
Has access to private user context and provides personalized therapy.
"""

from app.config.settings import settings
from app.demo import get_llm_client
from app.config.prompts import (
    PRIVATE_AGENT_SYSTEM_PROMPT,
    PRIVATE_AGENT_GROUP_QUERY_PROMPT,
    PRIVATE_AGENT_PARTNER_QUERY_PROMPT,
    format_context_for_llm,
    format_messages_for_llm
)
from app.agents.private_agent.repo import PrivateAgentRepository
from app.utils.logger import get_logger
from typing import List, Dict, AsyncIterator, AsyncGenerator
import time

logger = get_logger(__name__)


class PrivateAgent:
    """
    Private Agent serves an individual user with personalized therapy.

    Three modes of operation:
    1. get_private_message(): Full context access for private sessions
    2. get_group_message(): Filtered context for joint session queries
    3. respond_to_partner_agent(): Answer queries from partner's agent (stricter filtering)

    The agent NEVER reveals private information (high secret_level) in
    group or inter-agent contexts, maintaining user privacy while being helpful.

    Inter-agent communication allows private agents to share context about
    their users to help each other provide better therapy advice.
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
        self.client = get_llm_client()

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
            logger.error(f"Error in Private Agent LLM call: {e}", exc_info=True)
            return "I'm having trouble processing that right now. Could you try rephrasing?"

    async def get_private_message_stream(
        self,
        user_input: str,
        messages_history: List[Dict]
    ) -> AsyncGenerator[str, None]:
        """
        Generate streaming response for private therapy session.

        Yields tokens as they are generated for real-time display.

        Args:
            user_input: User's current message
            messages_history: Previous messages in the session

        Yields:
            Individual tokens as they are generated

        Example:
            async for token in agent.get_private_message_stream(
                "I'm feeling frustrated",
                previous_messages
            ):
                await websocket.send_json({'type': 'stream_token', 'token': token})
        """
        # Load all context for this user
        contexts = self.repo.get_all_context(self.user_id, self.group_id)
        context_text = format_context_for_llm(contexts)
        history_text = format_messages_for_llm(messages_history[-10:])

        # Build prompt
        prompt = f"""Previous context about this user:
{context_text}

Recent conversation history:
{history_text}

User: {user_input}

Provide a warm, empathetic response as their therapist:"""

        start_time = time.time()
        total_tokens = 0

        try:
            # Use streaming API
            with self.client.messages.stream(
                model=settings.LLM_MODEL,
                max_tokens=settings.MAX_TOKENS,
                temperature=settings.LLM_TEMPERATURE,
                system=PRIVATE_AGENT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}]
            ) as stream:
                for text in stream.text_stream:
                    total_tokens += 1
                    yield text

            latency_ms = int((time.time() - start_time) * 1000)

            # Get final message for metrics
            final_message = stream.get_final_message()

            self._log_metrics = {
                'input_tokens': final_message.usage.input_tokens,
                'output_tokens': final_message.usage.output_tokens,
                'latency_ms': latency_ms,
                'model': settings.LLM_MODEL,
                'agent_type': 'private_stream'
            }

            logger.info(f"[PrivateAgent] Streaming complete ({total_tokens} chunks, {latency_ms}ms)")

        except Exception as e:
            logger.error(f"Error in Private Agent streaming: {e}", exc_info=True)
            yield "I'm having trouble processing that right now. Could you try rephrasing?"

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
            logger.error(f"Error in Private Agent group query: {e}", exc_info=True)
            return "Unable to retrieve context at this time."

    def get_last_metrics(self) -> Dict:
        """
        Get metrics from last LLM call for logging.

        Returns:
            Dictionary with tokens, latency, model info
        """
        return getattr(self, '_log_metrics', {})

    async def respond_to_partner_agent(self, query: str) -> str:
        """
        Respond to a query from the partner's private agent.

        Uses stricter privacy filtering (secret_level <= 4) than group queries
        to ensure sensitive information doesn't leak through agent-to-agent
        communication.

        Args:
            query: Question from partner's private agent

        Returns:
            Response with heavily filtered, pattern-focused information

        Example:
            # Partner's agent asks about communication preferences
            context = await agent.respond_to_partner_agent(
                "How does your user prefer to receive feedback?"
            )
        """
        # Load all context
        contexts = self.repo.get_all_context(self.user_id, self.group_id)

        # Use stricter filtering for agent-to-agent (threshold - 1)
        partner_agent_threshold = settings.SECRET_LEVEL_THRESHOLD - 1
        filtered = self.repo.filter_by_secret_level(
            contexts,
            partner_agent_threshold
        )

        context_text = format_context_for_llm(filtered)

        # Build prompt with privacy instructions
        prompt = f"""Available context (privacy-filtered, secret_level <= {partner_agent_threshold}):
{context_text}

Query from partner's private agent: {query}

Provide helpful context focused on patterns and preferences.
Be general and avoid revealing specific events or conversations.
Frame responses as "this person tends to..." not "they told me...".

Response:"""

        start_time = time.time()

        try:
            response = self.client.messages.create(
                model=settings.LLM_MODEL,
                max_tokens=200,  # Keep responses concise for inter-agent
                temperature=settings.LLM_TEMPERATURE,
                system=PRIVATE_AGENT_PARTNER_QUERY_PROMPT,
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
                'agent_type': 'private_partner_query'
            }

            logger.info(f"[PrivateAgent] Responded to partner agent query ({latency_ms}ms)")
            return response_text

        except Exception as e:
            logger.error(f"Error in Private Agent partner query: {e}", exc_info=True)
            return "Unable to provide context at this time."

    async def query_partner_about(
        self,
        partner_agent: 'PrivateAgent',
        topic: str
    ) -> str:
        """
        Query the partner's private agent about a specific topic.

        Used during private sessions when the user's agent needs context
        about their partner to provide better advice.

        Args:
            partner_agent: The partner's PrivateAgent instance
            topic: What to ask about (e.g., "communication preferences")

        Returns:
            Context from partner's agent

        Example:
            # During a private session, get context about partner
            partner_context = await agent.query_partner_about(
                partner_agent,
                "How does the partner handle conflict?"
            )
        """
        logger.info(f"[PrivateAgent] Querying partner agent about: {topic[:50]}...")

        # Build the query for the partner's agent
        query = f"""I'm helping my user understand their relationship better.
Could you share general patterns about how your user approaches: {topic}

Focus on communication styles, preferences, and positive approaches."""

        try:
            response = await partner_agent.respond_to_partner_agent(query)
            logger.info(f"[PrivateAgent] Received partner context: {response[:100]}...")
            return response
        except Exception as e:
            logger.error(f"Error querying partner agent: {e}", exc_info=True)
            return "Unable to get partner context."
