use tauri::{AppHandle, WebviewUrl, WebviewWindow, WebviewWindowBuilder};

pub const PET_WINDOW_LABEL: &str = "pet";
pub const PET_PAGE: &str = "app/entries/pet.html";

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
