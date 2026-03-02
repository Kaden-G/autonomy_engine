"""Benchmarks page — performance metrics and comparison."""

import plotly.graph_objects as go
import streamlit as st

from dashboard.data_loader import get_cache_stats, list_benchmark_results


def render(project_dir):
    st.title("📊 Benchmarks")

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

        fig = go.Figure(
            data=[
                go.Bar(
                    x=stages,
                    y=avg_times,
                    marker_color=[
                        "#3498DB",
                        "#9B59B6",
                        "#E67E22",
                        "#1ABC9C",
                        "#2ECC71",
                        "#E74C3C",
                    ],
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
