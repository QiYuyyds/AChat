## 1. Refactor Sidebar component structure

- [x] 1.1 Replace the outer `flex` container (icon rail + context panel side-by-side) with a single `flex w-[176px] flex-col` container
- [x] 1.2 Move "AChat" title to the top of the merged column as a shrink-0 header with `border-b`
- [x] 1.3 Place the 8 navigation buttons (RailButton) directly below the title in a `flex-col` with tighter spacing (`py-1.5`, `gap-0.5`), including the divider between agents and analytics
- [x] 1.4 Place the mode-dependent context content (conversation list / agent library / etc.) in a `flex-1 min-h-0` section below the navigation buttons
- [x] 1.5 Remove `collapsed` state and `setCollapsed` setter; remove the `PanelLeftClose`/`PanelLeftOpen` imports and the collapse toggle button; remove the `{!collapsed && (...)}` conditional wrapper around the context panel
- [x] 1.6 Remove the `max-md:contents` pattern on the outer container — the sidebar is now a single unit on all breakpoints

## 2. Build the consolidated bottom action bar

- [x] 2.1 Create a `BottomActionBar` sub-component (or inline section) at the bottom of the Sidebar with `shrink-0 border-t`
- [x] 2.2 Render avatar (size-5) + username (truncate, `text-xs font-medium`) + gear icon button in a single horizontal row
- [x] 2.3 Wire the gear icon to a `DropdownMenu` with items: 个人信息 / 设置 / 主题切换 / (separator) / 退出登录
- [x] 2.4 个人信息 menu item opens `ProfileDialog` (import `ProfileDialog` directly, manage `profileOpen` state locally)
- [x] 2.5 设置 menu item opens `SettingsDialog` (import `SettingsDialog` directly, manage `settingsOpen` state locally; also subscribe to `open-settings` UI command)
- [x] 2.6 主题切换 menu item calls `setTheme()` from `next-themes` (show Sun icon when dark, Moon icon when light)
- [x] 2.7 退出登录 menu item calls `logout()` from `useAuthStore`
- [x] 2.8 Remove the now-unused `ProfileButton` export from `profile-dialog.tsx` and `SettingsButton` export from `settings-dialog.tsx` (keep the Dialog components themselves)
- [x] 2.9 Remove the now-unused `ThemeToggle` usage from the Sidebar (the component itself can remain in `theme-toggle.tsx` for potential reuse)

## 3. Adapt mobile layout

- [x] 3.1 Make the entire Sidebar `max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-40` with slide-in/out via `translate-x` controlled by `mobileSidebarOpen`
- [x] 3.2 Update the mobile backdrop to cover from `left-0` (was `left-24`) since there is no persistent icon rail
- [x] 3.3 Add a hamburger `Menu` icon button to the ChatPanel header, visible only on mobile (`md:hidden`), that toggles `mobileSidebarOpen`
- [x] 3.4 Ensure conversation selection on mobile still closes the sidebar (`setMobileSidebarOpen(false)` in the `pickMode` / `setActive` handlers — verify existing behavior still works)

## 4. Make FileExplorerPanel a persistent in-flow column

- [x] 4.1 Change `FileExplorerPanel` desktop layout from `fixed inset-y-0 right-0 z-40` to in-flow `flex w-80 shrink-0 border-l` (remove `shadow-xl`, `animate-in slide-in-from-right`)
- [x] 4.2 Remove the desktop backdrop div (`fixed inset-0 z-30 hidden ... md:block`) entirely
- [x] 4.3 Keep mobile behavior: `max-md:fixed max-md:inset-0 max-md:z-40 max-md:w-full` full-screen overlay
- [x] 4.4 Change `fileExplorerOpen` default from `false` to `true` in `src/stores/app-store.ts`
- [x] 4.5 Verify the ChatPanel header toggle button still works (now shows/hides an in-flow column rather than an overlay)
- [x] 4.6 Verify mutual exclusion with `previewArtifactId` still works (opening artifact preview closes file explorer column)

## 5. Cleanup and verification

- [x] 5.1 Remove unused imports from `sidebar.tsx` (`PanelLeftClose`, `PanelLeftOpen`, `ProfileButton`, `SettingsButton`, `ThemeToggle`, `LogOut` if no longer used directly)
- [x] 5.2 Run `pnpm typecheck` to verify no type errors
- [x] 5.3 Run `pnpm lint` to verify no lint errors
- [x] 5.4 Visually verify the three-column layout on desktop: Sidebar (~176px) + ChatPanel (flex-1) + FileExplorerPanel (w-80)
- [x] 5.5 Visually verify file explorer toggle: clicking the PanelRight button in ChatPanel header hides/shows the file explorer column
- [x] 5.6 Visually verify mobile: hamburger button opens/closes sidebar, file explorer is full-screen overlay, backdrop dismisses, conversation selection closes sidebar
- [x] 5.7 Verify all gear menu actions work: profile dialog opens, settings dialog opens, theme toggles, logout works
- [x] 5.8 Verify artifact preview overlay still works and hides the file explorer column when opened
