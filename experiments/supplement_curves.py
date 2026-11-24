"""Figures for the joined-table and command-wrapper supplements."""
from statistics import mean

import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator, StrMethodFormatter

MIB = 1024**2
STYLES = {
    "ssh-reparse": ("SSH · reread source", "#365D91", "o", "-"),
    "ssh-cache": ("SSH · disk cache", "#AE5529", "s", "--"),
    "one-warm": ("∅ne · keep object", "#167865", "^", "-."),
    "one-reset": ("∅ne · reset", "#795588", "x", ":"),
    "ssh": ("Direct SSH", "#365D91", "o", "-"),
    "one": ("∅ne over SSH", "#167865", "^", "-."),
    "pwsh-ssh": ("PowerShell → SSH", "#365D91", "o", "-"),
    "one-ssh": ("∅ne over SSH", "#167865", "^", "-."),
    "pwsh-docker": ("PowerShell → Docker", "#AE5529", "s", "--"),
}


def line(axis, key, x, y):
    label, color, marker, style = STYLES[key]
    axis.plot(x, y, label=label, color=color, marker=marker, linestyle=style, linewidth=1.7)


def panel(axis, title, unit, ticks, xlabel):
    axis.set(title=title, ylabel=unit, xlabel=xlabel, xticks=ticks)
    axis.set_ylim(bottom=0)
    axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
    axis.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))


def fixed(summary):
    sizes = sorted({trial["size_mib"] for trial in summary["trials"]})
    fig, rows = plt.subplots(len(sizes), 3, figsize=(12.8, 3.3 * len(sizes) + 0.7), squeeze=False)
    ticks = [1, 2, 4, 8]
    for size, axes in zip(sizes, rows):
        for trial in summary["trials"]:
            if trial["size_mib"] != size:
                continue
            pts = trial["cumulative"]
            x = [p["turns"] for p in pts]
            line(axes[0], trial["arm"], x, [p["source_read_bytes"] / MIB for p in pts])
            line(axes[1], trial["arm"], x, [(p["cache_read_bytes"] + p["cache_write_bytes"]) / MIB for p in pts])
            line(axes[2], trial["arm"], x, [p["target_after_kib"] / 1024 for p in pts])
        for ax, title in zip(axes, ("Source read, cumulative", "Cache I/O, cumulative", "Target RSS after query")):
            panel(ax, f"{size} MiB · {title}", "MiB", ticks, "Queries answered")
    fig.legend(*rows[0][0].get_legend_handles_labels(), loc="upper center", ncols=4, frameon=False)
    fig.subplots_adjust(top=.86, bottom=.15, hspace=.65, wspace=.37)
    fig.text(.04, .018, "File counters measure logical application bytes. Equal series overlap. RSS sums target processes after the action.\n"
             "One deterministic eight-query sequence per size and strategy.", fontsize=9)
    return fig, rows.flat


def live(summary):
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 4.5))
    ticks = [1, 2, 4, 8]
    for mode in ("ssh", "one"):
        trials = [t for t in summary["trials"] if t["mode"] == mode]
        if not trials:
            continue
        for ax, field in zip(axes, ("command_tokens", "input_tokens", "output_tokens")):
            values = []
            for n in ticks:
                totals = []
                for trial in trials:
                    stages = trial["stages"][:n]
                    if len(stages) != n:
                        raise ValueError("Incomplete trajectory in live plot")
                    totals.append(sum(s[field] if field == "command_tokens" else s["usage"][field] for s in stages))
                values.append(totals)
            line(ax, mode, ticks, [mean(v) for v in values])
            ax.fill_between(ticks, [min(v) for v in values], [max(v) for v in values],
                            color=STYLES[mode][1], alpha=.1)
    for ax, title in zip(axes, ("Submitted command tokens", "Provider input (includes cached)", "Provider output")):
        panel(ax, title, "Cumulative tokens", ticks, "Queries answered")
    axes[0].legend(frameon=False)
    fig.subplots_adjust(left=.075, right=.985, top=.88, bottom=.28, wspace=.4)
    fig.text(.045, .045, "Lines show means and shading shows the observed range across paired trajectories.", fontsize=9)
    return fig, axes


def render(summary, output):
    plt.rcParams.update({"svg.fonttype": "none", "axes.spines.top": False, "axes.spines.right": False})
    if summary["suite"] == "reuse-fixed":
        fig, axes = fixed(summary)
    elif summary["suite"] == "reuse-live":
        fig, axes = live(summary)
    elif summary["suite"] == "reuse-trace":
        fig, axes = trace(summary)
    else:
        fig, axes = wrapper(summary)
    for ax in axes:
        ax.set_axisbelow(True)
        ax.grid(axis="y", color="#E1E5EB")
        ax.spines[["left", "bottom"]].set_color("#A8ACB3")
        ax.tick_params(length=0, pad=5)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=165)
    plt.close(fig)


def wrapper(summary):
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.5))
    trials = summary["trials"]
    cases = sorted({(t["tier"], t["case"]) for t in trials})
    ticks = [tier for tier, _ in cases]
    for mode in ("pwsh-ssh", "one-ssh", "pwsh-docker"):
        path = [t for t in trials if t["mode"] == mode]
        if not path:
            continue
        mode_ticks = sorted({t["tier"] for t in path})
        costs, excess, wire = [], [], []
        for tier in mode_ticks:
            matched = [t for t in path if t["tier"] == tier]
            tokens = [sum(a.get("model_payload_tokens", 0) for a in t["attempts"]) for t in matched]
            costs.append(mean(tokens))
            excess.append(mean(value - t["reference_script_tokens"] for value, t in zip(tokens, matched)))
            if mode != "pwsh-docker":
                wire.append(mean(sum(a.get("transport", {}).get(key, 0)
                                     for a in t["attempts"] for key in ("ssh_tx_bytes", "ssh_rx_bytes"))
                                 for t in matched))
        line(axes[0], mode, mode_ticks, costs)
        line(axes[1], mode, mode_ticks, excess)
        if wire:
            line(axes[2], mode, mode_ticks, wire)
    for ax, (title, unit) in zip(axes, (("Submitted command + stdin", "Tokens"),
                                       ("Tokens above exact Bash source", "Tokens"),
                                       ("SSH relay traffic", "Bytes, both directions"))):
        panel(ax, title, unit, ticks, "Script fixture")
        ax.set_xticks(ticks, [case for _, case in cases], rotation=22, ha="right")
    if not axes[2].lines:
        axes[2].set_visible(False)
    if any(min(s.get_ydata()) < 0 for s in axes[1].lines):
        axes[1].autoscale(axis="y")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="upper center", ncols=3, frameon=False)
    fig.subplots_adjust(left=.065, right=.985, top=.80, bottom=.32, wspace=.37)
    label = "Model-selected commands" if summary["kind"] == "model" else "Fixed reference commands"
    if summary.get("carrier_policy") == "free_with_target_file":
        label += " (target script files allowed)"
    fig.text(.04, .026, label + ". Totals include retries. The SSH panel compares the same boundary on both SSH paths.", fontsize=9)
    return fig, axes

def trace(summary):
    fig, axes = plt.subplots(1, 3, figsize=(12.8, 4.5))
    ticks = [1, 2, 4, 8]
    for mode in ("ssh", "one"):
        trials = [t for t in summary["trials"] if t["mode"] == mode]
        if not trials:
            continue
        for i, ax in enumerate(axes):
            ys = []
            for n in ticks:
                pts = [next(p for p in t["cumulative"] if p["turns"] == n) for t in trials]
                if i == 0:
                    ys.append([p["actions_opening"]["source.csv"] for p in pts])
                else:
                    key = "target_peak_kib" if i == 1 else "target_after_kib"
                    ys.append([p[key] / 1024 for p in pts])
            line(ax, mode, ticks, [mean(group) for group in ys])
            ax.fill_between(ticks, [min(x) for x in ys], [max(x) for x in ys], color=STYLES[mode][1], alpha=.1)
    for ax, (title, unit) in zip(axes, (("Actions opening the fact table", "Cumulative actions"),
                                         ("Highest target RSS so far", "MiB"),
                                         ("Target RSS after query", "MiB"))):
        panel(ax, title, unit, ticks, "Queries answered")
    axes[0].legend(frameon=False)
    fig.subplots_adjust(left=.075, right=.985, top=.88, bottom=.28, wspace=.4)
    fig.text(.045, .045, "Replay of the recorded model actions. Lines show means and shading shows observed ranges. "
             "File-open counts do not measure bytes.", fontsize=9)
    return fig, axes
