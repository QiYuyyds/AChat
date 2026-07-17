'use client'

import ReactDiffViewer from 'react-diff-viewer-continued'

import { cn } from '@/lib/utils'

interface DiffBlockProps {
  oldCode: string
  newCode: string
  language?: string
  maxLines?: number
  className?: string
}

export function DiffBlock({ oldCode, newCode, language, maxLines, className }: DiffBlockProps) {
  const splitView = false

  return (
    <div className={cn('overflow-hidden rounded-md text-xs', className)}>
      <ReactDiffViewer
        oldValue={oldCode}
        newValue={newCode}
        splitView={splitView}
        hideLineNumbers={false}
        showDiffOnly
        extraLinesSurroundingDiff={1}
        leftTitle="Before"
        rightTitle="After"
        styles={{
          diffContainer: {
            fontSize: '0.75rem',
            fontFamily: 'var(--font-mono), ui-monospace, monospace',
          },
          line: {
            fontSize: '0.75rem',
          },
          contentText: {
            fontSize: '0.75rem',
          },
        }}
      />
    </div>
  )
}
