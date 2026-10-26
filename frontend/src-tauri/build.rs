fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "get_backend_info",
                "open_chat_window",
                "open_settings_window",
            ]),
        ),
    )
    .expect("tauri 构建脚本执行失败");
}
