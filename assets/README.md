# 专属应用图标

采用用户确认的第二版：深蓝灰圆形底，银白折页/文件夹符号，内侧带少量暗青色。源图由内置 imagegen 生成，保存为 `app-icon-source.png`，没有使用 CLI/API 后备模式。

运行资源位于 `quick_access/assets`：`app.ico` 包含 16、20、24、32、40、48、64、128、256 像素版本，供程序、窗口、托盘和安装包使用；`app-256.png` 用于非 Windows 窗口；`app-ball.png` 为 44 像素悬浮球。图标转换只改变尺寸、编码和悬浮球边缘透明掩码，不重新绘制设计。可安装 Pillow 并运行 `python tools/build_icons.py` 更新这些资源。程序运行时不依赖 Pillow。

## 最终生成提示词

Use case: logo-brand. Create one final production app icon for a refined, minimal Windows file-access utility. The previous concept used a bright blue circle, yellow folder and large white arrow and was rejected as cheap; create a substantially more sophisticated new design. A perfectly circular deep midnight blue-gray disc with extremely subtle matte tonal depth. Inside, a bespoke sculptural silver-white mark: two overlapping folded document planes that together suggest an open folder and a passage, with a distinctive elegant negative-space opening between them. The silhouette must be bold, calm, balanced, instantly legible at 16 and 44 pixels. Think understated professional productivity software, precise industrial design, soft ivory / cool silver against dark ink, a restrained hint of muted teal only in the inner folded plane. Use large simple planes, a quiet shallow bevel or delicate lighting only, no busy detail. Icon fills 92 percent of square canvas with genuinely transparent exterior corners. Centered, compact, premium, beautiful at small scale. No yellow, bright electric blue, arrows, letters, words, conventional generic manila folder, cheap clipart, mascots, glitter, exaggerated glossy 3D, decorative rings, drop shadow outside the disc, UI mockup, presentation sheet or multiple alternatives. ONE actual icon asset, no text.
