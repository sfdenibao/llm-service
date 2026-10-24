"""FIFO bounded admission; no batching or waiting to fill a batch."""
import asyncio
from collections import deque
from server.errors import ServiceError


class Admission:
    def __init__(self, slots, queue, timeout):
        self.slots, self.capacity, self.timeout = slots, queue, timeout
        self.active = 0
        self.waiters = deque()

    @property
    def queued(self):
        return len(self.waiters)

    async def acquire(self):
        # All state transitions run on one event loop; there is no await between check and write.
        if self.active < self.slots and not self.waiters:
            self.active += 1
            return
        if len(self.waiters) >= self.capacity:
            raise ServiceError(429, 'queue_full', headers={'Retry-After': '1'})
        future = asyncio.get_running_loop().create_future()
        self.waiters.append(future)
        try:
            await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except BaseException as exc:
            if future in self.waiters:
                self.waiters.remove(future)
                future.cancel()
            elif future.done() and not future.cancelled():
                # A slot was handed to this waiter just before cancellation/timeout.
                self.release()
            if isinstance(exc, TimeoutError):
                raise ServiceError(504, 'queue_timeout') from None
            raise

    def release(self):
        while self.waiters:
            future = self.waiters.popleft()
            if not future.done():
                future.set_result(None)
                return
        if self.active <= 0:
            raise RuntimeError('Admission double release')
        self.active -= 1
