"""Plot audit timelines as a Gantt-style PNG chart.

Usage::

    assai audit plot                   # last 5 requests
    assai audit plot --last 10         # last 10
    assai audit plot --output out.png  # custom output path
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from argklass import argument
from argklass.command import Command

from assai.cli import CommonArguments, setup
from assai.cli.audit import load_audits


@dataclass
class PlotArguments(CommonArguments):
    last: int = argument(default=5, help="number of recent requests to plot")
    output: str = argument(default=None, help="output PNG path (default: <audit_dir>/_plot.png)")


def _build_spans(audits: list[dict]) -> list[dict]:
    """Pair .start/.end events into span rows for the Gantt chart.

    Unpaired events (worker.acquired, payload.saved, etc.) become thin
    marker bars so they still appear on the timeline.

    When ``dispatch.tokens`` data is available the dispatch bar is split
    into **ttft** (waiting for first token) and **generation** (token
    streaming) sub-bars so the breakdown is visible.
    """
    rows: list[dict] = []

    def _row(label, span, start, end_, total, lbl=None):
        return {
            "request": label,
            "span": span,
            "start_ms": start,
            "end_ms": end_,
            "duration_ms": round(end_ - start, 2),
            "total_ms": total,
            "label": lbl or span,
        }

    for audit in audits:
        rid = audit["request_id"]
        endpoint = audit.get("meta", {}).get("endpoint", "")
        label = f"{endpoint} ({rid[:8]})"
        events = audit.get("events", [])
        total_ms = audit.get("total_duration_ms", 0)

        open_spans: dict[str, dict] = {}
        pending_tokens: dict[str, dict] = {}

        for ev in events:
            name = ev.get("event", "")
            elapsed = ev.get("elapsed_ms", 0.0)

            if name == "dispatch.tokens":
                pending_tokens["dispatch"] = ev
                continue

            if name.endswith(".start"):
                span_name = name.removesuffix(".start")
                open_spans[span_name] = _row(
                    label, span_name, elapsed, elapsed, total_ms,
                )

            elif name.endswith(".end") or name.endswith(".error"):
                suffix = ".end" if name.endswith(".end") else ".error"
                span_name = name.removesuffix(suffix)
                if span_name in open_spans:
                    entry = open_spans.pop(span_name)
                    entry["end_ms"] = elapsed
                    entry["duration_ms"] = round(elapsed - entry["start_ms"], 2)

                    tok = pending_tokens.pop(span_name, None)
                    if tok:
                        ds = entry["start_ms"]
                        de = entry["end_ms"]
                        ttft = tok.get("ttft_ms", 0)
                        gen = tok.get("generation_ms", 0)
                        tokens = tok.get("token_count", 0)
                        itl = tok.get("itl_ms", 0)

                        rows.append(_row(
                            label, "ttft", ds, ds + ttft,
                            total_ms, f"TTFT {ttft}ms",
                        ))
                        rows.append(_row(
                            label, "generation", ds + ttft, ds + gen,
                            total_ms,
                            f"gen {round(gen - ttft, 1)}ms  "
                            f"ITL:{itl}ms  tokens:{tokens}",
                        ))
                        if de > ds + gen + 0.5:
                            rows.append(_row(
                                label, "dispatch.tail",
                                ds + gen, de, total_ms,
                            ))
                    else:
                        rows.append(entry)
                else:
                    rows.append(_row(
                        label, span_name, elapsed, elapsed + 0.5, total_ms,
                    ))

            else:
                rows.append(_row(
                    label, name, elapsed, elapsed + 0.5, total_ms,
                ))

        for entry in open_spans.values():
            entry["end_ms"] = total_ms
            entry["duration_ms"] = round(total_ms - entry["start_ms"], 2)
            rows.append(entry)

    return rows


class Plot(Command):
    """Plot event timelines comparing recent requests (Gantt chart)."""

    name = "plot"

    Arguments = PlotArguments

    @staticmethod
    def execute(args) -> int:
        try:
            import altair as alt
        except ImportError:
            print("altair is required: pip install altair vl-convert-python", file=sys.stderr)
            return 1

        config, _ = setup(args)
        audit_root = config.audit.dir

        audits = load_audits(audit_root, args.last)
        if not audits:
            print("No audit data found.", file=sys.stderr)
            return 1

        rows = _build_spans(audits)
        if not rows:
            print("No span data to plot.", file=sys.stderr)
            return 1

        bars = alt.Chart(alt.Data(values=rows)).mark_bar().encode(
            x=alt.X("start_ms:Q", title="Elapsed time (ms)"),
            x2="end_ms:Q",
            y=alt.Y("request:N", title=None, sort=None),
            color=alt.Color("span:N", title="Span"),
            tooltip=[
                alt.Tooltip("request:N"),
                alt.Tooltip("span:N"),
                alt.Tooltip("label:N", title="Detail"),
                alt.Tooltip("start_ms:Q", format=".1f"),
                alt.Tooltip("end_ms:Q", format=".1f"),
                alt.Tooltip("duration_ms:Q", format=".1f"),
            ],
        )

        chart = bars.properties(
            title="Audit Timeline",
            width=800,
            height=max(80, len(audits) * 50),
        )

        out_path = args.output or os.path.join(audit_root, "_plot.png")
        chart.save(out_path)
        print(f"Saved to {out_path}")

        return 0


COMMANDS = Plot
