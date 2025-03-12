from datetime import datetime
from typing import Annotated
from fastapi import Depends
from sqlalchemy import func
from sqlmodel import Field, Relationship, Session, SQLModel, create_engine


class KnowledgeBase(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = Field(default=None)
    created_at: datetime | None = Field(default_factory=func.now)
    doc_list: list["DocFile"] = Relationship(cascade_delete=True)


class DocFile(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    kgb_id: int = Field(foreign_key="knowledgebase.id", ondelete="CASCADE")
    filename: str = Field(index=True)
    suffix: str = Field(index=True)
    content: bytes = Field()
    created_at: datetime = Field(default_factory=func.now)
    md_list: list["MarkdownFile"] = Relationship(cascade_delete=True)


class MarkdownFile(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    doc_id: int = Field(foreign_key="docfile.id", ondelete="CASCADE")
    content: str = Field()
    summary: str = Field(default="")
    created_at: datetime = Field(default_factory=func.now)


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
