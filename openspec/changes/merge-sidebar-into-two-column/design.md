# Design: Merge Sidebar And Add Persistent File Explorer Column

## Context

The `Sidebar` component (`src/components/sidebar.tsx`) currently renders two side-by-side sub-columns inside a `flex` container:

1. **Icon rail** (`<nav className="w-24 ...">`) — 8 vertical navigation buttons + bottom action buttons (ProfileButton, SettingsButton, ThemeToggle, logout, collapse toggle)
2. **Context panel** (`<div className="w-72 ...">`) — "AChat" title + mode-dependent content (conversation list, agent library, etc.), hidden when `collapsed=true`

Combined width is 96px + 288px = 384px. The goal is to merge these into a single ~160-180px column.

The bottom action area currently has 5 separate full-width buttons: ProfileButton (avatar + "个人"), SettingsButton (gear + "设置"), ThemeToggle (sun/moon + "主题"), logout (LogOut + "退出"), collapse toggle (PanelLeftClose + "收起").

### Existing component contracts

- `ProfileButton` (`profile-dialog.tsx`): renders avatar + "个人" label, manages its own `ProfileDialog` open state internally.
- `SettingsButton` (`settings-dialog.tsx`): renders gear icon + "设置" label, manages its own `SettingsDialog` open state internally, listens for `open-settings` UI command.
- `ThemeToggle` (`theme-toggle.tsx`): renders sun/moon + "主题" label, calls `setTheme()` directly, no dialog.

## Goals / Non-Goals

**Goals:**
- Merge icon rail and context panel into a single sidebar column (~160-180px)
- Retain 8 vertical navigation buttons with icon + label, tighter spacing
- Keep "AChat" title at top
- Replace 5 bottom buttons with: avatar + username + gear icon (single row), clicking gear opens dropdown menu with: 个人信息 / 设置 / 主题切换 / 退出登录
- Remove collapse button and `collapsed` state entirely
- Make FileExplorerPanel a persistent in-flow column on desktop (right side of chat panel)
- Preserve all existing functionality (navigation, profile dialog, settings dialog, theme toggle, logout)

**Non-Goals:**
- Changing the ChatPanel component internals (header buttons, message list, input)
- Changing the ArtifactPreviewPanel (remains a `fixed` overlay, mutually exclusive with file explorer)
- Modifying the Zustand store structure beyond `fileExplorerOpen` default value
- Redesigning the navigation button set (same 8 buttons, same modes)
- Changing mobile gesture patterns for sidebar (still slide-in/out)

## Decisions

### Decision 1: Single `flex-col` container replaces dual `flex-row`

The outer `<div className="flex shrink-0 ...">` becomes `<div className="flex w-[176px] shrink-0 flex-col ...">`. Inside:

```
┌─────────────────────┐
│ AChat (title)       │  ← shrink-0, border-b
│ ─────────────────── │
│ 💬 对话              │  ← nav buttons, flex-col, gap-0.5
│ 📦 产物库            │     tighter py-1.5 (was py-2)
│ 👤 Agents            │
│ ──── (divider)       │
│ 📊 分析              │
│ 📚 知识库            │
│ 🔧 技能              │
│ 🔌 MCP               │
│ 🗄 记忆管理           │
│ ─────────────────── │  ← border-t
│                     │
│  [context content]  │  ← flex-1, overflow-hidden
│  (conversation list  │     same mode dispatch as before
│   / agent library    │
│   / etc.)            │
│                     │
│ ─────────────────── │  ← border-t
│ [👤] username [⚙]   │  ← shrink-0, single row
└─────────────────────┘
```

**Why not keep the icon rail as a sub-element?** The user explicitly wants to merge into one column, and keeping a sub-rail would defeat the purpose. The vertical button layout naturally fits a narrow column.

**Alternative considered:** Horizontal tab bar at top. Rejected — user wants to keep the vertical 8-button arrangement.

### Decision 2: Navigation button spacing tightened

Current `RailButton` uses `px-1.5 py-2 gap-1.5` with `size-5` icons. Changes:
- `py-2` → `py-1.5` (vertical padding reduced)
- `gap-1` → `gap-0.5` (gap between buttons reduced)
- Keep `px-1.5`, `size-5` icons, `text-xs` labels
- Column width: `w-24` (96px) + `w-72` (288px) → `w-[176px]` (176px). This fits icon (20px) + gap (4px) + label text (~40-60px for 2-3 Chinese chars) + padding (12px) comfortably.

### Decision 3: Bottom row = avatar + username + gear dropdown

A new `BottomActionBar` sub-component replaces the 5 separate buttons. It renders:
- Avatar (size-5, same as ProfileButton currently uses)
- Username (truncate, `text-xs font-medium`)
- Gear icon button (triggers DropdownMenu)

The DropdownMenu contains:
1. **个人信息** → opens `ProfileDialog`
2. **设置** → opens `SettingsDialog`
3. **主题切换** → toggles theme (shows current state via icon prefix: Sun = currently dark, Moon = currently light)
4. *(separator)*
5. **退出登录** → calls `logout()`

**Why a dropdown instead of inline buttons?** The user explicitly requested a single row with avatar + name + gear icon that opens a menu. This saves vertical space and declutters the bottom area.

**How to manage dialog state?** The `BottomActionBar` component holds `profileOpen` and `settingsOpen` state. It imports `ProfileDialog` and `SettingsDialog` directly (not `ProfileButton`/`SettingsButton` wrappers, since those include their own trigger UI). Theme toggle logic is inlined (call `setTheme` from `next-themes` directly in the menu item onClick).

### Decision 4: Remove `collapsed` state

The `collapsed` boolean and `setCollapsed` setter are removed from the Sidebar component. The context panel is always visible in the merged column. The "收起" button is removed.

**Impact:** The `page.tsx` layout is unchanged (still `<Sidebar />` + `<ChatPanel />`), but Sidebar renders narrower. No store changes needed — `sidebarMode` still controls which content shows, `mobileSidebarOpen` still controls mobile slide-in.

### Decision 5: Mobile adaptation

Current mobile pattern:
- Icon rail (`w-24`) is `max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40` — always visible
- Context panel (`w-72`) is `max-md:fixed max-md:inset-y-0 max-md:left-24 max-md:z-40` — slides in/out via `translate-x`
- `mobileSidebarOpen` controls the slide

New mobile pattern:
- Entire sidebar is `max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40` — slides in/out as one unit
- `mobileSidebarOpen` controls the slide (same state, same behavior)
- When closed on mobile, the sidebar is fully hidden (translate-x to -100%)
- A trigger is needed to open it — the ChatPanel header already has buttons, or we add a floating hamburger button

**Trigger for opening on mobile:** The ChatPanel header already shows on mobile with a "更多" dropdown. We can add a hamburger menu button to the ChatPanel header on mobile (`md:hidden`), or keep the existing `mobileSidebarOpen` trigger from elsewhere. The simplest approach: add a `Menu` icon button at the start of the ChatPanel header, visible only on mobile.

### Decision 6: FileExplorerPanel becomes persistent in-flow column

Currently `FileExplorerPanel` is a `fixed inset-y-0 right-0 z-40 w-80` overlay with a `fixed inset-0 z-30` backdrop. The change:

**Desktop (md+):**
- Remove the backdrop div entirely (no `fixed inset-0 z-30`)
- Change `<aside>` from `fixed inset-y-0 right-0 z-40` to an in-flow `flex` column with `w-80 shrink-0 border-l`
- Remove `shadow-xl` and `animate-in slide-in-from-right` (no longer an overlay)
- Keep the header (folder icon + close button) and `ScrollArea` as-is
- The close button still works — sets `fileExplorerOpen=false`, which removes the column from the flex layout

**Mobile (below md):**
- Keep as `max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full` full-screen overlay
- No backdrop needed on mobile (panel is full-screen)

**Store change:** `fileExplorerOpen` default changes from `false` to `true` — the file explorer is visible by default on desktop. The toggle button in ChatPanel header still works to show/hide the column. The mutual exclusion with `previewArtifactId` (artifact preview) remains: opening an artifact preview sets `fileExplorerOpen=false`, closing the artifact preview could optionally re-open the file explorer.

**Layout result on desktop:**
```
┌──────────┬─────────────────────┬──────────┐
│ Sidebar  │   ChatPanel         │ FileExplorer│
│ ~176px   │   flex-1            │ w-80 (320px)│
│          │                     │            │
└──────────┴─────────────────────┴──────────┘
```

**Why not also make ArtifactPreviewPanel in-flow?** The artifact preview has variable width (`w-1/2 min-w-[420px]`) and is opened on-demand for specific artifacts. Keeping it as an overlay is fine — it can overlay the file explorer column when active. The mutual exclusion ensures only one is visible at a time.

## Risks / Trade-offs

- **[Narrower sidebar may truncate long labels]** → Navigation labels are short (对话/产物库/Agents/分析/知识库/技能/MCP/记忆管理), all fit in 176px. Context panel content (conversation titles etc.) may wrap more, but this is acceptable and the `ScrollArea` handles overflow.
- **[Extra click for profile/settings/theme]** → Previously 1 click (direct button), now 2 clicks (gear → menu item). Acceptable trade-off for cleaner UI, and these are infrequent actions.
- **[Mobile: no persistent icon rail]** → On mobile, the entire sidebar is hidden when closed. Mitigated by adding a hamburger button to ChatPanel header. This is actually cleaner — the icon rail at 96px was already a lot of lost space on mobile.
- **[ProfileButton/SettingsButton components become unused]** → They are only used in the Sidebar. After refactoring, they can be removed or left as dead code. We'll remove them to keep the codebase clean.
- **[Persistent file explorer reduces chat panel width]** → On a 1280px screen, the layout is 176 + flex-1 + 320 = ChatPanel gets ~784px (was ~896px with 384px sidebar and no file explorer). This is still comfortable for chat. Users can toggle the file explorer off if they need more chat space.
- **[File explorer shows nothing when no conversation selected]** → When `conv` is null, `FileExplorerPanel` returns null. The `w-80` column space is only occupied when a conversation is active and `fileExplorerOpen=true`. When no conversation is selected, the chat panel gets the full remaining width.
