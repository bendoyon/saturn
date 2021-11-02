import asyncio
from typing import Callable
from unittest.mock import Mock

import pytest

from saturn_engine.core import PipelineMessage
from saturn_engine.core.api import DummyJob
from saturn_engine.core.api import Inventory
from saturn_engine.core.api import PipelineInfo
from saturn_engine.core.api import QueuePipeline
from saturn_engine.core.api import ResourceItem
from saturn_engine.core.api import SyncResponse
from saturn_engine.worker.broker import Broker
from saturn_engine.worker.executors import Executor
from tests.worker.conftest import FakeResource


class FakeExecutor(Executor):
    def __init__(self) -> None:
        self.done_event = asyncio.Event()

    async def process_message(self, message: PipelineMessage) -> None:
        assert isinstance(message.message.args["resource"], dict)
        assert message.message.args["resource"]["data"] == "fake"
        if message.message.args["n"] == 999:
            self.done_event.set()


def pipeline(resource: FakeResource) -> None:
    ...


@pytest.mark.asyncio
async def test_broker_dummy(
    fake_resource_class: str,
    broker_maker: Callable[..., Broker],
    worker_manager_client: Mock,
) -> None:
    executor = FakeExecutor()
    broker = broker_maker(executor=lambda: executor)
    pipeline_info = PipelineInfo.from_pipeline(pipeline)
    worker_manager_client.sync.return_value = SyncResponse(
        items=[
            DummyJob(
                id="j1",
                pipeline=QueuePipeline(args={}, info=pipeline_info),
                inventory=Inventory(
                    name="dummy", type="dummy", options={"count": 10000}
                ),
                options={},
            )
        ],
        resources=[
            ResourceItem(
                id="r1",
                type="FakeResource",
                data={"data": "fake"},
            )
        ],
    )

    wait_task = asyncio.create_task(executor.done_event.wait())
    broker_task = asyncio.create_task(broker.run())
    tasks: set[asyncio.Task] = {wait_task, broker_task}
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    assert wait_task in done
    assert broker_task in pending
    broker_task.cancel()