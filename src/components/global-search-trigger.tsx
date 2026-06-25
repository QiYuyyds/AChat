'use client'

import { TextSearch } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useSearchStore } from '@/stores/search-store'

export function GlobalSearchTrigger() {
  const openSearch = useSearchStore((s) => s.openSearch)

  return (
    <Button
      type="button"
      variant="outline"
      size="icon"
      onClick={openSearch}
      title="搜索消息内容 (⌘K)"
      aria-label="搜索消息内容"
      className="size-8 shrink-0"
    >
      <TextSearch className="size-4" />
    </Button>
  )
}
