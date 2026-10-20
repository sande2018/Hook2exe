# Hook2Exe · 网页打包工具

把「**网址 + 油猴脚本**」一键打包成 Windows 桌面应用（exe，Edge WebView2 内核）。

生成器为**纯 Python 实现**（单文件、仅标准库、无需 pip 装任何东西）；生成的 exe 来自内置的固化 C# 模板，由 Python 自动填充参数后调用系统自带的 .NET 编译器现场编译 —— **不需要懂 C#，也不需要安装 Visual Studio / .NET SDK**。

```
网址 + 脚本.js  ──►  Hook2Exe(Python)  ──►  csc.exe(系统自带)  ──►  独立 exe（Edge 内核）
```

---

## 特性亮点

- **零依赖生成器**：`hook2exe.py` 单文件，仅用 Python 标准库，`pip install` 都不用跑
- **自动推断**：标题留空 → 自动抓网页 `<title>`；图标留空 → 自动抓 favicon（apple-touch-icon 优先），抓不到用「渐变圆角 + 首字」默认图标
- **油猴元数据感知**：自动解析 `@match / @include / @run-at / @name / @version`，按规则门控注入、按时机调度执行
- **GM_\* 全兼容**：内置 GM 兼容层，`GM_xmlhttpRequest` 经宿主 exe 转发，**不受 CORS 限制**
- **轻量产物**：exe + 3 个 DLL 共约 0.9MB（直接复用系统 Edge 内核，不捆绑浏览器）
- **开箱即用**：Win10/11 目标机一般自带 .NET Framework 4.x 和 Edge 运行时，拷过去就能跑
- **常驻后台**：默认关窗缩托盘继续运行，单实例互斥，登录态按网址隔离持久化

---

## 环境要求

### 打包机（运行 Hook2Exe 的机器）

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows x64 | |
| Python | 3.8+（3.12 已验证） | 官网安装包自带 tkinter |
| .NET Framework | 4.x（含 csc.exe） | Win10/11 系统自带，无需另装 |
| 第三方包 | **无** | 可选 `pip install pillow` 增强图标转换 |

### 目标机（运行生成的 exe 的机器）

| 项 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows x64 | |
| Edge / WebView2 运行时 | 任一即可 | Win10/11 一般自带 |
| .NET Framework | 4.x | 系统自带 |

---

## 快速开始

### GUI 模式（推荐日常使用）

```bash
python hook2exe.py
```

填网址 → 选脚本 → 点「生成应用」，日志区实时显示进度，完成后可直接打开输出目录。支持把 `.js` 脚本和图标文件**直接拖进窗口**。

### 命令行模式（适合批处理/自动化）

```bash
# 最简：只有网址（无脚本、无图标，全自动推断）
python hook2exe.py --url https://www.example.com/

# 完整示例：用原版闲鱼 AI 客服脚本打包
python hook2exe.py --url https://www.goofish.com/im \
    --script "闲鱼 AI 智能客服-1.0.0.txt"

# 指定标题 / 应用名 / 图标 / 输出目录，关窗直接退出
python hook2exe.py --url https://www.goofish.com/im \
    --script bot.js --title "闲鱼客服" --name XianyuBot \
    --icon logo.ico --out D:\apps --no-tray
```

### 参数一览

| 参数 | 必填 | 说明 | 缺省行为 |
|---|---|---|---|
| `--url` | CLI 必填 | 目标网址（`://` 缺省自动补 `https://`） | — |
| `--script` | 可选 | 油猴脚本路径（`.js` / `.txt` 均可） | 不注入用户脚本 |
| `--title` | 可选 | 窗口标题 | 自动取网页标题 → 再兜底用域名 |
| `--name` | 可选 | 应用名 / exe 文件名 | 用标题（自动清理非法字符） |
| `--icon` | 可选 | 图标文件（`.ico/.png/.jpg/.jpeg/.bmp`） | 自动抓网站 favicon → 兜底默认图标 |
| `--out` | 可选 | 输出根目录 | `./output` |
| `--no-tray` | 开关 | 关闭窗口直接退出进程 | 默认最小化到托盘 |
| `--all-frames` | 开关 | 允许脚本在 iframe 内运行 | 默认仅顶层窗口 |
| `--gui` | 开关 | 强制打开图形界面 | 无参数启动时自动进 GUI |

---

## 生成产物

```
output/<应用名>/
├── <应用名>.exe                      # 主程序（图标、标题、目标网址均已固化）
├── Microsoft.Web.WebView2.Core.dll   # ┐
├── Microsoft.Web.WebView2.WinForms.dll  # ├ WebView2 依赖，共 ~0.9MB
└── WebView2Loader.dll                # ┘
```

**分发时 4 个文件必须放在一起**（可整体打成 zip）。

生成的应用首次运行会自动创建数据目录：

```
%LOCALAPPDATA%\HookApp\<网址哈希8位>\
├── WebView2Data\    # 浏览器登录态 / localStorage / Cookie
└── host.log         # 宿主异常日志（正常运行为空或不存在）
```

不同网址的应用数据互相隔离；同一网址的多个应用共享登录态。

---

## 油猴脚本兼容性

### 支持的 GM_\* API

| API | 实现 | 备注 |
|---|---|---|
| `GM_xmlhttpRequest` | 宿主桥转发 | **无 CORS 限制**，超时/错误回调齐全 |
| `GM_xmlHttpRequest` | 同上 | 别名 |
| `GM.getValue / setValue / deleteValue / listValues` | localStorage 持久化 | Promise 变体 |
| `GM_getValue / GM_setValue / GM_deleteValue / GM_listValues` | 同上 | 同步变体，自动 JSON 序列化 |
| `GM_addStyle` | 注入 `<style>` | 带 `data-gm-style` 标记 |
| `GM_notification` | 系统托盘气泡 | 两种参数形式都支持 |
| `GM_setClipboard` | 系统剪贴板 | |
| `GM_registerMenuCommand` | 静态登记 | 不报错（桌面版无油猴菜单 UI） |
| `GM_log` | console 输出 | |
| `GM_info` | 元数据对象 | 脚本名/版本来自 `@name @version` |
| `unsafeWindow` | `window` 直通 | |
| `GM.*` 聚合对象 | 上述 Promise 变体合集 | |

### 元数据处理

- **`@match / @include`**：转换为正则做注入门控（页面 URL 不匹配则脚本不执行）；涉及的域名同时进入宿主桥白名单
- **`@run-at`**：`document-start` / `document-body` / `document-end` / `document-idle`（默认）
- **`@name / @version`**：填充 `GM_info`

### 图标与标题自动推断（优先级从高到低）

```
图标：用户指定文件 → 网页 <link rel="apple-touch-icon"> → <link rel="icon">（按尺寸排序）
      → 站点根 /favicon.ico → 渐变默认图标（标题首字）
标题：用户指定 → 网页 <title> → 域名
应用名：用户指定 → 标题 → "HookApp"
```

---

## 生成的应用行为

- **托盘常驻**：点 × 收进托盘继续后台运行（托盘双击恢复；右键菜单可退出/重载页面）；`--no-tray` 生成的则直接退出
- **单实例互斥**：同一网址的应用只允许运行一个，重复启动弹窗提示
- **F12 开发者工具**：应用内按 F12 可调试注入的脚本（WebView2 DevTools）
- **安全防护**：宿主 HTTP 桥只接受白名单域名（目标域名 + 脚本 `@match/@include` 域名）发来的请求；拒绝回环/内网地址（基础 SSRF 防护）

---

## 目录结构

```
Hook2Exe/
├── hook2exe.py               # 生成器主程序（Python，CLI + GUI）
├── README.md                 # 本文档
├── requirements.txt          # 依赖说明（核心零依赖）
├── lib/                      # WebView2 运行时 DLL（随生成的应用分发）
│   ├── Microsoft.Web.WebView2.Core.dll
│   ├── Microsoft.Web.WebView2.WinForms.dll
│   └── WebView2Loader.dll
├── templates/                # 固化模板（生成器填参数后编译）
│   ├── host_program.cs       # exe 入口（单实例互斥）
│   ├── host_mainform.cs      # 主窗体（WebView2 + 托盘 + 页面消息桥）
│   ├── host_httpbridge.cs    # HTTP 转发桥（无 CORS + SSRF 防护）
│   ├── host_manifest.xml     # DPI 感知清单
│   └── gm_prelude.js         # GM_* 兼容层（注入脚本头部）
└── output/                   # 生成产物（首次生成时自动创建）
```

---

## 二次开发指南

**日常改生成逻辑：只动 `hook2exe.py`，不碰 C#。**

| 想改什么 | 改哪里 |
|---|---|
| 生成参数、校验、输出布局 | `hook2exe.py`（`build()` 流程） |
| 图标抓取/生成规则 | `hook2exe.py`（`fetch_site_icon` / `build_letter_ico`） |
| 油猴元数据解析 | `hook2exe.py`（`parse_userscript_meta`） |
| GM API 行为 | `templates/gm_prelude.js`（纯 JS） |
| 宿主行为（托盘菜单、快捷键、窗口尺寸） | `templates/host_mainform.cs` |
| HTTP 桥/安全策略 | `templates/host_httpbridge.cs` |

模板占位符对照表（Python 渲染时替换）：

| 占位符 | 所在模板 | 含义 |
|---|---|---|
| `__TARGET_URL__` | host_mainform.cs | 目标网址 |
| `__APP_TITLE__` | host_program.cs / host_mainform.cs | 窗口标题 |
| `__DATA_NAME__` | host_mainform.cs | 数据目录名（网址哈希） |
| `__ALLOWED_HOSTS__` | host_mainform.cs | 桥接域名白名单（C# 数组字面量） |
| `__TRAY_CLOSE__` | host_mainform.cs | 关窗缩托盘（true/false） |
| `__MUTEX_ID__` | host_program.cs | 单实例互斥量名 |
| `__SCRIPT_NAME__` / `__SCRIPT_VERSION__` | gm_prelude.js | GM_info 元数据 |

---

## 常见问题（FAQ）

**Q：生成的 exe 双击没反应 / 闪退？**
检查目标机是否 64 位 Windows、是否装有 Edge 或 WebView2 运行时；再看 `%LOCALAPPDATA%\HookApp\<哈希>\host.log` 里的报错。

**Q：脚本好像没生效？**
应用内按 F12 打开控制台看报错；确认脚本的 `@match` 能匹配目标网址；如果脚本要跑在 iframe 里，生成时加 `--all-frames`。

**Q：脚本里的网络请求失败？**
`GM_xmlhttpRequest` 走宿主桥（无 CORS）；但白名单只包含目标域名和脚本 `@match/@include` 声明的域名 —— 想请求别的域名，在脚本元数据里加一条对应域名的 `@match`（如 `// @match https://api.example.com/*`）后重新生成。用浏览器 `fetch` 的请求仍受页面 CORS 限制。

**Q：登录态会丢吗？**
不会。登录态存 `%LOCALAPPDATA%\HookApp\<网址哈希>\`，关机重启都保留；同网址重新生成的应用沿用同一份登录态。

**Q：favicon 抓不到 / 图标难看？**
部分网站用 SVG 图标或不允许抓取，此时用默认图标；可手动 `--icon xxx.png` 指定。装了 Pillow（`pip install pillow`）后任意图片转图标效果更好。

**Q：杀毒软件报毒？**
csc 编译的未签名 exe 可能被误报（常见于小众杀软），把生成的目录加入信任列表即可。

**Q：能生成 32 位版本吗？**
当前模板固定 `/platform:x64`。如需 x86，把 `hook2exe.py` 中编译参数改为 `/platform:x86` 并自备 32 位 WebView2Loader.dll。

---

## 实战示例

```bash
# 例 1：把原版闲鱼 AI 客服 1.0.0 打包成桌面应用
python hook2exe.py --url https://www.goofish.com/im \
    --script "闲鱼 AI 智能客服-1.0.0.txt"
# → output/闲鱼 AI 智能客服/（自动用闲鱼 favicon + 网页标题）

# 例 2：纯净浏览器壳（无脚本）
python hook2exe.py --url https://mail.example.com --name 公司邮箱

# 例 3：批处理打包多个站点
for /f %u in (urls.txt) do python hook2exe.py --url %u
```

