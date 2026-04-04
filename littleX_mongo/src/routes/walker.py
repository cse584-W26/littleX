from src import app, build_error, mongo, get_validated_body
from flask import Blueprint, request, g, abort, jsonify
from datetime import datetime
from bson import ObjectId
import re

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
        
        existing_user = mongo.db.users.find_one({'username': username})
        
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
    
    result = mongo.db.users.find_one_and_update({'_id': g.user['_id']}, {'$set': {
        'bio': data['bio'],
        'handle': data['username']
    }})
    
    result['id'] = result['_id']
    
    return build_response([result])

@bp.route('/load_feed', methods=['POST'])
def load_feed():
    data = get_validated_body(['search_query'])
    
    regex_query = re.compile(f'.*{data['search_query']}.*', re.IGNORECASE)
    
    query = [
        # find current user
        {'$match': {'_id': g.user['_id']}},
        # add the current user to the following, for simplicity
        {'$addFields': {
            'following': {
                '$concatArrays': ['$following', [g.user['_id']]]
            }
        }},
        {'$unwind': '$following'},
        {'$lookup': {
            'from': 'users',
            'localField': 'following',
            'foreignField': '_id',
            'as': 'new_user'
        }},
        {'$unwind': '$new_user'},
        {'$replaceRoot': {'newRoot': '$new_user'}},
        {'$lookup': {
            'from': 'tweets',
            'localField': '_id',
            'foreignField': 'author_id',
            'as': 'tweets'
        }},
        {'$unwind': '$tweets'},
        {'$addFields': {'tweets.author_username': '$handle'}},
        {'$replaceRoot': {'newRoot': '$tweets'}},
        # match any thing that contains the query, also case insensitive
        # when query is nothing, will just match anything
        {'$match': {'content': {'$regex': regex_query}}},
        {'$sort': {'created_at': -1}},
        {'$project': {
            '_id': 0,
            'id': {'$toString': '$_id'},
            'author_username': 1,
            'comments': {
                'handle': 1,
                'content': 1,
                'created_at': {'$toString': '$created_at'}
            },
            'created_at': {'$toString': '$created_at'},
            'likes': 1,
            'content': 1
        }}
    ]
    
    # unsupported by littleX, but useful
    if 'limit' in data:
        query.append({'$limit': int(data['limit'])})
        
    results = mongo.db.users.aggregate(query)
    
    return build_response(results)

@bp.route('/get_profile', methods=['POST'])
def get_profile():
    match_pipeline = [
        {'$match': {
            '$expr': {'$in': ['$_id', '$$targets']}
        }},
        {'$project': {
            '_id': 0,
            'id': {'$toString': '$_id'},
            'username': '$handle'
        }}
    ]
    query = [
        {'$match': {'_id': g.user['_id']}},
        {'$lookup': {
            'from': 'users',
            'as': 'following',
            'let': {
                'targets': '$following'
            },
            'pipeline': match_pipeline
        }},
        {'$lookup': {
            'from': 'users',
            'as': 'followers',
            'let': {
                'targets': '$followers'
            },
            'pipeline': match_pipeline
        }},
        {'$lookup': {
            'from': 'tweets',
            'as': 'tweets',
            'localField': '_id',
            'foreignField': 'author_id'
        }},
        {'$project': {
            'created_at': {'$toString': '$created_at'},
            'id': {'$toString': '$_id'},
            '_id': 0,
            'tweets': 1,
            'following': 1,
            'followers': 1,
            'bio': 1,
            'username': '$handle'            
        }},
    ]
    results = mongo.db.users.aggregate(query)
    return build_response(results)

@bp.route('/get_all_profiles', methods=['POST'])
def get_all_profiles():
    results = mongo.db.users.find({}, {
        '_id': 0,
        'id': {'$toString': '$_id'},
        'username': '$handle',
        'bio': 1
    })
    return build_response(results)

@bp.route('/follow_user', methods=['POST'])
def follow_user():
    data = get_validated_body(['target_id'])
    
    _id = ObjectId(data['target_id'])
    
    result = mongo.db.users.update_one({'_id': _id}, {'$addToSet': {
        'followers': g.user['_id']
    }})
    
    if result.matched_count == 0:
        return build_error('User not found', 400)
    
    mongo.db.users.update_one({'_id': g.user['_id']}, {'$addToSet': {
        'following': _id
    }})
    
    return build_response([{"success": True}])

@bp.route('/unfollow_user', methods=['POST'])
def unfollow_user():
    data = get_validated_body(['target_id'])
    
    _id = ObjectId(data['target_id'])
    result = mongo.db.users.update_one({'_id': _id}, {'$pull': {
        'followers': g.user['_id']
    }})
    
    if result.matched_count == 0:
        return build_error('User not found', 400)
    
    mongo.db.users.update_one({'_id': g.user['_id']}, {'$pull': {
        'following': _id
    }})
    
    return build_response([{"success": True}])

@bp.route('/create_tweet', methods=['POST'])
def create_tweet():
    data = get_validated_body(['content'])
    
    tweet = {
        'content': data['content'],
        'author_id': g.user['_id'],
        'created_at': datetime.utcnow(),
        'likes': [],
        'comments': []
    }
    
    result = mongo.db.tweets.insert_one(tweet)
    tweet['id'] = str(result.inserted_id)
    del tweet['_id']
    tweet['author_id'] = str(tweet['author_id'])
    tweet['created_at'] = tweet['created_at'].isoformat()
    return build_response([tweet])

@bp.route('/delete_tweet', methods=['POST'])
def delete_tweet():
    data = get_validated_body(['tweet_id'])
    
    result = mongo.db.tweets.delete_one({
        '_id': ObjectId(data['tweet_id']),
        'author_id': g.user['_id']
    })
    
    if result.deleted_count == 0:
        return build_error('Tweet not found', 404)
        
    return build_response([{'success': True}])

@bp.route('/like_tweet', methods=['POST'])
def like_tweet():
    data = get_validated_body(['tweet_id'])
    
    result = mongo.db.tweets.find_one({'_id': ObjectId(data['tweet_id'])})
    
    if not result:
        return build_error('Tweet not found', 400)
    
    likes = result['likes']
    existing_like = g.user['username'] in likes
    
    if existing_like:
        mongo.db.tweets.update_one({'_id': ObjectId(data['tweet_id'])}, {'$pull': {
            'likes': g.user['username']
        }})
        likes.remove(g.user['username'])
    else:
        mongo.db.tweets.update_one({'_id': ObjectId(data['tweet_id'])}, {'$addToSet': {
            'likes': g.user['username']
        }})
        likes.append(g.user['username'])
    
    return build_response([{"liked": not existing_like, "likes": likes}])

@bp.route('/add_comment', methods=['POST'])
def add_comment():
    data = get_validated_body(['tweet_id', 'content'])
    
    new_comment = {
        'handle': g.user['handle'],
        'content': data['content'],
        'created_at': datetime.utcnow()
    }
    
    result = mongo.db.tweets.update_one({
        '_id': ObjectId(data['tweet_id'])
    }, {'$push': {
        'comments': new_comment
    }})
    if result.modified_count == 0:
        return build_error('Tweet not found', 404)
    
    new_comment['created_at'] = new_comment['created_at'].isoformat()
    
    return build_response([{'success': True, 'comment': new_comment}])

@bp.route('/import_data', methods=['POST'])
def import_data():
    if not request.is_json:
        abort(build_error('Expected JSON body'), 415)
        
    data = request.get_json()
    
    all_users = [u for u in mongo.db.users.find({}, {'_id': 1, 'username': 1})]
    all_user_names = [u['username'] for u in all_users]
    all_user_ids = [u['_id'] for u in all_users]
    
    followers_map = {u['_id']: [] for u in all_users}
    for user in data['data'].values():
        _id = user['jaseci_user_id']
        for follow in user['following']:
            followers_map[follow].append(_id)
            
    viewer = mongo.db.users.find_one({'handle': 'Viewer'})
    viewer_id = viewer['_id']
    
    for user in data['data'].values():
        _id = user['jaseci_user_id']
        if len(user['tweets']) > 0:
            tweets = [{
                "content": t['content'],
                'author_id': _id,
                'created_at': datetime.fromisoformat(t['timestamp']),
                # if we only have n user ids, only do n likes
                'likes': all_user_names[:min(t['likes'], len(all_user_names))],
            } for t in user['tweets']]
            
            mongo.db.tweets.insert_many(tweets)
        
        mongo.db.users.update_one({'username': user['email']}, {
            '$set': {
                'following': user['following'],
                'followers': followers_map[_id] + [viewer_id]
            }
        })
        
    # don't let the viewer follow themself
    all_user_ids.remove(viewer_id)
    # have the viewer follow all users
    mongo.db.users.update_one({'handle': 'Viewer'}, {
        '$set': {'following': all_user_ids}
    })
    return 'Success', 200