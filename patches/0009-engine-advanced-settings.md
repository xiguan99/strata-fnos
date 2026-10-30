# 0009 — 引擎高级参数进设置页（参数透传 + 显存预检数字可配）

日期：2026-10-30（0.2.7.10）
来源要求：Rain m01184「这套调优值不要再写死在代码里，在网页设置页加『引擎高级参数』区」；
用户口径「一切尽量以网页配置为主，尽量不硬编码任何配置项，别人用的环境和我们不一样」。

## 改动文件

- `fnpack/app/host/server/native-settings.mjs`
  - `NATIVE_FIELDS` 新增：`kv_resident`、`vram_reserve_later_mib`、`prefill`、`pool_workers`、
    `host_core`（enum auto/first/last）、`spec`；`vram_floor_mib`、`vram_extra_mib`、`vram_headroom_mib`。
  - `APP_OWNED_FIELDS` 加入这些 id（它们由本应用解释，不在引擎源码文本里，`nativeFields()` 靠它标 `supported:true`）。
  - 新增 `ENGINE_ARG_FIELDS`（设置项 → 引擎 flag）与 `engineArgValue(args, flag)`（从 `args` 回读实际值）。
  - `validateNativeSettings` 对 `prefill` 做格式校验（`auto` / `auto:<正整数>` / 正整数）。
- `fnpack/app/host/server/native-control.mjs`
  - `settings()`：设置项为空时用 `engineArgValue` 从 `args` 回填，界面显示即引擎实际值。
  - `save()`：保存时把 `ENGINE_ARG_FIELDS` 的值写进 `args` 与 `user_args`（下次启动生效）。
  - `buildConfig()`：把 `cfg` 上的这些值注入生成参数，保证没有历史 `args` 时也生效。
  - 显存预检：`Math.max(2560, reserve+1536)` → `Math.max(cfg.vram_floor_mib??2560, reserve+cfg.vram_extra_mib??1536)`；
    `needed+512` → `needed+(cfg.vram_headroom_mib??512)`。默认值与改动前一致。
- `scripts/build.sh`：两条守卫，防止 `vram_headroom_mib??512` 与可配地板表达式在同步上游后丢失。

## 验证口径

1. `PATCH /api/native/models/<model>/settings` 写入上述键 → 该模型运行配置 `args`/`user_args` 同时出现对应 flag。
2. 加载后引擎日志能看到参数生效（例：`(--host-core last)`）。
3. 默认值下双卡能正常加载（预检不误杀）。
