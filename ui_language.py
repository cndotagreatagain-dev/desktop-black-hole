"""Presentation-only translations; status readers keep their existing protocol."""

ENGLISH = {
    "鼠标引力效果": "Cursor gravity",
    "桌面背景扭曲": "Desktop background lensing",
    "齐马蓝伴星（状态灯）": "Zima Blue companion (status light)",
    "状态来源": "Status sources",
    "Codex 总状态": "Codex overall status",
    "DSH 总状态（可选）": "DSH overall status (optional)",
    "DSH 接入向导": "DSH setup guide",
    "安装 Codex 接入（无需 Python）": "Install Codex integration (no Python required)",
    "移除本黑洞的 Codex 接入": "Remove this app's Codex integration",
    "始终置顶": "Always on top",
    "固定在桌面层（置底）": "Keep below other windows",
    "渲染画质": "Rendering quality",
    "标准": "Standard",
    "高清": "High",
    "电影级": "Cinematic",
    "恢复默认大小": "Restore default size",
    "在当前屏幕居中": "Center on current screen",
    "退出": "Quit",
    "开机启动（当前用户）": "Start with Windows (current user)",
    "开机启动": "Windows startup",
    "默认关闭；仅在当前用户登录 Windows 时启动，无需管理员权限。移动程序后请重新开关一次。":
        "Off by default. Starts when this user signs in, without administrator rights. Toggle off/on after moving the app.",
    "开机启动不可用：启动器缺失、路径过长、权限不足或存在同名非本程序启动项。":
        "Startup unavailable: missing launcher, long path, insufficient permission or a conflicting startup entry.",
    "无法更改开机启动。请检查启动器路径与权限；同名的其他启动项不会被覆盖。":
        "Could not change startup. Check the launcher path and permissions; unrelated entries are not overwritten.",
    "语言 / Language": "Language / 语言",
    "本机总状态灯": "Local overall status",
    "实时读取本机桌面，不录制。关闭后恢复普通透明效果。":
        "Reads the local desktop live without recording. Disable for ordinary transparency.",
    "空闲：外环慢飞；处理中：盘上方悬浮、五秒一圈、弧形长尾。\n未连接或状态待确认：外环暗光。可选 Codex / DSH 本机总状态。":
        "Idle: slow outer orbit. Busy: above the disk, one orbit every five seconds, with a long curved tail.\nDisconnected or unconfirmed: dim outer orbit. Optional local Codex / DSH status.",
    "留在其他应用窗口下面，保留右键、拖动和文件拖放。\n与始终置顶互斥；两项都关闭时为普通窗口。\n这是窗口置底，不是嵌入壁纸；Win+D 显示桌面时可能一起隐藏。":
        "Stays below other apps; right-click, dragging and file drops remain available.\nMutually exclusive with Always on top. Disable both for a normal window.\nNot embedded in the wallpaper; Win+D may hide it.",
}


def translate(text: str, language: str) -> str:
    if language == "zh":
        return text
    if text in ENGLISH:
        return ENGLISH[text]
    if text.startswith("背景扭曲暂不可用："):
        return "Background lensing unavailable: " + text.split("：", 1)[1]
    for chinese, english in (
        ("未启用来源", "No sources enabled"), ("等待接入确认", "Awaiting integration confirmation"),
        ("状态待确认", "Status unconfirmed"), ("未连接", "Disconnected"),
        ("处理中", "Busy"), ("空闲", "Idle"), ("总状态", "Overall status"),
        ("状态灯", "Status light"), ("：", ": "),
    ):
        text = text.replace(chinese, english)
    return text


def activity_detail(activity, language: str) -> str:
    if language == "zh":
        return activity.detail
    # Compose from structured fields rather than translating arbitrary log text.
    return (f"{translate(activity.label, language)}\n"
            f"Connected sources: {activity.connected} | Busy: {activity.busy} | "
            f"Uncertain: {activity.uncertain}\n\n"
            "Only local instances with the integration loaded are monitored. "
            "Unknown or disconnected does not mean idle.\n"
            "Codex: install the local integration, review its commands in /hooks, "
            "then restart Codex and send a message.\n"
            "DSH: open Status sources > DSH setup guide to load the included local plugin. Enabling the "
            "source does not install or launch DSH.\n"
            "Status checks are local and do not call a model or consume tokens.")


def translate_menu(menu, language: str) -> None:
    for action in menu.actions():
        action.setText(translate(action.text(), language))
        action.setToolTip(translate(action.toolTip(), language))
        if action.menu() is not None:
            translate_menu(action.menu(), language)
