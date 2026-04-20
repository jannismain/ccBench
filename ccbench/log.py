import logging
import os

import coloredlogs

log = logging.getLogger("ccBench")
coloredlogs.install(
    level=os.getenv("CCBENCH_LOG_LEVEL", "INFO"),
    fmt="%(asctime)s %(name)s %(levelname)-6s %(message)s (%(filename)s:%(lineno)d)",
    logger=log,
)
log.propagate = False

