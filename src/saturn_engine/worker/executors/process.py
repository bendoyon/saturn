import asyncio
import concurrent.futures
from functools import partial

from saturn_engine.core import PipelineResult
from saturn_engine.worker.pipeline_message import PipelineMessage

from . import Executor
from .bootstrap import bootstrap_pipeline


class ProcessExecutor(Executor):
    def __init__(self, concurrency: int = 8) -> None:
        self.pool_executor = concurrent.futures.ProcessPoolExecutor()

    async def process_message(self, message: PipelineMessage) -> PipelineResult:
        loop = asyncio.get_running_loop()
        execute = partial(bootstrap_pipeline, message=message)
        return await loop.run_in_executor(self.pool_executor, execute)

    async def close(self) -> None:
        self.pool_executor.shutdown(wait=False, cancel_futures=True)
