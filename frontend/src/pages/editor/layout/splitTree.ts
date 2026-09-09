/**
 * Split-layout tree (the VS Code model).
 *
 * A node is either a leaf (one panel) or a split along one axis holding ordered
 * children with relative weights. The tree is a pure data structure: every
 * function here returns a new tree, so it can be stored, persisted and unit
 * tested without React.
 */

export type PanelId = string
export type SplitDir = 'row' | 'col'
export type DropSide = 'left' | 'right' | 'top' | 'bottom' | 'center'

export type TreeNode =
  | { kind: 'leaf'; id: PanelId }
  | { kind: 'split'; dir: SplitDir; children: TreeNode[]; weights: number[] }

export interface PanelMinSize {
  w: number
  h: number
}

/** gap between two siblings, in px (the draggable divider) */
export const DIVIDER = 4

export function makeLeaf(id: PanelId): TreeNode {
  return { kind: 'leaf', id }
}

/** a single row holding the given panels with equal weights */
export function makeRow(ids: PanelId[], weights?: number[]): TreeNode {
  if (ids.length === 1) return makeLeaf(ids[0])
  const w = weights ?? ids.map(() => 1 / ids.length)
  return { kind: 'split', dir: 'row', children: ids.map(makeLeaf), weights: w }
}

export function leafIds(node: TreeNode): PanelId[] {
  return node.kind === 'leaf' ? [node.id] : node.children.flatMap(leafIds)
}

export function containsLeaf(node: TreeNode, id: PanelId): boolean {
  return leafIds(node).includes(id)
}

function normalize(weights: number[]): number[] {
  const total = weights.reduce((s, w) => s + w, 0)
  if (total <= 0) return weights.map(() => 1 / weights.length)
  return weights.map(w => w / total)
}

/** drop a leaf and collapse splits that are left with a single child */
export function removeLeaf(node: TreeNode, id: PanelId): TreeNode | null {
  if (node.kind === 'leaf') return node.id === id ? null : node
  const children: TreeNode[] = []
  const weights: number[] = []
  node.children.forEach((child, i) => {
    const next = removeLeaf(child, id)
    if (next) {
      children.push(next)
      weights.push(node.weights[i] ?? 1)
    }
  })
  if (children.length === 0) return null
  if (children.length === 1) return children[0]
  return { kind: 'split', dir: node.dir, children, weights: normalize(weights) }
}

/** keep only the leaves accepted by `keep`, collapsing as needed */
export function prune(node: TreeNode, keep: (id: PanelId) => boolean): TreeNode | null {
  if (node.kind === 'leaf') return keep(node.id) ? node : null
  const children: TreeNode[] = []
  const weights: number[] = []
  node.children.forEach((child, i) => {
    const next = prune(child, keep)
    if (next) {
      children.push(next)
      weights.push(node.weights[i] ?? 1)
    }
  })
  if (children.length === 0) return null
  if (children.length === 1) return children[0]
  return { kind: 'split', dir: node.dir, children, weights: normalize(weights) }
}

function swapLeaves(node: TreeNode, a: PanelId, b: PanelId): TreeNode {
  if (node.kind === 'leaf') {
    if (node.id === a) return makeLeaf(b)
    if (node.id === b) return makeLeaf(a)
    return node
  }
  return { ...node, children: node.children.map(c => swapLeaves(c, a, b)) }
}

function insertBeside(node: TreeNode, targetId: PanelId, draggedId: PanelId, side: DropSide): TreeNode {
  if (node.kind === 'leaf') {
    if (node.id !== targetId) return node
    const horizontal = side === 'left' || side === 'right'
    const before = side === 'left' || side === 'top'
    return {
      kind: 'split',
      dir: horizontal ? 'row' : 'col',
      children: before ? [makeLeaf(draggedId), node] : [node, makeLeaf(draggedId)],
      weights: [0.5, 0.5],
    }
  }

  const idx = node.children.findIndex(c => c.kind === 'leaf' && c.id === targetId)
  const sameAxis = (side === 'left' || side === 'right') === (node.dir === 'row')

  // the target is a direct child and the split already runs along the drop
  // axis: insert as a sibling and share the target's weight (keeps the tree flat)
  if (idx >= 0 && sameAxis) {
    const at = side === 'left' || side === 'top' ? idx : idx + 1
    const children = [...node.children]
    const weights = [...node.weights]
    const half = (weights[idx] ?? 0.5) / 2
    weights[idx] = half
    children.splice(at, 0, makeLeaf(draggedId))
    weights.splice(at, 0, half)
    return { kind: 'split', dir: node.dir, children, weights }
  }

  const childIndex = node.children.findIndex(c => containsLeaf(c, targetId))
  if (childIndex < 0) return node
  const children = [...node.children]
  children[childIndex] = insertBeside(children[childIndex], targetId, draggedId, side)
  return { kind: 'split', dir: node.dir, children, weights: node.weights }
}

/**
 * Move `draggedId` next to `targetId` (or swap the two when side is 'center').
 * The dragged leaf is detached first, so the result never contains it twice.
 */
export function movePanel(
  node: TreeNode,
  draggedId: PanelId,
  targetId: PanelId,
  side: DropSide,
): TreeNode {
  if (draggedId === targetId) return node
  if (side === 'center') return swapLeaves(node, draggedId, targetId)
  const detached = removeLeaf(node, draggedId) ?? makeLeaf(draggedId)
  return insertBeside(detached, targetId, draggedId, side)
}

/** overwrite some weights of the split found at `path` */
export function setWeights(node: TreeNode, path: number[], updates: Record<number, number>): TreeNode {
  if (node.kind === 'leaf') return node
  if (path.length === 0) {
    const weights = [...node.weights]
    for (const [key, value] of Object.entries(updates)) weights[Number(key)] = value
    return { ...node, weights }
  }
  const [head, ...rest] = path
  const children = [...node.children]
  children[head] = setWeights(children[head], rest, updates)
  return { ...node, children, weights: node.weights }
}

/** smallest width/height the subtree can take along the given axis */
export function minAlong(
  node: TreeNode,
  axis: SplitDir,
  mins: (id: PanelId) => PanelMinSize,
): number {
  if (node.kind === 'leaf') {
    const m = mins(node.id)
    return axis === 'row' ? m.w : m.h
  }
  const values = node.children.map(c => minAlong(c, axis, mins))
  if (node.dir === axis) {
    return values.reduce((s, v) => s + v, 0) + DIVIDER * (values.length - 1)
  }
  return Math.max(...values)
}

/** true when the subtree holds at least one visible leaf */
export function hasVisible(node: TreeNode, visible: (id: PanelId) => boolean): boolean {
  return node.kind === 'leaf' ? visible(node.id) : node.children.some(c => hasVisible(c, visible))
}

/** index of the child of the split at `path` that holds the given leaf */
export function childIndexPath(node: TreeNode, id: PanelId): number[] | null {
  if (node.kind === 'leaf') return node.id === id ? [] : null
  for (let i = 0; i < node.children.length; i++) {
    const rest = childIndexPath(node.children[i], id)
    if (rest) return [i, ...rest]
  }
  return null
}