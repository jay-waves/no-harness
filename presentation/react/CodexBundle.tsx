import { scaleLinear } from '@visx/scale'
import { Bar } from '@visx/shape'

const components = [
  { name: 'Bubblewrap and package metadata', bytes: 508206, color: 'grey' },
  { name: 'Bundled ripgrep', bytes: 5408904, color: 'olive' },
  { name: 'Voice shared libraries', bytes: 12291152, color: 'sand' },
  { name: 'Voice helper and other voice files', bytes: 40153216, color: 'rose' },
  { name: 'Code Mode host', bytes: 70122008, color: 'purple' },
  { name: 'Execution server', bytes: 74516208, color: 'teal' },
  { name: 'Codex CLI executable', bytes: 298309560, color: 'indigo' },
]

const total = components.reduce((sum, component) => sum + component.bytes, 0)
const height = scaleLinear({ domain: [0, total], range: [0, 324] })
const formatMiB = (bytes: number) => (bytes / 2 ** 20).toFixed(2)

export function CodexBundle() {
  let top = 14
  return <svg className="codex-bundle" viewBox="0 0 904 364" role="img"
    aria-label={`Unpacked Codex bundle, ${formatMiB(total)} MiB. ${components.map(({ name, bytes }) => `${name}: ${formatMiB(bytes)} MiB`).join('. ')}`}>
    {components.map(({ name, bytes, color }, index) => {
      const segment = height(bytes)
      const start = top
      top += segment
      const anchorY = start + segment / 2
      const labelY = 18 + index * 48
      return <g key={name}>
        <Bar x={98} y={start} width={202} height={segment} fill={`var(--tol-${color})`} />
        <polyline points={`300,${anchorY} 326,${anchorY} 390,${labelY} 419,${labelY}`} />
        <circle cx={419} cy={labelY} r={3} fill={`var(--tol-${color})`} />
        <text x={435} y={labelY} dominantBaseline="middle">{name}</text>
        <text className="codex-bundle-value" x={853} y={labelY} dominantBaseline="middle" textAnchor="end">
          {formatMiB(bytes)} MiB
        </text>
      </g>
    })}
    <text className="codex-bundle-value" x={199} y={359} textAnchor="middle">{formatMiB(total)} MiB total</text>
  </svg>
}
