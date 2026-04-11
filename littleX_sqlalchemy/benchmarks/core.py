"""Core benchmark utilities for LittleX SQLAlchemy (def:priv endpoints).
Adapted from littleX-benchmarks/core.py for /function/ endpoints."""

import argparse
import json
import requests
from urllib.parse import urljoin
import time
import psutil

parser = argparse.ArgumentParser(
    prog='LittleX SQLAlchemy Benchmark',
    description='Benchmarks for LittleX SQLAlchemy backend'
)

parser.add_argument('-u', '--url', default='http://localhost:8000',
                    help='The base url of the backend server')

NS_TO_MS = 1000000


class LittleXSession(requests.Session):
    def __init__(self, base_url, *args, **kwargs):
        super(LittleXSession, self).__init__(*args, **kwargs)
        self.base_url = base_url

    def add_bearer_token(self, bearer):
        self.headers.update({'Authorization': f'Bearer {bearer}'})

    def request(self, method, url, **kwargs):
        modified_url = urljoin(self.base_url, url)

        for attempt in range(5):
            try:
                r = super(LittleXSession, self).request(method, modified_url, **kwargs)
                break
            except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
                if attempt < 4:
                    time.sleep(3 * (attempt + 1))
                else:
                    raise

        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            print(f'URL: {r.url}')
            print(f'Request Data: {r.request.body}')
            print(f'Response Data: {r.text}')
            raise e

        return r


class LittleXAPI():
    """API wrapper for SQLAlchemy version using /function/ endpoints."""

    def __init__(self, base_url: str):
        self.session = LittleXSession(base_url)

    def _get_result(self, r):
        """Extract result from function endpoint response (data.result)."""
        return r.json()['data']['result']

    def create_user(self, email: str, password: str, username: str = None, bio: str = 'Example Bio'):
        if username is None:
            username = email
        try:
            self.session.post('/user/register', json={
                'username': email,
                'password': password
            })
        except requests.HTTPError as e:
            print('Error when creating user. Might already exist.')
            raise e

        self.authenticate_user(email, password)
        r = self.setup_profile(username, bio)
        user_data = self._get_result(r)
        return user_data['id']

    def authenticate_user(self, username: str, password: str):
        r = self.session.post('/user/login', json={
            'username': username,
            'password': password
        })
        try:
            token = r.json()['data']['token']
        except KeyError as e:
            print(f'Could not find token in json: {r.json()}')
            raise e
        self.current_token = token
        self.session.add_bearer_token(token)

    def setup_profile(self, username: str, bio: str):
        return self.session.post('/function/setup_profile', json={
            'username': username,
            'bio': bio
        })

    def create_tweet(self, post: str):
        r = self.session.post('/function/create_tweet', json={
            'content': post
        })
        return self._get_result(r)['id']

    def like_tweet(self, tweet_id):
        return self.session.post('/function/like_tweet', json={
            'tweet_id': tweet_id
        })

    def comment_tweet(self, tweet_id, content: str):
        return self.session.post('/function/add_comment', json={
            'tweet_id': tweet_id,
            'content': content
        })

    def load_feed(self):
        return self.session.post('/function/load_feed', json={})

    def load_feed_with_search(self, search_query: str):
        return self.session.post('/function/load_feed', json={
            'search_query': search_query
        })

    def follow_user(self, target_id):
        return self.session.post('/function/follow_user', json={
            'target_id': target_id
        })

    def unfollow_user(self, target_id):
        return self.session.post('/function/unfollow_user', json={
            'target_id': target_id
        })

    def get_profile(self):
        return self.session.post('/function/get_profile', json={})

    def get_all_profiles(self):
        return self.session.post('/function/get_all_profiles', json={})

    def delete_tweet(self, tweet_id):
        return self.session.post('/function/delete_tweet', json={
            'tweet_id': tweet_id
        })

    def import_data(self, data):
        return self.session.post('/function/import_data', json={
            'data': data
        })

    def clear_data(self):
        return self.session.post('/function/clear_data', json={})


class Timer():
    def __init__(self, operation: str = "Operation"):
        self.start = None
        self.operation = operation

    def __enter__(self):
        self.start = time.time_ns()

    def __exit__(self, exc_type, exc_val, exc_tb):
        diff = (time.time_ns() - self.start) // NS_TO_MS
        print(f'{self.operation} took {diff} ms')


if __name__ == "__main__":
    print('Don\'t run this directly.')
