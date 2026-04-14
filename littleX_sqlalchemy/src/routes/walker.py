import time
from src import app, build_error, db, get_validated_body
from src.models import (
    User, Tweet, Comment, Channel,
    like_table, following_table, channel_members_table,
)
from flask import Blueprint, request, g, abort, jsonify
from sqlalchemy.orm import aliased, selectinload, joinedload
from datetime import datetime

bp = Blueprint('walker', __name__)

# Returns a response shaped to satisfy BOTH:
#   - the original Jac graph backend bench client (reads data.reports[0])
#   - the JacSQL def:priv bench client (reads data.result, where result is a
#     dict for singleton-returning endpoints and a list for collection ones)
#
# Pass `result` as a dict (or {success:True} placeholder) for singleton routes.
# Pass `result` as a list and `reports` matching the same list for collection
# routes. The helpers `singleton_response` and `list_response` below wrap the
# common cases.
def build_response(reports, result=None, status_code: int = 200):
    if result is None:
        result = reports[0] if len(reports) == 1 else reports
    response = jsonify({
        "data": {
            "result": result,
            "reports": reports,
        }
    })
    response.status_code = status_code
    return response


def singleton_response(payload: dict, status_code: int = 200):
    """For endpoints returning a single object (create/update/follow/etc).

    JacSQL exposes payload at data.result; original walker clients read it
    from data.reports[0]. Both views see the same dict.
    """
    return build_response([payload], result=payload, status_code=status_code)


def list_response(items: list, status_code: int = 200):
    """For endpoints returning a collection (load_feed, get_all_profiles).

    JacSQL exposes the list at data.result; original walker clients read it
    from data.reports as a list of items.
    """
    return build_response(items, result=items, status_code=status_code)

# Endpoints that mirror JacSQL `def:pub` (no auth required).
PUBLIC_ENDPOINTS = {'get_all_profiles', 'import_data'}


@bp.before_request
def check_login():
    # Match JacSQL: get_all_profiles and clear_data are public; everything
    # else requires authentication. (clear_data is registered at the app
    # level, not on this blueprint, so it bypasses this hook entirely.)
    endpoint = (request.endpoint or '').rsplit('.', 1)[-1]
    if endpoint in PUBLIC_ENDPOINTS:
        return

    auth_header = request.headers.get('Authorization')
    if not auth_header:
        abort(build_error('Not Logged In', 401))

    # naively assumes the auth header is in the form "Bearer username"
    username = auth_header.replace('Bearer ', '')
    existing_user = db.session.execute(db.select(User).filter_by(username=username)).scalar()
    if not existing_user:
        abort(build_error(
            f'User with username {username} not found. Did the database get cleared?',
            400,
        ))
    g.user = existing_user

@bp.route('/setup_profile', methods=['POST'])
def setup_profile():
    # Match JacSQL: username and bio are optional; only update what's provided.
    if not request.is_json:
        abort(build_error('Expected JSON body', 415))
    data = request.get_json() or {}
    if data.get('username'):
        g.user.handle = data['username']
    if 'bio' in data:
        g.user.bio = data['bio']
    db.session.commit()
    return singleton_response(g.user.report())

@bp.route('/load_feed', methods=['POST'])
def load_feed():
    # Match JacSQL: search_query is optional and defaults to "".
    data = request.get_json(silent=True) or {}
    search_query = data.get('search_query', '')

    other_user = aliased(User)
    eligible_users = aliased(User, db.union_all(db
        .select(other_user.id)
        .where(User.id == g.user.id)
        .join(other_user, User.following),
        db.select(User.id).where(User.id == g.user.id)
    ).subquery())
    # Eager-load Tweet.author / .likes / .comments so Tweet.report() does not
    # trigger one extra query per tweet (the classic ORM N+1). joinedload is
    # the right tool for the many-to-one Tweet.author (FK is in the row),
    # while selectinload handles the one-to-many likes/comments without a
    # cartesian explosion. This is the standard ORM-developer fix; we are
    # NOT writing raw SQL or composite covering indexes here.
    tweets_query = (db
        .select(Tweet)
        .join_from(eligible_users, eligible_users.tweets)
        .options(
            joinedload(Tweet.author),
            selectinload(Tweet.likes),
            selectinload(Tweet.comments),
        )
    )

    if search_query:
        # case-insensitive filter for tweets containing the query
        tweets_query = tweets_query.filter(Tweet.content.ilike(f'%{search_query}%'))

    tweets_query = tweets_query.order_by(Tweet.created_at)
    if 'limit' in data:
        tweets_query = tweets_query.limit(data['limit'])

    results = db.session.execute(tweets_query).unique().scalars().all()
    return list_response([r.report() for r in results])

@bp.route('/get_profile', methods=['POST'])
def get_profile():
    # Re-fetch g.user with eager-loaded relationships so User.report(True)
    # does not trigger N+1: without this, .report() lazy-loads following,
    # followers, tweets, and then per-tweet author/likes/comments — easily
    # 1000+ round-trips for a celebrity profile. selectinload batches the
    # fan-outs into a small number of WHERE id IN (...) queries.
    user = db.session.execute(
        db.select(User)
        .where(User.id == g.user.id)
        .options(
            selectinload(User.following),
            selectinload(User.followers),
            selectinload(User.tweets).joinedload(Tweet.author),
            selectinload(User.tweets).selectinload(Tweet.likes),
            selectinload(User.tweets).selectinload(Tweet.comments),
        )
    ).unique().scalar()
    return singleton_response(user.report(True))

@bp.route('/get_all_profiles', methods=['POST'])
def get_all_profiles():
    results = db.session.execute(db.select(User.id, User.handle, User.bio)).all()
    profiles = [{'id': r[0], 'username': r[1], 'bio': r[2]} for r in results]
    return list_response(profiles)

@bp.route('/follow_user', methods=['POST'])
def follow_user():
    data = get_validated_body(['target_id'])
    
    target = db.session.execute(db.select(User).filter_by(
        id = data['target_id'])
    ).scalar()
    
    if not target:
        return build_error('User not found', 400)
    
    if g.user not in target.followers:
        target.followers.add(g.user)
        db.session.commit()

    return singleton_response({"success": True})

@bp.route('/unfollow_user', methods=['POST'])
def unfollow_user():
    data = get_validated_body(['target_id'])
    
    result = db.session.execute(db
        .delete(following_table)
        .where(following_table.c.followee_id == data['target_id'])
        .where(following_table.c.follower_id == g.user.id)
    )
    
    if result.rowcount == 0:
        return build_error('User not found', 400)

    db.session.commit()
    return singleton_response({"success": True})

@bp.route('/create_tweet', methods=['POST'])
def create_tweet():
    data = get_validated_body(['content'])
    
    new_tweet = Tweet(
        content=data['content'],
        author_id=g.user.id,
        created_at=datetime.utcnow(),
        likes=[],
        comments=[]
    )
    
    db.session.add(new_tweet)
    db.session.commit()
    return singleton_response(new_tweet.report())

@bp.route('/delete_tweet', methods=['POST'])
def delete_tweet():
    data = get_validated_body(['tweet_id'])
    
    result = db.session.execute(db
        .delete(Tweet)
        .where(Tweet.id == data['tweet_id'])
        .where(Tweet.author_id == g.user.id)
    )
    
    if result.rowcount == 0:
        return build_error('Tweet not found', 404)

    db.session.commit()
    return singleton_response({'success': True})

@bp.route('/like_tweet', methods=['POST'])
def like_tweet():
    data = get_validated_body(['tweet_id'])

    tweet = db.session.execute(db
        .select(Tweet)
        .where(Tweet.id == data['tweet_id'])
    ).scalar()

    if not tweet:
        return build_error('Tweet not found', 404)

    # BUG FIX: previously this only filtered on tweet_id, so it returned
    # True whenever ANYONE had liked the tweet — meaning a second user could
    # never like a tweet that already had a like, and the like-toggle was
    # functionally wrong. Adding the user_id filter checks whether THIS user
    # currently likes this tweet, which is what the toggle semantics need.
    # The (tweet_id, user_id) composite PK on like_table makes this an O(1)
    # indexed lookup.
    existing_like = db.session.execute(db
        .select(like_table)
        .where(like_table.c.tweet_id == data['tweet_id'])
        .where(like_table.c.user_id == g.user.id)
    ).first() is not None


    if not existing_like:
        tweet.likes.append(g.user)
    else:
        tweet.likes.remove(g.user)

    db.session.commit()
    return singleton_response({"liked": not existing_like, "likes": tweet.report_likes()})

@bp.route('/add_comment', methods=['POST'])
def add_comment():
    data = get_validated_body(['tweet_id', 'content'])    
    
    tweet = db.session.execute(db
        .select(Tweet)
        .where(Tweet.id == data['tweet_id'])
    ).scalar()
    
    if not tweet:
        return build_error('Tweet not found', 404)
    
    new_comment = Comment(handle=g.user.handle, content=data['content'], tweet_id=tweet.id, created_at=datetime.utcnow())
    
    db.session.add(new_comment)
    db.session.commit()
    return singleton_response({'success': True, 'comment': new_comment.report()})

@bp.route('/create_channel', methods=['POST'])
def create_channel():
    # Untimed setup for the own-tweets selectivity sweep: creates a
    # channel row and enrolls g.user as a member. The sweep uses this
    # purely to pad fan-out with "noise" edges that load_own_tweets
    # should ignore.
    data = get_validated_body(['name'])
    description = data.get('description', '') or ''
    new_channel = Channel(name=data['name'], description=description)
    db.session.add(new_channel)
    db.session.flush()
    db.session.execute(channel_members_table.insert().values(
        user_id=g.user.id, channel_id=new_channel.id,
    ))
    db.session.commit()
    return singleton_response({'id': new_channel.id, 'name': new_channel.name})


@bp.route('/load_own_tweets', methods=['POST'])
def load_own_tweets():
    # Mirrors Jac's `walker load_own_tweets`: returns only the caller's
    # own tweets (no follow-traversal). Reports server-timed ms_traversal
    # (SQL round-trip) and ms_build_payload (per-tweet .report()) so the
    # shared bench driver can separate engine work from ORM serialization.
    t0 = time.perf_counter()
    tweets = db.session.execute(
        db.select(Tweet)
        .where(Tweet.author_id == g.user.id)
        .options(
            joinedload(Tweet.author),
            selectinload(Tweet.likes),
            selectinload(Tweet.comments),
        )
        .order_by(Tweet.created_at.desc())
    ).unique().scalars().all()
    ms_traversal = (time.perf_counter() - t0) * 1000

    t1 = time.perf_counter()
    payload = [t.report() for t in tweets]
    ms_build = (time.perf_counter() - t1) * 1000

    report = {
        'tweets': payload,
        'ms_traversal': round(ms_traversal, 4),
        'ms_build_payload': round(ms_build, 4),
    }
    return build_response([report], result=payload)


@bp.route('/import_data', methods=['POST'])
def import_data():
    if not request.is_json:
        abort(build_error('Expected JSON body'), 415)
        
    data = request.get_json()
    all_user_ids = db.session.scalars(db
        .select(User.id)
    ).all()
    for user in data['data'].values():
        user_obj = db.session.execute(db
            .select(User)
            .filter_by(username = user['email'])
        ).scalar()
        # insert all tweets at once
        # get the tweet IDs back in the order inserted
        if len(user['tweets']) > 0:
            ids = db.session.scalars(
                db.insert(Tweet).returning(Tweet.id, sort_by_parameter_order=True),
                [{
                    "content": t['content'],
                    'author_id': user_obj.id,
                    'created_at': datetime.fromisoformat(t['timestamp'])
                } for t in user['tweets']]
            ).all()
            for idx, tweet in enumerate(user['tweets']):
                tweet_id = ids[idx]
                # like the import_data on littleX, just use the first X users as the likes
                if tweet['likes'] > 0:
                    likes = [
                        {
                            "tweet_id": tweet_id,
                            "user_id": all_user_ids[user_idx]
                        } for user_idx in range(0, min(tweet['likes'], len(all_user_ids)))
                        # if we only have n user ids, only do n likes
                    ]
                    db.session.execute(like_table.insert(), likes)
        
        follows = [
            {
                "followee_id": followee,
                "follower_id": user_obj.id
            } for followee in user['following']
        ]
        
        if len(follows) > 0:
            db.session.execute(following_table.insert(), follows)
        
    viewer_obj = db.session.execute(db
        .select(User)
        .filter_by(handle = 'Viewer')
    ).scalar()
    viewer_follows = [
        {
            "followee_id": user_id,
            "follower_id": viewer_obj.id
        } for user_id in all_user_ids if user_id != viewer_obj.id
    ]
    db.session.execute(following_table.insert(), viewer_follows)

    db.session.commit()
    return singleton_response({'success': True})