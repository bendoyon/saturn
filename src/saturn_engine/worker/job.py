import typing as t

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from saturn_engine.core import MessageId
from saturn_engine.core import TopicMessage
from saturn_engine.core.api import QueueItemWithState
from saturn_engine.utils.log import getLogger
from saturn_engine.worker.inventories import Inventory
from saturn_engine.worker.inventory import Item
from saturn_engine.worker.services import Services
from saturn_engine.worker.services.job_state.service import JobStateService
from saturn_engine.worker.topics import Topic
from saturn_engine.worker.topics import TopicOutput

JOB_NAMESPACE: t.Final[str] = "job"


class Job(Topic):
    def __init__(
        self,
        *,
        inventory: Inventory,
        queue_item: QueueItemWithState,
        services: Services,
    ) -> None:
        self.logger = getLogger(__name__, self)
        self.inventory = inventory
        self.services = services
        self.queue_item = queue_item
        self.state_service = services.cast_service(JobStateService)

    async def run(self) -> AsyncGenerator[TopicOutput, None]:
        cursor = self.queue_item.state.cursor

        try:
            async for item in self.inventory.iterate(after=cursor):
                cursor = item.cursor
                yield self.item_to_topic(item)

                if cursor:
                    self.state_service.set_job_cursor(
                        self.queue_item.name, cursor=cursor
                    )

            self.state_service.set_job_completed(self.queue_item.name)
        except Exception as e:
            self.logger.exception("Exception raised from job")
            self.state_service.set_job_failed(self.queue_item.name, error=e)

    @asynccontextmanager
    async def item_to_topic(self, item_ctx: Item) -> t.AsyncIterator[TopicMessage]:
        async with item_ctx as item:
            yield TopicMessage(
                id=MessageId(item.id),
                args=item.args,
                tags=item.tags,
                metadata=item.metadata | {"job": {"cursor": item.cursor}},
            )
