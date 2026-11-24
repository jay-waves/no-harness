# /// script
# requires-python = ">=3.11"
# dependencies = ["matplotlib==3.10.8"]
# ///
"""Plot token usage and task measurements from a task-run summary."""

import argparse
import json
from collections import defaultdict
from itertools import accumulate
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

STYLES = {
    ("fresh", "full"): ("#345995", "-", "o"),
    ("fresh", "bounded"): ("#345995", "--", "s"),
    ("persistent", "full"): ("#A24621", "-", "^"),
    ("persistent", "bounded"): ("#A24621", "--", "D"),
    ("fresh", None): ("#345995", "-", "o"),
    ("persistent", None): ("#A24621", "--", "^"),
}

INTERACTIVE_STYLES = {
    "ssh": ("Direct SSH", "#345995", "-", "o"),
    "docker": ("Docker exec", "#A24621", "--", "s"),
    "one": ("∅ne MCP", "#28766F", "-.", "^"),
}

TRANSPORT_STYLES = {
    ("ssh", "ssh"): ("Direct SSH raw", "#345995", "-", "o"),
    ("docker", "docker"): ("Docker API", "#A24621", "--", "s"),
    ("one", "ssh"): ("∅ne SSH raw", "#28766F", "-.", "^"),
    ("one", "mcp"): ("∅ne MCP stdio", "#70447D", ":", "D"),
}


def plot_tokens(ax, results, title, xlabel):
    counts = set()
    steps = set()
    for (mode, policy), values in results.items():
        if not values:
            continue
        ordered = sorted(values)
        counts.update(map(len, values.values()))
        steps.update(ordered)
        color, linestyle, marker = STYLES[mode, policy]
        ax.fill_between(
            ordered, [min(values[i]) for i in ordered],
            [max(values[i]) for i in ordered], color=color, alpha=0.08,
        )
        ax.plot(
            ordered, [mean(values[i]) for i in ordered],
            color=color, linestyle=linestyle, marker=marker,
            label=mode.capitalize() if policy is None else f"{mode.capitalize()}, {policy}",
        )
    if not steps:
        raise ValueError("Summary contains no comparable turns")
    per_step = str(min(counts)) if len(counts) == 1 else f"{min(counts)}–{max(counts)}"
    ax.set(
        title=title,
        xlabel=xlabel,
        ylabel="Input tokens",
        xticks=sorted(steps),
    )
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
    ax.legend(loc="upper left", fontsize=8)
    return per_step, counts == {1}


def plot_replay(ax, groups):
    results = {}
    for condition, trials in groups.items():
        values = defaultdict(list)
        for trial in trials:
            for turn in trial["turns"]:
                values[turn["step"]].append(turn["cumulative_input_tokens"])
        results[condition] = values
    count, singular = plot_tokens(ax, results, "Cumulative replay input", "Task step")
    return f"Replay shows means and observed ranges ({count} {'trial' if singular else 'trials'} per point)."


def plot_model(ax, groups):
    completed = {
        condition: {
            (trial["family"], trial["variant"]): trial["turns"]
            for trial in trials if trial["status"] == "completed"
        }
        for condition, trials in groups.items()
    }
    fresh, persistent = ("fresh", None), ("persistent", None)
    fresh_turns, persistent_turns = completed.get(fresh, {}), completed.get(persistent, {})
    results = {fresh: defaultdict(list), persistent: defaultdict(list)}
    for key in sorted(fresh_turns.keys() & persistent_turns.keys()):
        baseline, treatment = fresh_turns[key], persistent_turns[key]
        decisions = min(len(baseline), len(treatment))
        for condition, turns in ((fresh, baseline), (persistent, treatment)):
            tokens = accumulate(turn["usage"]["input_tokens"] for turn in turns[:decisions])
            for decision, cumulative in enumerate(tokens, 1):
                results[condition][decision].append(cumulative)
    count, singular = plot_tokens(ax, results, "Cumulative provider input", "Model decision")
    return (
        f"Provider input includes cached tokens. Means and ranges use {count} completed "
        f"{'pair' if singular else 'pairs'} per decision."
    )


def plot_checks(ax, groups, *, model=False):
    families = sorted({trial["family"] for trials in groups.values() for trial in trials})
    width = 0.75 / len(groups)
    for index, (condition, trials) in enumerate(groups.items()):
        color, linestyle, _ = STYLES[condition]
        for x, family in enumerate(families):
            checks = [trial["checks"] for trial in trials if trial["family"] == family]
            if not checks:
                continue
            passed = sum(bool(result) and all(result.values()) for result in checks)
            position = x + (index - (len(groups) - 1) / 2) * width
            rate = passed / len(checks)
            hatch = "/" if not model and linestyle == "--" else ""
            ax.bar(position, rate, width, color=color, hatch=hatch)
            ax.text(position, rate + 0.03, f"{passed}/{len(checks)}", ha="center", fontsize=8)
    ax.set(
        title="Trials passing all checks",
        ylabel="Share of trials",
        xticks=range(len(families)),
        xticklabels=[family.capitalize() for family in families],
        yticks=[0, 0.25, 0.5, 0.75, 1],
        ylim=(0, 1.16),
    )
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0%}"))
    if model:
        return "Checks include all attempts, in legend order."
    return "Bar order matches the legend. Hatching marks bounded output."


def plot_size(ax, groups):
    plotted = False
    for policy in ("full", "bounded"):
        fresh = {
            (trial["family"], trial["variant"]): trial
            for trial in groups.get(("fresh", policy), [])
        }
        pairs = [
            (fresh[(trial["family"], trial["variant"])], trial)
            for trial in groups.get(("persistent", policy), [])
            if (trial["family"], trial["variant"]) in fresh
        ]
        points = []
        for baseline, persistent in pairs:
            size = baseline.get("fixture_bytes")
            if size is None or size != persistent.get("fixture_bytes"):
                continue
            baseline_turns = baseline["turns"]
            persistent_turns = persistent["turns"]
            if not baseline_turns or [t["step"] for t in baseline_turns] != [
                t["step"] for t in persistent_turns
            ]:
                continue
            saved = (
                baseline_turns[-1]["cumulative_input_tokens"]
                - persistent_turns[-1]["cumulative_input_tokens"]
            )
            points.append((size, saved))
        if points:
            color, _, _ = STYLES["persistent", policy]
            ax.scatter(
                *zip(*points), edgecolors=color, marker="o" if policy == "full" else "s",
                facecolors="none" if policy == "full" else color,
                s=48 if policy == "full" else 19, alpha=0.8, label=policy.capitalize(),
            )
            plotted = True
    if plotted:
        ax.axhline(0, color="#999999", linewidth=0.8)
        ax.set(
            title="Replay savings by fixture size",
            xlabel="Fixture bytes",
            ylabel="Input tokens saved",
        )
        ax.xaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        ax.legend(loc="best", fontsize=8)
    return plotted


def plot_interactive(summary):
    model = summary["kind"] == "model"
    metrics = ("tokens", "rss", "payload", "transport")
    results = {metric: defaultdict(lambda: defaultdict(list)) for metric in metrics}
    checkpoints = set()
    trial_counts = defaultdict(int)
    for trial in summary["trials"]:
        if trial["release"] != "staged":
            continue
        mode = trial["mode"]
        if mode not in INTERACTIVE_STYLES:
            raise ValueError(f"Unknown interactive mode {mode!r}")
        trial_counts[mode] += 1
        tokens = 0
        payload = 0
        stages = sorted(trial["stages"], key=lambda stage: stage["step"])
        transport_totals = {
            boundary: 0
            for series_mode, boundary in TRANSPORT_STYLES
            if mode == series_mode
            and any(f"{boundary}_tx_bytes" in (stage.get("transport") or {}) for stage in stages)
        }
        for stage in stages:
            step = stage["step"]
            checkpoints.add(step)
            if model:
                used = (stage.get("usage") or {}).get("input_tokens")
                tokens = tokens + used if tokens is not None and used is not None else None
                if tokens is not None:
                    results["tokens"][mode][step].append(tokens)
            peak = (stage.get("memory") or {}).get("target_peak_kib")
            if peak is not None:
                results["rss"][mode][step].append(peak / 1024)
            payload += stage["code_bytes"] + stage["stdout_bytes"] + stage["stderr_bytes"]
            results["payload"][mode][step].append(payload)
            transport = stage.get("transport") or {}
            for boundary, total in transport_totals.items():
                if total is None:
                    continue
                sent = transport.get(f"{boundary}_tx_bytes")
                received = transport.get(f"{boundary}_rx_bytes")
                if sent is not None and received is not None:
                    total += sent + received
                elif stage.get("actions") != []:
                    transport_totals[boundary] = None
                    continue
                transport_totals[boundary] = total
                results["transport"][mode, boundary][step].append(total)
    if not checkpoints:
        raise ValueError("Summary contains no staged checkpoints")

    steps = sorted(checkpoints)
    fig, grid = plt.subplots(2, 2, figsize=(10.5, 7))
    axes = grid.ravel()
    titles = (
        "Cumulative provider input", "Peak target RSS",
        "Cumulative remote payload", "Cumulative transport traffic",
    )
    units = ("Input tokens", "MiB", "Application bytes", "Bytes sent and received")
    for ax, metric, title, unit in zip(axes, metrics, titles, units):
        ax.set(
            title=title, xlabel="Checkpoint", ylabel=unit, xticks=steps,
            xlim=(steps[0] - 0.25, steps[-1] + 0.25),
        )
        if not results[metric]:
            message = "Replay makes no provider calls" if metric == "tokens" and not model else "No measurements available"
            ax.text(0.5, 0.5, message, transform=ax.transAxes, ha="center")
            ax.set_yticks([])
            continue
        styles = TRANSPORT_STYLES if metric == "transport" else INTERACTIVE_STYLES
        for series, (label, color, linestyle, marker) in styles.items():
            values = results[metric].get(series)
            if not values:
                continue
            points = [values.get(step) for step in steps]
            missing = float("nan")
            if any(point and len(point) > 1 for point in points):
                ax.fill_between(
                    steps, [min(point) if point else missing for point in points],
                    [max(point) if point else missing for point in points], color=color, alpha=0.08,
                )
            ax.plot(
                steps, [mean(point) if point else missing for point in points],
                color=color, linestyle=linestyle, marker=marker, label=label,
            )
        ax.set_ylim(bottom=0, top=max(ax.get_ylim()[1], 1))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=6))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:,.0f}"))
        ax.legend(loc="best", fontsize=8)
    sample_note = (
        "One staged trajectory per route. " if max(trial_counts.values()) == 1
        else "Staged trajectories. Lines show means and shading shows observed ranges. "
    )
    notes = (
        sample_note +
        "Payload counts code, stdout, and stderr before transport framing.\n"
        "Transport counts sent and received bytes at each boundary. ∅ne SSH and MCP measure overlapping traffic."
    )
    return fig, axes, notes


def plot_tasks(summary, kind):
    model = kind == "model"
    groups = defaultdict(list)
    paired_keys = set()
    for trial in summary["trials"]:
        condition = (trial["mode"], None if model else trial["policy"])
        if condition not in STYLES:
            raise ValueError(f"Unknown {kind} condition {condition}")
        key = (trial["family"], trial["variant"], *condition)
        if key in paired_keys:
            raise ValueError(f"Duplicate {kind} trial {key}")
        paired_keys.add(key)
        groups[condition].append(trial)
    groups = {condition: groups[condition] for condition in STYLES if condition in groups}

    fig, (tokens, comparison) = plt.subplots(1, 2, figsize=(10.5, 4))
    if model:
        notes = plot_model(tokens, groups) + " " + plot_checks(comparison, groups, model=True)
    else:
        notes = plot_replay(tokens, groups)
        if plot_size(comparison, groups):
            notes += " Savings compare matched fresh and persistent trials at the same completed step."
        else:
            notes += " " + plot_checks(comparison, groups)
    return fig, (tokens, comparison), notes


def plot_evolution(summary):
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4))
    for mode, label, color, marker in (
        ("ssh", "Multiplexed SSH", "#345995", "o"),
        ("one", "Persistent REPL", "#28766F", "^"),
    ):
        trails = [trial["stages"] for trial in summary["trials"] if trial["mode"] == mode]
        cumulative = [list(accumulate(stage["command_tokens"] for stage in stages)) for stages in trails]
        axes[0].plot(range(1, 9), [mean(group) for group in zip(*cumulative, strict=True)],
                     color=color, marker=marker, label=label)
        axes[1].plot(range(1, 9), [sum(stage["success"] for stage in group)
                                 for group in zip(*trails, strict=True)],
                     color=color, marker=marker, label=label)
    for ax, title, ylabel in zip(axes, ("Submitted Python", "Strict passes"),
                                 ("Cumulative tokens per trajectory (mean)", "Trajectories passing"),
                                 strict=True):
        ax.set(title=title, xlabel="Checkpoint", ylabel=ylabel, xticks=range(1, 9))
        ax.set_ylim(bottom=0)
        ax.legend()
    axes[1].yaxis.set_major_locator(MaxNLocator(integer=True))
    return fig, axes, "Three tasks, two pairs per task. Curves include every recorded attempt."


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("output", type=Path, help="Destination ending in .svg or .png")
    args = parser.parse_args()
    if args.output.suffix.lower() not in (".svg", ".png"):
        parser.error("Output must have an .svg or .png extension")

    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if summary.get("suite") in {"reuse-fixed", "reuse-live", "reuse-trace", "wrapper"}:
        from supplement_curves import render
        render(summary, args.output)
        return
    kind = summary.get("kind", "replay")
    if kind not in ("replay", "model"):
        parser.error(f"Unknown summary kind {kind!r}")
    plt.rcParams.update({"svg.fonttype": "none", "axes.spines.top": False, "axes.spines.right": False})
    try:
        if summary.get("suite") == "evolution":
            fig, axes, notes = plot_evolution(summary)
        elif summary.get("suite") == "interactive":
            fig, axes, notes = plot_interactive(summary)
        else:
            fig, axes, notes = plot_tasks(summary, kind)
    except ValueError as exc:
        parser.error(str(exc))
    for ax in axes:
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#E1E5EB")
        ax.spines[["left", "bottom"]].set_color("#A8ACB3")
        ax.tick_params(length=0, pad=5)
    fig.text(0.025, 0.027, notes, fontsize=8, color="#454A52")
    fig.tight_layout(rect=(0, 0.07, 1, 1), w_pad=2.5)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)


if __name__ == "__main__":
    main()
