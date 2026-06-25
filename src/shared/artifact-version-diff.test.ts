import { describe, expect, it } from 'vitest'

import { buildArtifactVersionDiff } from './artifact-version-diff'

import type { ArtifactContent } from './types'

describe('buildArtifactVersionDiff', () => {
  it('compares document markdown content', () => {
    const oldContent: ArtifactContent = { type: 'document', format: 'markdown', content: '# Old' }
    const newContent: ArtifactContent = { type: 'document', format: 'markdown', content: '# New' }

    const result = buildArtifactVersionDiff(oldContent, newContent)

    expect(result).toEqual({
      status: 'ready',
      sections: [
        {
          key: 'document',
          title: 'document.md',
          oldText: '# Old',
          newText: '# New',
        },
      ],
    })
  })

  it('compares web app file unions and entry changes', () => {
    const oldContent: ArtifactContent = {
      type: 'web_app',
      entry: 'index.html',
      files: {
        'index.html': '<main>old</main>',
        'style.css': 'body { color: black; }',
      },
    }
    const newContent: ArtifactContent = {
      type: 'web_app',
      entry: 'app.html',
      files: {
        'app.html': '<main>new</main>',
        'style.css': 'body { color: white; }',
      },
    }

    const result = buildArtifactVersionDiff(oldContent, newContent)

    expect(result.status).toBe('ready')
    if (result.status !== 'ready') return
    expect(result.sections.map((section) => section.key)).toEqual([
      '__entry__',
      'app.html',
      'index.html',
      'style.css',
    ])
    expect(result.sections[1]?.oldText).toBe('')
    expect(result.sections[2]?.newText).toBe('')
  })

  it('uses stable json for slide deck comparison', () => {
    const oldContent: ArtifactContent = {
      type: 'ppt',
      slides: [{ title: 'Intro', bullets: ['A'] }],
      theme: { textBody: '222222', primary: '111111' },
    }
    const newContent: ArtifactContent = {
      type: 'ppt',
      theme: { primary: '333333', textBody: '222222' },
      slides: [{ title: 'Intro', bullets: ['A', 'B'] }],
    }

    const result = buildArtifactVersionDiff(oldContent, newContent)

    expect(result.status).toBe('ready')
    if (result.status !== 'ready') return
    const section = result.sections[0]
    expect(section).toBeDefined()
    if (!section) return
    expect(section.oldText).toContain('"primary": "111111"')
    expect(section.oldText.indexOf('"primary"')).toBeLessThan(section.oldText.indexOf('"textBody"'))
    expect(section.newText).toContain('"primary": "333333"')
  })

  it('compares code file metadata instead of live file contents', () => {
    const oldContent: ArtifactContent = {
      type: 'code_file',
      workspacePath: 'src/a.ts',
      language: 'typescript',
      sizeBytes: 10,
      checksum: 'old',
    }
    const newContent: ArtifactContent = {
      type: 'code_file',
      workspacePath: 'src/a.ts',
      language: 'typescript',
      sizeBytes: 12,
      checksum: 'new',
    }

    const result = buildArtifactVersionDiff(oldContent, newContent)

    expect(result.status).toBe('ready')
    if (result.status !== 'ready') return
    expect(result.sections[0]?.oldText).toContain('checksum: old')
    expect(result.sections[0]?.newText).toContain('checksum: new')
  })

  it('rejects unsupported or mismatched comparisons', () => {
    const image: ArtifactContent = { type: 'image', url: 'data:image/png;base64,abc', alt: '' }
    const document: ArtifactContent = { type: 'document', format: 'markdown', content: 'doc' }

    expect(buildArtifactVersionDiff(image, image).status).toBe('unsupported')
    expect(buildArtifactVersionDiff(image, document).status).toBe('unsupported')
  })
})
