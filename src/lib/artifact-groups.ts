export interface ArtifactVersionListItem {
  id: string
  parentArtifactId?: string | null
  title: string
  version: number
  createdAt: number
}

export interface ArtifactVersionGroup<T extends ArtifactVersionListItem> {
  rootId: string
  latest: T
  versions: T[]
}

export function groupArtifactVersions<T extends ArtifactVersionListItem>(
  items: T[],
): ArtifactVersionGroup<T>[] {
  const byId = new Map(items.map((item) => [item.id, item]))
  const groups = new Map<string, T[]>()

  for (const item of items) {
    const rootId = findRootId(item, byId)
    const group = groups.get(rootId)
    if (group) group.push(item)
    else groups.set(rootId, [item])
  }

  return Array.from(groups.entries())
    .map(([rootId, versions]) => {
      const sorted = [...versions].sort(compareVersionAsc)
      return {
        rootId,
        latest: sorted.reduce((latest, item) =>
          compareVersionAsc(latest, item) <= 0 ? item : latest,
        ),
        versions: sorted,
      }
    })
    .sort((a, b) => b.latest.createdAt - a.latest.createdAt)
}

function findRootId<T extends ArtifactVersionListItem>(item: T, byId: Map<string, T>): string {
  let current = item
  const visited = new Set<string>([item.id])

  while (current.parentArtifactId) {
    if (visited.has(current.parentArtifactId)) break
    const parent = byId.get(current.parentArtifactId)
    if (!parent) break
    visited.add(parent.id)
    current = parent
  }

  return current.id
}

function compareVersionAsc<T extends ArtifactVersionListItem>(a: T, b: T): number {
  return a.version - b.version || a.createdAt - b.createdAt
}
