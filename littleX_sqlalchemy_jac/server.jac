"""LittleX SQLAlchemy — def:priv endpoints with JWT auth. No walkers, no graph.
Auth (register/login/JWT validation) handled by jac-scale — same overhead as walker version.
SQL user created on first setup_profile call, keyed by jac root UUID."""

import from models { User, Tweet, Comment, Base, following_table, like_table, get_session, get_engine }
import from sqlalchemy { select, delete, union_all }
import from sqlalchemy.orm { aliased }
import from datetime { datetime }

# ============================================================
# Helper: get SQL user from authenticated user's jac root UUID
# ============================================================

def _get_root_id() -> str {
    import from jaclang { JacRuntimeInterface as Jac }
    ctx = Jac.get_context();
    return str(ctx.user_root.id) if ctx.user_root else "";
}

def _get_sql_user(session: any) -> any {
    root_id = _get_root_id();
    if not root_id { return None; }
    return session.execute(select(User).filter_by(username=root_id)).scalar();
}

# ============================================================
# Public endpoints
# ============================================================

def:pub clear_data() -> dict {
    engine = get_engine();
    Base.metadata.drop_all(engine);
    Base.metadata.create_all(engine);
    return {"success": True, "message": "Database reset"};
}

def:pub get_all_profiles() -> list {
    session = get_session();
    results = session.execute(select(User.id, User.handle, User.bio)).all();
    profiles = [{"id": r[0], "username": r[1], "bio": r[2]} for r in results];
    session.close();
    return profiles;
}

# ============================================================
# Private endpoints — JWT auth required (same pipeline as walkers)
# ============================================================

def:priv setup_profile(username: str = "", bio: str = "") -> dict {
    root_id = _get_root_id();
    session = get_session();
    user = session.execute(select(User).filter_by(username=root_id)).scalar();
    if not user {
        user = User(
            username=root_id,
            handle=username if username else root_id,
            password="",
            created_at=datetime.utcnow(),
            bio=bio
        );
        session.add(user);
        session.commit();
    } else {
        if username { user.handle = username; }
        if bio { user.bio = bio; }
        session.commit();
    }
    result = user.report();
    session.close();
    return result;
}

def:priv load_feed(search_query: str = "") -> list {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return [];
    }
    other_user = aliased(User);
    eligible_users = aliased(User, union_all(
        select(other_user.id).where(User.id == user.id).join(other_user, User.following),
        select(User.id).where(User.id == user.id)
    ).subquery());
    tweets_query = select(Tweet).join_from(eligible_users, eligible_users.tweets);
    if search_query and len(search_query) > 0 {
        tweets_query = tweets_query.filter(Tweet.content.ilike(f"%{search_query}%"));
    }
    tweets_query = tweets_query.order_by(Tweet.created_at);
    results = session.execute(tweets_query).scalars().all();
    feed = [r.report() for r in results];
    session.close();
    return feed;
}

def:priv get_profile() -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    result = user.report(True);
    session.close();
    return result;
}

def:priv follow_user(target_id: int) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    target = session.execute(select(User).filter_by(id=target_id)).scalar();
    if not target {
        session.close();
        return {"error": "User not found"};
    }
    if user not in target.followers {
        target.followers.add(user);
        session.commit();
    }
    session.close();
    return {"success": True};
}

def:priv unfollow_user(target_id: int) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    result = session.execute(
        delete(following_table)
        .where(following_table.c.followee_id == target_id)
        .where(following_table.c.follower_id == user.id)
    );
    if result.rowcount == 0 {
        session.close();
        return {"error": "Not following"};
    }
    session.commit();
    session.close();
    return {"success": True};
}

def:priv create_tweet(content: str) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    new_tweet = Tweet(content=content, author_id=user.id, created_at=datetime.utcnow());
    session.add(new_tweet);
    session.commit();
    result = new_tweet.report();
    session.close();
    return result;
}

def:priv delete_tweet(tweet_id: int) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    result = session.execute(
        delete(Tweet).where(Tweet.id == tweet_id).where(Tweet.author_id == user.id)
    );
    if result.rowcount == 0 {
        session.close();
        return {"error": "Tweet not found"};
    }
    session.commit();
    session.close();
    return {"success": True};
}

def:priv like_tweet(tweet_id: int) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    tweet = session.execute(select(Tweet).where(Tweet.id == tweet_id)).scalar();
    if not tweet {
        session.close();
        return {"error": "Tweet not found"};
    }
    existing = session.execute(
        select(like_table).where(like_table.c.tweet_id == tweet_id).where(like_table.c.user_id == user.id)
    ).first() is not None;
    if not existing { tweet.likes.append(user); }
    else { tweet.likes.remove(user); }
    session.commit();
    result = {"liked": not existing, "likes": [u.username for u in tweet.likes]};
    session.close();
    return result;
}

def:priv add_comment(tweet_id: int, content: str) -> dict {
    session = get_session();
    user = _get_sql_user(session);
    if not user {
        session.close();
        return {"error": "No profile"};
    }
    tweet = session.execute(select(Tweet).where(Tweet.id == tweet_id)).scalar();
    if not tweet {
        session.close();
        return {"error": "Tweet not found"};
    }
    new_comment = Comment(handle=user.handle, content=content, tweet_id=tweet.id, created_at=datetime.utcnow());
    session.add(new_comment);
    session.commit();
    result = {"success": True, "comment": new_comment.report()};
    session.close();
    return result;
}

def:pub import_data(data: dict) -> dict {
    session = get_session();
    all_user_ids = session.scalars(select(User.id)).all();
    for user_data in data.values() {
        user_obj = session.execute(select(User).filter_by(username=user_data["email"])).scalar();
        if not user_obj { continue; }
        for t in user_data["tweets"] {
            new_tweet = Tweet(content=t["content"], author_id=user_obj.id, created_at=datetime.fromisoformat(t["timestamp"]));
            session.add(new_tweet);
        }
        session.flush();
        follows = [{"followee_id": fid, "follower_id": user_obj.id} for fid in user_data["following"]];
        if len(follows) > 0 { session.execute(following_table.insert(), follows); }
    }
    viewer = session.execute(select(User).filter_by(handle="Viewer")).scalar();
    if viewer {
        viewer_follows = [{"followee_id": uid, "follower_id": viewer.id} for uid in all_user_ids if uid != viewer.id];
        if len(viewer_follows) > 0 { session.execute(following_table.insert(), viewer_follows); }
    }
    session.commit();
    session.close();
    return {"success": True};
}
