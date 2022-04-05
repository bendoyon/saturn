import typing as t

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from collections.abc import AsyncIterator
from collections.abc import Awaitable
from collections.abc import Coroutine
from collections.abc import Iterable

from saturn_engine.utils.log import getLogger

T = t.TypeVar("T")

AsyncFNone = t.TypeVar("AsyncFNone", bound=t.Callable[..., Awaitable[None]])


async def aiter2agen(iterator: AsyncIterator[T]) -> AsyncGenerator[T, None]:
    """
    Convert an async iterator into an async generator.
    """
    async for x in iterator:
        yield x


class TasksGroup:
    def __init__(
        self, tasks: Iterable[asyncio.Task] = (), *, name: t.Optional[str] = None
    ) -> None:
        self.logger = getLogger(__name__, self)
        self.tasks = set(tasks)
        self.updated = asyncio.Event()
        if name:
            name = f"task-group-{name}.wait"
        self.name = name
        self.updated_task = asyncio.create_task(self.updated.wait(), name=self.name)

    def add(self, task: asyncio.Task) -> None:
        self.tasks.add(task)
        self.notify()

    def create_task(self, coroutine: Coroutine, **kwargs: t.Any) -> asyncio.Task:
        task = asyncio.create_task(coroutine, **kwargs)
        self.add(task)
        return task

    def remove(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        self.notify()

    def notify(self) -> None:
        self.updated.set()

    async def wait(self) -> set[asyncio.Task]:
        self.updated.clear()
        done, _ = await asyncio.wait(
            self.tasks | {self.updated_task}, return_when=asyncio.FIRST_COMPLETED
        )

        if self.updated_task in done:
            done.remove(self.updated_task)
            self.updated_task = asyncio.create_task(self.updated.wait(), name=self.name)

        self.tasks.difference_update(done)

        return done

    async def close(self, timeout: t.Optional[float] = None) -> None:
        # Cancel the update event task.
        self.updated_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.updated_task

        if not self.tasks:
            return

        # Give the chance of all task to terminate within 'timeout'.
        if timeout:
            done, _ = await asyncio.wait(self.tasks, timeout=timeout)

        # Cancel all remaining tasks.
        for task in self.tasks:
            if not task.done():
                task.cancel()

        # Collect results to log errors.
        done, pending = await asyncio.wait(self.tasks, timeout=timeout)
        for task in done:
            if not task.cancelled() and isinstance(task.exception(), Exception):
                self.logger.error(
                    "Task '%s' cancelled with error", task, exc_info=task.exception()
                )
        for task in pending:
            self.logger.error("Task '%s' won't complete", task)

        self.tasks.clear()

    def all(self) -> set[asyncio.Task]:
        return self.tasks


class TasksGroupRunner(TasksGroup):
    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)
        self._runner_task: t.Optional[asyncio.Task]
        self.is_running = False

    def start(self) -> asyncio.Task:
        if self.is_running:
            raise RuntimeError("task group is already running")
        self.is_running = True
        self._runner_task = asyncio.create_task(self.run())
        return self._runner_task

    async def stop(self) -> None:
        self.is_running = False
        self.notify()

    async def close(self, timeout: t.Optional[float] = None) -> None:
        if self.is_running is False:
            return

        # Stop the runner.
        await self.stop()
        # Wait for the running task to complete.
        if self._runner_task:
            await self._runner_task
        # Clean the tasks.
        await super().close(timeout=timeout)

    async def run(self) -> None:
        while self.is_running:
            done = await self.wait()
            for task in done:
                if not task.cancelled() and isinstance(task.exception(), Exception):
                    self.logger.error(
                        "Task '%s' failed", task, exc_info=task.exception()
                    )


class DelayedThrottle(t.Generic[AsyncFNone]):
    __call__: AsyncFNone

    def __init__(self, func: AsyncFNone, *, delay: float) -> None:
        self.func = func
        self.delay = delay
        self.delayed_task: t.Optional[asyncio.Task] = None
        self.delayed_lock = asyncio.Lock()
        self.delayed_args: tuple[t.Any, ...] = ()
        self.delayed_kwargs: dict[str, t.Any] = {}

    async def __call__(self, *args: t.Any, **kwargs: t.Any) -> None:  # type: ignore
        async with self.delayed_lock:
            self.delayed_args = args
            self.delayed_kwargs = kwargs

            if self.delayed_task is None:
                name = f"{self.func.__qualname__}.delayed"
                self.delayed_task = asyncio.create_task(self._delay_call(), name=name)

    async def _delay_call(self) -> None:
        await asyncio.sleep(self.delay)
        async with self.delayed_lock:
            await self.func(*self.delayed_args, **self.delayed_kwargs)
            self.delayed_task = None

    async def cancel(self) -> None:
        if self.delayed_task is None:
            return
        self.delayed_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.delayed_task

    async def flush(self) -> None:
        if self.delayed_task is None:
            return
        async with self.delayed_lock:
            await self.cancel()
            await self.func(*self.delayed_args, **self.delayed_kwargs)
            self.delayed_task = None


def print_tasks_summary(loop: t.Optional[asyncio.AbstractEventLoop] = None) -> None:
    loop = loop or asyncio.get_running_loop()
    tasks = list(asyncio.all_tasks(loop))
    tasks.sort(key=lambda t: t.get_name())
    for task in tasks:
        print(task.get_name() + f" <{task._state}>")
        stack = task.get_stack()
        if stack:
            frame = stack[-1]
            print(f"  {frame.f_code.co_name}:{frame.f_lineno}")


class CachedProperty(t.Generic[T]):
    def __init__(self, getter: t.Callable[[t.Any], Awaitable[T]]) -> None:
        self.__wrapped__ = getter
        self._name = getter.__name__
        self.__doc__ = getter.__doc__

    def __set_name__(self, owner: t.Any, name: str) -> None:
        # Check whether we can store anything on the instance
        # Note that this is a failsafe, and might fail ugly.
        # People who are clever enough to avoid this heuristic
        # should also be clever enough to know the why and what.
        if not any("__dict__" in dir(cls) for cls in owner.__mro__):
            raise TypeError(
                "'cached_property' requires '__dict__' "
                f"on {owner.__name__!r} to store {name}"
            )
        self._name = name

    def __get__(self, instance: t.Any, owner: t.Any) -> t.Any:
        if instance is None:
            return self
        return self._get_attribute(instance)

    async def _get_attribute(self, instance: t.Any) -> T:
        value = instance.__dict__.get(self._name)
        if value is None:
            task = asyncio.create_task(self.__wrapped__(instance))
            instance.__dict__[self._name] = task
            try:
                value = await task
            except BaseException:
                del instance.__dict__[self._name]
                raise

            # Once the task is complete, replace with a future to only hold
            # the value and drop the task.
            future: asyncio.Future[T] = asyncio.Future()
            future.set_result(value)
            instance.__dict__[self._name] = future
            return value

        return await value


cached_property = CachedProperty


class WouldBlock(Exception):
    pass


class FakeSemaphore(contextlib.AbstractAsyncContextManager):
    def locked(self) -> bool:
        return False


class SharedLock:
    """Like a lock, but bind a lock to a reservation.
    * A reservation can only be made if there's no active lock.
    * A reservation can lock, blocking any new reservation or reservation
      locking.
    * Once reservation is locked, this reservation can be locked many time
      without blocking.
    """

    def __init__(self, *, max_reservations: int = 0):
        self._lock = asyncio.Lock()
        self._locker: t.Optional[object] = None
        if max_reservations:
            self._reservations_lock = asyncio.Semaphore(max_reservations)
        else:
            self._reservations_lock = t.cast(asyncio.Semaphore, FakeSemaphore())

    @contextlib.asynccontextmanager
    async def reserve(self) -> AsyncIterator["SharedLockReservation"]:
        async with self._reservations_lock:
            async with self._lock:
                reservation = SharedLockReservation(lock=self)

            try:
                yield reservation
            finally:
                reservation.release()

    def locked(self) -> bool:
        return self._lock.locked()

    def locked_reservations(self) -> bool:
        return self.locked() or self._reservations_lock.locked()

    async def _acquire(self, reservation: object) -> None:
        if self._locker is reservation:
            return

        await self._lock.acquire()
        self._locker = reservation

    def _release(self, reservation: object) -> None:
        if self._locker is not reservation:
            return

        self._locker = None
        self._lock.release()


class SharedLockReservation:
    def __init__(self, *, lock: SharedLock) -> None:
        self._lock = lock

    async def acquire(self) -> None:
        """Lock the queue, blocking any other reservation or lock itself if the
        queue is already locked.
        Return True if the lock don't block itself, False otherwise.
        """
        await self._lock._acquire(self)

    def locked(self) -> bool:
        return self._lock._locker is self

    def release(self) -> None:
        self._lock._release(self)
