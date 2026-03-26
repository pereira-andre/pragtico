"""Unit tests for CSRF protection and rate limiting."""

import unittest

from security import RateLimiter, generate_csrf_token, init_csrf

from flask import Flask


class CSRFTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "test-secret"
        self.app.config["TESTING"] = True
        init_csrf(self.app)

        @self.app.route("/form-post", methods=["POST"])
        def form_post():
            return "ok"

        @self.app.route("/api/data", methods=["POST"])
        def api_data():
            return "ok"

        self.client = self.app.test_client()

    def test_post_without_token_returns_403(self):
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid-token"
        resp = self.client.post("/form-post")
        self.assertEqual(resp.status_code, 403)

    def test_post_with_valid_token_returns_200(self):
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid-token"
        resp = self.client.post("/form-post", data={"csrf_token": "valid-token"})
        self.assertEqual(resp.status_code, 200)

    def test_post_with_wrong_token_returns_403(self):
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "valid-token"
        resp = self.client.post("/form-post", data={"csrf_token": "wrong-token"})
        self.assertEqual(resp.status_code, 403)

    def test_api_endpoints_exempt_from_csrf(self):
        resp = self.client.post("/api/data", json={"key": "value"})
        self.assertEqual(resp.status_code, 200)

    def test_get_requests_exempt(self):
        @self.app.route("/form-get")
        def form_get():
            return "ok"
        resp = self.client.get("/form-get")
        self.assertEqual(resp.status_code, 200)

    def test_csrf_header_accepted(self):
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = "header-token"
        resp = self.client.post("/form-post", headers={"X-CSRF-Token": "header-token"})
        self.assertEqual(resp.status_code, 200)

    def test_generate_csrf_token_consistent(self):
        with self.app.test_request_context():
            with self.app.test_client() as c:
                with c.session_transaction() as sess:
                    pass
                with c.application.test_request_context():
                    from flask import session as s
                    token1 = generate_csrf_token()
                    token2 = generate_csrf_token()
                    self.assertEqual(token1, token2)


class RateLimiterTests(unittest.TestCase):
    def test_allows_within_limit(self):
        limiter = RateLimiter(max_calls=3, window_seconds=60)
        self.assertTrue(limiter.is_allowed("key1"))
        self.assertTrue(limiter.is_allowed("key1"))
        self.assertTrue(limiter.is_allowed("key1"))

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_calls=2, window_seconds=60)
        self.assertTrue(limiter.is_allowed("key1"))
        self.assertTrue(limiter.is_allowed("key1"))
        self.assertFalse(limiter.is_allowed("key1"))

    def test_separate_keys(self):
        limiter = RateLimiter(max_calls=1, window_seconds=60)
        self.assertTrue(limiter.is_allowed("key1"))
        self.assertTrue(limiter.is_allowed("key2"))
        self.assertFalse(limiter.is_allowed("key1"))

    def test_remaining(self):
        limiter = RateLimiter(max_calls=5, window_seconds=60)
        self.assertEqual(limiter.remaining("key1"), 5)
        limiter.is_allowed("key1")
        self.assertEqual(limiter.remaining("key1"), 4)


if __name__ == "__main__":
    unittest.main()
