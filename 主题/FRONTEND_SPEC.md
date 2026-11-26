# AI Quota Display 前端页面设计规格说明

> 本文档用于指导重新设计并替换本项目的单文件前端页面 `index.html`。替换后无需重启后台服务，手机刷新即可生效。

---

## 一、显示设备与视口规格

- **机型与系统**：三星 Galaxy S8（`SM-G9500`），Android 9，去电池直供电桌面常亮摆件。
- **屏幕材质**：Super AMOLED（OLED 屏，建议纯黑 `#000000` 背景 + 克制亮度 + 定时像素微移防烧屏）。
- **摆放方式**：**横屏常亮**，已通过 ADB 开启沉浸式全屏 + PWA 主屏幕模式（**无顶部状态栏、无地址栏、无导航栏**，整屏 100% 为页面可视区域）。
- **分辨率参数**：
  - 物理分辨率：`2960 × 1440`（横屏，18.5:9 宽屏）
  - 系统逻辑分辨率：`2220 × 1080`（横屏），DPI `480`（3x）
  - **页面实际 CSS 逻辑视口**：约 **`740px × 360px`**（宽屏、高度紧凑，要求**一屏完整展示、禁止出现滚动条**）。

---

## 二、前端技术与兼容性红线（必读）

1. **单文件交付**：所有 HTML / CSS / JS 必须内联在单个 `index.html` 中，**禁止引入任何外部 CDN 字体、图标库或 JS 框架**（手机通过 `adb reverse` 访问本地服务，可能无外网环境）。
2. **老版本 Android 浏览器兼容（三星 Android 9 自带浏览器内核较老）**：
   - **禁止使用 ES2020+ 语法**：禁用可选链 `?.`、空值合并 `??`、无参数 `catch {}`（会导致整段脚本报语法错、页面空白）。请使用传统 ES5 / 基础 ES6 语法与 `XMLHttpRequest`。
   - **禁止使用 Flexbox `gap`**：老内核不生效，Flex 元素间距必须用 `margin-right` / `margin-bottom`；Grid 布局需同时写 `grid-gap` 与 `gap`。
3. **OLED 防烧屏**：建议保留每 60 秒将主容器在 `±3px` 内平滑微移（`transform: translate(...)`）的机制。
4. **PWA 元标签**：保留 `<link rel="manifest" href="/manifest.webmanifest" />` 与 `<meta name="theme-color" content="#000000" />`。

---

## 三、后端接口与交互约定

页面由本地 Python 服务（`http://127.0.0.1:8787`）托管，前端无需处理任何登录或认证：

- **`GET /api/quota`**：获取最新配额 JSON（前端建议每 15 秒轮询一次，附带 `?t=Date.now()` 防缓存）。
- **`POST /api/refresh`**：手动触发电脑端立即向线上重新拉取（后端内置 15 秒防抖），触发后约 1.5 秒重新拉取 `/api/quota`。

### `/api/quota` 返回结构与样例

```json
{
  "fetchedAt": "2026-11-26T10:56:17.903903Z",
  "refreshing": false,
  "pollIntervalSeconds": 120,
  "codex": {
    "ok": true,
    "plan": "plus",
    "source": "oauth",
    "updatedAt": "2026-11-26T10:56:12Z",
    "creditsRemaining": 3000,
    "windows": [
      { "label": "5 小时", "usedPercent": null, "resetsAt": null, "windowMinutes": 300, "inactive": true },
      { "label": "7 天", "usedPercent": 0, "resetsAt": "2026-12-03T10:56:12Z", "windowMinutes": 10080, "inactive": false }
    ],
    "resetCards": {
      "available": 4,
      "nextExpiresAt": "2026-11-30T19:32:55Z",
      "items": [
        { "title": "完全重置", "expiresAt": "2026-11-30T19:32:55Z", "grantedAt": "2026-10-31T19:32:55Z" },
        { "title": "完全重置", "expiresAt": "2026-12-01T21:16:10Z", "grantedAt": "2026-11-01T21:16:10Z" },
        { "title": "完全重置", "expiresAt": "2026-12-12T22:06:29Z", "grantedAt": "2026-11-12T22:06:29Z" },
        { "title": "完全重置", "expiresAt": "2026-12-26T04:31:11Z", "grantedAt": "2026-11-26T04:31:11Z" }
      ]
    }
  },
  "antigravity": {
    "ok": true,
    "plan": "Google AI Pro",
    "source": "oauth",
    "updatedAt": "2026-11-26T10:56:07Z",
    "windows": [
      { "id": "antigravity-quota-summary-gemini-5h", "group": "Gemini", "label": "5 小时", "usedPercent": 17.14, "resetsAt": "2026-11-26T12:13:37Z", "windowMinutes": 300 },
      { "id": "antigravity-quota-summary-gemini-weekly", "group": "Gemini", "label": "7 天", "usedPercent": 5.01, "resetsAt": "2026-12-03T07:13:37Z", "windowMinutes": 10080 },
      { "id": "antigravity-quota-summary-3p-5h", "group": "Claude / GPT", "label": "5 小时", "usedPercent": 0, "resetsAt": null, "windowMinutes": 300 },
      { "id": "antigravity-quota-summary-3p-weekly", "group": "Claude / GPT", "label": "7 天", "usedPercent": 0.79, "resetsAt": "2026-12-02T02:34:12Z", "windowMinutes": 10080 }
    ]
  }
}
```

> 异常字段说明：若某一方拉取失败，`ok` 为 `false` 且包含 `error` 字符串；若使用历史缓存降级，`ok` 为 `true` 并附带 `staleError` 字符串。

---

## 四、页面需要展示的具体内容与业务规则

1. **全局信息**
   - 当前本地时间（时:分）与日期/星期（兼作桌面时钟）。
   - 同步状态指示（正常 / 同步中 / 断连）、最近更新时间（`fetchedAt` 转本地时间）与手动「刷新」按钮。
2. **Codex 区域**
   - 套餐名称（`plan`，如 `PLUS`）与 Credits 余额（`creditsRemaining`，如 `3,000 Credits`）。
   - **5 小时窗口**与 **7 天窗口**：**显示剩余百分比**（`100 - usedPercent`）及重置倒计时（如 `6天23时后`）；当 `inactive: true` 时显示“未激活”。
   - **重置卡信息**：可用张数（`resetCards.available`）、最近到期倒计时（`nextExpiresAt`），以及各张卡的到期日期时间（**5 天内到期高亮预警**）。
3. **Antigravity 区域**
   - 套餐名称（`plan`，如 `Google AI Pro`）。
   - 4 个配额窗口：**Gemini 5 小时、Gemini 7 天、Claude / GPT 5 小时、Claude / GPT 7 天**，均显示**剩余百分比**（`100 - usedPercent`）与重置倒计时（`resetsAt` 为 `null` 时显示“未计费”或留空）。
4. **预警配色规则（按剩余百分比）**
   - 剩余 `> 30%`：正常色
   - 剩余 `≤ 30%`：预警色（琥珀/黄）
   - 剩余 `≤ 10%`：紧张色（红）

---

## 五、设计要求提示（可按需补充你的审美偏好）

- 优先保证桌面 50–80cm 视距下**关键数字（剩余百分比、重置卡张数、时钟）一眼可读**。
- 整体布局需适配 `740×360` 极宽横屏比例，信息密度均衡、无溢出、无滚动条。
- 交付完整的单个 `index.html` 代码，直接覆盖本地文件即可生效。
