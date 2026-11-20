"""Process-level lease coordinating the service with offline maintenance."""

import fcntl


class ServiceLease:
    def __init__(self, data):
        data.mkdir(parents=True, exist_ok=True)
        self.file = (data / "layout.lock").open("a")
        try:
            fcntl.flock(self.file, fcntl.LOCK_SH | fcntl.LOCK_NB)
            if (data / "layout-maintenance.json").exists():
                raise ValueError("Recover the pending layout migration before starting the service")
        except BaseException:
            self.file.close()
            raise

    def close(self):
        self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
