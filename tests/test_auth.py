import unittest

from app.services.auth import MockFirebaseAuthService, VerifiedUser


class MockFirebaseAuthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = VerifiedUser(
            uid="user-1",
            email="alice@example.com",
            name="Alice",
        )
        self.service = MockFirebaseAuthService(users={"user-1": self.user})

    def test_verify_token_returns_verified_user(self) -> None:
        self.service.set_token("valid-token", "user-1")
        result = self.service.verify_token("valid-token")
        self.assertEqual(result.uid, "user-1")
        self.assertEqual(result.email, "alice@example.com")

    def test_verify_token_raises_for_invalid_token(self) -> None:
        with self.assertRaises(ValueError):
            self.service.verify_token("bad-token")

    def test_get_user_returns_user(self) -> None:
        result = self.service.get_user("user-1")
        self.assertEqual(result.uid, "user-1")

    def test_get_user_raises_for_missing_user(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_user("nobody")


if __name__ == "__main__":
    unittest.main()
