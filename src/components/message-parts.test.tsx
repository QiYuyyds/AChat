// @vitest-environment jsdom
import { describe, it, expect } from 'vitest'
import { render, screen } from '@testing-library/react'

import { PartList } from '@/components/message-parts'
import type { MessagePart } from '@/shared/types'

describe('TextPart streaming fallback', () => {
  it('renders <pre> fallback when isStreaming=true', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Hello\n\nThis is **markdown** content' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, content should be in a <pre> element (plain text fallback)
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('# Hello')
    expect(pre).toHaveTextContent('This is **markdown** content')

    // The pre should use font-sans (not font-mono) to match markdown body text
    expect(pre?.className).toContain('font-sans')
  })

  it('renders <Markdown> when isStreaming=false (complete)', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Hello\n\nThis is **markdown** content' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // When complete, Markdown should render the heading as an <h1> element
    const heading = screen.getByRole('heading', { level: 1 })
    expect(heading).toHaveTextContent('Hello')

    // The bold text should be rendered as <strong>
    const strong = document.querySelector('strong')
    expect(strong).toBeInTheDocument()
    expect(strong).toHaveTextContent('markdown')
  })

  it('does not mount Markdown during streaming (no heading elements)', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Heading\n\n- list item' },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, markdown should NOT be parsed — no <h1> heading
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()
    // No list elements either
    expect(document.querySelector('ul')).not.toBeInTheDocument()
  })

  it('switches from <pre> to <Markdown> when status transitions streaming→complete', () => {
    const parts: MessagePart[] = [
      { type: 'text', content: '# Title' },
    ]

    const { rerender } = render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // Initially: <pre> fallback
    expect(document.querySelector('pre')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1 })).not.toBeInTheDocument()

    // After complete: <Markdown> renders
    rerender(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Title')
  })
})

describe('FileWritePreviewPart streaming fallback', () => {
  it('renders <pre> fallback when status=streaming (no Shiki/CodeBlock)', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: 'def hello():\n    print("world")',
        callId: 'call-1',
        status: 'streaming',
        language: 'python',
      },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // During streaming, content should be in a <pre> element (plain text fallback)
    const pre = document.querySelector('pre')
    expect(pre).toBeInTheDocument()
    expect(pre).toHaveTextContent('def hello():')
    expect(pre).toHaveTextContent('print("world")')

    // The pre should use font-mono to match CodeBlock styling
    expect(pre?.className).toContain('font-mono')

    // The card header should show the file name and "生成中" indicator
    expect(screen.getByText('test.py')).toBeInTheDocument()
    expect(screen.getByText('生成中')).toBeInTheDocument()
  })

  it('renders CodeBlock when status=complete with newContent', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: '',
        callId: 'call-1',
        status: 'complete',
        language: 'python',
        oldContent: null,
        newContent: 'def hello():\n    print("world")',
      },
    ]

    render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    // When complete, the file name and "已创建" should be visible
    expect(screen.getByText('test.py')).toBeInTheDocument()
    expect(screen.getByText('已创建')).toBeInTheDocument()

    // Content should be present (CodeBlock renders code in a <code> element)
    expect(screen.getByText(/def hello/)).toBeInTheDocument()
  })

  it('switches from <pre> to CodeBlock when status transitions streaming→complete', () => {
    const parts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: 'def hello():\n    print("world")',
        callId: 'call-1',
        status: 'streaming',
        language: 'python',
      },
    ]

    const { rerender } = render(
      <PartList
        parts={parts}
        conversationId="conv-test"
        messageStatus="streaming"
      />,
    )

    // Initially: <pre> fallback with "生成中"
    expect(screen.getByText('生成中')).toBeInTheDocument()

    // After complete with newContent: switch to CodeBlock, show "已创建"
    const completedParts: MessagePart[] = [
      {
        type: 'file_write_preview',
        path: 'test.py',
        content: '',
        callId: 'call-1',
        status: 'complete',
        language: 'python',
        oldContent: null,
        newContent: 'def hello():\n    print("world")',
      },
    ]

    rerender(
      <PartList
        parts={completedParts}
        conversationId="conv-test"
        messageStatus="complete"
      />,
    )

    expect(screen.getByText('已创建')).toBeInTheDocument()
    expect(screen.queryByText('生成中')).not.toBeInTheDocument()
  })
})
