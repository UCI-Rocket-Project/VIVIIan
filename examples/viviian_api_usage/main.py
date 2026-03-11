from __future__ import annotations

import logging
import time

import viviian as vivii

from . import tasks


def main() -> None:
    tasks.configure_logging()
    logger = logging.getLogger(__name__)
    ring_size = tasks.ROWS_PER_BATCH * 8 * 8
    logger.info("Starting VIVIIan API usage example. Throughput is logged once per second.")

    with vivii.Manager() as mgr:
        mgr.create_ring(vivii.RingSpec(name=tasks.COLUMN_ONE_RING, size=ring_size, num_readers=1))
        mgr.create_ring(vivii.RingSpec(name=tasks.COLUMN_TWO_RING, size=ring_size, num_readers=1))
        mgr.create_ring(vivii.RingSpec(name=tasks.COLUMN_THREE_RING, size=ring_size, num_readers=1))

        mgr.create_task(vivii.TaskSpec(name="server", fn=tasks.run_server))
        mgr.create_task(
            vivii.TaskSpec(
                name="client",
                fn=tasks.run_client,
                writing_rings=(tasks.COLUMN_ONE_RING, tasks.COLUMN_TWO_RING, tasks.COLUMN_THREE_RING),
            )
        )
        mgr.create_task(
            vivii.TaskSpec(
                name="fft_consumer",
                fn=tasks.run_fft_consumer,
                reading_rings=(tasks.COLUMN_ONE_RING, tasks.COLUMN_TWO_RING, tasks.COLUMN_THREE_RING),
            )
        )

        mgr.start("server")
        time.sleep(0.2)
        mgr.start("fft_consumer")
        mgr.start("client")
        logger.info("Started tasks: server, fft_consumer, client")

        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            logger.info("Stopping VIVIIan API usage example.")


if __name__ == "__main__":
    main()
