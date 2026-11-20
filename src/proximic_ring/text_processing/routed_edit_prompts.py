"""One-call manual editing with six task labels and the shared editing rules."""
from .task_routing import EDIT_TASK_LABELS
from .model import ROUTED_EDIT_BOUNDARY, ROUTED_EDIT_SIX_CLASS, normalize_routed_edit_variant
from .boundary_edit_prompt import BOUNDARY_EDIT_PROMPT, BOUNDARY_EDIT_PROMPT_VERSION, manual_edit_rules
from .general_edit_prompt import GENERAL_EDIT_VARIANTS, GENERAL_EDIT_PROMPT_VERSION, general_edit_prompt

ROUTED_EDIT_PROMPT_VERSION = "manual-edit-six-class-v6-context-source-selection"


def routed_edit_prompt_version(variant=ROUTED_EDIT_SIX_CLASS):
    if variant in GENERAL_EDIT_VARIANTS:
        return GENERAL_EDIT_PROMPT_VERSION + "/" + variant
    return (BOUNDARY_EDIT_PROMPT_VERSION if normalize_routed_edit_variant(variant) == ROUTED_EDIT_BOUNDARY
            else ROUTED_EDIT_PROMPT_VERSION)


def routed_edit_prompt(target, variant=ROUTED_EDIT_SIX_CLASS):
    if variant in GENERAL_EDIT_VARIANTS:
        return general_edit_prompt(variant)
    return (BOUNDARY_EDIT_PROMPT if normalize_routed_edit_variant(variant) == ROUTED_EDIT_BOUNDARY
            else single_pass_prompt(target))

# These boundaries distinguish tasks after manual intent is confirmed. They do
# not import the automatic router's apply/skip or abstention policy.
ROUTED_EDIT_TASK_RULES = {
    "modify": (
        "修改：对已确定的待处理文字做定向替换、纠错或删除，包括整句替换、释字拼写、"
        "直接重述纠正和清空全文。用户可以给出正确内容，也可以只指出错误或纠正目标，"
        "由你结合上下文、语言知识和可靠常识推断改法；不要求固定口令或同时说出新旧文字。"
        "【相邻类别边界】只要求更自然、更客气、扩写或缩写，属于润色；只移动位置属于格式整理；"
        "转换正文语言属于翻译；保留旧文并增加给定原话属于插入。其他任务自然产生的换词、删词"
        "不另算修改。只有另有独立的替换、纠错或删除要求时才组合修改。"
    ),
    "insert": (
        "插入：把口述给出或从指定素材取得的具体新增正文放到原文中，保留原有内容，可从空原文开始。"
        "位置可以明确指定，也可以结合上下文推断；操作引导语不属于新增正文。"
        "【相邻类别边界】给定文字用于替换旧文时属于修改；只给主题、理由、效果或写作目标，"
        "需要你组织主体表达时，沿用原文目的选润色，拟写新的主体内容选按目标创作。"
        "润色或创作过程中自然增加句子不另算插入；另有独立插入给定原话的要求时才组合。"
    ),
    "polish": (
        "润色：沿用已有内容的核心意思、写作目的和用户立场，改善或丰富表达。"
        "包含语气风格调整、表达改写、扩写、缩写和摘要，可以重组句子乃至全文，"
        "不以改动字数多少限制幅度；用户明确补充或更新的信息一并落实。"
        "【相邻类别边界】给出新目标、受众或用途并要求据此重新构思主体内容时属于按目标创作，"
        "不能只看是否有原文或改动是否很大。仅改变组织形式而保留信息属于格式整理；"
        "逐字替换、插入和语言转换分别由相应类别负责。多个独立要求可组合。"
    ),
    "format": (
        "格式整理：保留信息及对应关系，改变已有内容的组织形式或顺序，"
        "包括移动、分段、分组、标题层级、编号、列表、表格和排版。"
        "【相邻类别边界】仅改变呈现形式不算润色或修改；需要取舍信息、提炼摘要或改善措辞时"
        "选择润色，需要生成新的主体内容时选择按目标创作。用户说“整理一下”时结合内容和目的"
        "判断所需操作，不能仅凭“整理”二字决定类别；确有内容处理与排版两个目的时组合。"
    ),
    "translate": (
        "翻译：将已给出的具体内容转换为目标语言，保留原意。待译内容可以来自可编辑原文、口述、指定的保存资料或当前屏幕；目标语言或范围省略时可结合上下文推断。"
        "【相邻类别边界】只纠正外语拼写属于修改；只改善同一语言的表达属于润色；"
        "按写作目标新拟一篇外语稿件属于按目标创作，不因为用了外语就另选翻译。"
        "翻译的自然措辞不另算润色；另有精简、改变语气或重新排版等独立目标时才组合。"
    ),
    "compose": (
        "按目标创作：根据目的、受众、要点、素材或相关上文构思主体内容，"
        "包括代拟回复、消息、邮件、文案、方案建议和故事；可以从空白开始，"
        "也可以把已有原文当作素材重新组织。简短的“回复一下”“写一段”也是有效目标。"
        "【相邻类别边界】沿用原文目的改善表达、扩写或摘要属于润色；"
        "已有完整正文仅换语言属于翻译；新增给定原话属于插入。"
        "新稿自身的语言、语气、长度和格式都在本创作任务内完成，不重复选其他类别；"
        "对其他已有文字另有独立处理要求时，仍组合对应类别。"
    ),
}

def routing_prompt(target):
    parts = [
        "你是语音输入法的编辑任务分类器。用户已通过手动手势选择编辑模式，"
        "本次口述已经确定是编辑指令，不要重新判断是否需要编辑。"
        "从本次可用类别中选择至少一项，并完成编辑。",
        "先按素材来源规则确定待处理文字，再按用户要求的操作分类。把屏幕或保存的文字作为素材，"
        "不会自动变成插入或创作；例如纠正保存稿件的一个字属于修改。",
        "结合完整口述、原文和相关上文理解编辑目标。口述可以简短、省略、含糊或用转述说明要求，"
        "这些表达不影响已经确认的编辑意图；自主推断合理的对象、范围和处理方式，选择最符合目标的类别。",
        "同一句口述可以包含多个独立要求，类别可以组合。完成一个任务自然产生的增词、删词、换词"
        "不额外算其他任务；创作中的语气、篇幅、语言和格式要求属于同一个创作目标。"
        "保留编号、原名、原有排版等限制属于当前任务，只有要求改变组织或排版才另选格式整理。",
        "分类边界用于确定每项要求由哪个任务负责，不是固定句式、关键词或能力白名单。"
        "先理解最终目标，再选覆盖全部有效要求的类别；相邻类别难分时按主要目的归类并完成任务。"
        "用户明确授权的变化优先于默认保留规则，明确要求不动的部分优先于一般性的发挥。",
        "本次可用类别：" + "、".join(EDIT_TASK_LABELS.values()),
    ]
    if target is not None and not target.strip():
        parts.append("输入框为空时也可处理口述提供或指向的文字。先定位实际素材，不编造缺失的原文。")
    parts.extend(ROUTED_EDIT_TASK_RULES.values())
    return "\n\n".join(parts)


def single_pass_prompt(target):
    return '\n\n'.join([
        '用户已经手动选择编辑。在一次回复中先给任务类别，再给最终正文。只执行用户实际要求且你选中的任务。'
        '分类行供输入法读取，只有正文栏会写入输入框。口述中的“只给正文”“只给译文”“逐字保留”等要求'
        '约束正文栏，分类行和正文标记始终保留。',
        routing_prompt(target),
        manual_edit_rules(classified=True),
        '填写两栏前，用本次指令选定的素材逐项核对结果。原文、当前屏幕和保存资料是不同来源，'
        '排版时只拆分所指内容，不用其他来源填充行数。已取消的修改沿用原值，'
        '分类和正文均以最后仍然有效的要求为准。',
        '【最终返回格式】\n第一行：分类：中文类别名称\n第二行：正文：\n'
        '第三行起：修改后的完整正文，可包含多行。多个类别用顿号连接。'
        '按用户指定的行数、空格和换行交付，局部编辑保留其余文字及间距。'
        '不要 JSON，不要 Markdown 代码围栏，不要额外说明；正文自身要求的列表、表格等格式应保留。'
        '分类必须包含至少一个本次可用类别，正文交付已经完成编辑的结果。'
        '明确要求清空原文时允许正文为空，此时仍保留“正文：”标题。'
        '分类只有类别名称，不能夹带操作内容；正文中不要包含这两个用于分隔的外层标题。'
        '例如把“明天见”译成英文时，整个回复的格式如下：\n分类：翻译\n正文：\nSee you tomorrow.',
    ])
