"""heavybag: punch your code over to the GPU box and watch it run.

Library entry points:

    from heavybag import Settings, Heavybag

    hb = Heavybag(Settings(host="gpu-box"))
    hb.push()
    job = hb.start(["python", "train.py"])
    for line in hb.follow(job):
        print(line, end="")
    print(hb.exit_code(job))
    hb.pull()
"""

from .client import ConnectionLost, Heavybag, JobInfo, JobNotFound, SyncStats
from .config import ConfigError, Settings, load_settings

__version__ = "0.1.0"

__all__ = [
    "ConfigError",
    "ConnectionLost",
    "Heavybag",
    "JobInfo",
    "JobNotFound",
    "Settings",
    "SyncStats",
    "__version__",
    "load_settings",
]
