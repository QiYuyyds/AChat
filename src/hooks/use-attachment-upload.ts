'use client'

import { nanoid } from 'nanoid'
import { useEffect, useState } from 'react'

import { uploadAttachment as uploadAttachmentAPI } from '@/lib/api'
import { useAppStore } from '@/stores/app-store'

export interface UploadingItem {
  tempId: string
  name: string
}

export function useAttachmentUpload(conversationId: string): {
  handleFiles: (files: FileList | File[] | null) => Promise<void>
  uploading: UploadingItem[]
} {
  const [uploading, setUploading] = useState<UploadingItem[]>([])
  const addPendingAttachment = useAppStore((s) => s.addPendingAttachment)

  useEffect(() => {
    setUploading([])
  }, [conversationId])

  const handleFiles = async (files: FileList | File[] | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    const placeholders = list.map((f) => ({ tempId: nanoid(), name: f.name }))
    setUploading((prev) => [...prev, ...placeholders])

    await Promise.all(
      list.map(async (file, i) => {
        const tempId = placeholders[i].tempId
        try {
          const att = await uploadAttachmentAPI(conversationId, file)
          addPendingAttachment(conversationId, att)
        } catch (err) {
          console.error('[useAttachmentUpload] upload failed', err)
        } finally {
          setUploading((prev) => prev.filter((p) => p.tempId !== tempId))
        }
      }),
    )
  }

  return { handleFiles, uploading }
}
