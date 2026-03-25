from flask_sqlalchemy import SQLAlchemy
from flask import request, jsonify, abort, Flask
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime
import os

class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

app = Flask(__name__)
if database_url := os.environ.get('DATABASE_URL'):
    print('Database URL provided in environment. Using that')
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    print('No Database URL provided. Using memory')
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite+pysqlite:///:memory:'
db.init_app(app)

def build_error(message: str, status_code: int):
    response = jsonify({
        "error": message
    })
    response.status_code = status_code
    return response

# aborts if the following attributes aren't in a JSON body
# returns the body otherwise
def get_validated_body(keys: list[str]):
    if not request.is_json:
        abort(build_error('Expected JSON body', 415))
    
    data = request.get_json()
    for key in keys:
        if key not in data:
            abort(build_error(f'Missing expected key {key}', 422))
    
    return data
    
import src.models

from src.routes.user import bp as user_bp
from src.routes.walker import bp as walker_bp

app.register_blueprint(user_bp, url_prefix='/user')
app.register_blueprint(walker_bp, url_prefix='/walker')

# changed url to line up with the one the script expects
@app.route('/walker/clear_data', methods=["POST"])
def reset_db():
    db.drop_all()
    db.create_all()
    return "Database Reset", 200

with app.app_context():
    db.create_all()