import { scaleLinear } from '@visx/scale'
import { LinePath } from '@visx/shape'

// Cumulative o200k_base counts from experiments/command_token_suite.py.
const tasks = [
  { label: 'CSV join', ssh: [171, 191, 231, 255], repl: [143, 159, 195, 215] },
  { label: 'HTTP logs', ssh: [37, 57, 82, 109], repl: [20, 36, 57, 80] },
  { label: 'Dependencies', ssh: [99, 111, 132, 150], repl: [70, 78, 95, 109] },
  { label: 'Configuration', ssh: [54, 68, 80, 101], repl: [34, 44, 52, 69] },
  { label: 'File inventory', ssh: [38, 56, 78, 100], repl: [21, 35, 53, 71] },
  { label: 'Independent commands', ssh: [7, 15, 28, 35], repl: [7, 15, 28, 35] },
]
const modes = ['ssh', 'repl'] as const
const sum = (mode: typeof modes[number], round: number) => tasks.reduce((n, t) => n + t[mode][round], 0)
const x = scaleLinear({domain: [0, 3], range: [50, 292]})
const y = scaleLinear({domain: [0, 800], range: [205, 24]})

export function CommandTokens() {
  return <>
    <p>SSH reused its connection and started fresh Python for each question. ∅ne kept one Python interpreter open.</p>
    <div className="command-comparison">
      <table aria-label="Submitted Python tokens after four questions"><thead><tr><th>Task</th><th>SSH</th><th>REPL</th></tr></thead>
        <tbody>{tasks.map(t => <tr key={t.label}><td>{t.label}</td><td>{t.ssh[3]}</td><td>{t.repl[3]}</td></tr>)}
          <tr><th>Total</th>{modes.map(m => <th key={m}>{sum(m, 3)}</th>)}</tr></tbody>
      </table>
      <svg viewBox="0 0 400 264" role="img" aria-label={modes.map(m => m + ' cumulative tokens: ' + [0,1,2,3].map(i => sum(m,i)).join(', ')).join('. ')}>
        <text x="50" y="15">Cumulative command tokens</text>
        {[0, 200, 400, 600, 800].map(n => <g key={n}><line x1="48" x2="292" y1={y(n)} y2={y(n)} stroke="var(--color-border)"/><text x="40" y={y(n)+5} textAnchor="end">{n}</text></g>)}
        {[0,1,2,3].map(i => <text key={i} x={x(i)} y="228" textAnchor="middle">{i+1}</text>)}
        <text x="170" y="253" textAnchor="middle">Questions per task</text>
        {modes.map(m => <g key={m} fill={m === 'ssh' ? 'var(--color-primary)' : 'var(--color-success)'}>
          <LinePath data={[0,1,2,3]} x={x} y={i => y(sum(m,i))} stroke={m === 'ssh' ? 'var(--color-primary)' : 'var(--color-success)'} strokeWidth={3} strokeDasharray={m === 'ssh' ? undefined : '6 3'}/>
          {[0,1,2,3].map(i => <circle key={i} cx={x(i)} cy={y(sum(m,i))} r="3"/>)}
          <text x="301" y={y(sum(m,3))+5}>{m.toUpperCase()} {sum(m,3)}</text>
        </g>)}
      </svg>
    </div>
  </>
}
