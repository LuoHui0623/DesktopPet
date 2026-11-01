use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const PET_WINDOW_LABEL: &str = "pet";
pub const PET_PAGE: &str = "app/entries/pet.html";
pub const PET_WINDOW_SIZE: (f64, f64) = (320.0, 360.0);
pub const CHAT_WINDOW_LABEL: &str = "chat";
pub const CHAT_PAGE: &str = "app/entries/chat.html";
pub const CHAT_HISTORY_PAGE: &str = "app/entries/chat.html#history";
pub const STATUS_WINDOW_LABEL: &str = "status";
pub const STATUS_PAGE: &str = "app/entries/status.html";
pub const SETTINGS_WINDOW_LABEL: &str = "settings";
pub const SETTINGS_PAGE: &str = "app/entries/settings.html";

pub fn create_pet_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, PET_WINDOW_LABEL, WebviewUrl::App(PET_PAGE.into()))
        .title("桌面宠物")
        .inner_size(PET_WINDOW_SIZE.0, PET_WINDOW_SIZE.1)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .resizable(false)
        .build()
}

pub fn open_chat_window(app: &AppHandle, history: bool) -> tauri::Result<()> {
    let page = if history {
        CHAT_HISTORY_PAGE
    } else {
        CHAT_PAGE
    };
    open_or_focus(app, CHAT_WINDOW_LABEL, page, "对话", (420.0, 560.0))
}

pub fn open_status_window(app: &AppHandle) -> tauri::Result<()> {
    open_or_focus(app, STATUS_WINDOW_LABEL, STATUS_PAGE, "状态", (480.0, 620.0))
}

pub fn open_settings_window(app: &AppHandle) -> tauri::Result<()> {
    open_or_focus(app, SETTINGS_WINDOW_LABEL, SETTINGS_PAGE, "设置", (560.0, 680.0))
}

fn open_or_focus(
    app: &AppHandle,
    label: &str,
    page: &str,
    title: &str,
    size: (f64, f64),
) -> tauri::Result<()> {
    if let Some(existing) = app.get_webview_window(label) {
        existing.show()?;
        existing.set_focus()?;
        return Ok(());
    }
    WebviewWindowBuilder::new(app, label, WebviewUrl::App(page.into()))
        .title(title)
        .inner_size(size.0, size.1)
        .build()?;
    Ok(())
}
