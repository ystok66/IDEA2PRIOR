"""One arXiv API connection across processes in this workspace, >=3s apart."""
from contextlib import contextmanager
import os
from pathlib import Path
import time


@contextmanager
def arxiv_connection(max_wait=120):
    directory = Path(os.getenv('PAPERARENA_CACHE_DIR') or Path.home() / '.cache' / 'paperarena' / 'retrieval')
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / 'arxiv_api.lock', os.O_RDWR | os.O_CREAT)
    stream = os.fdopen(fd, 'r+b', buffering=0)
    locked = False
    started = time.monotonic()
    try:
        if os.fstat(fd).st_size == 0:
            stream.write(b'0\n0')
        while not locked:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError:
                if time.monotonic() - started >= max_wait:
                    raise TimeoutError('arXiv cross-process connection wait exhausted')
                time.sleep(0.1)
        stream.seek(2)
        try:
            previous = float(stream.read() or b'0')
        except ValueError:
            raise ValueError('Corrupt arXiv pacing record; refusing uncontrolled requests')
        delay = max(0.0, previous + 3.0 - time.time())
        if delay > max_wait - (time.monotonic() - started):
            raise TimeoutError('arXiv pacing wait exhausted')
        if delay:
            time.sleep(delay)
        stream.seek(2)
        stream.write(str(time.time()).encode('ascii'))
        stream.truncate()
        yield
    finally:
        if locked:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        stream.close()
