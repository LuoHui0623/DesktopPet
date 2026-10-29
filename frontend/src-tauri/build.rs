fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "get_backend_info",
                "open_chat_window",
                "open_settings_window",
                "show_context_menu",
                "exit_app",
                "start_perception",
                "update_perception_config",
                "stop_perception",
            ]),
        ),
    )
    .expect("tauri 构建脚本执行失败");
}
