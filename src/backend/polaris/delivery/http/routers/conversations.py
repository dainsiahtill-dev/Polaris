"""Conversation API Router - 对话会话管理

提供对话会话的 CRUD API，支持持久化存储和恢复。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException
from polaris.cells.roles.session.public import Conversation, ConversationMessage, get_db
from pydantic import BaseModel, Field
from sqlalchemy import desc

from ._shared import require_auth

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(prefix="/v2/conversations", tags=["conversations"])


# 请求/响应模型


class MessageCreate(BaseModel):
    role: str = Field(..., description="角色: user, assistant, system")
    content: str = Field(..., description="消息内容")
    thinking: str | None = Field(None, description="思考过程")
    meta: dict[str, Any] | None = Field(None, description="元数据")


class ConversationCreate(BaseModel):
    title: str | None = Field(None, description="对话标题")
    role: str = Field(..., description="角色标识: pm, architect, director, qa")
    workspace: str | None = Field(None, description="工作区路径")
    context_config: dict[str, Any] | None = Field(None, description="上下文配置")
    initial_message: MessageCreate | None = Field(None, description="初始消息")


class ConversationUpdate(BaseModel):
    title: str | None = Field(None, description="对话标题")
    context_config: dict[str, Any] | None = Field(None, description="上下文配置")


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    role: str
    workspace: str | None
    context_config: dict[str, Any]
    message_count: int
    created_at: str
    updated_at: str
    messages: list[dict[str, Any]] | None = None


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    thinking: str | None
    meta: dict[str, Any]
    created_at: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationResponse]
    total: int


# 依赖注入数据库会话
async def get_db_session():
    """获取数据库会话"""
    from ..models.conversation import get_db

    db_gen = get_db()
    async for db in db_gen:
        yield db


# API 端点


@router.post("", response_model=ConversationResponse, dependencies=[Depends(require_auth)])
async def create_conversation(
    data: ConversationCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """创建新对话会话"""
    # 创建会话
    conversation = Conversation(
        title=data.title or f"新对话 - {data.role}",
        role=data.role,
        workspace=data.workspace,
        context_config=json.dumps(data.context_config) if data.context_config else None,
    )
    db.add(conversation)
    db.flush()  # 获取 ID

    # 如果有初始消息，添加
    if data.initial_message:
        msg = ConversationMessage(
            conversation_id=conversation.id,
            sequence=1,
            role=data.initial_message.role,
            content=data.initial_message.content,
            thinking=data.initial_message.thinking,
            meta=json.dumps(data.initial_message.meta) if data.initial_message.meta else None,
        )
        db.add(msg)
        setattr(conversation, "message_count", 1)  

    db.commit()
    db.refresh(conversation)

    return conversation.to_dict(include_messages=True)


@router.get("", response_model=ConversationListResponse, dependencies=[Depends(require_auth)])
async def list_conversations(
    role: str | None = None,
    workspace: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """列出对话会话"""
    query = db.query(Conversation).filter(Conversation.is_deleted == 0)

    if role:
        query = query.filter(Conversation.role == role)
    if workspace:
        query = query.filter(Conversation.workspace == workspace)

    total = query.count()
    conversations = query.order_by(desc(Conversation.updated_at)).offset(offset).limit(limit).all()

    return {
        "conversations": [c.to_dict(include_messages=False) for c in conversations],
        "total": total,
    }


@router.get("/{conversation_id}", response_model=ConversationResponse, dependencies=[Depends(require_auth)])
async def get_conversation(
    conversation_id: str,
    include_messages: bool = True,
    message_limit: int = 1000,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """获取单个对话详情"""
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.is_deleted == 0).first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return conversation.to_dict(
        include_messages=include_messages,
        message_limit=message_limit,
    )


@router.put("/{conversation_id}", response_model=ConversationResponse, dependencies=[Depends(require_auth)])
async def update_conversation(
    conversation_id: str,
    data: ConversationUpdate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """更新对话信息"""
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.is_deleted == 0).first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if data.title is not None:
        setattr(conversation, "title", data.title)  
    if data.context_config is not None:
        setattr(conversation, "context_config", json.dumps(data.context_config))  

    db.commit()
    db.refresh(conversation)

    return conversation.to_dict(include_messages=False)


@router.delete("/{conversation_id}", dependencies=[Depends(require_auth)])
async def delete_conversation(
    conversation_id: str,
    hard: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """删除对话（软删除或硬删除）"""
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.is_deleted == 0).first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if hard:
        db.delete(conversation)
    else:
        setattr(conversation, "is_deleted", 1)  

    db.commit()

    return {"ok": True, "deleted_id": conversation_id}


# 消息管理


@router.post("/{conversation_id}/messages", response_model=MessageResponse, dependencies=[Depends(require_auth)])
async def add_message(
    conversation_id: str,
    data: MessageCreate,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """添加消息到对话"""
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.is_deleted == 0).first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 获取当前最大序号
    max_seq = db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation_id).count()

    msg = ConversationMessage(
        conversation_id=conversation_id,
        sequence=max_seq + 1,
        role=data.role,
        content=data.content,
        thinking=data.thinking,
        meta=json.dumps(data.meta) if data.meta else None,
    )
    db.add(msg)

    # 更新消息计数和时间戳
    setattr(conversation, "message_count", max_seq + 1)  

    db.commit()
    db.refresh(msg)

    return msg.to_dict()


@router.get("/{conversation_id}/messages", response_model=list[MessageResponse], dependencies=[Depends(require_auth)])
async def list_messages(
    conversation_id: str,
    limit: int = 1000,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """列出对话消息"""
    messages = (
        db.query(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.sequence)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return [m.to_dict() for m in messages]


@router.post("/{conversation_id}/messages/batch", dependencies=[Depends(require_auth)])
async def add_messages_batch(
    conversation_id: str,
    messages: list[MessageCreate],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量添加消息（用于保存完整对话）"""
    conversation = (
        db.query(Conversation).filter(Conversation.id == conversation_id, Conversation.is_deleted == 0).first()
    )

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # 获取当前最大序号
    max_seq = db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation_id).count()

    for i, data in enumerate(messages):
        msg = ConversationMessage(
            conversation_id=conversation_id,
            sequence=max_seq + i + 1,
            role=data.role,
            content=data.content,
            thinking=data.thinking,
            meta=json.dumps(data.meta) if data.meta else None,
        )
        db.add(msg)

    setattr(conversation, "message_count", max_seq + len(messages))  
    db.commit()

    return {"ok": True, "added_count": len(messages)}


@router.delete("/{conversation_id}/messages/{message_id}", dependencies=[Depends(require_auth)])
async def delete_message(
    conversation_id: str,
    message_id: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """删除单条消息"""
    msg = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.id == message_id,
            ConversationMessage.conversation_id == conversation_id,
        )
        .first()
    )

    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    db.delete(msg)

    # 重新计算消息计数
    conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conversation:
        # SQLAlchemy Column 赋值需要忽略类型检查
        count_value = (
            db.query(ConversationMessage).filter(ConversationMessage.conversation_id == conversation_id).count()
            - 1  # 因为还没 commit
        )
        setattr(conversation, "message_count", count_value)  

    db.commit()

    return {"ok": True, "deleted_id": message_id}
