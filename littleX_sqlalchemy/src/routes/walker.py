from src import app, build_error, db, get_validated_body
from src.models import User, Tweet, Comment, like_table, following_table
from flask import Blueprint, request, g, abort, jsonify
from sqlalchemy.orm import aliased
from datetime import datetime

bp = Blueprint('walker', __name__)

# returns a stripped down version of what the jaseci /walker/ endpoints return
def build_response(reports, status_code: int = 200):
    response = jsonify({
        "data": {
            # left empty for now, since none of the benchmarks use it
            "result": {},
            "reports": reports
        },
        
    })
    response.status_code = status_code
    return response

@bp.before_request
def check_login():
    if auth_header := request.headers.get('Authorization'):
        # naively assumes the auth header is in the form "Bearer username"
        username = auth_header.replace('Bearer ', '')
        
        existing_user = db.session.execute(db.select(User).filter_by(username = username)).scalar()
        
        if existing_user:
            g.user = existing_user
        else:
            abort(build_error(f'User with username {username} not found. Did the database get cleared?', 400));

        g.user = existing_user
    
    else:
        abort(build_error('Not Logged In', 401))

@bp.route('/setup_profile', methods=['POST'])
def setup_profile():
    data = get_validated_body(['username', 'bio'])
    
    g.user.bio = data['bio']
    g.user.handle = data['username']

    db.session.commit()
    
    return build_response([g.user.report()])

@bp.route('/load_feed', methods=['POST'])
def load_feed():
    data = get_validated_body(['search_query'])
    
    other_user = aliased(User)
    
    eligible_users = aliased(User, db.union_all(db
        .select(other_user.id)
        .where(User.id == g.user.id)
        .join(other_user, User.following),
        db.select(User.id).where(User.id == g.user.id)
    ).subquery())
    tweets_query = (db
        .select(Tweet)
        .join_from(eligible_users, eligible_users.tweets)
    )
    
    search_query = data['search_query']
    if len(search_query) > 0:
        # do case insensitive filter for tweets that contain the query
        tweets_query = tweets_query.filter(Tweet.content.ilike(f'%{search_query}%'))
    
    tweets_query = tweets_query.order_by(Tweet.created_at)
    
    # unsupported by littleX, but useful
    if 'limit' in data:
        tweets_query = tweets_query.limit(data['limit'])
        
    results = db.session.execute(tweets_query).scalars().all()
    
    return build_response([r.report() for r in results])

@bp.route('/get_profile', methods=['POST'])
def get_profile():
    return build_response([g.user.report(True)])

@bp.route('/get_all_profiles', methods=['POST'])
def get_all_profiles():
    results = db.session.execute(db.select(User.id, User.handle, User.bio)).all()
    return build_response([dict(row._mapping) for row in results])

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
    
    return build_response([{"success": True}])

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
    
    return build_response([{"success": True}])

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
    
    return build_response([new_tweet.report()])

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
    
    return build_response([{'success': True}])

@bp.route('/like_tweet', methods=['POST'])
def like_tweet():
    data = get_validated_body(['tweet_id'])
    
    tweet = db.session.execute(db
        .select(Tweet)
        .where(Tweet.id == data['tweet_id'])
    ).scalar()
    
    if not tweet:
        return build_error('Tweet not found', 404)
        
    existing_like = db.session.execute(db
        .select(like_table)
        .where(like_table.c.tweet_id == data['tweet_id'])
    ).first() is not None
    
    
    if not existing_like:
        tweet.likes.append(g.user)
    else:
        tweet.likes.remove(g.user)
    
    db.session.commit()
    return build_response([{"liked": not existing_like, "likes": tweet.report_likes()}]);

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
    return build_response([{'success': True, 'comment': new_comment.report()}])

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
    
    return 'Success', 200