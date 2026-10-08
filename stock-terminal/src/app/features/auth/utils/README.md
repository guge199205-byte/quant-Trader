# utils

用途：工具函数与通用辅助。

## 说明
- 归属路径：electron\src\features\auth\utils
- 修改本目录代码后请同步更新本 README

## 变更记录
- 新增 `preloadAiIdeResources`：可在应用启动阶段执行 AI-IDE 资源预加载。除页面 chunk 外，还会先加载 Prism core 并挂载全局后再预热语言包（避免 `Prism is not defined`）、预热 Monaco 依赖并尝试 warm-up 本地 `monaco/vs` 关键静态资源（不再请求旧版 `workerMain.js` 路径），同时预热 Electron 侧 AI-IDE runtime IPC，减少首次进入与初始化闪烁。
