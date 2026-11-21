import { scaleLinear } from '@visx/scale'
import { Bar } from '@visx/shape'

const components = [
  { label: 'Codex CLI', color: 'indigo', kib: [147629, 0] },
  { label: 'SSH MCP bridge', color: 'wine', kib: [0, 5031] },
  { label: 'SSH MCP worker', color: 'rose', kib: [0, 6455] },
  { label: 'Python excluding musl', color: 'teal', kib: [10019, 11101] },
  { label: 'musl mappings', color: 'sand', kib: [300, 296] },
]
const modes = ['Codex CLI', 'SSH worker']
const totals = modes.map((_, index) => components.reduce((sum, row) => sum + row.kib[index], 0))
const height = scaleLinear({ domain: [0, Math.max(...totals)], range: [0, 242] })
const format = new Intl.NumberFormat('en-US')

export function MemoryFootprint() {
  return <div className="memory-footprint">
    <svg viewBox="0 0 360 302" aria-hidden="true">
      {modes.map((mode, index) => {
        const x = 98 + index * 164
        let offset = 0
        return <g key={mode}>
          <text x={x} y={262 - height(totals[index])} textAnchor="middle">{format.format(totals[index])}</text>
          {components.map(({ label, color, kib }) => {
            if (!kib[index]) return null
            offset += kib[index]
            return <Bar key={label} x={x - 66} y={270 - height(offset)} width={132}
              height={height(kib[index])} fill={`var(--tol-${color})`} />
          })}
          <text x={x} y={294} textAnchor="middle">{mode}</text>
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
}
