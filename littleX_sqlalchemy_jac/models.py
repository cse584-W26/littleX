"""SQLAlchemy models — pure Python, imported by Jac endpoints."""

from sqlalchemy import create_engine, Column, Integer, String, DateTime, ForeignKey, Table, Text, select, delete, union_all
from sqlalchemy.orm import Session, DeclarativeBase, Mapped, mapped_column, relationship, aliased, sessionmaker
from typing import List, Optional, Set
from datetime import datetime
import os


class Base(DeclarativeBase):
    pass


# Module-level state
_engine = None
_SessionLocal = None


def init_db():
    global _engine, _SessionLocal
    database_url = os.environ.get("DATABASE_URL", "sqlite+pysqlite:///littlex.db")
    print(f"Database URL: {database_url}")
    _engine = create_engine(database_url, echo=False)
    _SessionLocal = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)
    print("Database initialized.")


def get_session() -> Session:
    global _engine, _SessionLocal
    if _engine is None:
        init_db()
    return _SessionLocal()


def get_engine():
    global _engine
    if _engine is None:
        init_db()
    return _engine


# Association tables
following_table = Table(
    "following", Base.metadata,
    Column("followee_id", Integer, ForeignKey("user.id"), primary_key=True),
    Column("follower_id", Integer, ForeignKey("user.id"), primary_key=True),
)

like_table = Table(
    "likes", Base.metadata,
    Column("tweet_id", Integer, ForeignKey("tweet.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("user.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "user"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handle: Mapped[str] = mapped_column(String, default="")
    username: Mapped[str] = mapped_column(String, unique=True)
    password: Mapped[str] = mapped_column(String)
    bio: Mapped[Optional[str]] = mapped_column(String, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    following: Mapped[Set["User"]] = relationship(
        "User", secondary=following_table,
        primaryjoin="User.id == following.c.follower_id",
        secondaryjoin="User.id == following.c.followee_id",
        back_populates="followers",
    )
    followers: Mapped[Set["User"]] = relationship(
        "User", secondary=following_table,
        primaryjoin="User.id == following.c.followee_id",
        secondaryjoin="User.id == following.c.follower_id",
        back_populates="following",
    )
    tweets: Mapped[List["Tweet"]] = relationship(back_populates="author")

    def report(self, include_relationships=False):
        res = {
            "id": self.id,
            "username": self.handle,
            "bio": self.bio,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }
        if include_relationships:
            res["following"] = [{"id": f.id, "username": f.username} for f in self.following]
            res["followers"] = [{"id": f.id, "username": f.username} for f in self.followers]
            res["tweets"] = [t.report() for t in self.tweets]
        return res


class Comment(Base):
    __tablename__ = "comment"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    handle: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    tweet_id: Mapped[int] = mapped_column(Integer, ForeignKey("tweet.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tweet: Mapped["Tweet"] = relationship(back_populates="comments")

    def report(self):
        return {
            "content": self.content,
            "username": self.handle,
            "created_at": self.created_at.isoformat() if self.created_at else "",
        }


class Tweet(Base):
    __tablename__ = "tweet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int] = mapped_column(Integer, ForeignKey("user.id"))
    author: Mapped["User"] = relationship(back_populates="tweets")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    likes: Mapped[List["User"]] = relationship(secondary=like_table)
    comments: Mapped[List["Comment"]] = relationship(back_populates="tweet")

    def report(self):
        return {
            "id": self.id,
            "content": self.content,
            "author_username": self.author.username if self.author else "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "likes": [u.username for u in self.likes],
            "comments": [c.report() for c in self.comments],
        }
