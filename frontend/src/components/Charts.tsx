import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'

const AX = { fill: '#9CA3AF', fontSize: 12 }
const GRID = '#232A3A'
const tip = { background: '#14161D', border: '1px solid #242836', borderRadius: 8, fontSize: 12 }

/** Compact axis formatters. */
export function fmtCount(n: number): string {
  const v = Number(n) || 0
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(1).replace(/\.0$/, '') + 'B'
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(v)
}
export function fmtUSDAxis(n: number): string {
  const v = Number(n) || 0
  if (Math.abs(v) >= 1000) return '$' + (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
  if (Math.abs(v) >= 1) return '$' + v.toFixed(0)
  if (Math.abs(v) >= 0.01) return '$' + v.toFixed(2)
  return '$' + v.toFixed(4)
}
export function fmtMsAxis(n: number): string {
  const v = Number(n) || 0
  if (v >= 1000) return (v / 1000).toFixed(1).replace(/\.0$/, '') + 's'
  return Math.round(v) + 'ms'
}

export function Sparkline({ data, color = '#6366F1' }: { data: number[]; color?: string }) {
  const d = data.map((v, i) => ({ i, v }))
  return (
    <ResponsiveContainer width="60%" height={32}>
      <LineChart data={d}>
        <Line type="monotone" dataKey="v" stroke={color} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

export function AreaTime({ data, keys, colors, height = 280, yFmt, yWidth = 50 }:
  { data: any[]; keys: string[]; colors: string[]; height?: number;
    yFmt?: (n: number) => string; yWidth?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data}>
        <defs>
          {keys.map((k, i) => (
            <linearGradient id={`g-${k}`} key={k} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colors[i]} stopOpacity={0.5} />
              <stop offset="100%" stopColor={colors[i]} stopOpacity={0.04} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={AX} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={28} />
        <YAxis tick={AX} tickLine={false} axisLine={false} width={yWidth}
               tickFormatter={yFmt as any} />
        <Tooltip contentStyle={tip} formatter={yFmt as any} />
        {keys.map((k, i) => (
          <Area key={k} type="monotone" dataKey={k} stackId="1" stroke={colors[i]}
            fill={`url(#g-${k})`} strokeWidth={1.5} isAnimationActive />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function Bars({ data, dataKey, color = '#6366F1', height = 280, horizontal = false }:
  { data: any[]; dataKey: string; color?: string; height?: number; horizontal?: boolean }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} layout={horizontal ? 'vertical' : 'horizontal'}>
        <CartesianGrid stroke={GRID} vertical={false} />
        {horizontal
          ? (<><XAxis type="number" tick={AX} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" tick={AX} width={120} axisLine={false} tickLine={false} /></>)
          : (<><XAxis dataKey="name" tick={AX} axisLine={{ stroke: GRID }} tickLine={false} interval={0} angle={-12} height={48} textAnchor="end" />
              <YAxis tick={AX} axisLine={false} tickLine={false} width={42} /></>)}
        <Tooltip contentStyle={tip} cursor={{ fill: '#ffffff08' }} />
        <Bar dataKey={dataKey} fill={color} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function Donut({ data, height = 260 }: { data: { name: string; value: number; color: string }[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <PieChart>
        <Pie data={data} dataKey="value" nameKey="name" innerRadius="58%" outerRadius="82%" paddingAngle={2} stroke="none">
          {data.map((d, i) => <Cell key={i} fill={d.color} />)}
        </Pie>
        <Tooltip contentStyle={tip} />
      </PieChart>
    </ResponsiveContainer>
  )
}

export function MultiLine({ data, keys, colors, height = 260, yFmt, yWidth = 50 }:
  { data: any[]; keys: string[]; colors: string[]; height?: number;
    yFmt?: (n: number) => string; yWidth?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={AX} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={28} />
        <YAxis tick={AX} tickLine={false} axisLine={false} width={yWidth}
               tickFormatter={yFmt as any} />
        <Tooltip contentStyle={tip} formatter={yFmt as any} />
        {keys.map((k, i) => (
          <Line key={k} type="monotone" dataKey={k} stroke={colors[i]} strokeWidth={1.5} dot={false} isAnimationActive />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}
