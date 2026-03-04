# Architecture for the [VIVIIan Backend](./)

## Overview

The [VIVIIan](./) backend is a proxy between any arbitrary data-sampling hardware and VIVIIan's frontend/database. Each data-sampling tool will require an instance of the VIVIIan backend to collect its data and forward it as required.

Data in [VIVIIan](./) is primarily handled using [Apache Arrow](https://arrow.apache.org/) / `pyarrow`.
Time-series data maps naturally to Arrow's columnar model, which can be easily transmitted to the frontend for processing and display.

Design constraints:

1. The backend must be hardware-agnostic. It should support a simple API to allow for integrating any arbitrary data-sampling hardware into VIVIIan

## Target Dataflow (Proposed)

This section describes the preferred dataflow for a better version of the backend and intentionally does not mirror the current implementation, just like the frontend.

### High-Level Flow

1. **Hardware Sampling**
   User/default implemented hardware sampling scripts that reads data from the hardware and repeatedly writes it to an instance of the VIVIIan backend.
2. **Data Preparation**
   Read chunked data from the hardware sampler.
3. **Data Transmit**
   When the data is in a sufficiently sized chunk or enough time has passed, the data is transmitted to QuestDB and the frontend.






