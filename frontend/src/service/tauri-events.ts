/**
 * Tauri 自定义事件名的唯一出处。
 *
 * Tauri 只接受字母、数字与 `-` `/` `:` `_` 组成的事件名，含点会被 `listen` 直接拒绝，
 * 真机上的表现是悬浮窗静默收不到任何原生侧事件（感知样本、菜单点击、设置变更都失效）。
 * 因此这里统一用短横线分段，并由 `tests/service/tauri-events.test.ts` 校验字符集，
 * Rust 侧同名常量（src-tauri/src/menu.rs、src-tauri/src/perception.rs）由
 * `cargo test` 的 event_names_follow_the_tauri_charset 保证与之一致。
 */
export const SETTINGS_CHANGED_EVENT = "settings-changed";
export const MENU_ACTION_EVENT = "menu-action";
export const PERCEPTION_SAMPLE_EVENT = "perception-sample";
export const PERCEPTION_CANDIDATE_EVENT = "perception-candidate";

/** Tauri 允许的事件名字符集，供测试与新增事件时自查。 */
export const TAURI_EVENT_NAME_PATTERN = /^[A-Za-z0-9/:_-]+$/;
