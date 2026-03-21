"""Benchmarks page — performance metrics, token usage, and comparison."""

import plotly.graph_objects as go
import streamlit as st

from dashboard.components.page_header import render_page_description
from dashboard.data_loader import get_cache_stats, list_benchmark_results
from dashboard.theme import STAGE_COLORS


def _render_usage_report(project_dir):
    """Render the Token Usage section — actual vs projected costs.

    Uses compact custom HTML instead of Streamlit's default (oversized) metric
    widgets so everything fits cleanly in the dashboard layout.
    """
    from pathlib import Path
    import json
    from dashboard.theme import BG_SURFACE, BORDER, TEXT_BODY, TEXT_MUTED, RADIUS

    st.markdown(
        '<h3 style="font-size:18px; margin-bottom:4px;">Token Usage — Actual vs Projected</h3>',
        unsafe_allow_html=True,
    )

    # Find the latest run with a usage report
    state_dir = Path(project_dir) / "state" / "runs"
    if not state_dir.exists():
        st.info("No pipeline runs found yet. Run the pipeline to see token usage.")
        return

    # Sort runs by directory mtime, newest first
    run_dirs = sorted(state_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
    reports = []
    for rd in run_dirs:
        report_path = rd / "usage_report.json"
        if report_path.exists():
            try:
                data = json.loads(report_path.read_text())
                data["_run_dir"] = rd.name
                reports.append(data)
            except (json.JSONDecodeError, OSError):
                continue

    if not reports:
        st.info(
            "No usage reports found. Usage tracking was added recently — "
            "run the pipeline again to see actual vs projected token costs."
        )
        return

    # Run selector (compact)
    run_labels = [
        f"{r['run_id']} ({r.get('tier', 'unknown')})" for r in reports
    ]
    selected = st.selectbox(
        "Select Run", range(len(run_labels)),
        format_func=lambda i: run_labels[i],
        key="usage_run",
        label_visibility="collapsed",
    )
    report = reports[selected]
    actual = report.get("actual", {})
    projected = report.get("projected", {})

    # Key metrics row — compact custom HTML instead of st.metric
    tier_label = report.get("tier", "—").upper()
    llm_calls = actual.get("llm_calls", 0)
    cost_str = f"${actual.get('cost_usd', 0):.4f}"
    if projected.get("total_tokens", 0) > 0:
        savings = (1 - actual.get("total_tokens", 0) / projected["total_tokens"]) * 100
        proj_str = f"{savings:+.0f}% tokens saved"
    else:
        proj_str = "N/A"

    def _mini_metric(label: str, value: str) -> str:
        return (
            f'<div style="text-align:center; padding:8px 4px;">'
            f'<div style="font-size:11px; color:{TEXT_MUTED}; text-transform:uppercase; '
            f'letter-spacing:0.5px; margin-bottom:2px;">{label}</div>'
            f'<div style="font-size:16px; font-weight:600; color:{TEXT_BODY};">{value}</div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat(4,1fr); gap:8px; '
        f'background:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:{RADIUS}; '
        f'padding:4px 8px; margin-bottom:12px;">'
        + _mini_metric("Tier", tier_label)
        + _mini_metric("LLM Calls", str(llm_calls))
        + _mini_metric("Actual Cost", cost_str)
        + _mini_metric("vs Projection", proj_str)
        + '</div>',
        unsafe_allow_html=True,
    )

    # Per-stage breakdown
    stages = report.get("stages", [])
    if stages:
        st.markdown(
            '<p style="font-size:13px; font-weight:600; margin:8px 0 4px;">Per-Stage Breakdown</p>',
            unsafe_allow_html=True,
        )

        stage_names = [s["stage"] for s in stages]
        input_tokens = [s["input_tokens"] for s in stages]
        output_tokens = [s["output_tokens"] for s in stages]

        fig = go.Figure(data=[
            go.Bar(
                name="Input Tokens",
                x=stage_names,
                y=input_tokens,
                marker_color="#60A5FA",
                text=[f"{t:,}" for t in input_tokens],
                textposition="outside",
                textfont_size=10,
            ),
            go.Bar(
                name="Output Tokens",
                x=stage_names,
                y=output_tokens,
                marker_color="#34D399",
                text=[f"{t:,}" for t in output_tokens],
                textposition="outside",
                textfont_size=10,
            ),
        ])
        fig.update_layout(
            barmode="group",
            yaxis_title="Tokens",
            height=300,
            margin=dict(t=10, b=30, l=50, r=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, font_size=11),
            font=dict(size=11),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Actual vs Projected comparison — compact two-column layout
    if projected.get("total_tokens", 0) > 0:
        def _usage_col(title: str, data: dict) -> str:
            return (
                f'<div style="padding:8px 12px;">'
                f'<div style="font-size:12px; font-weight:600; color:{TEXT_BODY}; '
                f'margin-bottom:6px;">{title}</div>'
                f'<div style="font-size:12px; color:{TEXT_MUTED}; line-height:1.8;">'
                f'Input: {data.get("input_tokens", 0):,}<br>'
                f'Output: {data.get("output_tokens", 0):,}<br>'
                f'Total: {data.get("total_tokens", 0):,}<br>'
                f'Cost: ${data.get("cost_usd", 0):.4f}'
                f'</div></div>'
            )

        st.markdown(
            f'<div style="display:grid; grid-template-columns:1fr 1fr; gap:0; '
            f'background:{BG_SURFACE}; border:1px solid {BORDER}; border-radius:{RADIUS}; '
            f'margin-bottom:12px;">'
            + _usage_col("Actual", actual)
            + _usage_col("Projected", projected)
            + '</div>',
            unsafe_allow_html=True,
        )

    # Cache info per stage
    cached_stages = [s for s in stages if s.get("cache_hit")]
    if cached_stages:
        st.caption(
            f"Cache hit on: {', '.join(s['stage'] for s in cached_stages)} "
            f"(0 tokens consumed — free re-run)"
        )


def render(project_dir):
    st.title("Benchmarks")

    render_page_description(
        "Track pipeline performance over time. The Summary shows wall time "
        "and model call counts for a benchmark run. Per-Stage Timing breaks "
        "down how long each stage takes. "
        "Cache Performance shows the hit rate — higher means more free re-runs. "
        "Use Compare Two Results at the bottom to see how config "
        "or prompt changes affect speed and cost."
    )

    # Token usage section (always shown, independent of benchmark runs)
    _render_usage_report(project_dir)
    st.divider()

    # Benchmark results section
    st.subheader("Benchmark Runs")
    results = list_benchmark_results(project_dir)

    if not results:
        st.info(
            "No benchmark results found. Run:\n\n"
            "```\npython bench/benchmark_runs.py --runs 3 --project-dir .\n```"
        )
        return

    # Result selector
    labels = [f"{r.get('git_sha', '?')} — {r.get('timestamp', '?')[:19]}" for r in results]
    selected_idx = st.selectbox(
        "Select Result",
        range(len(labels)),
        format_func=lambda i: labels[i],
    )
    result = results[selected_idx]
    agg = result.get("aggregate", {})

    st.divider()

    # Key metrics
    st.subheader("Summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Git SHA", result.get("git_sha", "?"))
    col2.metric("Runs", len(result.get("runs", [])))
    col3.metric("Mean Wall Time", f"{agg.get('mean_total_wall_s', 0):.1f}s")
    col4.metric("Mean Model Calls", agg.get("mean_model_calls", 0))

    st.divider()

    # Per-stage timing chart
    st.subheader("Per-Stage Timing")
    runs = result.get("runs", [])
    if runs and runs[0].get("stage_wall_s"):
        stages = list(runs[0]["stage_wall_s"].keys())
        avg_times = []
        for stage in stages:
            times = [r["stage_wall_s"].get(stage, 0) for r in runs]
            avg_times.append(sum(times) / len(times))

        # Use theme stage colors
        bar_colors = [STAGE_COLORS.get(s, "#94A3B8") for s in stages]

        fig = go.Figure(
            data=[
                go.Bar(
                    x=stages,
                    y=avg_times,
                    marker_color=bar_colors,
                    text=[f"{t:.1f}s" for t in avg_times],
                    textposition="outside",
                )
            ]
        )
        fig.update_layout(
            yaxis_title="Seconds",
            height=350,
            margin=dict(t=20, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)

    # Cache stats
    st.subheader("Cache Performance")
    cache_stats = get_cache_stats(project_dir)
    ccol1, ccol2 = st.columns(2)
    ccol1.metric("Total Cached Responses", cache_stats["total_entries"])

    total_hits = sum(r.get("cache_hits", 0) for r in runs)
    total_calls = sum(r.get("model_calls", 0) for r in runs)
    hit_rate = total_hits / total_calls if total_calls > 0 else 0
    ccol2.metric("Cache Hit Rate", f"{hit_rate:.0%}")

    # Comparison selector
    st.divider()
    st.subheader("Compare Two Results")
    if len(results) >= 2:
        comp_col1, comp_col2 = st.columns(2)
        with comp_col1:
            old_idx = st.selectbox(
                "Baseline",
                range(len(labels)),
                index=min(1, len(labels) - 1),
                format_func=lambda i: labels[i],
                key="old",
            )
        with comp_col2:
            new_idx = st.selectbox(
                "Current",
                range(len(labels)),
                index=0,
                format_func=lambda i: labels[i],
                key="new",
            )

        old_agg = results[old_idx].get("aggregate", {})
        new_agg = results[new_idx].get("aggregate", {})

        def pct_delta(old_val, new_val):
            if old_val == 0:
                return "N/A"
            delta = ((new_val - old_val) / old_val) * 100
            return f"{delta:+.1f}%"

        dcol1, dcol2, dcol3 = st.columns(3)
        old_time = old_agg.get("mean_total_wall_s", 0)
        new_time = new_agg.get("mean_total_wall_s", 0)
        dcol1.metric(
            "Wall Time",
            f"{new_time:.1f}s",
            delta=pct_delta(old_time, new_time),
            delta_color="inverse",
        )

        old_calls = old_agg.get("mean_model_calls", 0)
        new_calls = new_agg.get("mean_model_calls", 0)
        dcol2.metric(
            "Model Calls",
            new_calls,
            delta=pct_delta(old_calls, new_calls),
            delta_color="inverse",
        )

        old_bytes = old_agg.get("mean_prompt_bytes", 0)
        new_bytes = new_agg.get("mean_prompt_bytes", 0)
        dcol3.metric(
            "Prompt Bytes",
            new_bytes,
            delta=pct_delta(old_bytes, new_bytes),
            delta_color="inverse",
        )
    else:
        st.info("Run benchmarks at two different commits to enable comparison.")
