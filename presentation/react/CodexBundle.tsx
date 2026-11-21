import { scaleLinear } from '@visx/scale'
import { Bar } from '@visx/shape'

const components = [
  { name: 'Codex CLI executable', bytes: 298309560, color: 'indigo' },
  { name: 'Code Mode host', bytes: 70122008, color: 'purple' },
  { name: 'Exec server', bytes: 74516208, color: 'teal' },
  { name: 'Voice helper and resources', bytes: 40153216, color: 'rose' },
  { name: 'Voice shared libraries', bytes: 12291152, color: 'sand' },
  { name: 'Bundled ripgrep', bytes: 5408904, color: 'olive' },
  { name: 'Bubblewrap and package metadata', bytes: 508206, color: 'grey' },
]

const workerBytes = 9617568
const alpineBaseMiB = 49.77
const total = components.reduce((sum, component) => sum + component.bytes, 0)
const height = scaleLinear({ domain: [0, total], range: [0, 253] })
const formatMiB = (bytes: number) => (bytes / 2 ** 20).toFixed(2)
const baseline = 305
const baseHeight = height(alpineBaseMiB * 2 ** 20)
const left = 252
const barWidth = 210
const workerLeft = 21

export function CodexBundle() {
  let top = baseline - height(total)
  const workerTop = baseline - height(workerBytes)

  return <svg className="codex-bundle" viewBox="0 0 904 380" role="img"
    aria-label={`Added disk footprint. Codex bundle, ${formatMiB(total)} MiB. SSH worker, ${formatMiB(workerBytes)} MiB. Both targets include a shared Alpine base of ${alpineBaseMiB} MiB. ${components.map(({ name, bytes }) => `${name}: ${formatMiB(bytes)} MiB`).join('. ')}`}>
    {[workerLeft, left].map(x => <g key={x}>
      <Bar x={x} y={baseline} width={barWidth} height={baseHeight} fill="var(--tol-grey)" />
      <text className="codex-bundle-value" x={x + barWidth / 2} y={baseline + baseHeight / 2}
        textAnchor="middle" dominantBaseline="middle">Alpine base · {alpineBaseMiB} MiB</text>
    </g>)}
    <text className="codex-bundle-total" x={workerLeft + barWidth / 2} y={243} textAnchor="middle">{formatMiB(workerBytes)} MiB</text>
    <polyline points={`126,255 126,${workerTop}`} />
    <Bar x={workerLeft} y={workerTop} width={barWidth} height={height(workerBytes)} fill="var(--tol-green)" />
    <text x={workerLeft + barWidth / 2} y={baseline + baseHeight + 27} textAnchor="middle">SSH MCP worker</text>

    <text className="codex-bundle-total" x={left + barWidth / 2} y={37} textAnchor="middle">{formatMiB(total)} MiB</text>
    {components.map(({ name, bytes, color }, index) => {
      const segment = height(bytes)
      const start = top
      top += segment
      const anchorY = start + segment / 2
      const labelY = 55 + index * 41
      const edge = left + barWidth
      return <g key={name}>
        <Bar x={left} y={start} width={barWidth} height={segment} fill={`var(--tol-${color})`} />
        <polyline points={`${edge},${anchorY} 480,${anchorY} 520,${labelY} 543,${labelY}`} />
        <circle cx={543} cy={labelY} r={3.5} fill={`var(--tol-${color})`} />
        <text x={558} y={labelY - 4}>{name}</text>
        <text className="codex-bundle-value" x={558} y={labelY + 16}>
          {formatMiB(bytes)} MiB
        </text>
      </g>
    })}
    <text x={left + barWidth / 2} y={baseline + baseHeight + 27} textAnchor="middle">Standalone Codex</text>
  </svg>
}
