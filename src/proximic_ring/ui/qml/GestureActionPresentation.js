.pragma library

// Display metadata only. Presets use the existing validated custom-key binding path.
// macOS default combinations: https://support.apple.com/zh-cn/102650
var macShortcuts = [
    {id:"keyboard:spotlight",label:"聚焦搜索",shortcut:"Cmd+Space",description:"显示或隐藏 macOS 聚焦搜索栏。"},
    {id:"keyboard:switch-app",label:"切换应用",shortcut:"Cmd+Tab",description:"切换到最近使用的另一个应用。"},
    {id:"keyboard:screenshot",label:"截屏与录屏",shortcut:"Cmd+Shift+5",description:"打开系统截屏工具栏，选择截取区域或录制屏幕。"},
    {id:"keyboard:emoji",label:"表情与符号",shortcut:"Ctrl+Cmd+Space",description:"打开系统字符检视器，选择表情或其他符号。"},
    {id:"keyboard:dock",label:"显示／隐藏程序坞",shortcut:"Cmd+Alt+D",description:"切换 macOS 程序坞的显示状态。"}
].map(function(action) {
    return Object.assign({}, action, {keyboardPreset:true,systemShortcut:true,path:"macOS 系统快捷键",available:null})
})

var windowsShortcuts = [
    {id:"keyboard:search",label:"Windows 搜索",shortcut:"Win+S",description:"搜索应用、文件和设置。"},
    {id:"keyboard:switch-app",label:"切换应用",shortcut:"Alt+Tab",description:"切换到最近使用的另一个窗口。"},
    {id:"keyboard:task-view",label:"任务视图",shortcut:"Win+Tab",description:"查看打开的窗口和虚拟桌面。"},
    {id:"keyboard:screenshot",label:"Windows 截图工具",shortcut:"Win+Shift+S",description:"打开 Windows 截图工具，选择截图区域。"},
    {id:"keyboard:explorer",label:"文件资源管理器",shortcut:"Win+E",description:"打开文件资源管理器。"}
].map(function(action) {
    return Object.assign({}, action, {keyboardPreset:true,systemShortcut:true,path:"Windows 系统快捷键",available:null})
})

function systemShortcuts(platform) {
    return platform === "windows" ? windowsShortcuts : platform === "osx" ? macShortcuts : []
}

function presetFor(action, platform) {
    if (!action) return null
    // Normalize modifier order for persisted shortcuts, without changing their keys.
    return systemShortcuts(platform).find(function(preset) {
        if (preset.label !== action.label) return false
        var keys = String(action.shortcut || "").split("+").sort().join("+")
        return preset.shortcut.split("+").sort().join("+") === keys
    }) || null
}

function isSystemMenu(action) {
    var root = String(action.path || "").split(" › ")[0].trim().toLowerCase()
    return ["apple", "苹果", ""].indexOf(root) >= 0
}

function isKeyboard(action) { return action.custom === true || action.systemShortcut === true || isSystemMenu(action) || String(action.id || "").indexOf("custom:") === 0 }

function isBrowserControl(action) {
    return String(action.path || "").indexOf("浏览器扩展") >= 0
}

function description(action, applicationScope, applicationName, platform) {
    var details = actionDetails(action, applicationScope, applicationName, platform)
    return details + (!applicationScope && action.id && action.effect ? "\n" + action.effect : "")
}

function actionDetails(action, applicationScope, applicationName, platform) {
    if (action.voiceAction) return "语音输入 · 可与应用和全局映射重叠，输入框内优先执行。"
        + "\n仅在全局默认中修改；同一功能可绑定多个手势，选择后自动保存。"
    if (action.category) {
        return {application:"「" + applicationName + "」的快捷键与预设。",system:"打开应用、截图、粘贴、窗口选择、滚动和 Agent 等系统功能。",voice:"调整听写、定稿、润色和编辑的输入手势。",shortcut:"系统组合键，或你自己录制的快捷键。"}[action.category] || ""
    }
    if (!action.id) return applicationScope ? "移除此处的覆盖，回退到应用默认或全局配置。" : "解除此手势的全局绑定，保留应用内的独立配置。"
    if (action.id === "open_application") return "选择本机应用；触发手势后打开它，已打开时切换到该应用。\n最近选择的 5 个应用会保留在系统功能中，供所有全局手势使用。"
    if (action.applicationBundle) return "打开所选应用；已打开时切换到该应用。\n可再次选择「打开应用」更换目标。"
    var preset = presetFor(action, platform)
    if (preset) return preset.description + (isSystemMenu(action) ? "\n系统菜单：" + action.path : "\n如已在系统中改键，请录入实际组合键。")
    if (action.systemShortcut || isSystemMenu(action)) return "系统菜单：" + action.path + "\n由系统处理此组合键。"
    if (isBrowserControl(action)) return action.path + "\n通过扩展控制网页播放器，不发送键盘按键。"
    if (action.custom === true || String(action.id).indexOf("custom:") === 0)
        return "你录制的组合键；触发后发送给系统或当前前台应用。\n具体动作取决于系统或目标应用的快捷键设置。"
    if (!applicationScope) return {
        show_menu:"显示当前手势及其对应功能的提示面板。",
        window_selector:"打开窗口选择界面，选择要切换到的窗口。",
        screen_capture:"用光标点击第一个角，移动预览，再点击对角确定选区；支持任意方向，第三次点击开始重选。tap 手势或右滑保存 PNG、复制图片并退出。左滑或再次触发开启手势可退出；冲突时优先执行截图内置操作，绑定 tap 或右滑时用左滑退出。\n保存位置可在设置 → 界面与通用中修改。",
        clipboard_paste:"在当前应用粘贴剪贴板中的图片、文字等内容。截图保存并复制后，切到邮件等支持图片粘贴的位置，再触发此手势即可粘贴图片。",
        scroll_up:"向上滚动当前页面或可滚动内容。",
        scroll_down:"向下滚动当前页面或可滚动内容。",
        workflow_recommendations:"打开下一步推荐列表，选择任务交给 Agent。",
        agent:"唤起 Agent 面板，或返回已有的 Agent 对话。"
    }[action.id] || action.effect || action.path || ""
    var source = action.path || "应用动作"
    if (action.preset === true) return applicationName + " · " + source + "\n应用预设组合键，请与应用内设置保持一致。"
    return applicationName + " · " + source + "\n" + (action.available === true ? "上次读取时菜单可用。" : action.available === false ? "上次读取时菜单不可用，绑定仍可保留。" : "尚未确认菜单是否可用，可刷新快捷键核对。")
}
