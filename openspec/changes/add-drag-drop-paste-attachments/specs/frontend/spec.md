# Spec Delta: Frontend

## ADDED Requirements

### Requirement: ChatPanel SHALL accept drag-and-drop file uploads

The ChatPanel `<main>` element SHALL act as a drag-and-drop target for file uploads. When a user drags files over the ChatPanel, a full-area semi-transparent overlay with a centered hint card SHALL be displayed. The overlay SHALL use `pointer-events-none` so that drop events propagate to the `<main>` element. The drop handler SHALL upload all dropped files via the existing attachment upload pipeline (`uploadAttachmentAPI` + `addPendingAttachment`). Only drag operations carrying `Files` in `dataTransfer.types` SHALL activate the overlay; other drag types (text selection, internal DOM elements) SHALL be ignored. A drag counter SHALL be used to prevent flicker caused by nested `dragenter`/`dragleave` events. Drag-and-drop upload SHALL remain functional when the composer is locked (e.g., during plan review), since attachment upload is independent of plan approval.

#### Scenario: User drags a file onto the chat area
- **WHEN** the user drags one or more files from the OS file manager over the ChatPanel `<main>` element
- **THEN** a semi-transparent overlay with a centered hint card ("拖拽文件到此处上传") is displayed
- **AND** the overlay does not intercept pointer events (`pointer-events-none`)

#### Scenario: User drops files onto the chat area
- **WHEN** the user releases the dragged files over any part of the ChatPanel (header, message list, or input bar)
- **THEN** the overlay is dismissed immediately
- **AND** each dropped file is uploaded via `uploadAttachmentAPI`
- **AND** uploading state is shown as `PendingAttachmentChip` components above the input bar
- **AND** successfully uploaded files appear as `AttachmentChip` components in the pending attachments area

#### Scenario: Non-file drag does not activate overlay
- **WHEN** the user drags a text selection or internal DOM element over the ChatPanel
- **THEN** the overlay is not displayed
- **AND** no upload is triggered on drop

#### Scenario: Drag leaves the ChatPanel
- **WHEN** the user drags files out of the ChatPanel boundaries
- **THEN** the overlay is dismissed without uploading any files

#### Scenario: Drag-and-drop during plan review lock
- **WHEN** the composer is locked due to a pending plan review (`composerLocked === true`)
- **AND** the user drags and drops files onto the ChatPanel
- **THEN** the files are uploaded normally as attachments
- **AND** the uploaded attachments are available for use after the plan review is resolved

### Requirement: MessageInput SHALL accept pasted image uploads

The MessageInput textarea SHALL intercept paste events (`onPaste`) to detect image content in the clipboard. When the pasted content includes `image/*` items, the paste SHALL be prevented from inserting raw content into the textarea, and each image item SHALL be uploaded via the existing attachment upload pipeline. When the pasted content contains only text (no image items), the paste SHALL proceed normally without interception. Pasted image upload SHALL remain functional when the composer is locked (e.g., during plan review).

#### Scenario: User pastes a screenshot
- **WHEN** the user presses `Ctrl/Cmd+V` in the textarea with an image in the clipboard
- **THEN** the default paste behavior is prevented
- **AND** the image is uploaded via `uploadAttachmentAPI`
- **AND** an uploading indicator (`PendingAttachmentChip`) is shown in the pending attachments area
- **AND** on success, the image appears as an `AttachmentChip`

#### Scenario: User pastes plain text
- **WHEN** the user presses `Ctrl/Cmd+V` in the textarea with only text in the clipboard
- **THEN** the paste proceeds normally without interception
- **AND** the text is inserted into the textarea at the cursor position

#### Scenario: User pastes mixed content (text + image)
- **WHEN** the user pastes content that includes both text and image items
- **THEN** the default paste behavior is prevented
- **AND** only the `image/*` items are extracted and uploaded as attachments
- **AND** the text portion is not inserted into the textarea

#### Scenario: Paste during plan review lock
- **WHEN** the composer is locked due to a pending plan review (`composerLocked === true`)
- **AND** the user pastes an image into the textarea
- **THEN** the image is uploaded normally as an attachment
- **AND** the uploaded attachment is available for use after the plan review is resolved
