from src import app, build_error, mongo, get_validated_body
from datetime import datetime
from flask import Blueprint, request, jsonify

bp = Blueprint('user', __name__)

# returns a stripped down version of what the jaseci /user/ endpoints return
def build_response(data, status_code: int = 200):
    response = jsonify({
        "data": data
    })
    response.status_code = status_code
    return response

@bp.route('/register', methods=['POST'])
def register():
    data = get_validated_body(['username', 'password'])
    
    existing_user = mongo.db.users.find_one({'username': data['username']})
    
    if existing_user:
        return build_error('User with username already exists', 400)
    
    result = mongo.db.users.insert_one({
        'username': data['username'],
        'handle': data['username'],
        'password': data['password'],
        'created_at': datetime.utcnow(),
        'bio': '',
        'following': [],
        'followers': []
    })
    
    return build_response({
        "username": data['username'],
        "token": data['username'],
        "root_id": str(result.inserted_id)
    })

@bp.route('/login', methods=['POST'])
def login():
    data = get_validated_body(['username', 'password'])
    
    existing_user = mongo.db.users.find_one({
        'username': data['username'], 'password': data['password']
    }, {'_id': 1, 'username': 1})
    if not existing_user:
        return build_error('User with provided username/password not found', 400)
    
    # in this version, the "bearer token" is just the username, for simplicity
    return build_response({
        "username": data['username'],
        "token": data['username'],
        "root_id": str(existing_user['_id'])
    })
    