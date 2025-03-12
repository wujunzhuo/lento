from datetime import datetime
from typing import Annotated
from fastapi import Depends
from sqlalchemy import func, DateTime
from sqlmodel import Field, Column, Session, SQLModel, create_engine


class KnowledgeBase(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    created_at: datetime | None = Field(default_factory=func.now)


class DocFile(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    kb_id: int = Field(foreign_key="knowledgebase.id")
    filename: str = Field(index=True)
    suffix: str = Field(index=True)
    content: bytes = Field()
    created_at: datetime = Field(default_factory=func.now)


class MarkdownFile(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="docfile.id")
    content: str = Field()
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=func.now)
    updated_at: datetime = Field(
        sa_column=Column(DateTime(), onupdate=func.now()))


sqlite_file_name = "database.db"
sqlite_url = f"sqlite:///{sqlite_file_name}"

connect_args = {"check_same_thread": False}
engine = create_engine(sqlite_url, connect_args=connect_args)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
