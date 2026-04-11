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

# Connection-pool config — moved off Flask-SQLAlchemy's defaults
# (pool_size=5, max_overflow=10) so concurrency benchmarks measure SQL
# performance instead of pool contention. The bench_concurrent.py workload
# goes up to concurrency=20, which would exhaust the default ceiling of 15
# total connections and stall every request above the limit.
#
# These values stay well within "fair ORM" territory — they are what every
# Flask-SQLAlchemy production deployment guide recommends. They are NOT
# the kind of expert tuning (prepare_threshold=0, server-side cursor pools,
# etc.) that belongs in a hand-tuned Postgres baseline.
#
# SQLite uses a StaticPool and rejects pool_size / max_overflow, so we only
# apply the pool config when running against a real backend (Postgres etc).
if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_size': 20,
        'max_overflow': 10,
        'pool_pre_ping': True,
        'pool_recycle': 3600,
    }
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
# Register the same blueprint under both /walker/ (legacy: Jac graph backend
# compatibility) and /function/ (mirrors the littleX_sqlalchemy_jac def:priv
# endpoints) so the same handlers serve both URL prefixes.
app.register_blueprint(walker_bp, url_prefix='/walker')
app.register_blueprint(walker_bp, url_prefix='/function', name='function')

# clear_data is also exposed under both prefixes.
def _reset_db():
    db.drop_all()
    db.create_all()
    return jsonify({"data": {"result": {"success": True, "message": "Database reset"},
                              "reports": [{"success": True, "message": "Database reset"}]}})

app.add_url_rule('/walker/clear_data', view_func=_reset_db, methods=['POST'])
app.add_url_rule('/function/clear_data', view_func=_reset_db, methods=['POST'],
                 endpoint='function_clear_data')

with app.app_context():
    db.create_all()