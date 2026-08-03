# Frontend Delta: Merge Sidebar And Add Persistent File Explorer Column

## MODIFIED Requirements

### Requirement: Sidebar SHALL render as a single unified column

The Sidebar component SHALL render a single vertical column (~176px wide) that combines navigation buttons, context content, and a bottom action bar into one unified panel. The Sidebar SHALL NOT render a separate icon rail and context panel side-by-side.

The column SHALL contain three vertical sections in order:
1. **Header**: "AChat" title, shrink-0, with bottom border
2. **Navigation**: 8 vertical navigation buttons (conversations / artifacts / agents / analytics / knowledge / skills / mcp / memory) with a divider between agents and analytics, shrink-0
3. **Context content**: mode-dependent content (conversation list / agent library / etc.), flex-1, scrollable
4. **Bottom action bar**: avatar + username + gear icon (single row), shrink-0, with top border

Navigation buttons SHALL use tighter spacing (py-1.5, gap-0.5 between buttons) compared to the previous layout (py-2, gap-1).

The Sidebar SHALL NOT include a collapse button or collapsed state. The context content section SHALL always be visible.

#### Scenario: Desktop layout renders three columns
- **WHEN** the app renders on desktop (md breakpoint and above)
- **THEN** the page shows three columns: the unified Sidebar (~176px), the ChatPanel (flex-1), and the FileExplorerPanel (w-80, ~320px) when a conversation is active and `fileExplorerOpen=true`
- **AND** no separate icon rail is rendered
- **AND** no overlay backdrop is rendered for the file explorer on desktop

#### Scenario: Navigation button triggers mode switch
- **WHEN** the user clicks a navigation button (e.g., "对话")
- **THEN** the context content section switches to display the corresponding mode content
- **AND** the active navigation button is visually highlighted.

#### Scenario: Context content scrolls independently
- **WHEN** the context content (e.g., conversation list) exceeds the available vertical space
- **THEN** only the context content section scrolls
- **AND** the header, navigation buttons, and bottom action bar remain fixed.

### Requirement: Bottom action bar SHALL provide a consolidated user menu

The bottom action bar SHALL display the user's avatar, username (truncated), and a settings gear icon in a single horizontal row. Clicking the gear icon SHALL open a dropdown menu containing: 个人信息, 设置, 主题切换, and 退出登录 (separated by a divider before 退出登录).

- **个人信息**: SHALL open the ProfileDialog.
- **设置**: SHALL open the SettingsDialog.
- **主题切换**: SHALL toggle between light and dark theme. The menu item SHALL display a Sun icon when currently dark (indicating "switch to light") and a Moon icon when currently light.
- **退出登录**: SHALL call the logout function from AuthStore.

The bottom action bar SHALL NOT render separate full-width buttons for profile, settings, theme, logout, or collapse.

#### Scenario: User opens profile from gear menu
- **WHEN** the user clicks the gear icon and selects "个人信息"
- **THEN** the ProfileDialog opens.

#### Scenario: User toggles theme from gear menu
- **WHEN** the user clicks the gear icon and selects "主题切换"
- **THEN** the theme toggles between light and dark.

#### Scenario: User logs out from gear menu
- **WHEN** the user clicks the gear icon and selects "退出登录"
- **THEN** the AuthStore logout function is called
- **AND** the user is redirected to the login page.

### Requirement: Sidebar SHALL slide as a single unit on mobile

On mobile (below md breakpoint), the entire Sidebar SHALL be a fixed-position overlay that slides in from the left edge. The Sidebar SHALL NOT render a persistent icon rail on mobile. The `mobileSidebarOpen` store state SHALL control the slide-in/out animation.

A hamburger menu button SHALL be visible in the ChatPanel header on mobile only (`md:hidden`) to toggle `mobileSidebarOpen`.

#### Scenario: Mobile user opens sidebar
- **WHEN** the user taps the hamburger menu button in the ChatPanel header on mobile
- **THEN** `mobileSidebarOpen` is set to true
- **AND** the entire Sidebar slides in from the left.

#### Scenario: Mobile user closes sidebar
- **WHEN** the sidebar is open on mobile and the user taps the backdrop or selects a conversation
- **THEN** `mobileSidebarOpen` is set to false
- **AND** the Sidebar slides out to the left.

### Requirement: FileExplorerPanel SHALL be a persistent in-flow column on desktop

The FileExplorerPanel SHALL render as an in-flow flex column (`w-80 shrink-0 border-l`) on desktop (md+), NOT as a `fixed` overlay. The panel SHALL NOT render a backdrop on desktop. The `fileExplorerOpen` store state SHALL control whether the column is present in the layout: when `true`, the column occupies space; when `false`, the column is absent and ChatPanel expands.

The `fileExplorerOpen` default value SHALL be `true` (visible by default on desktop).

On mobile (below md), the FileExplorerPanel SHALL remain a full-screen `fixed` overlay (`max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full`).

The mutual exclusion with `previewArtifactId` SHALL be preserved: opening an artifact preview sets `fileExplorerOpen=false`.

#### Scenario: File explorer visible by default on desktop
- **WHEN** the user opens a conversation on desktop
- **THEN** the FileExplorerPanel is visible as a right-side column (w-80)
- **AND** the ChatPanel shares horizontal space with the file explorer
- **AND** no backdrop overlay is rendered.

#### Scenario: User toggles file explorer off on desktop
- **WHEN** the user clicks the file explorer toggle button in the ChatPanel header
- **THEN** `fileExplorerOpen` is set to false
- **AND** the FileExplorerPanel column is removed from the layout
- **AND** the ChatPanel expands to fill the remaining space.

#### Scenario: File explorer on mobile
- **WHEN** the user opens the file explorer on mobile
- **THEN** it appears as a full-screen overlay
- **AND** a close button is available to dismiss it.

#### Scenario: Artifact preview replaces file explorer
- **WHEN** the user opens an artifact preview while the file explorer is visible
- **THEN** `fileExplorerOpen` is set to false
- **AND** the file explorer column is removed
- **AND** the artifact preview overlay appears.

## REMOVED Requirements

### Requirement: Sidebar collapse toggle

**Reason**: The collapse button and `collapsed` state are no longer needed since the icon rail and context panel are merged into a single column. There is no separate context panel to collapse.

**Migration**: The `collapsed` boolean state and `setCollapsed` setter are removed from the Sidebar component. The `PanelLeftClose` / `PanelLeftOpen` icon imports are removed. Any external code referencing the collapsed state (none found in current codebase) would need to be updated.
