"""Regex pattern constants for Context OS."""

from __future__ import annotations

import re

# === Low Signal Detection ===
_LOW_SIGNAL_PATTERNS = (
    r"^(hi|hello|hey|你好|您好|嗨|thanks|thank you|谢谢|ok|好的|收到|稍等|bye|再见)\b",
    r"(换个名字|改名字|改名|叫我|叫你|你是什么模型|what model are you|who are you)",
)

# === Goal Detection ===
_GOAL_PATTERNS = (
    re.compile(r"(修复|重构|实现|继续|开工|落地|抽离|统一|兼容|改造|补测试|写蓝图|写文档|排查)"),
    re.compile(
        r"\b(fix|refactor|implement|continue|ship|start|unify|migrate|rewrite|debug|test|document)\b",
        re.IGNORECASE,
    ),
)

# === Plan Detection ===
_PLAN_PATTERNS = (re.compile(r"(计划|蓝图|方案|步骤|roadmap|plan|blueprint)", re.IGNORECASE),)

# === Decision Detection ===
_DECISION_PATTERNS = (re.compile(r"(改成|采用|决定|就按|必须|统一为|canonical|直接走)", re.IGNORECASE),)

# === Deliverable Detection ===
_DELIVERABLE_PATTERNS = (re.compile(r"(测试|文档|蓝图|代码|patch|artifact|验收|验证|receipt)", re.IGNORECASE),)

# === Blocked Detection ===
_BLOCKED_PATTERNS = (re.compile(r"(blocked|阻塞|卡住|依赖|等待)", re.IGNORECASE),)

# === Preference Detection ===
_PREFERENCE_PATTERNS = (re.compile(r"^(请|不要|必须|希望|只要|优先|尽量)", re.IGNORECASE),)

# === Open Loop Detection ===
_OPEN_LOOP_PATTERNS = (
    re.compile(r"(继续|开始|开工|实现|重构|修复|补|验证|测试|运行|排查|处理|收口|抽离|落地|总结|写计划|写蓝图)"),
    re.compile(
        r"\b(continue|start|implement|refactor|fix|add|update|verify|test|run|ship|document)\b",
        re.IGNORECASE,
    ),
)

# === Date Extraction ===
_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2}|20\d{2}/\d{2}/\d{2}|20\d{2}\.\d{2}\.\d{2})\b")

# === Code Path Extraction ===
_CODE_PATH_RE = re.compile(
    r"([A-Za-z]:\\[^ \n\r\t]+|[/\\][^ \n\r\t]+|`[^`]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)`|\b[\w./\\-]+\.(py|md|ya?ml|json|toml|ts|tsx|js|jsx|sql|sh|ps1)\b)",
    re.IGNORECASE,
)

# === Constraint Prefix ===
_CONSTRAINT_PREFIX_RE = re.compile(
    r"^(do not|don't|must not|keep|preserve|禁止|不要|必须|保持|保留)",
    re.IGNORECASE,
)

# === Assistant Follow-up Detection ===
_ASSISTANT_FOLLOWUP_PATTERNS = (
    re.compile(r"(?:需要|是否需要|要不要)\s*我\s*(?P<action>.+?)(?:吗|么|呢)?[?？]?$"),
    re.compile(r"(?:要我|让我)\s*(?P<action>.+?)(?:吗|么|呢)?[?？]?$"),
    re.compile(
        r"(?:need me to|should i|do you want me to|would you like me to)\s+(?P<action>.+?)(?:\?|$)",
        re.IGNORECASE,
    ),
)

# === Affirmative Response Detection ===
_AFFIRMATIVE_RESPONSE_PATTERNS = (
    re.compile(r"^(需要|要|可以|行|好|好的|继续|开始|确认|是|是的|要的|请继续|请开始|嗯|对)[!！。.]?$"),
    re.compile(r"^(yes|y|ok|okay|sure|go ahead|please do|do it|continue|start)[.!]?$", re.IGNORECASE),
)

# === Negative Response Detection ===
_NEGATIVE_RESPONSE_PATTERNS = (
    re.compile(r"^(不用|不需要|不要|先不用|暂时不用|不用了|先别|停止|别做)[!！。.]?$"),
    re.compile(r"^(no|nope|not now|later|hold off|stop)[.!]?$", re.IGNORECASE),
)


# === Dialog Act Classification Patterns ===

# Short affirmative responses: "需要", "好", "是", "可以"
_DIALOG_ACT_AFFIRM_PATTERNS = (
    re.compile(r"^(需要|要|可以|行|好|好的|继续|开始|确认|是|是的|要的|请继续|请开始|嗯|对)[!！。.]?$"),
    re.compile(r"^(yes|y|ok|okay|sure|go ahead|please do|do it|continue|start)[.!]?$", re.IGNORECASE),
)

# Short negative responses: "不用", "不要" (explicit refusals, NOT "先别" which is PAUSE)
_DIALOG_ACT_DENY_PATTERNS = (
    re.compile(r"^(不用|不需要|不要|先不用|暂时不用|不用了|停止|别做)[!！。.]?$"),
    re.compile(r"^(no|nope|not now|later|hold off|stop)[.!]?$", re.IGNORECASE),
)

# Pause signals: "先别", "等一下", "暂停" (pause, not full denial)
# Note: Removed ^(先|暂时|先放一放) - too broad, would match "先帮我实现"
_DIALOG_ACT_PAUSE_PATTERNS = (re.compile(r"^(先别|等一下|暂停|等等|稍等|等会|hold|pause|wait)[.!。]?$", re.IGNORECASE),)

# Redirect signals: "改成", "换一个", "另外"
# Note: Removed single char ^(改) - too broad, would match "改好了"
_DIALOG_ACT_REDIRECT_PATTERNS = (
    re.compile(r"^(改成|换|换成|换一个|改一下|改成另外一个|另外|另一个)"),
    re.compile(r"^(change|switch|another|different|other|other one)[.!]?$", re.IGNORECASE),
)

# Clarify signals: "什么意思", "再说说", "详细点"
_DIALOG_ACT_CLARIFY_PATTERNS = (
    re.compile(
        r"^(什么意思|什么|怎么|怎么说|再说说|详细点|具体说|解释一下|clarify|explain|what do you mean)",
        re.IGNORECASE,
    ),
)

# Commit signals: "就这样", "确定", "就这样吧"
_DIALOG_ACT_COMMIT_PATTERNS = (
    re.compile(
        r"^(就这样|就这样吧|确定|就这样办|就这么做|就这么定了|ok|sounds good|agreed|confirmed?)[.!]?$",
        re.IGNORECASE,
    ),
)

# Cancel signals: "取消", "算了", "不要了"
_DIALOG_ACT_CANCEL_PATTERNS = (
    re.compile(r"^(取消|算了|不要了|终止|停止|停止吧|cancel|abort|forget it)[.!]?$", re.IGNORECASE),
)

# Status acknowledgment: "知道了", "好的收到"
_DIALOG_ACT_STATUS_ACK_PATTERNS = (
    re.compile(
        r"^(知道了|好的收到|收到|好的|了解|明白|okay|ok|got it|understood|acknowledged)[.!]?$",
        re.IGNORECASE,
    ),
)

# Noise patterns (truly low-signal)
_DIALOG_ACT_NOISE_PATTERNS = (
    re.compile(r"^(hi|hello|hey|你好|您好|嗨|bye|再见|thanks|thank you|谢谢)[\s。!]*$", re.IGNORECASE),
)
