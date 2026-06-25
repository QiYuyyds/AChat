import { describe, expect, it } from 'vitest'

import { groupArtifactVersions } from './artifact-groups'

describe('groupArtifactVersions', () => {
  it('groups artifact versions by parent chain', () => {
    const groups = groupArtifactVersions([
      { id: 'art_3', parentArtifactId: 'art_2', title: 'Doc', version: 3, createdAt: 300 },
      { id: 'art_other', parentArtifactId: null, title: 'Other', version: 1, createdAt: 250 },
      { id: 'art_1', parentArtifactId: null, title: 'Doc', version: 1, createdAt: 100 },
      { id: 'art_2', parentArtifactId: 'art_1', title: 'Doc', version: 2, createdAt: 200 },
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0]).toMatchObject({ rootId: 'art_1', latest: { id: 'art_3' } })
    expect(groups[0].versions.map((v) => v.id)).toEqual(['art_1', 'art_2', 'art_3'])
    expect(groups[1]).toMatchObject({ rootId: 'art_other', latest: { id: 'art_other' } })
  })

  it('falls back to the item as root when parent metadata is missing', () => {
    const groups = groupArtifactVersions([
      { id: 'art_2', parentArtifactId: 'art_missing', title: 'Doc', version: 2, createdAt: 200 },
    ])

    expect(groups).toEqual([
      {
        rootId: 'art_2',
        latest: { id: 'art_2', parentArtifactId: 'art_missing', title: 'Doc', version: 2, createdAt: 200 },
        versions: [
          { id: 'art_2', parentArtifactId: 'art_missing', title: 'Doc', version: 2, createdAt: 200 },
        ],
      },
    ])
  })
})
