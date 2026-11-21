import { scaleLinear } from '@visx/scale'
import { Bar } from '@visx/shape'

const components = [
  { label: 'File-backed Codex pages', color: 'indigo', kib: [116825, 0] },
  { label: 'Anonymous Codex pages', color: 'purple', kib: [29164, 0] },
  { label: 'SSH MCP bridge', color: 'wine', kib: [0, 4927] },
  { label: 'SSH MCP worker', color: 'rose', kib: [0, 6421] },
  { label: 'Python (excl. musl)', color: 'teal', kib: [10039, 11085] },
  { label: 'musl', color: 'sand', kib: [300, 300] },
]
const modes = ['Codex CLI target', 'SSH worker target']
const totals = modes.map((_, index) => components.reduce((sum, row) => sum + row.kib[index], 0))
const gap = totals[0] - totals[1]
const fileShare = Math.round(100 * components[0].kib[0] / gap)
const height = scaleLinear({ domain: [0, totals[0]], range: [0, 244] })
const format = new Intl.NumberFormat('en-US')
const baseline = 307
const barWidth = 112

export function MemoryFootprint() {
  const summaries = modes.map((mode, index) =>
    `${mode}, ${format.format(totals[index])} KiB total. ${components
      .filter(({ kib }) => kib[index] > 0)
      .map(({ label, kib }) => `${label}: ${format.format(kib[index])} KiB`).join('. ')}.`
  )
  return <div className="memory-comparison">
    <p className="takeaway"><strong>{fileShare}% of the {format.format(gap)} KiB gap</strong> comes from file-backed pages in the Codex process.</p>
    <svg className="memory-footprint" viewBox="0 0 960 325" role="img" aria-label={summaries.join(' ')}>
      <line className="memory-baseline" x1={294} x2={666} y1={baseline} y2={baseline} />
      {modes.map((mode, index) => {
        const side = index === 0 ? -1 : 1
        const x = index === 0 ? 332 : 516
        const center = x + barWidth / 2
        const edge = side < 0 ? x : x + barWidth
        const dot = edge + side * 47
        const labelX = dot + side * 12
        let top = baseline - height(totals[index])
        return <g key={mode}>
          <text x={center} y={20} textAnchor="middle">{mode}</text>
          <text className="memory-value" x={center} y={43} textAnchor="middle">{format.format(totals[index])} KiB</text>
          {components.filter(({ kib }) => kib[index] > 0).map(({ label, color, kib }, row) => {
            const segment = height(kib[index])
            const start = top
            top += segment
            const anchorY = start + segment / 2
            const labelY = (index === 0 ? 110 : 131) + row * 50
            const fill = `var(--tol-${color})`
            const textAnchor = side < 0 ? 'end' : 'start'
            return <g key={label}>
              <Bar x={x} y={start} width={barWidth} height={segment} fill={fill} />
              <polyline className="memory-leader" points={`${edge},${anchorY} ${edge + side * 10},${anchorY} ${dot - side * 9},${labelY} ${dot},${labelY}`} />
              <circle cx={dot} cy={labelY} r={3} fill={fill} />
              <text x={labelX} y={labelY - 4} textAnchor={textAnchor}>{label}</text>
              <text className="memory-value" x={labelX} y={labelY + 16} textAnchor={textAnchor}>
                {format.format(kib[index])} KiB
              </text>
            </g>
          })}
        </g>
      })}
    </svg>
  </div>
}
