# LittleX Fork 

We're mostly using [`littleX_FULLSTACK`](https://github.com/cse584-W26/littleX/tree/main/littleX_FULLSTACK) as our fork to test on. There's a new endpoint `(POST) /import_data` that accepts a JSON body ([schema here](https://github.com/cse584-W26/littleX-benchmarks?tab=readme-ov-file#dataset-schema)) to import tweets, follows, and likes for a existing users. Users must be created seperately from the `/user/register` API.

The [littleX benchmarks](https://github.com/cse584-W26/littleX-benchmarks) are designed to work with `littleX_FULLSTACK`.

### SQLAlchemy Implementation

In the [`littleX_sqlalchemy`](https://github.com/cse584-W26/littleX/tree/main/littleX_sqlalchemy), there is a Flask app using SQLAlchemy that implements the following APIs exactly like `littleX_FULLSTACK`, so any scripts that work for fullstack can be compared to a sqlalchemy baseline. There's an example comparison for data imports in the README in that folder.

Authentication is not implemented properly at the moment, instead, passing a header `Authorization: Bearer <username>` will let the request be logged-in as `<username>`.

#### Implemented APIs

```
/user/login
/user/register

/walker/add_comment
/walker/create_tweet
/walker/delete_tweet
/walker/follow_user
/walker/unfollow_user
/walker/get_all_profiles
/walker/get_profile
/walker/import_data
/walker/like_tweet
/walker/load_feed
/walker/setup_profile
```

There's also another endpoint `(POST) /walker/clear_data` which will just clear the database, avoiding having to teardown a Postgres DB every time.