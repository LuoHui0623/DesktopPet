use tauri::menu::{
    CheckMenuItem, ContextMenu, IsMenuItem, Menu, MenuBuilder, MenuEvent, MenuItem, MenuItemKind,
    Submenu,
};
use tauri::{AppHandle, Emitter, Manager, Runtime, Window};

pub const MENU_EVENT: &str = "menu-action";

#[derive(Debug, serde::Deserialize)]
pub struct MenuSpec {
    id: String,
    label: String,
    enabled: bool,
    #[serde(default)]
    checked: Option<bool>,
    #[serde(default)]
    children: Vec<MenuSpec>,
}

#[tauri::command]
pub fn show_context_menu(window: Window, items: Vec<MenuSpec>) -> Result<(), String> {
    let menu = build_menu(&window, &items).map_err(|error| error.to_string())?;
    menu.popup(window).map_err(|error| error.to_string())
}

#[tauri::command]
pub fn exit_app(app: AppHandle) {
    app.exit(0);
}

pub fn handle_menu_event(app: &AppHandle, event: MenuEvent) {
    let id = event.id().as_ref().to_string();
    let _ = app.emit_to(crate::window::PET_WINDOW_LABEL, MENU_EVENT, id);
}

fn build_menu<R: Runtime, M: Manager<R>>(
    manager: &M,
    specs: &[MenuSpec],
) -> tauri::Result<Menu<R>> {
    let items = specs
        .iter()
        .map(|spec| build_item(manager, spec))
        .collect::<tauri::Result<Vec<_>>>()?;
    let refs: Vec<&dyn IsMenuItem<R>> = items
        .iter()
        .map(|item| item as &dyn IsMenuItem<R>)
        .collect();
    MenuBuilder::new(manager).items(&refs).build()
}

fn build_item<R: Runtime, M: Manager<R>>(
    manager: &M,
    spec: &MenuSpec,
) -> tauri::Result<MenuItemKind<R>> {
    if spec.children.is_empty() {
        return match spec.checked {
            Some(checked) => Ok(CheckMenuItem::with_id(
                manager,
                spec.id.as_str(),
                spec.label.as_str(),
                spec.enabled,
                checked,
                None::<&str>,
            )?
            .kind()),
            None => Ok(MenuItem::with_id(
                manager,
                spec.id.as_str(),
                spec.label.as_str(),
                spec.enabled,
                None::<&str>,
            )?
            .kind()),
        };
    }
    let children = spec
        .children
        .iter()
        .map(|child| build_item(manager, child))
        .collect::<tauri::Result<Vec<_>>>()?;
    let refs: Vec<&dyn IsMenuItem<R>> = children
        .iter()
        .map(|item| item as &dyn IsMenuItem<R>)
        .collect();
    Ok(Submenu::with_id_and_items(
        manager,
        spec.id.as_str(),
        spec.label.as_str(),
        spec.enabled,
        &refs,
    )?
    .kind())
}

#[cfg(test)]
mod tests {
    use super::MenuSpec;

    #[test]
    fn deserializes_the_backend_menu_shape() {
        let specs: Vec<MenuSpec> = serde_json::from_str(
            r#"[
                {"id":"open_chat","label":"打开对话","enabled":true,"checked":null,"children":[]},
                {"id":"switch_avatar","label":"切换形象","enabled":true,"checked":null,
                 "children":[
                    {"id":"avatar:default","label":"默认形象","enabled":true,"checked":true,"children":[]}
                 ]}
            ]"#,
        )
        .expect("menu json");

        assert_eq!(specs[0].id, "open_chat");
        assert_eq!(specs[0].checked, None);
        assert_eq!(specs[1].children[0].id, "avatar:default");
        assert_eq!(specs[1].children[0].checked, Some(true));
    }
}
