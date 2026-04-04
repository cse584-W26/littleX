# LittleX MONGO

Drop in replacement for [littleX_fullstack](https://github.com/cse584-W26/littleX/tree/main/littleX_FULLSTACK)

Works for the same APIs, but backed with native Python/Flask/MongoDB

### Usage

Clone and install depencies, either with `uv sync` or `pip install -r requirements.txt`. The only real dependencies are flask/pymongo

You can run the app like any flask app by just running `flask --app src run`. By default, it'll look for a default mongo instance running locally If you specify a mongo url as an environment variable (e.g. `export DATABASE_URL=mongodb://localhost:27017/littleX_mongo`), it'll use that instead.