import { describe, expect, it } from 'vitest'

import { normalizePptDeck, toEditablePptContent } from './ppt-normalize'

import type { ArtifactContent } from './types'

describe('normalizePptDeck', () => {
  it('converts legacy title and bullets into canonical blocks', () => {
    const deck: Extract<ArtifactContent, { type: 'ppt' }> = {
      type: 'ppt',
      title: 'Deck',
      slides: [
        { title: 'Cover', layout: 'title', bullets: ['Subline'] },
        { title: 'Points', bullets: ['A', 'B'] },
      ],
    }

    expect(normalizePptDeck(deck)).toEqual({
      title: 'Deck',
      theme: undefined,
      slides: [
        {
          title: 'Cover',
          layout: 'title',
          blocks: [{ type: 'bullets', items: ['Subline'] }],
        },
        {
          title: 'Points',
          layout: 'title-bullets',
          blocks: [{ type: 'bullets', items: ['A', 'B'] }],
        },
      ],
    })
  })

  it('keeps supported enhanced blocks and drops unsupported nested blocks', () => {
    const deck = {
      type: 'ppt',
      slides: [
        {
          title: 'Business Review',
          subtitle: 'Q2',
          layout: 'metrics',
          blocks: [
            { type: 'metric', label: 'ARR', value: '$12M', change: '+18%', tone: 'positive' },
            {
              type: 'columns',
              columns: [
                {
                  title: 'Growth',
                  blocks: [
                    { type: 'paragraph', text: 'Enterprise expansion' },
                    { type: 'timeline', items: [{ label: 'ignored in columns' }] },
                  ],
                },
              ],
            },
          ],
        },
      ],
    } as unknown as Extract<ArtifactContent, { type: 'ppt' }>

    expect(normalizePptDeck(deck).slides[0]).toEqual({
      title: 'Business Review',
      subtitle: 'Q2',
      layout: 'metrics',
      blocks: [
        { type: 'metric', label: 'ARR', value: '$12M', change: '+18%', tone: 'positive' },
        {
          type: 'columns',
          columns: [
            {
              title: 'Growth',
              blocks: [{ type: 'paragraph', text: 'Enterprise expansion' }],
            },
          ],
        },
      ],
    })
  })

  it('serializes legacy decks into editable block-based PPT content', () => {
    const deck: Extract<ArtifactContent, { type: 'ppt' }> = {
      type: 'ppt',
      title: 'Editable Deck',
      slides: [
        {
          title: 'Summary',
          subtitle: 'Q2 review',
          bullets: ['Revenue grew 18%', 'Churn down 3pp'],
          notes: 'Presenter note',
        },
      ],
    }

    expect(toEditablePptContent(deck)).toEqual({
      type: 'ppt',
      title: 'Editable Deck',
      slides: [
        {
          title: 'Summary',
          subtitle: 'Q2 review',
          layout: 'title-bullets',
          blocks: [
            {
              type: 'bullets',
              items: ['Revenue grew 18%', 'Churn down 3pp'],
            },
          ],
          notes: 'Presenter note',
        },
      ],
    })
  })
})
