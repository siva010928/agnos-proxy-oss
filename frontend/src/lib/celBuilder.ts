// Visual rule builder ↔ CEL bridge. Owned by us; generates the same CEL the
// backend's celpy evaluator runs, against OUR neutral request context.
//
// Exposed attributes (resolved server-side in core/guardrails/engine.py):
//   request.model · request.workspace · request.component · request.user
//   request.role  · request.message_text · request.message_length
//   request.headers["<name>"]

export type Attr =
  | 'model' | 'workspace' | 'component' | 'user' | 'role'
  | 'message_contains' | 'message_length' | 'header'

export type Op =
  | 'eq' | 'neq' | 'contains' | 'starts_with' | 'ends_with'
  | 'has_value' | 'gt' | 'lt' | 'in'

export interface Cond {
  type: 'cond'
  attribute: Attr
  header_name?: string
  operator: Op
  value: string
}
export interface Group {
  type: 'group'
  combinator: 'and' | 'or'
  children: Node[]
}
export type Node = Cond | Group

export const ATTR_LABELS: Record<Attr, string> = {
  model: 'Model',
  workspace: 'Workspace',
  component: 'Component',
  user: 'User',
  role: 'Role (first message)',
  message_contains: 'Message contains',
  message_length: 'Message length',
  header: 'Header',
}

export const OP_LABELS: Record<Op, string> = {
  eq: 'equals',
  neq: 'not equals',
  contains: 'contains',
  starts_with: 'starts with',
  ends_with: 'ends with',
  has_value: 'has value',
  gt: 'greater than',
  lt: 'less than',
  in: 'in (comma-separated)',
}

/** Allowed operators per attribute. Numbers can use gt/lt; message_contains is contains-only. */
export function opsForAttr(a: Attr): Op[] {
  if (a === 'message_contains') return ['contains']
  if (a === 'message_length') return ['gt', 'lt', 'eq']
  if (a === 'role') return ['eq', 'neq', 'in']
  if (a === 'header') return ['eq', 'neq', 'contains', 'has_value']
  return ['eq', 'neq', 'contains', 'starts_with', 'ends_with', 'has_value', 'in']
}

/** Default new condition (after picking attribute we re-validate operator). */
export function newCondition(): Cond {
  return { type: 'cond', attribute: 'model', operator: 'contains', value: '' }
}
export function newGroup(combinator: 'and' | 'or' = 'and'): Group {
  return { type: 'group', combinator, children: [] }
}

function jsonNum(v: string): boolean {
  return /^-?\d+(\.\d+)?$/.test(v.trim())
}
function lit(v: string, op: Op): string {
  if (op === 'in') {
    const items = v.split(',').map((x) => x.trim()).filter(Boolean)
    return '[' + items.map((x) => (jsonNum(x) ? x : JSON.stringify(x))).join(', ') + ']'
  }
  if (jsonNum(v)) return v.trim()
  return JSON.stringify(v)
}

function lhs(c: Cond): string {
  switch (c.attribute) {
    case 'model': return 'request.model'
    case 'workspace': return 'request.workspace'
    case 'component': return 'request.component'
    case 'user': return 'request.user'
    case 'role': return 'request.role'
    case 'message_contains': return 'request.message_text'
    case 'message_length': return 'request.message_length'
    case 'header': {
      const n = (c.header_name || '').toLowerCase().replace(/"/g, '\\"')
      return `request.headers["${n}"]`
    }
  }
}

function condCel(c: Cond): string {
  const L = lhs(c)
  switch (c.operator) {
    case 'eq': return `${L} == ${lit(c.value, c.operator)}`
    case 'neq': return `${L} != ${lit(c.value, c.operator)}`
    case 'contains': return `${L}.contains(${lit(c.value, c.operator)})`
    case 'starts_with': return `${L}.startsWith(${lit(c.value, c.operator)})`
    case 'ends_with': return `${L}.endsWith(${lit(c.value, c.operator)})`
    case 'has_value': return `size(${L}) > 0`
    case 'gt': return `${L} > ${lit(c.value, c.operator)}`
    case 'lt': return `${L} < ${lit(c.value, c.operator)}`
    case 'in': return `${L} in ${lit(c.value, c.operator)}`
  }
}

/** Build a CEL string from a builder tree. Empty group → "true" (apply to all). */
export function buildCel(node: Node): string {
  if (node.type === 'cond') return condCel(node)
  if (!node.children.length) return 'true'
  const parts = node.children.map(buildCel).filter(Boolean)
  if (parts.length === 1) return parts[0]
  const sep = node.combinator === 'and' ? ' && ' : ' || '
  return '(' + parts.join(sep) + ')'
}

/** True when the condition has all the inputs CEL needs (e.g., header_name when attr=header). */
export function condComplete(c: Cond): boolean {
  if (c.operator === 'has_value') {
    return c.attribute !== 'header' || !!(c.header_name || '').trim()
  }
  if (c.attribute === 'header' && !(c.header_name || '').trim()) return false
  return c.value.trim().length > 0
}

export function treeComplete(n: Node): boolean {
  if (n.type === 'cond') return condComplete(n)
  return n.children.length === 0 || n.children.every(treeComplete)
}

export const EMPTY_BUILDER: Group = { type: 'group', combinator: 'and', children: [] }
