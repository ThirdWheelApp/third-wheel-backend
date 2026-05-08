"""
Joint Agent

AI therapist for couples therapy sessions.
Orchestrates conversation using LangGraph state machine.
"""

from app.config.settings import settings
from app.demo import get_llm_client
from app.agents.joint_agent.repo import JointAgentRepository
from app.agents.joint_agent.graph import create_joint_agent_graph
from app.agents.private_agent.agent import PrivateAgent
from app.config.prompts import format_context_for_llm, format_joint_guidance_for_llm
from app.utils.logger import get_logger
from typing import Dict, Optional, Tuple
import asyncio

logger = get_logger(__name__)


class JointAgent:
    """
    Joint Agent orchestrates couples therapy sessions using LangGraph.

    Responsibilities:
    - Facilitate balanced conversation between partners
    - Query Private Agents when deeper context is needed (using LLM decision)
    - Maintain neutrality and therapeutic boundaries
    - Detect when session should end
    - Enforce turn limits and query limits
    """

    def __init__(
        self,
        group_id: str,
        repo: JointAgentRepository,
        private_agent_a: Optional[PrivateAgent] = None,
        private_agent_b: Optional[PrivateAgent] = None
    ):
        """
        Initialize Joint Agent for a couples session.

        Args:
            group_id: UUID of the relationship
            repo: Repository for data access
            private_agent_a: Private agent for partner A (optional)
            private_agent_b: Private agent for partner B (optional)
        """
        self.group_id = group_id
        self.repo = repo
        self.private_agent_a = private_agent_a
        self.private_agent_b = private_agent_b
        self.client = get_llm_client()

        # Load group context once
        self.relationship_profile_text = self.repo.get_relationship_profile_text(group_id)
        group_contexts = self.repo.get_group_context(group_id)
        self.group_context_text = format_context_for_llm(group_contexts)
        joint_guidance_contexts = self.repo.get_joint_guidance_context(group_id)
        self.joint_guidance_text = format_joint_guidance_for_llm(joint_guidance_contexts)

        # Persistent state across messages in same session
        self.accumulated_state = {
            'private_a_context': [],
            'private_b_context': [],
            'query_count_a': 0,
            'query_count_b': 0,
            'turn_count': 0
        }

        # Metrics from last run
        self._last_metrics = {}

        logger.info(f"[JointAgent] Initialized for group {group_id}")

    async def process_message(
        self,
        user_input: str,
        sender_name: str,
        messages_history: list[Dict],
        accumulated_context: Dict = None
    ) -> Tuple[str, bool, Dict]:
        """
        Process a message in the joint session using LangGraph.

        This is the main entry point. It:
        1. Creates LangGraph state machine
        2. Runs the workflow (decide → query → generate → check_end)
        3. Returns response and updated context

        Args:
            user_input: Current message from user
            sender_name: Name of the sender
            messages_history: Previous messages in session
            accumulated_context: Previously gathered context (deprecated, using internal state)

        Returns:
            Tuple of (response_text, suggest_end_session, updated_context)

        Example:
            response, should_end, context = await agent.process_message(
                "I feel like we don't communicate well",
                "User A",
                previous_messages
            )
        """
        logger.info(
            f"[JointAgent] Processing message from {sender_name}: {user_input[:100]}...",
            extra={'group_id': self.group_id}
        )

        # Create the LangGraph workflow
        graph = create_joint_agent_graph(
            client=self.client,
            group_id=self.group_id,
            private_agent_a=self.private_agent_a,
            private_agent_b=self.private_agent_b,
            relationship_profile=self.relationship_profile_text,
            group_context=self.group_context_text,
            joint_guidance_context=self.joint_guidance_text
        )

        # Prepare initial state
        initial_state = {
            'user_input': user_input,
            'sender_name': sender_name,
            'messages_history': messages_history,
            'relationship_profile': self.relationship_profile_text,
            'group_context': self.group_context_text,
            'joint_guidance_context': self.joint_guidance_text,
            'private_a_context': self.accumulated_state['private_a_context'],
            'private_b_context': self.accumulated_state['private_b_context'],
            'query_count_a': self.accumulated_state['query_count_a'],
            'query_count_b': self.accumulated_state['query_count_b'],
            'turn_count': self.accumulated_state['turn_count'],
            'needs_more_context': False,
            'should_end_session': False,
            'response_text': '',
            'error': None,
            'input_tokens': 0,
            'output_tokens': 0,
            'latency_ms': 0
        }

        try:
            # Run the LangGraph workflow
            logger.info("[JointAgent] Executing LangGraph workflow...")

            # Prefer LangGraph's async path because this graph can route
            # through async private-agent nodes when private context is needed.
            if hasattr(graph, "ainvoke"):
                final_state = await graph.ainvoke(initial_state)
            else:
                maybe_state = graph.invoke(initial_state)
                final_state = await maybe_state if asyncio.iscoroutine(maybe_state) else maybe_state

            logger.info(
                f"[JointAgent] Workflow complete. Response: {len(final_state['response_text'])} chars, "
                f"End session: {final_state['should_end_session']}, "
                f"Tokens: {final_state['input_tokens']}/{final_state['output_tokens']}, "
                f"Latency: {final_state['latency_ms']}ms"
            )

            # Update accumulated state
            self.accumulated_state['private_a_context'] = final_state['private_a_context']
            self.accumulated_state['private_b_context'] = final_state['private_b_context']
            self.accumulated_state['query_count_a'] = final_state['query_count_a']
            self.accumulated_state['query_count_b'] = final_state['query_count_b']
            self.accumulated_state['turn_count'] = final_state['turn_count']

            # Store metrics
            self._last_metrics = {
                'input_tokens': final_state['input_tokens'],
                'output_tokens': final_state['output_tokens'],
                'latency_ms': final_state['latency_ms'],
                'model': settings.LLM_MODEL,
                'agent_type': 'joint',
                'turns': final_state['turn_count'],
                'queries_a': final_state['query_count_a'],
                'queries_b': final_state['query_count_b']
            }

            # Prepare accumulated context for return (for compatibility)
            updated_context = {
                'private_a_context': final_state['private_a_context'],
                'private_b_context': final_state['private_b_context']
            }

            return (
                final_state['response_text'],
                final_state['should_end_session'],
                updated_context
            )

        except Exception as e:
            logger.error(f"[JointAgent] Error in workflow execution: {e}", exc_info=True)

            # Return error response
            self._last_metrics = {
                'input_tokens': 0,
                'output_tokens': 0,
                'latency_ms': 0,
                'model': settings.LLM_MODEL,
                'agent_type': 'joint',
                'error': str(e)
            }

            return (
                "I'm having trouble reaching the therapist model right now. Your message was received, but I can't give a thoughtful response until the connection is back. Please try again in a moment.",
                False,
                accumulated_context or {'private_a_context': [], 'private_b_context': []}
            )

    def get_last_metrics(self) -> Dict:
        """
        Get metrics from last LLM calls.

        Returns:
            Dictionary with token counts, latency, and turn information
        """
        return self._last_metrics

    def reset_context(self):
        """
        Reset accumulated context and counters.

        Call this when starting a new session or when you want to
        clear the agent's memory of previous context queries.
        """
        logger.info("[JointAgent] Resetting context and counters")
        self.accumulated_state = {
            'private_a_context': [],
            'private_b_context': [],
            'query_count_a': 0,
            'query_count_b': 0,
            'turn_count': 0
        }
