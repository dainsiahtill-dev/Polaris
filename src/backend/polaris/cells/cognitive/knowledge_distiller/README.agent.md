# cognitive.knowledge_distiller Cell

## 职责
Distill patterns from completed sessions and retrieve relevant distilled knowledge for future task execution. Provides session post-processing and knowledge retrieval capabilities.

## 公开契约
模块: polaris.cells.cognitive.knowledge_distiller.public.contracts

## 依赖
- roles.session
- context.catalog

## 效果
- fs.read:workspace/**
- fs.write:workspace/.polaris/knowledge_distiller/*
