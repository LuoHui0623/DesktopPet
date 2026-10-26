use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const PET_WINDOW_LABEL: &str = "pet";
pub const PET_PAGE: &str = "app/entries/pet.html";
pub const CHAT_WINDOW_LABEL: &str = "chat";
pub const CHAT_PAGE: &str = "app/entries/chat.html";

pub fn create_pet_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    WebviewWindowBuilder::new(app, PET_WINDOW_LABEL, WebviewUrl::App(PET_PAGE.into()))
        .title("桌面宠物")
        .inner_size(320.0, 240.0)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .resizable(false)
        .build()
}

pub fn open_chat_window(app: &AppHandle) -> tauri::Result<()> {
    if let Some(existing) = app.get_webview_window(CHAT_WINDOW_LABEL) {
        existing.show()?;
        existing.set_focus()?;
        return Ok(());
    }
    WebviewWindowBuilder::new(app, CHAT_WINDOW_LABEL, WebviewUrl::App(CHAT_PAGE.into()))
        .title("对话")
        .inner_size(420.0, 560.0)
        .build()?;
    Ok(())
}
