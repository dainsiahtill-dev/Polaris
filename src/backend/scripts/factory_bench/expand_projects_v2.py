#!/usr/bin/env python3
"""Expand projects_v2.json from 96 to 120 projects.

Adds 2 new creative projects per level (L1-L12), reassigns IDs sequentially,
and writes back to projects_v2.json.  This script is idempotent — running it
twice without editing the new-project definitions produces the same result
because it rebuilds the full ID sequence from scratch.

Usage:
    python expand_projects_v2.py [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_V2_PATH = Path(__file__).resolve().parent / "projects_v2.json"

# 24 new projects (2 per level).  Creative hooks chosen to avoid overlap with
# existing 96 projects while maximizing genre/language diversity.

NEW_PROJECTS: list[dict] = [
    # ── L1 ──
    {
        "level": 1,
        "domain": "science_creative",
        "project_type": "simulation_toy",
        "primary_language": "rust",
        "title": "星尘炼金配方生成器",
        "creative_hook": "元素碎片通过星座排列和温度梯度合成未知药剂",
        "novelty_tags": ["simulation-toy", "playful", "novel", "rust"],
        "brief": "用 Rust 实现「星尘炼金配方生成器」。创意钩子: 元素碎片通过星座排列和温度梯度合成未知药剂。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "元素碎片通过星座排列和温度梯度合成未知药剂; 同时验证 Rust 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "rust_compile",
            "min_files:3",
            "content_any:alchemy|stardust|element|recipe",
            "source_target_coverage:src/**/*.rs",
        ],
    },
    {
        "level": 1,
        "domain": "creative",
        "project_type": "interactive_visual",
        "primary_language": "go",
        "title": "情绪涂鸦色轮",
        "creative_hook": "涂鸦笔触随情绪词和BGM节拍变色并生成色轮报告",
        "novelty_tags": ["interactive-visual", "playful", "novel", "go"],
        "brief": "用 Go 实现「情绪涂鸦色轮」。创意钩子: 涂鸦笔触随情绪词和BGM节拍变色并生成色轮报告。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "涂鸦笔触随情绪词和BGM节拍变色并生成色轮报告; 同时验证 Go 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "go_compile",
            "min_files:3",
            "content_any:mood|color|wheel|doodle",
            "source_target_coverage:**/*.go",
        ],
    },
    # ── L2 ──
    {
        "level": 2,
        "domain": "game",
        "project_type": "rhythm_game",
        "primary_language": "python",
        "title": "水滴节奏打击垫",
        "creative_hook": "水滴落点和频率生成节奏型并解锁涟漪动效",
        "novelty_tags": ["rhythm-game", "playful", "novel", "python"],
        "brief": "用 Python 实现「水滴节奏打击垫」。创意钩子: 水滴落点和频率生成节奏型并解锁涟漪动效。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "水滴落点和频率生成节奏型并解锁涟漪动效; 同时验证 Python 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "py_compile",
            "min_files:4",
            "content_any:water|drop|ripple|rhythm",
            "source_target_coverage:src/**/*.py",
        ],
    },
    {
        "level": 2,
        "domain": "creative",
        "project_type": "sound_tool",
        "primary_language": "cpp",
        "title": "风铃代码翻译器",
        "creative_hook": "代码缩进和符号频率映射为风铃音色和节奏",
        "novelty_tags": ["sound-tool", "playful", "novel", "cpp"],
        "brief": "用 C++17 实现「风铃代码翻译器」。创意钩子: 代码缩进和符号频率映射为风铃音色和节奏。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "代码缩进和符号频率映射为风铃音色和节奏; 同时验证 C++17 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "cpp_compile",
            "min_files:4",
            "content_any:wind chime|indent|tone|frequency",
            "source_target_coverage:src/**/*.cpp",
        ],
    },
    # ── L3 ──
    {
        "level": 3,
        "domain": "game",
        "project_type": "puzzle_game",
        "primary_language": "java",
        "title": "符文电路拼图",
        "creative_hook": "符文块按颜色和方向连接电路以点亮古代机器",
        "novelty_tags": ["puzzle-game", "playful", "novel", "java"],
        "brief": "用 Java 实现「符文电路拼图」。创意钩子: 符文块按颜色和方向连接电路以点亮古代机器。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "符文块按颜色和方向连接电路以点亮古代机器; 同时验证 Java 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "java_compile",
            "min_files:5",
            "content_any:rune|circuit|puzzle|glyph",
            "source_target_coverage:src/main/java/**/*.java",
        ],
    },
    {
        "level": 3,
        "domain": "creative",
        "project_type": "creative_tool",
        "primary_language": "typescript",
        "title": "微型世界种子编辑器",
        "creative_hook": "编辑物理参数和生物特征并观察微型世界演化",
        "novelty_tags": ["creative-tool", "playful", "novel", "typescript"],
        "brief": "用 TypeScript 实现「微型世界种子编辑器」。创意钩子: 编辑物理参数和生物特征并观察微型世界演化。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "编辑物理参数和生物特征并观察微型世界演化; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:5",
            "content_any:world|seed|evolve|biome",
            "source_target_coverage:src/**/*.ts",
        ],
    },
    # ── L4 ──
    {
        "level": 4,
        "domain": "game",
        "project_type": "rhythm_adventure",
        "primary_language": "python",
        "title": "地牢节奏战斗引擎",
        "creative_hook": "攻击节拍、防御和弦与BOSS音乐状态机联动",
        "novelty_tags": ["rhythm-adventure", "playful", "novel", "python"],
        "brief": "用 Python 实现「地牢节奏战斗引擎」。创意钩子: 攻击节拍、防御和弦与BOSS音乐状态机联动。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "攻击节拍、防御和弦与BOSS音乐状态机联动; 同时验证 Python 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "py_compile",
            "min_files:7",
            "content_any:dungeon|rhythm|beat|boss",
            "source_target_coverage:src/**/*.py",
        ],
    },
    {
        "level": 4,
        "domain": "science_creative",
        "project_type": "science_tool",
        "primary_language": "rust",
        "title": "微型粒子碰撞模拟器",
        "creative_hook": "粒子种类、能量和角度决定衰变产物和新粒子发现",
        "novelty_tags": ["science-tool", "playful", "novel", "rust"],
        "brief": "用 Rust 实现「微型粒子碰撞模拟器」。创意钩子: 粒子种类、能量和角度决定衰变产物和新粒子发现。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "粒子种类、能量和角度决定衰变产物和新粒子发现; 同时验证 Rust 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "rust_compile",
            "min_files:7",
            "content_any:particle|collision|energy|decay",
            "source_target_coverage:src/**/*.rs",
        ],
    },
    # ── L5 ──
    {
        "level": 5,
        "domain": "internet_platform",
        "project_type": "social_platform",
        "primary_language": "javascript",
        "title": "幽灵信件社交网络",
        "creative_hook": "匿名信件漂流、笔迹指纹匹配和幽灵信箱寻宝",
        "novelty_tags": ["social-platform", "playful", "novel", "javascript"],
        "brief": "用 JavaScript 实现「幽灵信件社交网络」。创意钩子: 匿名信件漂流、笔迹指纹匹配和幽灵信箱寻宝。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "匿名信件漂流、笔迹指纹匹配和幽灵信箱寻宝; 同时验证 JavaScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "js_syntax",
            "package_scripts",
            "min_files:8",
            "content_any:ghost|letter|drift|mailbox",
            "source_target_coverage:src/**/*.js",
        ],
    },
    {
        "level": 5,
        "domain": "game",
        "project_type": "tower_defense",
        "primary_language": "typescript",
        "title": "声波塔防构造器",
        "creative_hook": "不同音频频率和波形构成防御塔并共振消灭入侵者",
        "novelty_tags": ["tower-defense", "playful", "novel", "typescript"],
        "brief": "用 TypeScript 实现「声波塔防构造器」。创意钩子: 不同音频频率和波形构成防御塔并共振消灭入侵者。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "不同音频频率和波形构成防御塔并共振消灭入侵者; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:8",
            "content_any:wave|frequency|tower|resonance",
            "source_target_coverage:src/**/*.ts",
        ],
    },
    # ── L6 ──
    {
        "level": 6,
        "domain": "creative",
        "project_type": "world_builder",
        "primary_language": "go",
        "title": "多人在线魔法学院注册处",
        "creative_hook": "学生选课、咒语树、学院杯和魔法事故实时同步",
        "novelty_tags": ["world-builder", "playful", "novel", "go"],
        "brief": "用 Go 实现「多人在线魔法学院注册处」。创意钩子: 学生选课、咒语树、学院杯和魔法事故实时同步。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "学生选课、咒语树、学院杯和魔法事故实时同步; 同时验证 Go 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "go_compile",
            "min_files:10",
            "content_any:magic|academy|spell|enrollment",
            "source_target_coverage:**/*.go",
        ],
    },
    {
        "level": 6,
        "domain": "game",
        "project_type": "simulation_game",
        "primary_language": "cpp",
        "title": "太空站生态闭环模拟",
        "creative_hook": "氧气、食物、水、废物和宇航员心理健康构成闭环",
        "novelty_tags": ["simulation-game", "playful", "novel", "cpp"],
        "brief": "用 C++17 实现「太空站生态闭环模拟」。创意钩子: 氧气、食物、水、废物和宇航员心理健康构成闭环。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "氧气、食物、水、废物和宇航员心理健康构成闭环; 同时验证 C++17 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "cpp_compile",
            "min_files:10",
            "content_any:oxygen|food|waste|astronaut",
            "source_target_coverage:src/**/*.cpp",
        ],
    },
    # ── L7 ──
    {
        "level": 7,
        "domain": "internet_platform",
        "project_type": "auction_platform",
        "primary_language": "javascript",
        "title": "奇幻文物拍卖行引擎",
        "creative_hook": "文物来源、鉴定师信誉和竞拍者心理战影响成交价",
        "novelty_tags": ["auction-platform", "playful", "novel", "javascript"],
        "brief": "用 JavaScript 实现「奇幻文物拍卖行引擎」。创意钩子: 文物来源、鉴定师信誉和竞拍者心理战影响成交价。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "文物来源、鉴定师信誉和竞拍者心理战影响成交价; 同时验证 JavaScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "js_syntax",
            "package_scripts",
            "min_files:11",
            "content_any:auction|artifact|bid|appraisal",
            "source_target_coverage:src/**/*.js",
        ],
    },
    {
        "level": 7,
        "domain": "music",
        "project_type": "music_game",
        "primary_language": "python",
        "title": "合唱团声部调度指挥",
        "creative_hook": "声部进退、音准偏差和情感表达影响整体演出评分",
        "novelty_tags": ["music-game", "playful", "novel", "python"],
        "brief": "用 Python 实现「合唱团声部调度指挥」。创意钩子: 声部进退、音准偏差和情感表达影响整体演出评分。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "声部进退、音准偏差和情感表达影响整体演出评分; 同时验证 Python 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "py_compile",
            "min_files:11",
            "content_any:choir|voice|pitch|ensemble",
            "source_target_coverage:src/**/*.py",
        ],
    },
    # ── L8 ──
    {
        "level": 8,
        "domain": "game",
        "project_type": "3a_game_prototype",
        "primary_language": "go",
        "title": "3A 废土拾荒者经济模拟",
        "creative_hook": "废料品质、辐射区、商队路线和派系战争驱动经济",
        "novelty_tags": ["3a-game-prototype", "playful", "novel", "go"],
        "brief": "用 Go 实现「3A 废土拾荒者经济模拟」。创意钩子: 废料品质、辐射区、商队路线和派系战争驱动经济。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "废料品质、辐射区、商队路线和派系战争驱动经济; 同时验证 Go 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "go_compile",
            "min_files:12",
            "content_any:scavenge|radiation|caravan|faction",
            "source_target_coverage:**/*.go",
        ],
    },
    {
        "level": 8,
        "domain": "science_creative",
        "project_type": "bio_simulation",
        "primary_language": "java",
        "title": "深海热泉生命起源模拟",
        "creative_hook": "化学梯度、温度和矿物催化原始生命自组织",
        "novelty_tags": ["bio-simulation", "playful", "novel", "java"],
        "brief": "用 Java 实现「深海热泉生命起源模拟」。创意钩子: 化学梯度、温度和矿物催化原始生命自组织。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "化学梯度、温度和矿物催化原始生命自组织; 同时验证 Java 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "java_compile",
            "min_files:12",
            "content_any:hydrothermal|mineral|emergence|gradient",
            "source_target_coverage:src/main/java/**/*.java",
        ],
    },
    # ── L9 ──
    {
        "level": 9,
        "domain": "game",
        "project_type": "grand_strategy",
        "primary_language": "typescript",
        "title": "文明演化沙盒观察者",
        "creative_hook": "文化基因、气候和随机事件驱动多文明兴衰模拟",
        "novelty_tags": ["grand-strategy", "playful", "novel", "typescript"],
        "brief": "用 TypeScript 实现「文明演化沙盒观察者」。创意钩子: 文化基因、气候和随机事件驱动多文明兴衰模拟。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "文化基因、气候和随机事件驱动多文明兴衰模拟; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:13",
            "content_any:civilization|meme|climate|collapse",
            "source_target_coverage:src/**/*.ts",
        ],
    },
    {
        "level": 9,
        "domain": "creative",
        "project_type": "knowledge_tool",
        "primary_language": "rust",
        "title": "跨维度图书馆编目引擎",
        "creative_hook": "书籍按主题、情感色彩、年代和读者梦境关联编目",
        "novelty_tags": ["knowledge-tool", "playful", "novel", "rust"],
        "brief": "用 Rust 实现「跨维度图书馆编目引擎」。创意钩子: 书籍按主题、情感色彩、年代和读者梦境关联编目。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "书籍按主题、情感色彩、年代和读者梦境关联编目; 同时验证 Rust 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "rust_compile",
            "min_files:13",
            "content_any:catalog|dimension|emotion|dream",
            "source_target_coverage:src/**/*.rs",
        ],
    },
    # ── L10 ──
    {
        "level": 10,
        "domain": "game",
        "project_type": "online_rts",
        "primary_language": "python",
        "title": "实时多人太空站紧急指挥",
        "creative_hook": "多个玩家同时指挥不同舱段应对连锁灾难事件",
        "novelty_tags": ["online-rts", "playful", "novel", "python"],
        "brief": "用 Python 实现「实时多人太空站紧急指挥」。创意钩子: 多个玩家同时指挥不同舱段应对连锁灾难事件。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "多个玩家同时指挥不同舱段应对连锁灾难事件; 同时验证 Python 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "py_compile",
            "min_files:14",
            "content_any:station|emergency|module|cascade",
            "source_target_coverage:src/**/*.py",
        ],
    },
    {
        "level": 10,
        "domain": "internet_platform",
        "project_type": "tabletop_platform",
        "primary_language": "cpp",
        "title": "奇幻世界多人桌游模拟器",
        "creative_hook": "自定义骰子、卡牌、地图和角色状态通过网络同步",
        "novelty_tags": ["tabletop-platform", "playful", "novel", "cpp"],
        "brief": "用 C++17 实现「奇幻世界多人桌游模拟器」。创意钩子: 自定义骰子、卡牌、地图和角色状态通过网络同步。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "自定义骰子、卡牌、地图和角色状态通过网络同步; 同时验证 C++17 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "cpp_compile",
            "min_files:14",
            "content_any:dice|card|board|tabletop",
            "source_target_coverage:src/**/*.cpp",
        ],
    },
    # ── L11 ──
    {
        "level": 11,
        "domain": "game",
        "project_type": "immersive_sim",
        "primary_language": "typescript",
        "title": "3A 沉浸式谍报沙盒原型",
        "creative_hook": "NPC记忆、社交网络、伪装和物理证据链联动",
        "novelty_tags": ["immersive-sim", "playful", "novel", "typescript"],
        "brief": "用 TypeScript 实现「3A 沉浸式谍报沙盒原型」。创意钩子: NPC记忆、社交网络、伪装和物理证据链联动。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "NPC记忆、社交网络、伪装和物理证据链联动; 同时验证 TypeScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "ts_syntax",
            "package_scripts",
            "min_files:15",
            "content_any:espionage|disguise|evidence|memory",
            "source_target_coverage:src/**/*.ts",
        ],
    },
    {
        "level": 11,
        "domain": "science_creative",
        "project_type": "climate_sim",
        "primary_language": "javascript",
        "title": "行星气候工程决策模拟器",
        "creative_hook": "太阳辐射、洋流、碳循环和政策博弈共同影响气候",
        "novelty_tags": ["climate-sim", "playful", "novel", "javascript"],
        "brief": "用 JavaScript 实现「行星气候工程决策模拟器」。创意钩子: 太阳辐射、洋流、碳循环和政策博弈共同影响气候。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "太阳辐射、洋流、碳循环和政策博弈共同影响气候; 同时验证 JavaScript 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "js_syntax",
            "package_scripts",
            "min_files:15",
            "content_any:climate|radiation|carbon|policy",
            "source_target_coverage:src/**/*.js",
        ],
    },
    # ── L12 ──
    {
        "level": 12,
        "domain": "game",
        "project_type": "metaverse_engine",
        "primary_language": "java",
        "title": "多人跨维度元宇宙传送门引擎",
        "creative_hook": "物理规则、材质、NPC行为和经济系统在维度间可变",
        "novelty_tags": ["metaverse-engine", "playful", "novel", "java"],
        "brief": "用 Java 实现「多人跨维度元宇宙传送门引擎」。创意钩子: 物理规则、材质、NPC行为和经济系统在维度间可变。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "物理规则、材质、NPC行为和经济系统在维度间可变; 同时验证 Java 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "java_compile",
            "min_files:16",
            "content_any:portal|dimension|physics|metaverse",
            "source_target_coverage:src/main/java/**/*.java",
        ],
    },
    {
        "level": 12,
        "domain": "creative",
        "project_type": "ai_director_os",
        "primary_language": "python",
        "title": "AI 叙事导演操作系统",
        "creative_hook": "统一管理剧情弧、角色情感、玩家行为和动态难度",
        "novelty_tags": ["ai-director-os", "playful", "novel", "python"],
        "brief": "用 Python 实现「AI 叙事导演操作系统」。创意钩子: 统一管理剧情弧、角色情感、玩家行为和动态难度。必须交付真实可运行代码、README、示例数据或种子内容,并包含至少一个可执行入口和一个能验证核心规则的脚本/测试/检查。",
        "test_focus": "统一管理剧情弧、角色情感、玩家行为和动态难度; 同时验证 Python 产物结构、入口可运行性和核心领域规则。",
        "checks": [
            "py_compile",
            "min_files:16",
            "content_any:narrative|arc|emotion|difficulty",
            "source_target_coverage:src/**/*.py",
        ],
    },
]


def load_existing() -> list[dict]:
    data = json.loads(_V2_PATH.read_text(encoding="utf-8"))
    return data.get("projects", [])


def merge_and_reindex(existing: list[dict], new_projects: list[dict]) -> list[dict]:
    """Append new projects and reassign sequential IDs per level."""
    # Group existing by level
    by_level: dict[int, list[dict]] = {}
    for p in existing:
        lvl = int(p["level"])
        by_level.setdefault(lvl, []).append(p)

    # Add new projects to their respective levels
    for p in new_projects:
        lvl = int(p["level"])
        by_level.setdefault(lvl, []).append(p)

    # Flatten in level order and reassign IDs
    result: list[dict] = []
    seq = 1
    for level in sorted(by_level.keys()):
        for p in by_level[level]:
            p["id"] = f"L{level}-{seq:02d}"
            seq += 1
            result.append(p)
    return result


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    existing = load_existing()
    new_projects = [p for p in NEW_PROJECTS if isinstance(p, dict)]

    merged = merge_and_reindex(existing, new_projects)

    # Validate no duplicate IDs
    ids = [p["id"] for p in merged]
    dupes = [x for x in ids if ids.count(x) > 1]
    if dupes:
        print(f"ERROR: duplicate IDs found: {sorted(set(dupes))}", file=sys.stderr)
        sys.exit(1)

    # Build output
    data = json.loads(_V2_PATH.read_text(encoding="utf-8"))
    data["projects"] = merged
    data["comment"] = (
        "Polaris 全链路创意实战评估矩阵 v2 — 独立默认题库,不继承 v1。"
        f"共 {len(merged)} 个 L1-L12 项目,全部要求有趣、新颖、有明确 creative_hook,"
        "覆盖 TypeScript/JavaScript、Go、Rust、C++、Java、Python,"
        "并覆盖多人在线游戏、3A 游戏原型、音乐/声音、创造工具、创意平台、"
        "专业软件的奇思妙想版本、科幻/奇幻/沉浸式行业软件等。"
        "projects_v1.json 保留为历史基线,可通过 --projects-file 显式运行。"
    )

    output = json.dumps(data, ensure_ascii=False, indent=1) + "\n"

    if dry_run:
        print(f"[dry-run] Would write {len(merged)} projects to {_V2_PATH}")
        # Print language distribution
        langs: dict[str, int] = {}
        for p in merged:
            lang = p.get("primary_language", "unknown")
            langs[lang] = langs.get(lang, 0) + 1
        for lang, count in sorted(langs.items()):
            print(f"  {lang}: {count}")
        # Print per-level counts
        levels: dict[int, int] = {}
        for p in merged:
            lvl = int(p["level"])
            levels[lvl] = levels.get(lvl, 0) + 1
        for lvl in sorted(levels):
            print(f"  L{lvl}: {levels[lvl]} projects")
    else:
        _V2_PATH.write_text(output, encoding="utf-8")
        print(f"Wrote {len(merged)} projects to {_V2_PATH}")


if __name__ == "__main__":
    main()
