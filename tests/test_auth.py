import unittest

from app.api.dependencies import get_current_user

from fastapi import HTTPException


class HeaderAuthTests(unittest.TestCase):
    def test_x_user_id_header_returns_verified_user(self) -> None:
        user = get_current_user(x_user_id="user-1")
        self.assertEqual(user.uid, "user-1")

    def test_x_user_id_with_spaces_is_stripped(self) -> None:
        user = get_current_user(x_user_id="  user-1  ")
        self.assertEqual(user.uid, "user-1")

    def test_empty_x_user_id_raises(self) -> None:
        with self.assertRaises(HTTPException):
            get_current_user(x_user_id="")

    def test_blank_x_user_id_raises(self) -> None:
        with self.assertRaises(HTTPException):
            get_current_user(x_user_id="   ")


if __name__ == "__main__":
    unittest.main()
