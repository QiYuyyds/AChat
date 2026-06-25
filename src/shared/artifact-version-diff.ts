import type { ArtifactContent } from '@/shared/types'

export interface ArtifactVersionDiffSection {
  key: string
  title: string
  oldText: string
  newText: string
}

export type ArtifactVersionDiff =
  | { status: 'ready'; sections: ArtifactVersionDiffSection[] }
  | { status: 'unsupported'; reason: string }

export function buildArtifactVersionDiff(
  oldContent: ArtifactContent,
  newContent: ArtifactContent,
): ArtifactVersionDiff {
  if (oldContent.type !== newContent.type) {
    return { status: 'unsupported', reason: 'Selected versions use different artifact types.' }
  }

  switch (oldContent.type) {
    case 'document': {
      const next = newContent as Extract<ArtifactContent, { type: 'document' }>
      return {
        status: 'ready',
        sections: [
          {
            key: 'document',
            title: 'document.md',
            oldText: oldContent.content,
            newText: next.content,
          },
        ],
      }
    }
    case 'web_app':
      return buildWebAppDiff(oldContent, newContent as Extract<ArtifactContent, { type: 'web_app' }>)
    case 'diagram': {
      const next = newContent as Extract<ArtifactContent, { type: 'diagram' }>
      return {
        status: 'ready',
        sections: [
          {
            key: 'diagram',
            title: 'diagram.mmd',
            oldText: oldContent.source,
            newText: next.source,
          },
        ],
      }
    }
    case 'ppt': {
      const next = newContent as Extract<ArtifactContent, { type: 'ppt' }>
      return {
        status: 'ready',
        sections: [
          {
            key: 'slides',
            title: 'slides.json',
            oldText: stableStringify(oldContent),
            newText: stableStringify(next),
          },
        ],
      }
    }
    case 'code_file': {
      const next = newContent as Extract<ArtifactContent, { type: 'code_file' }>
      return {
        status: 'ready',
        sections: [
          {
            key: 'metadata',
            title: 'workspace file metadata',
            oldText: codeFileMetadata(oldContent),
            newText: codeFileMetadata(next),
          },
        ],
      }
    }
    case 'project': {
      const next = newContent as Extract<ArtifactContent, { type: 'project' }>
      return {
        status: 'ready',
        sections: [
          {
            key: 'files',
            title: 'project files',
            oldText: projectMetadata(oldContent),
            newText: projectMetadata(next),
          },
        ],
      }
    }
    case 'image':
      return { status: 'unsupported', reason: 'Image artifacts do not have stored text content to compare.' }
    case 'diff':
      return { status: 'unsupported', reason: 'Legacy diff artifacts are preview-only and are not compared.' }
  }
}

function buildWebAppDiff(
  oldContent: Extract<ArtifactContent, { type: 'web_app' }>,
  newContent: Extract<ArtifactContent, { type: 'web_app' }>,
): ArtifactVersionDiff {
  const sections: ArtifactVersionDiffSection[] = []

  if (oldContent.entry !== newContent.entry) {
    sections.push({
      key: '__entry__',
      title: 'entry file',
      oldText: oldContent.entry,
      newText: newContent.entry,
    })
  }

  const filenames = Array.from(
    new Set([...Object.keys(oldContent.files), ...Object.keys(newContent.files)]),
  ).sort((a, b) => a.localeCompare(b))

  for (const filename of filenames) {
    sections.push({
      key: filename,
      title: filename,
      oldText: oldContent.files[filename] ?? '',
      newText: newContent.files[filename] ?? '',
    })
  }

  return { status: 'ready', sections }
}

function codeFileMetadata(content: Extract<ArtifactContent, { type: 'code_file' }>): string {
  return [
    `workspacePath: ${content.workspacePath}`,
    `language: ${content.language}`,
    `sizeBytes: ${content.sizeBytes}`,
    `checksum: ${content.checksum}`,
  ].join('\n')
}

function projectMetadata(content: Extract<ArtifactContent, { type: 'project' }>): string {
  const files = [...content.files].sort((a, b) => a.path.localeCompare(b.path))
  return [
    `agentId: ${content.agentId ?? '-'}`,
    `taskId: ${content.taskId ?? '-'}`,
    `files: ${files.length}`,
    '',
    ...files.map((file) => `${file.path}\t${file.sizeBytes}`),
  ].join('\n')
}

function stableStringify(value: unknown): string {
  return JSON.stringify(sortJsonValue(value), null, 2) ?? ''
}

function sortJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJsonValue)
  if (!value || typeof value !== 'object') return value

  const record = value as Record<string, unknown>
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(record).sort((a, b) => a.localeCompare(b))) {
    sorted[key] = sortJsonValue(record[key])
  }
  return sorted
}
