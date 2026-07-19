// Prevents an extra console window on Windows in release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // If the process somehow inherited/attached a console (odd launchers or shells),
    // detach immediately so the user never sees a black cmd flash.
    #[cfg(all(windows, not(debug_assertions)))]
    {
        unsafe {
            // FreeConsole is a no-op when no console is attached.
            windows_sys_free_console();
        }
    }

    achat_desktop_lib::run();
}

#[cfg(all(windows, not(debug_assertions)))]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn FreeConsole() -> i32;
}

#[cfg(all(windows, not(debug_assertions)))]
unsafe fn windows_sys_free_console() {
    let _ = FreeConsole();
}
