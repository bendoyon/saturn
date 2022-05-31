import typing as t

import asyncio
import contextlib
import dataclasses
import json
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import aio_pika
import aio_pika.abc
import aio_pika.exceptions

from saturn_engine.core import TopicMessage
from saturn_engine.utils.asyncutils import SharedLock
from saturn_engine.utils.asyncutils import cached_property
from saturn_engine.utils.log import getLogger
from saturn_engine.utils.options import asdict
from saturn_engine.utils.options import fromdict
from saturn_engine.worker.services import Services
from saturn_engine.worker.services.rabbitmq import RabbitMQService

from . import Topic


class RabbitMQTopic(Topic):
    """A queue that consume message from RabbitMQ"""

    RETRY_PUBLISH_DELAY = timedelta(seconds=1)
    FAILURE_RETRY_BACKOFFS = [timedelta(seconds=s) for s in (0, 1, 5, 15, 30)]

    @dataclasses.dataclass
    class Options:
        queue_name: str
        auto_delete: bool = False
        durable: bool = True
        max_length: t.Optional[int] = None
        prefetch_count: t.Optional[int] = None

    class TopicServices:
        rabbitmq: RabbitMQService

    def __init__(self, options: Options, services: Services, **kwargs: object) -> None:
        self.logger = getLogger(__name__, self)
        self.options = options
        self.services = services.cast(RabbitMQTopic.TopicServices)
        self.exit_stack = contextlib.AsyncExitStack()
        self._queue: t.Optional[aio_pika.abc.AbstractQueue] = None
        self._publish_lock = SharedLock(max_reservations=8)

    async def run(self) -> AsyncGenerator[t.AsyncContextManager[TopicMessage], None]:
        self.logger.info("Starting queue %s", self.options.queue_name)
        attempt = 0
        while True:
            try:
                self.logger.info("Processing queue %s", self.options.queue_name)
                queue = await self.queue
                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        attempt = 0
                        yield self.message_context(message)
            except Exception:
                self.logger.exception("Failed to consume")
                await self.backoff_sleep(attempt)
                attempt += 1
            else:
                break

    async def publish(
        self,
        message: TopicMessage,
        wait: bool,
    ) -> bool:
        attempt = 0

        # Wait for the queue to unblock.
        if not wait and self._publish_lock.locked_reservations():
            return False

        async with self._publish_lock.reserve() as reservation:
            while True:
                try:
                    await self.ensure_queue()  # Ensure the queue is created.
                    channel = await self.channel
                    exchange = channel.default_exchange
                    if exchange is None:
                        raise ValueError("Channel has no exchange")
                    await exchange.publish(
                        aio_pika.Message(
                            body=json.dumps(asdict(message)).encode(),
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=self.options.queue_name,
                    )
                    return True
                except aio_pika.exceptions.DeliveryError as e:

                    # Only handle Nack
                    if e.frame.name != "Basic.Nack":
                        raise

                    if not wait:
                        return False

                    # If the lock is being help by another task, we can assume
                    # that once the control is being yielded to this task it
                    # will be after successfully publishing a message, therefor
                    # there's no need to sleep.
                    has_locked = reservation.locked() or not self._publish_lock.locked()
                    await reservation.acquire()
                    if has_locked:
                        await asyncio.sleep(self.RETRY_PUBLISH_DELAY.total_seconds())
                    attempt = 0
                except Exception:
                    self.logger.exception("Failed to publish")
                    if not wait:
                        raise

                    # If the lock is being help by another task, we can assume
                    # that once the control is being yielded to this task it
                    # will be after successfully publishing a message, therefor
                    # there's no need to sleep.
                    has_locked = reservation.locked() or not self._publish_lock.locked()
                    await reservation.acquire()
                    if has_locked:
                        await self.backoff_sleep(attempt)
                        attempt += 1

            return False

    async def backoff_sleep(self, attempt: int) -> None:
        retry_delay = self.FAILURE_RETRY_BACKOFFS[-1]
        if attempt < len(self.FAILURE_RETRY_BACKOFFS):
            retry_delay = self.FAILURE_RETRY_BACKOFFS[attempt]
        await asyncio.sleep(retry_delay.total_seconds())

    @asynccontextmanager
    async def message_context(
        self, message: aio_pika.abc.AbstractIncomingMessage
    ) -> AsyncIterator[TopicMessage]:
        async with message.process():
            yield fromdict(json.loads(message.body.decode()), TopicMessage)

    @cached_property
    async def channel(self) -> aio_pika.abc.AbstractChannel:
        connection = await self.services.rabbitmq.connection
        channel = await self.exit_stack.enter_async_context(
            connection.channel(on_return_raises=True)
        )

        if self.options.prefetch_count is not None:
            await channel.set_qos(prefetch_count=self.options.prefetch_count)
        channel.close_callbacks.add(self.channel_closed)
        return channel

    def channel_closed(
        self, channel: aio_pika.abc.AbstractChannel, reason: t.Optional[Exception]
    ) -> None:
        if isinstance(reason, BaseException):
            self.logger.error("Channel closed", exc_info=reason)
        elif reason:
            self.logger.error("Channel closed: %s", reason)

    @cached_property
    async def queue(self) -> aio_pika.abc.AbstractQueue:
        arguments: dict[str, t.Any] = {}
        if self.options.max_length:
            arguments["x-max-length"] = self.options.max_length
            arguments["x-overflow"] = "reject-publish"

        channel = await self.channel
        queue = await channel.declare_queue(
            self.options.queue_name,
            auto_delete=self.options.auto_delete,
            durable=self.options.durable,
            arguments=arguments,
        )

        return queue

    async def ensure_queue(self) -> aio_pika.abc.AbstractQueue:
        return await self.queue

    async def close(self) -> None:
        await self.exit_stack.aclose()
