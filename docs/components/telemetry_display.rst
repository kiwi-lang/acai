TelemetryDisplay
================

A live system-telemetry widget shown in the sidebar footer.

Props
-----

This component takes **no props**.  It subscribes to real-time data via
``useAgentSocket``.

Features
--------

* Displays CPU usage, per-GPU utilisation, and optional network/disk
  metrics.
* **Sparkline** — small inline SVG line charts for time-series data,
  normalised to a 0–100 range.
* **BidirectionalSparkline** — variant for upload/download or read/write
  pairs.

Source
------

``acai/ui/src/components/TelemetryDisplay.tsx``
