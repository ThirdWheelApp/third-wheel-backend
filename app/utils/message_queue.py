"""
Message Queue for Session Messages

Ensures messages in a session are processed sequentially to avoid race conditions.
Each session has its own queue.
"""

import asyncio
from typing import Dict, Callable
from collections import defaultdict


class SessionMessageQueue:
    """
    Manages message queues for each session.

    Ensures that messages in the same session are processed one at a time,
    preventing race conditions when multiple users send messages simultaneously.
    """

    def __init__(self):
        # Dictionary of session_id -> asyncio.Queue
        self._queues: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        # Dictionary of session_id -> Task (processing task)
        self._tasks: Dict[str, asyncio.Task] = {}

    async def enqueue(self, session_id: str, message_data: dict, handler: Callable):
        """
        Add a message to the session's queue and ensure processing is running.

        Args:
            session_id: Session identifier
            message_data: Message payload
            handler: Async function to process the message
        """
        queue = self._queues[session_id]
        await queue.put((message_data, handler))

        # Start processing task if not already running
        if session_id not in self._tasks or self._tasks[session_id].done():
            self._tasks[session_id] = asyncio.create_task(
                self._process_queue(session_id)
            )

    async def _process_queue(self, session_id: str):
        """
        Process messages in the queue sequentially.

        Args:
            session_id: Session identifier
        """
        queue = self._queues[session_id]

        while not queue.empty():
            message_data, handler = await queue.get()
            try:
                await handler(message_data)
            except Exception as e:
                print(f"Error processing message in session {session_id}: {e}")
            finally:
                queue.task_done()


# Global message queue instance
session_message_queue = SessionMessageQueue()
