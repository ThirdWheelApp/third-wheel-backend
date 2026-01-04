"""
Demo Mode Module

Provides mock LLM responses for testing and demo purposes.
Enables the application to run without Anthropic API calls.
"""

from app.demo.mock_llm import MockAnthropicClient, get_llm_client

__all__ = ['MockAnthropicClient', 'get_llm_client']
