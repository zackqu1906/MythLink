.pragma library

// Display metadata only. Presets use the existing validated custom-key binding path.
// macOS default combinations: https://support.apple.com/zh-cn/102650
function systemShortcuts(platform) {
    if (platform !== "osx") return []
    return [
        {id:"keyboard:spotlight",label:"聚焦搜索",shortcut:"Cmd+Space",description:"显示或隐藏 macOS 聚焦搜索栏。"},
        {id:"keyboard:switch-app",label:"切换应用",shortcut:"Cmd+Tab",description:"切换到最近使用的另一个应用。"},
        {id:"keyboard:screenshot",label:"截屏与录屏",shortcut:"Cmd+Shift+5",description:"打开系统截屏工具栏，选择截取区域或录制屏幕。"},
        {id:"keyboard:emoji",label:"表情与符号",shortcut:"Ctrl+Cmd+Space",description:"打开系统字符检视器，选择表情或其他符号。"},
        {id:"keyboard:dock",label:"显示／隐藏程序坞",shortcut:"Cmd+Alt+D",description:"切换 macOS 程序坞的显示状态。"}
    ].map(function(action) {
        return Object.assign({}, action, {keyboardPreset:true,systemShortcut:true,path:"macOS 系统快捷键",available:null})
    })
}

function presetFor(action, platform) {
    if (!action) return null
    // Normalize modifier order for persisted shortcuts, without changing their keys.
    var keys = String(action.shortcut || "").split("+").sort().join("+")
    return systemShortcuts(platform).filter(function(preset) {
        return preset.label === action.label && preset.shortcut.split("+").sort().join("+") === keys
    })[0] || null
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
    if (action.category) {
        return {application:"来自「" + applicationName + "」的菜单和场景预设。",system:"Mythlink 内置功能，如窗口选择、滚动和 Agent。",voice:"调整听写、定稿、润色和编辑的输入手势。",shortcut:"macOS 系统组合键，或你自己录制的快捷键。"}[action.category] || ""
    }
    if (!action.id) return applicationScope ? "移除此处的覆盖，回退到应用默认或全局配置。" : "解除此手势的全局绑定，保留应用内的独立配置。"
    var preset = presetFor(action, platform)
    if (preset) return preset.description + (isSystemMenu(action) ? "\n系统菜单：" + action.path : "\nmacOS 默认组合键；如已在系统中改键，请录入实际组合键。")
    if (action.systemShortcut || isSystemMenu(action)) return "系统菜单：" + action.path + "\n由 macOS 处理此组合键。"
    if (isBrowserControl(action)) return action.path + "\n通过扩展控制网页播放器，不发送键盘按键。"
    if (action.custom === true || String(action.id).indexOf("custom:") === 0)
        return "你录制的组合键；触发后发送给系统或当前前台应用。\n具体动作取决于系统或目标应用的快捷键设置。"
    if (!applicationScope) return {
        show_menu:"显示当前手势及其对应功能的提示面板。",
        window_selector:"打开窗口选择界面，选择要切换到的窗口。",
        scroll_up:"向上滚动当前页面或可滚动内容。",
        scroll_down:"向下滚动当前页面或可滚动内容。",
        workflow_recommendations:"打开下一步推荐列表，选择任务交给 Agent。",
        agent:"唤起 Agent 面板，或返回已有的 Agent 对话。"
    }[action.id] || action.effect || action.path || ""
    var source = action.path || "应用动作"
    if (action.preset === true) return applicationName + " · " + source + "\n应用预设组合键，请与应用内设置保持一致。"
    return applicationName + " · " + source + "\n" + (action.available === true ? "上次读取时菜单可用。" : action.available === false ? "上次读取时菜单不可用，绑定仍可保留。" : "尚未确认菜单是否可用，可刷新快捷键核对。")
}
