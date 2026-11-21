import { scaleLinear } from '@visx/scale'
import { Bar } from '@visx/shape'

const components = [
  { label: 'Codex file-backed pages', color: 'indigo', kib: [116825, 0] },
  { label: 'Codex anonymous pages', color: 'purple', kib: [29164, 0] },
  { label: 'SSH MCP bridge', color: 'wine', kib: [0, 4927] },
  { label: 'SSH MCP worker', color: 'rose', kib: [0, 6421] },
  { label: 'Python excluding musl', color: 'teal', kib: [10039, 11085] },
  { label: 'musl mappings', color: 'sand', kib: [300, 300] },
]
const modes = ['Codex CLI target', 'SSH worker target']
const totals = modes.map((_, index) => components.reduce((sum, row) => sum + row.kib[index], 0))
const gap = totals[0] - totals[1]
const fileShare = Math.round(100 * components[0].kib[0] / gap)
const width = scaleLinear({ domain: [0, totals[0]], range: [0, 330] })
const format = new Intl.NumberFormat('en-US')

export function MemoryFootprint() {
  return <div className="memory-comparison">
    <p className="takeaway"><strong>{fileShare}% of the {format.format(gap)} KiB gap</strong> comes from file-backed pages in the Codex process.</p>
    <div className="memory-footprint">
      <svg viewBox="0 0 350 222" aria-hidden="true">
        {modes.map((mode, index) => {
          const y = 48 + 108 * index
          let offset = 0
          return <g key={mode}>
            <text x={10} y={y - 14}>{mode}</text>
            <text x={340} y={y - 14} textAnchor="end">{format.format(totals[index])} KiB</text>
            {components.map(({ label, color, kib }) => {
              if (!kib[index]) return null
              const x = 10 + width(offset)
              const segment = width(kib[index])
              offset += kib[index]
              return <Bar key={label} x={x} y={y} width={segment} height={36} fill={`var(--tol-${color})`} />
            })}
            {index === 0 && <text className="memory-bar-label" x={22} y={y + 23}>File-backed pages</text>}
          </g>
        })}
      </svg>
      <table aria-label="Median proportional set size by component in KiB">
        <thead><tr><th>Component</th><th>Codex</th><th>Worker</th></tr></thead>
        <tbody>
          {components.map(({ label, color, kib }) => <tr key={label}>
            <td><span className={`memory-swatch memory-swatch-${color}`} aria-hidden="true" />{label}</td>
            {kib.map((value, index) => <td key={modes[index]}>{value ? format.format(value) : '—'}</td>)}
          </tr>)}
          <tr><th scope="row">Stack total, KiB</th>{totals.map((value, index) => <td key={modes[index]}>{format.format(value)}</td>)}</tr>
        </tbody>
      </table>
    </div>
  </div>
}
