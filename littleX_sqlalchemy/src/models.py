from src import db
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Table, Column, ForeignKey
from typing import List, Optional, Set
from datetime import datetime

# association tables use SqlAlchemy CORE tables, not the ORM version
# https://docs.sqlalchemy.org/en/21/orm/basic_relationships.html#many-to-many
# self referential docs
# https://docs.sqlalchemy.org/en/21/orm/join_conditions.html#self-referential-many-to-many-relationship
following_table = Table(
    "following",
    db.Model.metadata,
    Column('followee_id', ForeignKey('user.id'), primary_key=True),
    Column('follower_id', ForeignKey('user.id'), primary_key=True),
)

like_table = Table(
    "likes",
    db.Model.metadata,
    Column('tweet_id', ForeignKey('tweet.id'), primary_key=True),
    Column('user_id', ForeignKey('user.id'), primary_key=True),
)

class User(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    # public facing
    handle: Mapped[str]
    # login username
    username: Mapped[str]
    # plaintest password okay since this is just a benchmarking baseline
    password: Mapped[str]
    bio: Mapped[Optional[str]]
    created_at: Mapped[datetime]
    following: Mapped[Set['User']] = relationship(
        'User',
        secondary=following_table,
        primaryjoin=id == following_table.c.follower_id,
        secondaryjoin=id == following_table.c.followee_id,
        back_populates="followers"
    )
    followers: Mapped[Set['User']] =  relationship(
        'User',
        secondary=following_table,
        primaryjoin=id == following_table.c.followee_id,
        secondaryjoin=id == following_table.c.follower_id,
        back_populates="following"
    )
    tweets: Mapped[List['Tweet']] = relationship(back_populates='author')
    
    # include_relationships include the tweets, follows, and followings
    def report(self, include_relationships: bool=False):
       res = {
           "id": self.id,
           "username": self.handle,
           "bio": self.bio,
           "created_at": self.created_at.isoformat()
       }
       
       if include_relationships:
           res['following'] = [{"id": f.id, "username": f.username} for f in self.following]
           res['followers'] = [{"id": f.id, "username": f.username} for f in self.followers]
           res['tweets'] = [t.report() for t in self.tweets]
           
       return res
    
class Comment(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    handle: Mapped[str]
    # we could link out and look up the username, but littleX isn't doing that, so ignore for now
    #user_id: Mapped[id] = mapped_column(ForeignKey('user.id'))
    content: Mapped[str]
    tweet_id: Mapped[int] = mapped_column(ForeignKey('tweet.id'))
    created_at: Mapped[datetime]
    tweet: Mapped['Tweet'] = relationship(back_populates="comments")
    
    def report(self):
       return {
           "content": self.content,
           "username": self.handle,
           "created_at": self.created_at.isoformat(),
       }
    

class Tweet(db.Model):
    id: Mapped[int] = mapped_column(primary_key=True)
    content: Mapped[str]
    author_id: Mapped[int] = mapped_column(ForeignKey('user.id'))
    author: Mapped['User'] = relationship(back_populates='tweets')
    created_at: Mapped[datetime]
    likes: Mapped[List['User']] = relationship(secondary=like_table)
    comments: Mapped[List['Comment']] = relationship(back_populates='tweet')
    
    def report(self):
       return {
           "id": self.id,
           "content": self.content,
           "author_username": self.author.username,
           "created_at": self.created_at.isoformat(),
           "likes": [u.username for u in self.likes],
           "comments": [c.report() for c in self.comments]
       }
       
    def report_likes(self):
        return [u.username for u in self.likes]
        
    
    