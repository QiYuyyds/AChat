# Proposal: Merge Sidebar And Add Persistent File Explorer Column

## Why

The current three-column layout (icon rail 96px + context panel 288px + chat panel flex-1) consumes 384px for the sidebar alone. Additionally, the file explorer is an overlay that covers the chat panel when opened, preventing simultaneous viewing of chat and files. This change merges the sidebar into a single column AND makes the file explorer a persistent right-side column, enabling real-time file viewing alongside the chat.

## What Changes

- **Merge icon rail and context panel into a single column**: The icon rail (`w-24`) and context panel (`w-72`) become one unified sidebar column (~160-180px wide).
- **Retain vertical 8-button navigation**: The 8 navigation buttons (conversations / artifacts / agents / analytics / knowledge / skills / mcp / memory) stay vertical with icon + label, but with tighter spacing (reduced padding/gap).
- **Preserve "AChat" title**: The "AChat" title sits at the top of the merged column, above the navigation buttons.
- **Bottom action area becomes a single row**: The 5 bottom buttons (profile / settings / theme / logout / collapse) are replaced by a single row showing avatar + username + a settings gear icon. Clicking the gear icon opens a dropdown menu with: personal info, settings, theme toggle, logout.
- **Remove collapse button**: The "collapse sidebar" button is removed entirely since there is no longer a separate context panel to collapse.
- **Remove `collapsed` state**: The `collapsed` boolean state in the Sidebar component is removed; the context panel is always visible in the merged column.
- **Make FileExplorerPanel a persistent layout column**: The file explorer changes from a `fixed` overlay (with backdrop) to an in-flow flex column on the right side of the chat panel. On desktop it is always visible (`w-80`, ~320px); the toggle button in ChatPanel header controls its visibility. On mobile it remains a full-screen overlay.
- **Remove file explorer backdrop on desktop**: The `fixed inset-0 z-30` backdrop is removed for desktop (md+). Mobile retains full-screen overlay behavior.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `frontend`: Sidebar layout changes from three-column (icon rail + context panel + chat) to three-column (unified sidebar + chat + persistent file explorer). Navigation button arrangement, bottom action area, collapse behavior, and file explorer positioning are modified.

## Impact

- **`src/components/sidebar.tsx`**: Major refactor — merge icon rail and context panel into single column layout, restructure navigation buttons with tighter spacing, replace bottom 5-button area with avatar + username + gear dropdown, remove collapse button and `collapsed` state.
- **`src/components/file-explorer-panel.tsx`**: Change from `fixed` overlay to in-flow flex column on desktop; remove desktop backdrop; keep mobile as full-screen overlay.
- **`src/app/page.tsx`**: Layout structure changes — FileExplorerPanel moves from overlay to in-flow column (still rendered in the same position, but its CSS changes from `fixed` to flex).
- **`src/stores/app-store.ts`**: `fileExplorerOpen` default changes from `false` to `true` (persistent by default on desktop). The mutual exclusion with `previewArtifactId` remains.
- **`src/components/chat-panel.tsx`**: The file explorer toggle button still works but now shows/hides an in-flow column rather than an overlay.
- **`src/components/profile-dialog.tsx`**: ProfileButton component is restructured (may be inlined into the new bottom row or kept as-is if it already renders an avatar + name).
- **`src/components/settings-dialog.tsx`**: SettingsButton is restructured (may become just the gear icon trigger in the dropdown).
- **`src/components/theme-toggle.tsx`**: ThemeToggle is restructured (moves into the dropdown menu).
- **Mobile**: Mobile layout needs adjustment — the merged column replaces the icon rail + slide-in pattern. The entire sidebar slides in/out as one unit. File explorer remains full-screen overlay on mobile.
