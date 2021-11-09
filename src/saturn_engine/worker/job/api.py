from typing import Any
from typing import Optional

import aiohttp

from saturn_engine.core.api import JobInput
from saturn_engine.core.api import JobResponse
from saturn_engine.utils import DelayedThrottle
from saturn_engine.utils import urlcat
from saturn_engine.utils.log import getLogger
from saturn_engine.utils.options import asdict
from saturn_engine.utils.options import fromdict

from . import JobStore


class ApiJobStore(JobStore):
    def __init__(
        self,
        *,
        http_client: aiohttp.ClientSession,
        base_url: str,
        job_id: int,
        **kwargs: Any
    ) -> None:
        self.http_client = http_client
        self.job_id = job_id
        self.base_url = base_url
        self.logger = getLogger(__name__, self)

        self.after: Optional[str] = None
        self.throttle_save_cursor = DelayedThrottle(self.delayed_save_cursor, delay=1)

    async def load_cursor(self) -> Optional[str]:
        job_url = urlcat(self.base_url, "api/jobs", str(self.job_id))
        async with self.http_client.get(job_url) as response:
            return fromdict(await response.json(), JobResponse).data.cursor

    async def save_cursor(self, *, after: str) -> None:
        self.after = after
        await self.throttle_save_cursor(after=self.after)

    async def delayed_save_cursor(self, *, after: str) -> None:
        try:
            job_url = urlcat(self.base_url, "api/jobs", str(self.job_id))
            json = asdict(JobInput(cursor=after))
            async with self.http_client.put(job_url, json=json) as response:
                response.raise_for_status()
        except Exception:
            self.logger.exception("Failed to save cursor")