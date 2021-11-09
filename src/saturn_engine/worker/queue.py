from collections.abc import AsyncGenerator
from typing import AsyncContextManager

from saturn_engine.core import PipelineMessage
from saturn_engine.core import QueuePipeline

from .executable_message import ExecutableMessage
from .parkers import Parkers
from .topics import Topic


class ExecutableQueue:
    def __init__(
        self, topic: Topic, pipeline: QueuePipeline, output: dict[str, list[Topic]]
    ):
        self.topic = topic
        self.parkers = Parkers()
        self.pipeline = pipeline
        self.output = output

    async def run(self) -> AsyncGenerator[ExecutableMessage, None]:
        async for message in self.topic.run():
            await self.parkers.wait()
            context = None
            if isinstance(message, AsyncContextManager):
                context = message
                message = await message.__aenter__()

            yield ExecutableMessage(
                parker=self.parkers,
                message=PipelineMessage(
                    info=self.pipeline.info, message=message.extend(self.pipeline.args)
                ),
                message_context=context,
                output=self.output,
            )